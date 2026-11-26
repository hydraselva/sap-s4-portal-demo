"""
Mock SAP S/4HANA OData V2 server (Flask blueprint).

Serves the same entity sets and JSON shape ({"d": {...}} / {"d": {"results": [...]}})
as the real SAP Business Accelerator Hub APIs, so the portal code runs unchanged
against either this mock or a real S/4HANA tenant.

Supported query options: $filter (eq/ne/ge/le/gt/lt, and/or, parentheses,
startswith, substringof, tolower, datetime literals), $select, $expand, $top,
$skip, $orderby, and Basic authentication.
"""
import base64
import os
import re
from datetime import datetime, timezone

from flask import Blueprint, jsonify, request

from . import data

bp = Blueprint("mock_sap", __name__, url_prefix="/mock-sap/sap/opu/odata/sap")

MOCK_USER = os.environ.get("MOCK_SAP_USER", "DEMO_API")
MOCK_PASS = os.environ.get("MOCK_SAP_PASSWORD", "demo-pass")


# ══════════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════════
def odata_date(dt):
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return f"/Date({int(dt.timestamp() * 1000)})/"


def odata_error(status, code, message):
    return jsonify({"error": {"code": code, "message": {"lang": "en", "value": message}}}), status


@bp.before_request
def check_auth():
    hdr = request.headers.get("Authorization", "")
    if not hdr.startswith("Basic "):
        return odata_error(401, "401", "Authentication required")
    try:
        user, _, pwd = base64.b64decode(hdr[6:]).decode().partition(":")
    except Exception:
        return odata_error(401, "401", "Malformed credentials")
    if user != MOCK_USER or pwd != MOCK_PASS:
        return odata_error(401, "401", "Invalid user or password")


# ── tiny $filter parser ───────────────────────────────────────────────
_TOKEN = re.compile(
    r"\s*(?:"
    r"(?P<lp>\()|(?P<rp>\))|"
    r"(?P<and>and\b)|(?P<or>or\b)|"
    r"startswith\((?P<sw_f>\w+),'(?P<sw_v>(?:[^']|'')*)'\)\s*(?:eq\s+true)?|"
    r"substringof\('(?P<so_v>(?:[^']|'')*)',\s*(?:tolower\()?(?P<so_f>\w+)\)?\)\s*(?:eq\s+true)?|"
    r"(?P<f>\w+)\s+(?P<op>eq|ne|ge|le|gt|lt)\s+"
    r"(?:datetime'(?P<dt>[^']*)'|'(?P<s>(?:[^']|'')*)'|(?P<lit>true|false|null|-?\d+(?:\.\d+)?))"
    r")", re.I)


def _tokenize(expr):
    pos, out = 0, []
    expr = expr.strip()
    while pos < len(expr):
        m = _TOKEN.match(expr, pos)
        if not m or m.end() == pos:
            raise ValueError(f"Unsupported $filter near: {expr[pos:pos + 40]!r}")
        out.append(m)
        pos = m.end()
    return out


def _cmp(a, op, b):
    if op == "eq":
        return a == b
    if op == "ne":
        return a != b
    if a is None or b is None:
        return False
    return {"ge": a >= b, "le": a <= b, "gt": a > b, "lt": a < b}[op]


def _pred(m):
    if m.group("sw_f"):
        f, v = m.group("sw_f"), m.group("sw_v").replace("''", "'")
        return lambda r: str(r.get(f) or "").startswith(v)
    if m.group("so_f"):
        f, v = m.group("so_f"), m.group("so_v").replace("''", "'").lower()
        return lambda r: v in str(r.get(f) or "").lower()
    f, op = m.group("f"), m.group("op").lower()
    if m.group("dt") is not None:
        v = datetime.fromisoformat(m.group("dt")).replace(tzinfo=timezone.utc)
        return lambda r: _cmp(r.get(f), op, v)
    if m.group("s") is not None:
        v = m.group("s").replace("''", "'")
        return lambda r: _cmp(str(r.get(f) or ""), op, v)
    lit = m.group("lit").lower()
    v = {"true": True, "false": False, "null": None}.get(lit, lit)
    if isinstance(v, str):
        v = float(v)
    return lambda r: _cmp(r.get(f), op, v)


