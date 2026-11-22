"""Stripe billing over its REST API (stdlib urllib, no SDK).

Flow: the UI asks for a Checkout session -> the customer pays on Stripe -> Stripe calls our webhook ->
we set the workspace's plan from the subscription. The customer portal handles card changes and cancels.
The webhook is the only thing that changes a plan, and it is idempotent (billing_events).

Without STRIPE_SECRET_KEY, development cloud mode switches plans instantly (settings.dev_billing) so the
whole upgrade flow can be clicked through locally. Production refuses that.
"""

import hashlib
import hmac
import json
import time
import urllib.error
import urllib.parse
import urllib.request

from . import db, plans, settings, tenants
from .util import log

API = "https://api.stripe.com/v1/"
TOLERANCE = 300  # seconds a webhook signature stays valid


class BillingError(Exception):
    pass


def _stripe(path, params=None, method="POST"):
    s = settings.get()
    data = urllib.parse.urlencode(_flatten(params or {})).encode() if method == "POST" else None
    req = urllib.request.Request(API + path, data=data, method=method, headers={
        "Authorization": f"Bearer {s.stripe_secret_key}", "Stripe-Version": "2024-06-20",
        "Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        msg = json.loads(e.read() or b"{}").get("error", {}).get("message", str(e))
        raise BillingError(msg) from None


def _flatten(d, prefix=""):
    """Stripe's form encoding: {"a": {"b": 1}, "l": [{"x": 1}]} -> a[b]=1, l[0][x]=1."""
    out = []
    for k, v in d.items():
        key = f"{prefix}[{k}]" if prefix else k
        if isinstance(v, dict):
            out += _flatten(v, key)
        elif isinstance(v, list):
            for i, item in enumerate(v):
                out += _flatten(item, f"{key}[{i}]") if isinstance(item, dict) else [(f"{key}[{i}]", item)]
        elif v is not None:
            out.append((key, "true" if v is True else "false" if v is False else v))
    return out


def _set_plan(wsid, plan, status="active", interval=None, period_end=None, seats=1, customer=None, subscription=None):
    db.run("UPDATE workspaces SET plan=?, plan_status=?, plan_interval=?, current_period_end=?, seats=?, "
           "stripe_customer_id=COALESCE(?, stripe_customer_id), stripe_subscription_id=COALESCE(?, stripe_subscription_id) WHERE id=?",
           plan, status, interval, period_end, seats, customer, subscription, wsid)
    ws = db.one("SELECT * FROM workspaces WHERE id=?", wsid)
    if ws:
        tenants.recompute(ws)  # limits may have changed (e.g. custom weights unlocked)
    return ws


def checkout(ws, user, plan, interval="month"):
    """Returns {"url": ...} to send the browser to."""
    s = settings.get()
    if plan not in plans.PAID:
        raise BillingError("Choose Pro or Team")
    if interval not in ("month", "year"):
        raise BillingError("Choose monthly or yearly billing")
    if s.dev_billing:
        _set_plan(ws["id"], plan, interval=interval, period_end=time.time() + (365 if interval == "year" else 30) * 86400,
                  seats=plans.PLANS[plan]["limits"]["members"] or 1)
        db.event("plan.changed", ws["id"], user["id"], plan=plan, dev=True)
        return {"url": f"/app?upgraded={plan}#settings", "dev": True}
    if not s.billing_live:
        raise BillingError("Billing is not configured on this server")
    price = s.price_for(plan, interval)
    if not price:
        raise BillingError(f"No Stripe price configured for {plan}/{interval}")
    params = {
        "mode": "subscription", "client_reference_id": ws["id"],
        "line_items": [{"price": price, "quantity": 1}],
        "success_url": f"{s.base_url}/app?upgraded={plan}#settings", "cancel_url": f"{s.base_url}/pricing?canceled=1",
        "allow_promotion_codes": True, "subscription_data": {"metadata": {"workspace_id": ws["id"]}},
        "metadata": {"workspace_id": ws["id"]},
    }
    if ws.get("stripe_customer_id"):
        params["customer"] = ws["stripe_customer_id"]
    else:
        params["customer_email"] = user["email"]
    sess = _stripe("checkout/sessions", params)
    db.event("checkout.started", ws["id"], user["id"], plan=plan, interval=interval)
    return {"url": sess["url"]}


def portal(ws):
    s = settings.get()
    if s.dev_billing:
        _set_plan(ws["id"], "free")
        return {"url": "/app?downgraded=1#settings", "dev": True}
    if not ws.get("stripe_customer_id"):
        raise BillingError("No billing account yet: upgrade first")
    sess = _stripe("billing_portal/sessions", {"customer": ws["stripe_customer_id"], "return_url": f"{s.base_url}/app#settings"})
    return {"url": sess["url"]}


# ── webhooks ─────────────────────────────────────────────────────────────────────────
def verify(payload, header, secret, now=None):
    """Stripe-Signature: t=<ts>,v1=<hex HMAC-SHA256(secret, "<ts>.<payload>")>[,v1=...]."""
    if not secret or not header:
        return False
    parts = {}
    for item in header.split(","):
        k, _, v = item.strip().partition("=")
        parts.setdefault(k, []).append(v)
    try:
        ts = int(parts["t"][0])
    except (KeyError, ValueError):
        return False
    if abs((now or time.time()) - ts) > TOLERANCE:
        return False
    want = hmac.new(secret.encode(), f"{ts}.".encode() + payload, hashlib.sha256).hexdigest()
    return any(hmac.compare_digest(want, sig) for sig in parts.get("v1", []))


def handle_event(evt):
    """Apply one verified Stripe event. Returns a short description (for logs/tests)."""
    if db.one("SELECT 1 FROM billing_events WHERE id=?", evt["id"]):
        return "duplicate"
    db.run("INSERT INTO billing_events (id, type, received_at) VALUES (?,?,?)", evt["id"], evt["type"], time.time())
    obj = evt["data"]["object"]
    t = evt["type"]
    if t == "checkout.session.completed":
        wsid = obj.get("client_reference_id") or (obj.get("metadata") or {}).get("workspace_id")
        if wsid:
            db.run("UPDATE workspaces SET stripe_customer_id=?, stripe_subscription_id=? WHERE id=?",
                   obj.get("customer"), obj.get("subscription"), wsid)
        return "linked"
    if t in ("customer.subscription.created", "customer.subscription.updated", "customer.subscription.deleted"):
        wsid = (obj.get("metadata") or {}).get("workspace_id")
        if not wsid:
            row = db.one("SELECT id FROM workspaces WHERE stripe_customer_id=?", obj.get("customer"))
            wsid = row and row["id"]
        if not wsid:
            log.warning("stripe %s for unknown customer %s", t, obj.get("customer"))
            return "unknown customer"
        item = ((obj.get("items") or {}).get("data") or [{}])[0]
        price = (item.get("price") or {}).get("id")
        plan, interval = settings.get().stripe_prices.get(price, (None, None))
        status = obj.get("status", "active")
        if t == "customer.subscription.deleted" or status in ("canceled", "incomplete_expired", "unpaid"):
            _set_plan(wsid, "free", status="active", customer=obj.get("customer"))
            db.event("plan.changed", wsid, plan="free", reason=status)
            return "downgraded"
        if not plan:
            log.warning("stripe price %s is not mapped to a plan", price)
            return "unknown price"
        seats = max(plans.PLANS[plan]["limits"]["members"] or 1, int(item.get("quantity") or 1))
        _set_plan(wsid, plan, status=status, interval=interval, period_end=obj.get("current_period_end"),
                  seats=seats, customer=obj.get("customer"), subscription=obj.get("id"))
        db.event("plan.changed", wsid, plan=plan, status=status)
        return f"plan {plan} ({status})"
    return "ignored"
