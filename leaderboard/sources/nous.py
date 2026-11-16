"""Nous Portal: public OpenAI-style /v1/models listing, which includes live prices.

Prices mirror OpenRouter for most models but differ for some (and carry promo `original`
prices), so it is treated as its own channel. `:free` variants are Portal free-tier models.
"""

import time

from ..util import fetch_json, per_million

NAME = "nous"
LABEL = "Nous Portal"
URL = "https://inference-api.nousresearch.com/v1/models"


def fetch(cfg, prev=None):
    data = fetch_json(URL, timeout=90)["data"]
    models = []
    for m in data:
        p = m.get("pricing") or {}
        orig = p.get("original") or {}
        arch = m.get("architecture") or {}
        params = m.get("supported_parameters") or []
        models.append(
            {
                "id": m["id"],
                "name": m.get("name") or m["id"],
                "created": m.get("created"),
                "context_length": m.get("context_length"),
                "in": per_million(p.get("prompt")),
                "out": per_million(p.get("completion")),
                "orig_in": per_million(orig.get("prompt")),
                "orig_out": per_million(orig.get("completion")),
                "output_modalities": arch.get("output_modalities") or [],
                "tools": "tools" in params,
                "reasoning": "reasoning" in params or "include_reasoning" in params,
                "frontier": bool(m.get("frontier")),
            }
        )
    return {"fetched_at": time.time(), "models": models}


def count(raw):
    return len(raw.get("models", []))
