"""Accounts payable / receivable views from SAP journal entry line items."""
from datetime import datetime, timezone

from .challan import fmt_date, sap_date
from .sap_client import lit

FI_SRV = "API_OPLACCTGDOCITEMCUBE_SRV"
FI_FIELDS = ("CompanyCode,FiscalYear,AccountingDocument,AccountingDocumentItem,PostingDate,DocumentDate,"
             "DebitCreditCode,AmountInCompanyCodeCurrency,CompanyCodeCurrency,Supplier,Customer,"
             "AssignmentReference,DocumentItemText,IsCleared,ClearingDate,NetDueDate,GLAccount,"
             "FinancialAccountType")
BUCKETS = [("not_due", "Not due"), ("d0_30", "1–30 days"), ("d31_60", "31–60 days"),
           ("d61_90", "61–90 days"), ("d90p", "90+ days")]


def line_items(client, date_from=None, date_to=None, party=None, open_only=False, top=500):
    filters = []
    if date_from:
        filters.append(f"PostingDate ge datetime'{date_from}T00:00:00'")
    if date_to:
        filters.append(f"PostingDate le datetime'{date_to}T23:59:59'")
    if party:
        filters.append(f"(Supplier eq {lit(party)} or Customer eq {lit(party)})")
    if open_only:
        filters.append("IsCleared eq false")
    params = {"$select": FI_FIELDS, "$filter": " and ".join(filters), "$orderby": "PostingDate desc",
              "$top": top}
    out = []
    for i in client.results(FI_SRV, "A_OperationalAcctgDocItemCube", params):
        out.append({
            "doc": i.get("AccountingDocument"), "year": i.get("FiscalYear"),
            "post_date": fmt_date(i.get("PostingDate")), "due_date": fmt_date(i.get("NetDueDate")),
            "due": sap_date(i.get("NetDueDate")),
            "dc": i.get("DebitCreditCode"), "amount": float(i.get("AmountInCompanyCodeCurrency") or 0),
            "party": i.get("Supplier") or i.get("Customer") or "",
            "party_type": "Supplier" if i.get("Supplier") else "Customer",
            "ref": i.get("AssignmentReference", ""), "text": i.get("DocumentItemText", ""),
            "cleared": bool(i.get("IsCleared")), "gl": i.get("GLAccount", ""),
        })
    return out


def ageing(items, today=None):
    today = today or datetime.now(timezone.utc)
    b = {k: 0.0 for k, _ in BUCKETS}
    for i in items:
        if i["cleared"]:
            continue
        amt = abs(i["amount"])
        days = (today - i["due"]).days if i["due"] else 0
        key = ("not_due" if days <= 0 else "d0_30" if days <= 30 else "d31_60" if days <= 60
               else "d61_90" if days <= 90 else "d90p")
        b[key] += amt
    return {k: round(v, 2) for k, v in b.items()}


def summary(items):
    open_items = [i for i in items if not i["cleared"]]
    return {
        "debit": round(sum(i["amount"] for i in items if i["dc"] == "S"), 2),
        "credit": round(sum(-i["amount"] for i in items if i["dc"] == "H"), 2),
        "open_count": len(open_items),
        "open_payable": round(sum(-i["amount"] for i in open_items if i["party_type"] == "Supplier"), 2),
        "open_receivable": round(sum(i["amount"] for i in open_items if i["party_type"] == "Customer"), 2),
        "ageing": ageing(items),
    }


def search_parties(client, q):
    q = (q or "").strip()
    if len(q) < 2:
        return []
    flt = (f"startswith(BusinessPartner,{lit(q)})" if q.isdigit()
           else f"substringof({lit(q.lower())},tolower(BusinessPartnerFullName))")
    rows = client.results("API_BUSINESS_PARTNER", "A_BusinessPartner",
                          {"$filter": flt, "$select": "BusinessPartner,BusinessPartnerFullName", "$top": 10},
                          cache=True)
    return [{"code": r["BusinessPartner"], "name": r.get("BusinessPartnerFullName", "")} for r in rows]


def trial_balance(client):
    rows = client.results("C_TRIALBALANCE_CDS", "C_TrialBalance", {"$top": 500})
    out = []
    for r in rows:
        dr, cr = float(r.get("DebitAmount") or 0), float(r.get("CreditAmount") or 0)
        out.append({"gl": r.get("GLAccount"), "name": r.get("GLAccountName", ""), "debit": dr, "credit": cr,
                    "balance": round(dr - cr, 2)})
    return out
