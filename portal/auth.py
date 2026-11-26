"""File-based users with hashed passwords and role checks."""
import json
import secrets
from functools import wraps

from flask import abort, current_app, flash, redirect, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

ROLES = {"admin": "Administrator", "operator": "Stores Operator", "accounts": "Accounts"}

# Demo accounts created on first run only. Change them in Admin → Users.
DEMO_USERS = {
    "admin": ("Admin@demo1", "admin", "Demo Admin"),
    "stores": ("Stores@demo1", "operator", "Stores Operator"),
    "accounts": ("Accounts@demo1", "accounts", "Accounts Executive"),
}


class UserStore:
    def __init__(self, path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            self._save({u: {"hash": generate_password_hash(p), "role": r, "name": n, "active": True}
                        for u, (p, r, n) in DEMO_USERS.items()})

    def all(self):
        return json.loads(self.path.read_text(encoding="utf-8"))

    def _save(self, users):
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(users, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    def verify(self, username, password):
        u = self.all().get(username)
        if u and u.get("active") and check_password_hash(u["hash"], password):
            return {"username": username, "name": u["name"], "role": u["role"]}
        return None

    def upsert(self, username, name, role, password=None, active=True):
        if role not in ROLES:
            raise ValueError("Unknown role")
        users = self.all()
        u = users.get(username, {})
        if not u and not password:
            raise ValueError("Password is required for a new user")
        if password and len(password) < 8:
            raise ValueError("Password must be at least 8 characters")
        u.update(name=name or username, role=role, active=active)
        if password:
            u["hash"] = generate_password_hash(password)
        users[username] = u
        self._save(users)

    def delete(self, username):
        users = self.all()
        users.pop(username, None)
        self._save(users)


def csrf_token():
    if "_csrf" not in session:
        session["_csrf"] = secrets.token_urlsafe(32)
    return session["_csrf"]


def check_csrf():
    sent = request.headers.get("X-CSRF-Token") or request.form.get("_csrf")
    if not sent or not secrets.compare_digest(sent, session.get("_csrf", "")):
        abort(400, "Invalid or missing CSRF token")


def login_required(*roles):
    def deco(fn):
        @wraps(fn)
        def wrapper(*a, **kw):
            user = session.get("user")
            if not user:
                return redirect(url_for("login", next=request.path))
            current = current_app.config["USERS"].all().get(user["username"])
            if not current or not current.get("active"):
                session.clear()
                return redirect(url_for("login"))
            if roles and user["role"] not in roles and user["role"] != "admin":
                if request.path.startswith("/api/"):
                    abort(403)
                flash("You do not have access to that page.", "error")
                return redirect(url_for("dashboard"))
            if request.method == "POST":
                check_csrf()
            return fn(*a, **kw)
        return wrapper
    return deco
