"""
Fictional seed data for the mock SAP S/4HANA OData server.

Every company, person, GSTIN and number here is invented for demo purposes.
Dates are generated relative to "today" so ageing buckets always look realistic.
"""
import random
from datetime import datetime, timedelta, timezone

# ── Business partners (plants are represented by their own BP) ─────────
BUSINESS_PARTNERS = {
    "10000001": {
        "BusinessPartner": "10000001", "BusinessPartnerType": "PLANT",
        "BusinessPartnerFullName": "Apex Precision Components – Chennai Plant",
        "address": {"HouseNumber": "Plot 14, SIDCO Industrial Estate",
                    "StreetName": "Ambattur", "CityName": "Chennai",
                    "Region": "TN", "PostalCode": "600098"},
        "gstin": "33AAPCA4321K1Z8",
    },
    "10000002": {
        "BusinessPartner": "10000002", "BusinessPartnerType": "PLANT",
        "BusinessPartnerFullName": "Apex Precision Components – Pune Plant",
        "address": {"HouseNumber": "Gat No. 212", "StreetName": "Chakan MIDC Phase II",
                    "CityName": "Pune", "Region": "MH", "PostalCode": "410501"},
        "gstin": "27AAPCA4321K1Z4",
    },
    "20000101": {
        "BusinessPartner": "20000101", "BusinessPartnerType": "SUPPLIER",
        "BusinessPartnerFullName": "Sri Murugan Engineering Works",
        "address": {"HouseNumber": "45", "StreetName": "Avinashi Road",
                    "CityName": "Coimbatore", "Region": "TN", "PostalCode": "641018"},
        "gstin": "33BBMPS7788Q1Z2",
    },
    "20000102": {
        "BusinessPartner": "20000102", "BusinessPartnerType": "SUPPLIER",
        "BusinessPartnerFullName": "Deccan Heat Treaters Pvt Ltd",
        "address": {"HouseNumber": "B-17", "StreetName": "Peenya Industrial Area",
                    "CityName": "Bengaluru", "Region": "KA", "PostalCode": "560058"},
        "gstin": "29AADCD5566H1Z9",
    },
    "20000103": {
        "BusinessPartner": "20000103", "BusinessPartnerType": "SUPPLIER",
        "BusinessPartnerFullName": "Shakti Castings & Forgings",
        "address": {"HouseNumber": "Survey 88", "StreetName": "GIDC Metoda",
                    "CityName": "Rajkot", "Region": "GJ", "PostalCode": "360021"},
        "gstin": "24AAKFS9012L1Z6",
    },
    "30000201": {
        "BusinessPartner": "30000201", "BusinessPartnerType": "CUSTOMER",
        "BusinessPartnerFullName": "Bharat Auto Assemblies Ltd",
        "address": {"HouseNumber": "Sector 8", "StreetName": "IMT Manesar",
                    "CityName": "Gurugram", "Region": "HR", "PostalCode": "122051"},
        "gstin": "06AABCB3344M1Z1",
    },
    "30000202": {
        "BusinessPartner": "30000202", "BusinessPartnerType": "CUSTOMER",
        "BusinessPartnerFullName": "Coastal Motors India Pvt Ltd",
        "address": {"HouseNumber": "12", "StreetName": "Oragadam Industrial Corridor",
                    "CityName": "Kanchipuram", "Region": "TN", "PostalCode": "602105"},
        "gstin": "33AACCC6677N1Z3",
    },
}

# Plant code → BP that holds its address/GSTIN (same idea as a real landscape)
PLANT_TO_BP = {"1000": "10000001", "2000": "10000002"}

