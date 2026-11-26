"""
Small, dependency-light client for SAP S/4HANA OData V2 APIs.

* Basic authentication (communication user / communication arrangement)
* Safe literal escaping for $filter and entity keys
* TTL cache for master data (business partners, products)
* Consistent error handling: 401 / 404 / OData error messages
* Pluggable transport: real HTTP (requests) or the in-process mock server
"""
import base64
import threading
import time
from urllib.parse import quote

import requests

ODATA_ROOT = "/sap/opu/odata/sap"


class SAPError(Exception):
    """Raised when SAP returns an error the caller should show to the user."""


def lit(value):
    """Quote a string literal for OData: O'Brien → 'O''Brien'."""
    return "'" + str(value).replace("'", "''") + "'"


def key(**parts):
    """Build an entity key: key(Product='X', Plant='1000') → (Product='X',Plant='1000')."""
    if len(parts) == 1 and "_" in parts:
        return f"({lit(parts['_'])})"
    return "(" + ",".join(f"{k}={lit(v)}" for k, v in parts.items()) + ")"


class HttpTransport:
    def __init__(self, base_url):
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()

    def __call__(self, path, headers, timeout):
        try:
            r = self.session.get(self.base_url + path, headers=headers, timeout=timeout)
        except requests.exceptions.ConnectionError:
            raise SAPError(f"Cannot reach SAP at {self.base_url}")
        except requests.exceptions.Timeout:
            raise SAPError("SAP did not respond in time")
        try:
            body = r.json()
        except ValueError:
            body = {"raw": r.text[:300]}
        return r.status_code, body


class FlaskTransport:
    """Calls the mock SAP blueprint in-process (no network, no second server)."""

    def __init__(self, app, prefix="/mock-sap"):
        self.app, self.prefix = app, prefix

    def __call__(self, path, headers, timeout):
        with self.app.test_client() as c:
            r = c.get(self.prefix + path, headers=headers)
            return r.status_code, r.get_json(silent=True) or {}


class SAPClient:
    def __init__(self, transport, user, password, timeout=20, cache_ttl=60):
        self.transport = transport
        token = base64.b64encode(f"{user}:{password}".encode()).decode()
        self.headers = {"Authorization": "Basic " + token, "Accept": "application/json"}
        self.timeout = timeout
        self.cache_ttl = cache_ttl
        self._cache, self._lock = {}, threading.Lock()
        self.calls = 0  # simple metric, handy in tests

    # ── public API ────────────────────────────────────────────────────
    def get(self, service, entity, params=None, cache=False, allow_404=False):
        """GET /<service>/<entity>?<params>  → the OData "d" payload."""
        path = f"{ODATA_ROOT}/{service}/{entity}"
        if params:
            path += "?" + "&".join(f"{k}={quote(str(v), safe=chr(39) + '(),=:$-')}"
                                   for k, v in params.items() if v not in (None, ""))
        if cache:
            hit = self._cache_get(path)
            if hit is not None:
                return hit
        self.calls += 1
        status, body = self.transport(path, self.headers, self.timeout)
        if status == 404 and allow_404:
            return None
        if status == 401:
            raise SAPError("SAP rejected the credentials (HTTP 401). Check the communication user.")
        if status >= 400:
            raise SAPError(f"SAP error {status}: {self._message(body)}")
        data = body.get("d", {})
        if cache:
            self._cache_set(path, data)
        return data

    def results(self, service, entity, params=None, cache=False):
        return (self.get(service, entity, params, cache) or {}).get("results", [])

    def clear_cache(self):
        with self._lock:
            self._cache.clear()

    # ── internals ─────────────────────────────────────────────────────
    @staticmethod
    def _message(body):
        err = body.get("error", {}) if isinstance(body, dict) else {}
        msg = err.get("message", {})
        return (msg.get("value") if isinstance(msg, dict) else msg) or body.get("raw") or "unknown error"

    def _cache_get(self, k):
        with self._lock:
            hit = self._cache.get(k)
            if hit and hit[1] > time.time():
                return hit[0]
        return None

    def _cache_set(self, k, v):
        with self._lock:
            self._cache[k] = (v, time.time() + self.cache_ttl)
