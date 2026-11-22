"""Plans and entitlements: the single source of truth for what each tier may do.

The server enforces these; the UI reads them from /api/plans to draw the pricing table and lock icons.
Change prices or limits here (and the Stripe price ids in the environment), nowhere else.
"""

UNLIMITED = None

PLANS = {
    "free": {
        "label": "Free", "tagline": "For individuals exploring models",
        "price": {"month": 0, "year": 0},
        "limits": {"aliases": 4, "api_keys": 1, "api_requests_per_day": 200, "gateway_requests_per_day": 100, "fallbacks": 1,
                   "members": 1, "history_days": 7},
        "features": {"routing_controls": False, "custom_weights": False, "custom_aliases": True, "webhooks": False, "csv_export": False,
                     "pick_history": True, "team": False, "priority_support": False},
        "highlights": ["Live price × benchmark matrix", "Up to 4 model aliases", "Stable routes + 1 fallback per alias",
                       "100 gateway requests / day", "200 API requests / day"],
    },
    "pro": {
        "label": "Pro", "tagline": "For builders routing real traffic", "popular": True,
        "price": {"month": 19, "year": 190},
        "limits": {"aliases": 25, "api_keys": 5, "api_requests_per_day": 10_000, "gateway_requests_per_day": 25_000, "fallbacks": 3,
                   "members": 1, "history_days": 90},
        "features": {"routing_controls": True, "custom_weights": True, "custom_aliases": True, "webhooks": True, "csv_export": True,
                     "pick_history": True, "team": False, "priority_support": False},
        "highlights": ["Everything in Free", "Up to 25 aliases", "Pin models, set switch thresholds, 3 fallbacks",
                       "25k gateway requests / day", "Tune every benchmark weight", "Webhooks when a route changes",
                       "10k API requests / day · CSV · 90-day history"],
    },
    "team": {
        "label": "Team", "tagline": "For teams standardising on models",
        "price": {"month": 79, "year": 790}, "per_seat_note": "includes 5 seats",
        "limits": {"aliases": 100, "api_keys": 25, "api_requests_per_day": 100_000, "gateway_requests_per_day": 250_000, "fallbacks": 5,
                   "members": 5, "history_days": 365},
        "features": {"routing_controls": True, "custom_weights": True, "custom_aliases": True, "webhooks": True, "csv_export": True,
                     "pick_history": True, "team": True, "priority_support": True},
        "highlights": ["Everything in Pro", "Shared workspace, 5 seats", "100 aliases · 5 fallbacks · 25 API keys",
                       "250k gateway requests / day", "100k API requests / day", "1-year history · priority support"],
    },
    # every feature, no limits: one person running their own copy
    "selfhost": {
        "label": "Self-hosted", "tagline": "Your own instance", "hidden": True,
        "price": {"month": 0, "year": 0},
        "limits": {"aliases": UNLIMITED, "api_keys": UNLIMITED, "api_requests_per_day": UNLIMITED,
                   "gateway_requests_per_day": UNLIMITED, "fallbacks": 5,
                   "members": UNLIMITED, "history_days": UNLIMITED},
        "features": {"routing_controls": True, "custom_weights": True, "custom_aliases": True, "webhooks": True, "csv_export": True,
                     "pick_history": True, "team": False, "priority_support": False},
        "highlights": [],
    },
}

PAID = ("pro", "team")
FEATURE_LABELS = {
    "routing_controls": "Route pinning & switch thresholds", "custom_weights": "Custom fit weights", "custom_aliases": "Custom aliases", "webhooks": "Pick-change webhooks",
    "csv_export": "CSV export", "pick_history": "Pick history", "team": "Team workspace",
    "priority_support": "Priority support",
}


def get(plan):
    return PLANS.get(plan) or PLANS["free"]


def effective_plan(ws):
    """A lapsed subscription falls back to Free (data is kept; paid features lock until it is fixed)."""
    if ws["plan"] in PAID and ws.get("plan_status") not in ("active", "trialing", "past_due"):
        return "free"
    return ws["plan"]


def allows(ws, feature):
    return bool(get(effective_plan(ws))["features"].get(feature))


def limit(ws, name):
    lim = get(effective_plan(ws))["limits"].get(name)
    if name == "members" and lim is not None and ws["plan"] == "team":
        lim = max(lim, ws.get("seats") or lim)
    return lim


class Upgrade(Exception):
    """Raised when an action needs a higher plan; the API turns it into 402 with an upgrade hint."""

    def __init__(self, message, feature=None, plan="pro"):
        super().__init__(message)
        self.feature, self.plan = feature, plan


def require(ws, feature):
    if not allows(ws, feature):
        need = next((p for p in PAID if PLANS[p]["features"].get(feature)), "pro")
        raise Upgrade(f"{FEATURE_LABELS.get(feature, feature)} is part of the {PLANS[need]['label']} plan", feature, need)


def check_limit(ws, name, current):
    lim = limit(ws, name)
    if lim is not None and current >= lim:
        need = next((p for p in PAID if (PLANS[p]["limits"][name] or 1e18) > lim), "team")
        raise Upgrade(f"Your plan allows {lim} {name.replace('_', ' ')}", name, need)


def public():
    """The pricing table as JSON (hidden plans left out)."""
    return {k: {kk: v for kk, v in p.items()} for k, p in PLANS.items() if not p.get("hidden")}