def parse_filter(expr):
    """Return predicate(row) for an OData V2 $filter expression."""
    toks, i = _tokenize(expr), 0

    def peek(kind):
        return i < len(toks) and toks[i].group(kind)

    def parse_or():
        nonlocal i
        left = parse_and()
        while peek("or"):
            i += 1
            right = parse_and()
            left = (lambda a, b: lambda r: a(r) or b(r))(left, right)
        return left

    def parse_and():
        nonlocal i
        left = parse_atom()
        while peek("and"):
            i += 1
            right = parse_atom()
            left = (lambda a, b: lambda r: a(r) and b(r))(left, right)
        return left

    def parse_atom():
        nonlocal i
        if peek("lp"):
            i += 1
            inner = parse_or()
            if not peek("rp"):
                raise ValueError("Missing ')' in $filter")
            i += 1
            return inner
        tok = toks[i]
        i += 1
        return _pred(tok)

    fn = parse_or()
    if i != len(toks):
        raise ValueError("Trailing tokens in $filter")
    return fn


def serialize(row):
    return {k: (odata_date(v) if isinstance(v, datetime) else v) for k, v in row.items()}


def collection(rows, entity):
    """Apply $filter/$orderby/$skip/$top/$select and wrap as OData V2 collection."""
    args = request.args
    try:
        if args.get("$filter"):
            pred = parse_filter(args["$filter"])
            rows = [r for r in rows if pred(r)]
    except ValueError as e:
        return odata_error(400, "400", str(e))
    if args.get("$orderby"):
        for part in reversed(args["$orderby"].split(",")):
            f, _, direction = part.strip().partition(" ")
            rows = sorted(rows, key=lambda r: (r.get(f) is None, r.get(f) or ""),
                          reverse=direction.lower() == "desc")
    skip = int(args.get("$skip", 0) or 0)
    top = int(args.get("$top", 0) or 0)
    rows = rows[skip:skip + top] if top else rows[skip:]
    out = [select(serialize(r)) for r in rows]
    for r in out:
        r["__metadata"] = {"type": f"{entity}Type"}
    return jsonify({"d": {"results": out}})


def select(row):
    sel = request.args.get("$select")
    if not sel:
        return row
    keep = {s.strip() for s in sel.split(",")}
    return {k: v for k, v in row.items() if k in keep or k.startswith("to_")}


def single(row, entity):
    if row is None:
        return odata_error(404, "404", f"Resource not found for segment '{entity}'")
    out = select(serialize(row))
    out["__metadata"] = {"type": f"{entity}Type"}
    return jsonify({"d": out})


def parse_key(raw):
    """"Product='MAT-1001',Plant='1000'" → dict ;  "'10000001'" → {'': '10000001'}"""
    pairs = re.findall(r"(?:(\w+)=)?'((?:[^']|'')*)'", raw)
    return {k: v.replace("''", "'") for k, v in pairs}


# ══════════════════════════════════════════════════════════════════════
# Material documents  (API_MATERIAL_DOCUMENT_SRV)
# ══════════════════════════════════════════════════════════════════════
def _mat_doc_rows():
    headers, items = [], []
    for num, doc in data.build_material_documents().items():
        doc_items = []
        for it in doc["items"]:
            row = dict(it, MaterialDocument=num, MaterialDocumentYear=str(doc["date"].year),
                       GoodsMovementType=doc["mov"])
            doc_items.append(row)
        items.extend(doc_items)
        headers.append({"MaterialDocument": num, "MaterialDocumentYear": str(doc["date"].year),
                        "DocumentDate": doc["date"], "PostingDate": doc["date"],
                        "MaterialDocumentHeaderText": f"Demo document {num}",
                        "_items": doc_items})
    return headers, items


@bp.get("/API_MATERIAL_DOCUMENT_SRV/A_MaterialDocumentHeader")
def mat_doc_headers():
    headers, _ = _mat_doc_rows()
    expand = "to_MaterialDocumentItem" in request.args.get("$expand", "")
    rows = []
    for h in headers:
        row = {k: v for k, v in h.items() if k != "_items"}
        if expand:
            row["to_MaterialDocumentItem"] = {"results": [serialize(i) for i in h["_items"]]}
        rows.append(row)
    return collection(rows, "A_MaterialDocumentHeader")


@bp.get("/API_MATERIAL_DOCUMENT_SRV/A_MaterialDocumentItem")
def mat_doc_items():
    _, items = _mat_doc_rows()
    return collection(items, "A_MaterialDocumentItem")