# ── Products ────────────────────────────────────────────────────────────
PRODUCTS = {
    "MAT-1001": {"desc": "Brake Disc Rotor 280 mm", "std": 1450.00, "map": 1432.10, "hsn": "87083000", "uom": "EA"},
    "MAT-1002": {"desc": "Brake Pad Set – Front Axle", "std": 620.00, "map": 611.75, "hsn": "87083000", "uom": "SET"},
    "MAT-1003": {"desc": "Caliper Bracket Casting (raw)", "std": 0.00, "map": 385.40, "hsn": "73259910", "uom": "EA"},
    "MAT-1004": {"desc": "Steel Backing Plate 2.5 mm", "std": 48.50, "map": 47.90, "hsn": "72111940", "uom": "EA"},
    "MAT-1005": {"desc": "Friction Compound Mix", "std": 210.00, "map": 205.00, "hsn": "68138100", "uom": "KG"},
    "MAT-1006": {"desc": "Shim Kit – Anti-noise", "std": 35.00, "map": 34.20, "hsn": None, "uom": "SET"},
}

# ── Purchase orders ─────────────────────────────────────────────────────
PURCHASE_ORDERS = {
    "4500000011": {"Supplier": "20000101", "items": [
        {"PurchaseOrderItem": "00010", "Material": "MAT-1003", "OrderQuantity": "500", "NetPriceAmount": "92.00"},
        {"PurchaseOrderItem": "00020", "Material": "MAT-1004", "OrderQuantity": "2000", "NetPriceAmount": "11.50"},
    ]},
    "4500000012": {"Supplier": "20000103", "items": [
        {"PurchaseOrderItem": "00010", "Material": "MAT-1003", "OrderQuantity": "1000", "NetPriceAmount": "372.00"},
    ]},
    "4500000013": {"Supplier": "20000102", "items": [
        {"PurchaseOrderItem": "00010", "Material": "MAT-1001", "OrderQuantity": "300", "NetPriceAmount": "140.00"},
    ]},
}


def _today():
    return datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)


def _item(n, mat, plant, qty, dc, sloc="0001", **extra):
    row = {"MaterialDocumentItem": f"{n:04d}", "Material": mat, "Plant": plant,
           "StorageLocation": sloc, "QuantityInBaseUnit": str(qty),
           "MaterialBaseUnit": PRODUCTS[mat]["uom"], "DebitCreditCode": dc,
           "Supplier": "", "PurchaseOrder": "", "PurchaseOrderItem": "",
           "MaterialDocumentItemText": ""}
    row.update(extra)
    return row


def build_material_documents():
    t = _today()
    return {
        # 303 – inter-plant transfer Chennai (TN) → Pune (MH): IGST
        "4900000101": {"mov": "303", "date": t - timedelta(days=1), "items": [
            _item(1, "MAT-1001", "1000", 40, "H"), _item(2, "MAT-1001", "2000", 40, "S", sloc="0002"),
            _item(3, "MAT-1002", "1000", 120, "H"), _item(4, "MAT-1002", "2000", 120, "S", sloc="0002"),
            _item(5, "MAT-1003", "1000", 60, "H"), _item(6, "MAT-1003", "2000", 60, "S", sloc="0002"),
        ]},
        # 541 – sub-contract to a Tamil Nadu supplier: CGST + SGST
        "4900000102": {"mov": "541", "date": t - timedelta(days=2), "items": [
            _item(1, "MAT-1003", "1000", 250, "H", Supplier="20000101", PurchaseOrder="4500000011", PurchaseOrderItem="00010"),
            _item(2, "MAT-1004", "1000", 1200, "H", Supplier="20000101", PurchaseOrder="4500000011", PurchaseOrderItem="00020"),
            _item(3, "MAT-1006", "1000", 300, "H", Supplier="20000101", PurchaseOrder="4500000011"),
        ]},
        # 541 – sub-contract to a Karnataka heat treater: IGST
        "4900000103": {"mov": "541", "date": t - timedelta(days=3), "items": [
            _item(1, "MAT-1001", "1000", 150, "H", Supplier="20000102", PurchaseOrder="4500000013", PurchaseOrderItem="00010"),
        ]},
        # 161 – return to vendor, priced from the PO
        "4900000104": {"mov": "161", "date": t - timedelta(days=4), "items": [
            _item(1, "MAT-1003", "1000", 35, "S", Supplier="20000103", PurchaseOrder="4500000012",
                  PurchaseOrderItem="00010", MaterialDocumentItemText="Blow holes found in incoming inspection"),
        ]},
        # 101 – goods receipt: NOT allowed for a delivery challan (shows validation)
        "5000000201": {"mov": "101", "date": t - timedelta(days=1), "items": [
            _item(1, "MAT-1005", "1000", 500, "S", Supplier="20000101", PurchaseOrder="4500000011"),
        ]},
    }


