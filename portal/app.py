"""Flask application factory and routes."""
import io
import logging
from datetime import datetime

from flask import (Flask, abort, current_app, flash, jsonify, redirect, render_template, request,
                   send_file, session, url_for)

from . import challan as dc
from . import finance as fi
from .auth import ROLES, UserStore, csrf_token, login_required
from .config import ROOT, Settings
from .gst import amount_in_words, inr
from .pdf import PDFUnavailable, html_to_pdf
from .sap_client import FlaskTransport, HttpTransport, SAPClient, SAPError

log = logging.getLogger("portal")


def create_app(settings=None):
    settings = settings or Settings.from_env()
    app = Flask(__name__, template_folder=str(ROOT / "templates"), static_folder=str(ROOT / "static"))
    app.config.update(SECRET_KEY=settings.secret_key, SETTINGS=settings,
                      SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax",
                      USERS=UserStore(settings.data_dir / "users.json"))

    if settings.sap_mode == "mock":
        from mock_sap.server import bp as mock_bp
        app.register_blueprint(mock_bp)
        transport = FlaskTransport(app)
    else:
        transport = HttpTransport(settings.sap_base_url)
    app.config["SAP"] = SAPClient(transport, settings.sap_user, settings.sap_password,
                                  settings.sap_timeout, settings.cache_ttl)

    app.jinja_env.filters["inr"] = inr
    app.jinja_env.globals.update(csrf_token=csrf_token, ROLES=ROLES)

    @app.context_processor
    def inject():
        return {"S": settings, "user": session.get("user"), "now": datetime.now()}

    register_routes(app)
    return app


def sap():
    return current_app.config["SAP"]


def cfg():
    return current_app.config["SETTINGS"]


def pdf_or_print(html, filename):
    """Return a PDF; if Chromium isn't available, return the HTML so the browser can print it."""
    try:
        return send_file(io.BytesIO(html_to_pdf(html)), mimetype="application/pdf",
                         as_attachment=True, download_name=filename)
    except PDFUnavailable as e:
        log.warning("PDF engine unavailable (%s) – falling back to browser print", e)
        return html.replace("</body>", "<script>window.print()</script></body>")