# ══════════════════════════════════════════════════════════════════════
# Business partners  (API_BUSINESS_PARTNER)
# ══════════════════════════════════════════════════════════════════════
def _bp_row(b):
    return {k: v for k, v in b.items() if k not in ("address", "gstin")}


@bp.get("/API_BUSINESS_PARTNER/A_BusinessPartner")
def bp_list():
    return collection([_bp_row(b) for b in data.BUSINESS_PARTNERS.values()], "A_BusinessPartner")


@bp.get("/API_BUSINESS_PARTNER/A_BusinessPartner(<key>)")
def bp_single(key):
    b = data.BUSINESS_PARTNERS.get(parse_key(key).get("", ""))
    if b is None:
        return single(None, "A_BusinessPartner")
    row = _bp_row(b)
    if "to_BusinessPartnerAddress" in request.args.get("$expand", ""):
        row["to_BusinessPartnerAddress"] = {"results": [
            dict(b["address"], BusinessPartner=b["BusinessPartner"], AddressID="1",
                 Country="IN", IsDefaultAddress=True)]}
    return single(row, "A_BusinessPartner")


@bp.get("/API_BUSINESS_PARTNER/A_BusinessPartnerTaxNumber")
def bp_tax():
    rows = [{"BusinessPartner": b["BusinessPartner"], "BPTaxType": "IN3", "BPTaxNumber": b["gstin"]}
            for b in data.BUSINESS_PARTNERS.values()]
    return collection(rows, "A_BusinessPartnerTaxNumber")


# ══════════════════════════════════════════════════════════════════════
# Products  (API_PRODUCT_SRV)
# ══════════════════════════════════════════════════════════════════════
@bp.get("/API_PRODUCT_SRV/A_ProductDescription(<key>)")
def product_desc(key):
    k = parse_key(key)
    p = data.PRODUCTS.get(k.get("Product"))
    return single(p and {"Product": k["Product"], "Language": k.get("Language", "EN"),
                         "ProductDescription": p["desc"]}, "A_ProductDescription")


@bp.get("/API_PRODUCT_SRV/A_ProductValuation(<key>)")
def product_valuation(key):
    k = parse_key(key)
    p = data.PRODUCTS.get(k.get("Product"))
    return single(p and {"Product": k["Product"], "ValuationArea": k.get("ValuationArea"),
                         "ValuationType": k.get("ValuationType", ""), "Currency": "INR",
                         "StandardPrice": f"{p['std']:.2f}", "MovingAveragePrice": f"{p['map']:.2f}",
                         "PriceUnitQty": "1"}, "A_ProductValuation")


@bp.get("/API_PRODUCT_SRV/A_ProductPlantIntlTrd(<key>)")
def product_intl_trade(key):
    k = parse_key(key)
    p = data.PRODUCTS.get(k.get("Product"))
    return single(p and {"Product": k["Product"], "Plant": k.get("Plant"),
                         "ConsumptionTaxCtrlCode": p["hsn"] or ""}, "A_ProductPlantIntlTrd")


# ══════════════════════════════════════════════════════════════════════
# Purchase orders  (API_PURCHASEORDER_PROCESS_SRV)
# ══════════════════════════════════════════════════════════════════════
@bp.get("/API_PURCHASEORDER_PROCESS_SRV/A_PurchaseOrder(<key>)")
def po_single(key):
    num = parse_key(key).get("", "")
    po = data.PURCHASE_ORDERS.get(num)
    return single(po and {"PurchaseOrder": num, "Supplier": po["Supplier"], "DocumentCurrency": "INR"},
                  "A_PurchaseOrder")


@bp.get("/API_PURCHASEORDER_PROCESS_SRV/A_PurchaseOrderItem")
def po_items():
    rows = [dict(i, PurchaseOrder=num, DocumentCurrency="INR")
            for num, po in data.PURCHASE_ORDERS.items() for i in po["items"]]
    return collection(rows, "A_PurchaseOrderItem")


# ══════════════════════════════════════════════════════════════════════
# Finance  (API_OPLACCTGDOCITEMCUBE_SRV, trial balance)
# ══════════════════════════════════════════════════════════════════════
@bp.get("/API_OPLACCTGDOCITEMCUBE_SRV/A_OperationalAcctgDocItemCube")
def gl_items():
    return collection(data.build_gl_items(), "A_OperationalAcctgDocItemCube")


@bp.get("/C_TRIALBALANCE_CDS/C_TrialBalance")
def trial_balance():
    return collection(list(data.TRIAL_BALANCE), "C_TrialBalance")
