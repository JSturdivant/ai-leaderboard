"""Runtime settings, read from the environment (12-factor). Nothing secret lives in config.json.

MODELFIT_MODE           selfhost (default: one implicit workspace, no sign-in, every feature on)
                        | cloud (accounts, workspaces, plans, billing)
MODELFIT_ENV            development (default) | production (refuses insecure defaults)
MODELFIT_BASE_URL       public URL in cloud mode, e.g. https://modelfit.ai (used for redirects, cookies, host check)
MODELFIT_DB             SQLite path (default app/var/modelfit.db)
MODELFIT_SECRET_KEY     random string; required in production (signs webhook test payloads, salts tokens)
MODELFIT_ALLOWED_HOSTS  extra comma-separated Host values to accept (selfhost LAN access, proxies)
STRIPE_SECRET_KEY, STRIPE_WEBHOOK_SECRET, STRIPE_PRICE_PRO_MONTHLY, STRIPE_PRICE_PRO_YEARLY,
STRIPE_PRICE_TEAM_MONTHLY, STRIPE_PRICE_TEAM_YEARLY      billing (without them, cloud mode in development
                        switches plans instantly so the upgrade flow can be exercised locally)
MODELFIT_WEBHOOK_URL, MODELFIT_WEBHOOK_SECRET            selfhost only: seed the workspace's pick-change webhook
OPENROUTER_API_KEY      selfhost only: the gateway's upstream key when the workspace has not stored one
MODELFIT_UPSTREAM_URL   the gateway's OpenAI-compatible upstream (default https://openrouter.ai/api/v1)
MODELFIT_BETA_KEY       the shared public key for the free ranking-API beta (default mf_beta_public)
MODELFIT_BETA           0 to turn the public beta key off
MODELFIT_BETA_DAILY     ranking requests per day per IP address on the beta key (default 1000)
"""

import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from . import ROOT


@dataclass
class Settings:
    mode: str = "selfhost"
    env: str = "development"
    base_url: str = ""
    db_path: Path = ROOT / "var" / "modelfit.db"
    secret_key: str = ""
    allowed_hosts: list = field(default_factory=list)
    stripe_secret_key: str = ""
    stripe_webhook_secret: str = ""
    stripe_prices: dict = field(default_factory=dict)  # price id -> (plan, interval)
    webhook_url: str = ""
    webhook_secret: str = ""
    openrouter_key: str = ""
    upstream_url: str = "https://openrouter.ai/api/v1"
    beta_key: str = "mf_beta_public"
    beta_enabled: bool = True
    beta_daily: int = 1000

    @property
    def cloud(self):
        return self.mode == "cloud"

    @property
    def production(self):
        return self.env == "production"

    @property
    def secure_cookies(self):
        return self.base_url.startswith("https://")

    @property
    def billing_live(self):
        return bool(self.stripe_secret_key)

    @property
    def dev_billing(self):
        """Instant plan switches without Stripe: only for local development of the cloud mode."""
        return self.cloud and not self.billing_live and not self.production

    def price_for(self, plan, interval):
        for pid, (p, i) in self.stripe_prices.items():
            if p == plan and i == interval:
                return pid
        return None


def load(env=None):
    e = os.environ if env is None else env
    s = Settings(
        mode=(e.get("MODELFIT_MODE") or "selfhost").lower(),
        env=(e.get("MODELFIT_ENV") or "development").lower(),
        base_url=(e.get("MODELFIT_BASE_URL") or "").rstrip("/"),
        db_path=Path(e.get("MODELFIT_DB") or ROOT / "var" / "modelfit.db"),
        secret_key=e.get("MODELFIT_SECRET_KEY") or "",
        allowed_hosts=[h.strip().lower() for h in (e.get("MODELFIT_ALLOWED_HOSTS") or "").split(",") if h.strip()],
        stripe_secret_key=e.get("STRIPE_SECRET_KEY") or "",
        stripe_webhook_secret=e.get("STRIPE_WEBHOOK_SECRET") or "",
        webhook_url=e.get("MODELFIT_WEBHOOK_URL") or "",
        webhook_secret=e.get("MODELFIT_WEBHOOK_SECRET") or "",
        openrouter_key=e.get("OPENROUTER_API_KEY") or "",
        upstream_url=(e.get("MODELFIT_UPSTREAM_URL") or "https://openrouter.ai/api/v1").rstrip("/"),
        beta_key=(e.get("MODELFIT_BETA_KEY") or "mf_beta_public").strip(),
        beta_enabled=(e.get("MODELFIT_BETA") or "1").strip().lower() not in ("0", "false", "no", "off"),
        beta_daily=int(e.get("MODELFIT_BETA_DAILY") or 1000),
    )
    for plan in ("pro", "team"):
        for interval in ("monthly", "yearly"):
            pid = e.get(f"STRIPE_PRICE_{plan.upper()}_{interval.upper()}")
            if pid:
                s.stripe_prices[pid] = (plan, "month" if interval == "monthly" else "year")
    if s.mode not in ("selfhost", "cloud"):
        raise SystemExit(f"MODELFIT_MODE must be selfhost or cloud, not {s.mode!r}")
    if s.production:
        problems = []
        if len(s.secret_key) < 32:
            problems.append("MODELFIT_SECRET_KEY (at least 32 characters)")
        if s.cloud and not s.base_url.startswith("https://"):
            problems.append("MODELFIT_BASE_URL (https://...)")
        if s.cloud and s.billing_live and not s.stripe_webhook_secret:
            problems.append("STRIPE_WEBHOOK_SECRET")
        if problems:
            raise SystemExit("production needs: " + ", ".join(problems))
    if not s.secret_key:  # development / selfhost: generate one once and keep it next to the database
        key_file = s.db_path.parent / "secret_key"
        try:
            s.secret_key = key_file.read_text().strip()
        except OSError:
            s.secret_key = ""
        if len(s.secret_key) < 32:
            s.secret_key = secrets.token_urlsafe(32)
            key_file.parent.mkdir(parents=True, exist_ok=True)
            key_file.write_text(s.secret_key)
            os.chmod(key_file, 0o600)
    if s.base_url:
        s.allowed_hosts.append(urlparse(s.base_url).netloc.lower())
    return s


_current = None


def get():
    global _current
    if _current is None:
        _current = load()
    return _current


def set_current(s):
    """Tests and the server entry point install their own settings."""
    global _current
    _current = s
    return s
