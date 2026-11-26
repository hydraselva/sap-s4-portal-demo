"""
Delivery challan builder: SAP material document → GST-ready challan data.

Supported goods movements
  303       Inter-plant stock transfer      plant  → plant
  541 / Z41 Sub-contracting (job work)      plant  → supplier
  161 / 122 Return to vendor                plant  → supplier  (priced from PO)
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import re

from .gst import amount_in_words, money, split_gst
from .sap_client import SAPError, key, lit

MD_SRV, BP_SRV, PR_SRV, PO_SRV = ("API_MATERIAL_DOCUMENT_SRV", "API_BUSINESS_PARTNER",
                                  "API_PRODUCT_SRV", "API_PURCHASEORDER_PROCESS_SRV")

MOVEMENTS = {
    "303": "Delivery Challan – Inter-Plant Transfer (303)",
    "541": "Delivery Challan – Sub-contracting / Job Work (541)",
    "Z41": "Delivery Challan – Sub-contracting / Job Work (Z41)",
    "161": "Delivery Challan – Return to Vendor (161)",
    "122": "Delivery Challan – Return to Vendor (122)",
}
RETURNS = {"161", "122"}
COPY_LABELS = ["ORIGINAL FOR CONSIGNEE", "DUPLICATE FOR TRANSPORTER", "TRIPLICATE FOR SUPPLIER", "EXTRA COPY"]


def sap_date(v):
    m = re.search(r"Date\((-?\d+)", v or "")
    return datetime.fromtimestamp(int(m.group(1)) / 1000, timezone.utc) if m else None


def fmt_date(v):
    d = sap_date(v)
    return d.strftime("%d-%m-%Y") if d else "—"


# ── master data lookups (cached) ──────────────────────────────────────
def get_party(client, bp_number):
    """Name, address, state code and GSTIN of a business partner."""
    empty = {"bp": bp_number or "", "name": "—", "lines": [], "state": "", "gstin": "—"}
    if not bp_number:
        return empty
    d = client.get(BP_SRV, f"A_BusinessPartner{key(_=bp_number)}",
                   {"$expand": "to_BusinessPartnerAddress"}, cache=True, allow_404=True)
    if d is None:
        return dict(empty, name=f"BP {bp_number} (not found)")
    tax = client.results(BP_SRV, "A_BusinessPartnerTaxNumber",
                         {"$filter": f"BusinessPartner eq {lit(bp_number)}"}, cache=True)
    gstin = next((t["BPTaxNumber"] for t in tax if t.get("BPTaxType") == "IN3"), "Not maintained")
    addrs = d.get("to_BusinessPartnerAddress", {}).get("results", [])
    a = next((x for x in addrs if x.get("IsDefaultAddress")), addrs[0] if addrs else {})
    lines = [x for x in (a.get("HouseNumber"), a.get("StreetName"), a.get("CityName")) if x]
    if a.get("Region") or a.get("PostalCode"):
        lines.append(f"{a.get('Region', '')} – {a.get('PostalCode', '')}".strip(" –"))
    return {"bp": bp_number, "name": d.get("BusinessPartnerFullName", bp_number),
            "lines": lines, "state": a.get("Region", ""), "gstin": gstin}


def get_plant(client, plant_to_bp, plant):
    bp = plant_to_bp.get(plant or "")
    if not bp:
        return {"bp": "", "name": f"Plant {plant or '—'}", "lines": ["Plant not mapped to a business partner"],
                "state": "", "gstin": "—"}
    party = get_party(client, bp)
    return dict(party, plant=plant)


def get_material(client, material, plant, default_hsn):
    """Description, valuation price (Standard → Moving Average) and HSN."""
    def desc():
        d = client.get(PR_SRV, f"A_ProductDescription{key(Product=material, Language='EN')}",
                       cache=True, allow_404=True)
        return (d or {}).get("ProductDescription") or material

    def price():
        d = client.get(PR_SRV, f"A_ProductValuation{key(Product=material, ValuationArea=plant, ValuationType='')}",
                       cache=True, allow_404=True) or {}
        std, mav = float(d.get("StandardPrice") or 0), float(d.get("MovingAveragePrice") or 0)
        return (std, "Standard price") if std > 0 else (mav, "Moving average price")

    def hsn():
        d = client.get(PR_SRV, f"A_ProductPlantIntlTrd{key(Product=material, Plant=plant)}",
                       cache=True, allow_404=True) or {}
        code = (d.get("ConsumptionTaxCtrlCode") or "").strip()
        return (code, "Plant foreign trade data") if code else (default_hsn, "Default HSN")

    with ThreadPoolExecutor(max_workers=3) as ex:
        fd, fp, fh = ex.submit(desc), ex.submit(price), ex.submit(hsn)
        (p, p_src), (h, h_src) = fp.result(), fh.result()
        return {"desc": fd.result(), "price": p, "price_src": p_src, "hsn": h, "hsn_src": h_src}


def get_po_price(client, po, po_item, material):
    """Net price from the purchase order line (exact item → same material → first line)."""
    if not po:
        return 0.0
    items = client.results(PO_SRV, "A_PurchaseOrderItem", {"$filter": f"PurchaseOrder eq {lit(po)}"})
    if not items:
        return 0.0
    want = str(po_item or "").zfill(5)
    pick = (next((i for i in items if str(i.get("PurchaseOrderItem", "")).zfill(5) == want), None)
            or next((i for i in items if i.get("Material") == material), None) or items[0])
    return float(pick.get("NetPriceAmount") or 0)


def get_po_supplier(client, po):
    if not po:
        return ""
    d = client.get(PO_SRV, f"A_PurchaseOrder{key(_=po)}", {"$select": "PurchaseOrder,Supplier"},
                   cache=True, allow_404=True)
    return (d or {}).get("Supplier", "")


# ── validation & build ────────────────────────────────────────────────
def validate_movement(client, doc):
    doc = (doc or "").strip()
    if not re.fullmatch(r"\d{10}", doc):
        return None, "Enter a 10-digit material document number."
    rows = client.results(MD_SRV, "A_MaterialDocumentItem",
                          {"$filter": f"MaterialDocument eq {lit(doc)}", "$select": "GoodsMovementType", "$top": 1})
    if not rows:
        return None, f"Material document {doc} was not found in SAP."
    mov = (rows[0].get("GoodsMovementType") or "").upper()
    if mov not in MOVEMENTS:
        return mov, f"Movement type {mov} cannot be used for a delivery challan. Allowed: {', '.join(MOVEMENTS)}."
    return mov, None


def build_challan(client, settings, doc):
    mov, err = validate_movement(client, doc)
    if err:
        raise SAPError(err)

    hdrs = client.results(MD_SRV, "A_MaterialDocumentHeader",
                          {"$filter": f"MaterialDocument eq {lit(doc)}", "$expand": "to_MaterialDocumentItem"})
    hdr = hdrs[0]
    items = sorted(hdr.get("to_MaterialDocumentItem", {}).get("results", []),
                   key=lambda i: i.get("MaterialDocumentItem", ""))
    is_return = mov in RETURNS
    lines = items if is_return else [i for i in items if i.get("DebitCreditCode") == "H"]
    if not lines:
        raise SAPError("The document has no issuing line items.")
    first = lines[0]
    receiving_line = next((i for i in items if i.get("DebitCreditCode") == "S"), {})

    sender = get_plant(client, settings.plant_to_bp, first.get("Plant"))
    if mov == "303":
        receiver = get_plant(client, settings.plant_to_bp, receiving_line.get("Plant"))
        refs = {"Issuing storage location": first.get("StorageLocation") or "—",
                "Receiving storage location": receiving_line.get("StorageLocation") or "—"}
        labels = ("Consignor (Sending Plant)", "Consignee (Receiving Plant)")
    else:
        po = first.get("PurchaseOrder", "")
        receiver = get_party(client, first.get("Supplier") or get_po_supplier(client, po))
        refs = {"Purchase order": po or "—"}
        if is_return:
            refs["Reason"] = first.get("MaterialDocumentItemText") or "—"
            labels = ("Consignor (Returning Plant)", "Consignee (Vendor)")
        else:
            refs["Nature"] = "Returnable – sent for job work"
            labels = ("Consignor (Dispatching Plant)", "Consignee (Job Worker)")

    def build_row(item):
        mat, plant = item.get("Material", ""), item.get("Plant", "")
        md = get_material(client, mat, plant, settings.default_hsn)
        price, src = md["price"], md["price_src"]
        if is_return:
            price = get_po_price(client, item.get("PurchaseOrder"), item.get("PurchaseOrderItem"), mat)
            src = f"PO {item.get('PurchaseOrder')} net price"
        qty = float(item.get("QuantityInBaseUnit") or 0)
        taxable = money(price * qty)
        tax = split_gst(sender["state"], receiver["state"], taxable, settings.gst_rate)
        return {"material": mat, "desc": md["desc"], "hsn": md["hsn"], "hsn_src": md["hsn_src"],
                "qty": qty, "uom": item.get("MaterialBaseUnit", ""), "rate": money(price),
                "price_src": src, "taxable": taxable, **tax}

    with ThreadPoolExecutor(max_workers=min(6, len(lines))) as ex:
        rows = list(ex.map(build_row, lines))   # map keeps SAP line order

    for n, r in enumerate(rows, 1):
        r["sl"] = n
    totals = {k: sum((r[k] for r in rows), money(0)) for k in ("taxable", "cgst", "sgst", "igst")}
    grand = totals["taxable"] + totals["cgst"] + totals["sgst"] + totals["igst"]
    qty_by_uom = {}
    for r in rows:
        qty_by_uom[r["uom"]] = qty_by_uom.get(r["uom"], 0) + r["qty"]

    n = settings.items_per_page
    return {
        "title": MOVEMENTS[mov], "mov": mov, "is_return": is_return,
        "doc": hdr.get("MaterialDocument"), "doc_year": hdr.get("MaterialDocumentYear"),
        "doc_date": fmt_date(hdr.get("DocumentDate")), "post_date": fmt_date(hdr.get("PostingDate")),
        "challan_no": f"DC/{hdr.get('MaterialDocumentYear')}/{hdr.get('MaterialDocument')}",
        "sender": sender, "receiver": receiver, "labels": labels, "refs": refs,
        "rows": rows, "pages": [rows[i:i + n] for i in range(0, len(rows), n)],
        "totals": totals, "grand_total": grand, "grand_words": amount_in_words(grand),
        "qty_by_uom": qty_by_uom,
        "tax_mode": "IGST (inter-state)" if totals["igst"] else "CGST + SGST (intra-state)",
        "copies": COPY_LABELS,
    }