def build_gl_items():
    """Open and cleared AP/AR line items spread over the last ~150 days."""
    rnd = random.Random(42)
    t = _today()
    texts_ap = ["Job work charges", "Heat treatment – batch", "Raw casting supply",
                "Machining charges", "Freight inward"]
    texts_ar = ["Sale – brake disc rotors", "Sale – pad sets", "Debit note – price diff"]
    rows, doc = [], 1900000100
    for party in ["20000101", "20000102", "20000103", "30000201", "30000202"]:
        is_sup = party.startswith("2")
        for _ in range(rnd.randint(6, 9)):
            doc += 1
            post = t - timedelta(days=rnd.randint(5, 150))
            due = post + timedelta(days=rnd.choice([30, 45, 60]))
            amt = round(rnd.uniform(8000, 240000), 2)
            cleared = rnd.random() < 0.35
            rows.append({
                "CompanyCode": "1000", "FiscalYear": str(post.year),
                "AccountingDocument": str(doc), "AccountingDocumentItem": "001",
                "PostingDate": post, "DocumentDate": post - timedelta(days=rnd.randint(0, 3)),
                "DebitCreditCode": "H" if is_sup else "S",
                "AmountInCompanyCodeCurrency": f"{(-amt if is_sup else amt):.2f}",
                "CompanyCodeCurrency": "INR",
                "Supplier": party if is_sup else "", "Customer": "" if is_sup else party,
                "AssignmentReference": f"INV/{post:%y}/{rnd.randint(100, 999)}",
                "DocumentItemText": rnd.choice(texts_ap if is_sup else texts_ar),
                "IsCleared": cleared,
                "ClearingDate": due - timedelta(days=rnd.randint(0, 10)) if cleared else None,
                "NetDueDate": due,
                "GLAccount": "21100000" if is_sup else "12100000",
                "FinancialAccountType": "K" if is_sup else "D",
            })
    return rows


TRIAL_BALANCE = [
    {"GLAccount": "11000000", "GLAccountName": "Cash and Bank", "DebitAmount": "4820000.00", "CreditAmount": "3910500.00"},
    {"GLAccount": "12100000", "GLAccountName": "Trade Receivables", "DebitAmount": "9650400.00", "CreditAmount": "7120800.00"},
    {"GLAccount": "13100000", "GLAccountName": "Raw Material Inventory", "DebitAmount": "6125000.00", "CreditAmount": "5480200.00"},
    {"GLAccount": "21100000", "GLAccountName": "Trade Payables", "DebitAmount": "5230000.00", "CreditAmount": "6890400.00"},
    {"GLAccount": "22300000", "GLAccountName": "GST Output Payable", "DebitAmount": "1102000.00", "CreditAmount": "1385600.00"},
    {"GLAccount": "31000000", "GLAccountName": "Share Capital and Reserves", "DebitAmount": "0.00", "CreditAmount": "1349900.00"},
    {"GLAccount": "41000000", "GLAccountName": "Revenue – Brake Components", "DebitAmount": "0.00", "CreditAmount": "11840000.00"},
    {"GLAccount": "51000000", "GLAccountName": "Cost of Materials Consumed", "DebitAmount": "6720000.00", "CreditAmount": "0.00"},
    {"GLAccount": "52000000", "GLAccountName": "Sub-contracting Charges", "DebitAmount": "1980000.00", "CreditAmount": "0.00"},
    {"GLAccount": "61000000", "GLAccountName": "Salaries and Wages", "DebitAmount": "2350000.00", "CreditAmount": "0.00"},
]
