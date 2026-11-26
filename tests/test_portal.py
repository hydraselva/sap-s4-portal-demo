import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from mock_sap.server import parse_filter
from portal import finance as fi
from portal.app import create_app
from portal.config import Settings
from portal.gst import amount_in_words, inr, split_gst
from portal.sap_client import key, lit


# ── pure helpers ─────────────────────────────────────────────────────
def test_gst_intra_state_splits_cgst_sgst():
    t = split_gst("TN", "TN", 1000)
    assert t == {"cgst": Decimal("90.00"), "sgst": Decimal("90.00"), "igst": Decimal("0.00")}


def test_gst_inter_state_is_igst():
    assert split_gst("TN", "KA", 1000)["igst"] == Decimal("180.00")
    assert split_gst("", "TN", 1000)["igst"] == Decimal("180.00")   # unknown state → IGST


def test_amount_in_words_indian_system():
    assert amount_in_words(1234567.5) == ("Rupees Twelve Lakh Thirty Four Thousand Five Hundred Sixty "
                                          "Seven and Fifty Paise Only")
    assert amount_in_words(0) == "Rupees Zero Only"
    assert amount_in_words(25_00_00_000) == "Rupees Twenty Five Crore Only"


def test_inr_grouping():
    assert inr(1234567.8) == "12,34,567.80"
    assert inr(-950) == "-950.00"


def test_odata_literal_escaping():
    assert lit("O'Brien") == "'O''Brien'"
    assert key(Product="A'1", Plant="1000") == "(Product='A''1',Plant='1000')"


def test_mock_filter_parser():
    rows = [{"A": "1", "B": "x"}, {"A": "2", "B": "y"}, {"A": "3", "B": "x"}]
    f = parse_filter("(A eq '1' or A eq '3') and B eq 'x'")
    assert [r["A"] for r in rows if f(r)] == ["1", "3"]


def test_ageing_buckets():
    today = datetime(2026, 6, 30, tzinfo=timezone.utc)
    items = [{"cleared": False, "amount": -100, "due": today + timedelta(days=5)},
             {"cleared": False, "amount": -200, "due": today - timedelta(days=10)},
             {"cleared": False, "amount": -300, "due": today - timedelta(days=120)},
             {"cleared": True, "amount": -999, "due": today - timedelta(days=120)}]
    assert fi.ageing(items, today) == {"not_due": 100, "d0_30": 200, "d31_60": 0, "d61_90": 0, "d90p": 300}


# ── app / integration (mock SAP) ─────────────────────────────────────
@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("SAP_MODE", "mock")
    s = Settings.from_env()
    s.data_dir = tmp_path
    s.secret_key = "test"
    return create_app(s)


def login(client, user="admin", pw="Admin@demo1"):
    r = client.post("/login", data={"username": user, "password": pw})
    assert r.status_code == 302
    page = client.get("/").get_data(as_text=True) if user != "accounts" else client.get("/finance").get_data(as_text=True)
    return re.search(r'name="csrf-token" content="([^"]+)"', page).group(1)


def test_login_required(app):
    assert app.test_client().get("/").status_code == 302


def test_bad_login(app):
    r = app.test_client().post("/login", data={"username": "admin", "password": "nope"})
    assert b"Invalid username or password" in r.data


def test_csrf_enforced(app):
    c = app.test_client()
    login(c)
    assert c.post("/api/dc/preview", json={"doc": "4900000102"}).status_code == 400


@pytest.mark.parametrize("doc,mov,tax", [("4900000101", "303", "IGST"), ("4900000102", "541", "CGST"),
                                         ("4900000103", "541", "IGST"), ("4900000104", "161", "IGST")])
def test_challan_preview(app, doc, mov, tax):
    c = app.test_client()
    tok = login(c)
    j = c.post("/api/dc/preview", json={"doc": doc}, headers={"X-CSRF-Token": tok}).get_json()
    assert j["ok"], j
    assert j["meta"]["mov"] == mov and tax in j["meta"]["tax_mode"]


def test_challan_values_541_same_state(app):
    from portal.challan import build_challan
    with app.app_context():
        c = build_challan(app.config["SAP"], app.config["SETTINGS"], "4900000102")
    # MAT-1003 has no standard price → moving average price is used
    row = c["rows"][0]
    assert row["material"] == "MAT-1003" and row["rate"] == Decimal("385.40")
    assert row["price_src"] == "Moving average price"
    # MAT-1006 has no HSN in SAP → default HSN
    assert c["rows"][2]["hsn"] == app.config["SETTINGS"].default_hsn
    assert c["totals"]["igst"] == 0 and c["totals"]["cgst"] == c["totals"]["sgst"] > 0


def test_return_priced_from_po(app):
    from portal.challan import build_challan
    with app.app_context():
        c = build_challan(app.config["SAP"], app.config["SETTINGS"], "4900000104")
    assert c["rows"][0]["rate"] == Decimal("372.00")
    assert c["receiver"]["name"] == "Shakti Castings & Forgings"


def test_blocked_movement_type(app):
    c = app.test_client()
    tok = login(c)
    j = c.post("/api/dc/preview", json={"doc": "5000000201"}, headers={"X-CSRF-Token": tok}).get_json()
    assert not j["ok"] and "101" in j["error"]


def test_unknown_document(app):
    c = app.test_client()
    tok = login(c)
    j = c.post("/api/dc/preview", json={"doc": "4999999999"}, headers={"X-CSRF-Token": tok}).get_json()
    assert not j["ok"] and "not found" in j["error"]


def test_operator_cannot_open_finance(app):
    c = app.test_client()
    login(c, "stores", "Stores@demo1")
    assert c.get("/finance").status_code == 302


def test_finance_summary_and_party_filter(app):
    c = app.test_client()
    tok = login(c, "accounts", "Accounts@demo1")
    h = {"X-CSRF-Token": tok}
    all_ = c.post("/api/finance/summary", json={}, headers=h).get_json()
    one = c.post("/api/finance/summary", json={"party": "20000101"}, headers=h).get_json()
    assert all_["ok"] and one["ok"]
    assert 0 < len(one["items"]) < len(all_["items"])
    assert {i["party"] for i in one["items"]} == {"20000101"}
    assert c.get("/api/finance/parties?q=deccan").get_json()["results"][0]["code"] == "20000102"
    tb = c.get("/api/finance/trial-balance").get_json()
    assert tb["total_debit"] == tb["total_credit"]


def test_wrong_sap_password_is_reported(app):
    app.config["SAP"].headers["Authorization"] = "Basic eDp5"   # x:y
    c = app.test_client()
    tok = login(c)
    j = c.post("/api/dc/preview", json={"doc": "4900000102"}, headers={"X-CSRF-Token": tok}).get_json()
    assert not j["ok"] and "401" in j["error"]