def register_routes(app):
    # ── auth ──────────────────────────────────────────────────────────
    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "POST":
            u = app.config["USERS"].verify(request.form.get("username", "").strip().lower(),
                                           request.form.get("password", ""))
            if u:
                session.clear()
                session["user"] = u
                nxt = request.args.get("next", "")
                return redirect(nxt if nxt.startswith("/") and not nxt.startswith("//") else url_for("dashboard"))
            flash("Invalid username or password.", "error")
        return render_template("login.html")

    @app.get("/logout")
    def logout():
        session.clear()
        return redirect(url_for("login"))

    @app.get("/health")
    def health():
        return {"status": "ok", "sap_mode": cfg().sap_mode}

    # ── delivery challan ──────────────────────────────────────────────
    @app.get("/")
    @login_required("operator")
    def dashboard():
        samples = ["4900000101", "4900000102", "4900000103", "4900000104", "5000000201"] \
            if cfg().sap_mode == "mock" else []
        return render_template("dashboard.html", samples=samples)

    @app.post("/api/dc/preview")
    @login_required("operator")
    def dc_preview():
        doc = (request.get_json(silent=True) or {}).get("doc", "")
        try:
            data = dc.build_challan(sap(), cfg(), doc)
        except SAPError as e:
            return jsonify(ok=False, error=str(e))
        html = render_template("challan.html", c=data, copies=data["copies"][:1], embedded=True)
        return jsonify(ok=True, html=html, meta={
            "doc": data["doc"], "mov": data["mov"], "title": data["title"], "lines": len(data["rows"]),
            "grand_total": inr(data["grand_total"]), "tax_mode": data["tax_mode"],
            "receiver": data["receiver"]["name"]})

    @app.get("/dc/<doc>.pdf")
    @login_required("operator")
    def dc_pdf(doc):
        try:
            data = dc.build_challan(sap(), cfg(), doc)
        except SAPError as e:
            flash(str(e), "error")
            return redirect(url_for("dashboard"))
        html = render_template("challan.html", c=data, copies=data["copies"], embedded=False)
        return pdf_or_print(html, f"DC_{data['mov']}_{doc}.pdf")

    # ── finance ───────────────────────────────────────────────────────
    @app.get("/finance")
    @login_required("accounts")
    def finance():
        return render_template("finance.html", buckets=fi.BUCKETS)

    @app.post("/api/finance/summary")
    @login_required("accounts")
    def finance_summary():
        b = request.get_json(silent=True) or {}
        try:
            items = fi.line_items(sap(), b.get("date_from"), b.get("date_to"), b.get("party") or None)
            names = {p: dc.get_party(sap(), p)["name"] for p in {i["party"] for i in items if i["party"]}}
        except SAPError as e:
            return jsonify(ok=False, error=str(e))
        summary = fi.summary(items)
        for i in items:
            i["party_name"] = names.get(i["party"], i["party"])
            i.pop("due", None)          # datetime isn't JSON; due_date (text) is kept
        return jsonify(ok=True, items=items, summary=summary)

    @app.get("/api/finance/parties")
    @login_required("accounts")
    def finance_parties():
        try:
            return jsonify(ok=True, results=fi.search_parties(sap(), request.args.get("q")))
        except SAPError as e:
            return jsonify(ok=False, error=str(e), results=[])

    @app.get("/api/finance/trial-balance")
    @login_required("accounts")
    def finance_tb():
        try:
            rows = fi.trial_balance(sap())
        except SAPError as e:
            return jsonify(ok=False, error=str(e))
        return jsonify(ok=True, rows=rows, total_debit=round(sum(r["debit"] for r in rows), 2),
                       total_credit=round(sum(r["credit"] for r in rows), 2))

    @app.post("/finance/payment-advice")
    @login_required("accounts")
    def payment_advice():
        party = request.form.get("party", "").strip()
        chosen = set(request.form.getlist("docs"))
        if not party or not chosen:
            flash("Pick a supplier and at least one open item.", "error")
            return redirect(url_for("finance"))
        # Re-read the items from SAP – never trust amounts posted by the browser.
        items = [i for i in fi.line_items(sap(), party=party, open_only=True) if i["doc"] in chosen]
        if not items:
            abort(400, "Selected items are no longer open in SAP")
        total = round(sum(abs(i["amount"]) for i in items), 2)
        html = render_template("payment_advice.html", party=dc.get_party(sap(), party), items=items,
                               total=total, words=amount_in_words(total),
                               advice_no=f"PA/{datetime.now():%Y%m%d}/{party}")
        return pdf_or_print(html, f"PaymentAdvice_{party}.pdf")

    # ── admin ─────────────────────────────────────────────────────────
    @app.get("/admin")
    @login_required("admin")
    def admin():
        return render_template("admin.html", users=app.config["USERS"].all())

    @app.post("/admin/users")
    @login_required("admin")
    def admin_save_user():
        f = request.form
        username = f.get("username", "").strip().lower()
        if not username.isidentifier():
            flash("Username: letters, digits and underscore only.", "error")
            return redirect(url_for("admin"))
        if username == session["user"]["username"] and (f.get("role") != "admin" or not f.get("active")):
            flash("You cannot demote or disable your own account.", "error")
            return redirect(url_for("admin"))
        try:
            app.config["USERS"].upsert(username, f.get("name", ""), f.get("role", "operator"),
                                       f.get("password") or None, active=bool(f.get("active")))
            flash(f"User '{username}' saved.", "success")
        except ValueError as e:
            flash(str(e), "error")
        return redirect(url_for("admin"))

    @app.post("/admin/users/<username>/delete")
    @login_required("admin")
    def admin_delete_user(username):
        if username == session["user"]["username"]:
            flash("You cannot delete your own account.", "error")
        else:
            app.config["USERS"].delete(username)
            flash(f"User '{username}' deleted.", "success")
        return redirect(url_for("admin"))

    @app.post("/api/admin/test-connection")
    @login_required("admin")
    def admin_test_connection():
        client = sap()
        client.clear_cache()
        try:
            client.results("API_MATERIAL_DOCUMENT_SRV", "A_MaterialDocumentHeader", {"$top": 1})
            return jsonify(ok=True, msg=f"Connected to SAP ({cfg().sap_mode} mode) – HTTP 200")
        except SAPError as e:
            return jsonify(ok=False, msg=str(e))

    @app.errorhandler(400)
    @app.errorhandler(403)
    @app.errorhandler(404)
    def http_error(e):
        if request.path.startswith("/api/"):
            return jsonify(ok=False, error=e.description), e.code
        return render_template("error.html", error=e), e.code
