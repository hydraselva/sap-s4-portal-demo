"""Settings loaded from environment variables (and an optional .env file)."""
import json
import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_dotenv(path=ROOT / ".env"):
    """Minimal .env loader (no extra dependency). Existing env vars win."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


@dataclass
class Settings:
    sap_mode: str = "mock"                 # "mock" or "live"
    sap_base_url: str = ""
    sap_user: str = ""
    sap_password: str = ""
    sap_timeout: int = 20
    cache_ttl: int = 60
    secret_key: str = ""
    company_name: str = "Apex Precision Components Pvt Ltd"
    company_short: str = "APEX"
    company_email: str = "accounts@apex-demo.example"
    default_hsn: str = "87089900"
    gst_rate: float = 18.0
    items_per_page: int = 10
    plant_to_bp: dict = field(default_factory=dict)
    data_dir: Path = ROOT / "instance"

    @classmethod
    def from_env(cls):
        load_dotenv()
        env = os.environ.get
        mode = env("SAP_MODE", "mock").lower()
        s = cls(
            sap_mode=mode,
            sap_base_url=env("SAP_BASE_URL", "").rstrip("/"),
            sap_user=env("SAP_USER", env("MOCK_SAP_USER", "DEMO_API") if mode == "mock" else ""),
            sap_password=env("SAP_PASSWORD", env("MOCK_SAP_PASSWORD", "demo-pass") if mode == "mock" else ""),
            sap_timeout=int(env("SAP_TIMEOUT", "20")),
            cache_ttl=int(env("SAP_CACHE_TTL", "60")),
            secret_key=env("SECRET_KEY") or secrets.token_hex(32),
            company_name=env("COMPANY_NAME", cls.company_name),
            company_short=env("COMPANY_SHORT", cls.company_short),
            company_email=env("COMPANY_EMAIL", cls.company_email),
            default_hsn=env("DEFAULT_HSN", cls.default_hsn),
            gst_rate=float(env("GST_RATE", "18")),
            plant_to_bp=json.loads(env("PLANT_TO_BP", '{"1000": "10000001", "2000": "10000002"}')),
            data_dir=Path(env("DATA_DIR", str(ROOT / "instance"))),
        )
        if mode == "live" and not (s.sap_base_url and s.sap_user and s.sap_password):
            raise RuntimeError("SAP_MODE=live needs SAP_BASE_URL, SAP_USER and SAP_PASSWORD in .env")
        return s
