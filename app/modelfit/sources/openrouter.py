"""OpenRouter: model catalogue (+ embedded Artificial Analysis indices) and per-endpoint prices.

/api/v1/models                      one row per model; `benchmarks` carries the Artificial Analysis
                                    intelligence / coding / agentic indices and Design Arena Elo.
/api/v1/models/{id}/endpoints       every provider endpoint for a model with its own price. Needed
                                    because the catalogue's top-level price can mix endpoints
                                    (e.g. cheapest input from one provider, output from another).
Both are public; no API key needed.
"""

import concurrent.futures as cf
import time

from ..util import fetch_json, log, num, per_million

NAME = "openrouter"
LABEL = "OpenRouter"
URL = "https://openrouter.ai/api/v1/models"
ENDPOINTS_URL = "https://openrouter.ai/api/v1/models/{id}/endpoints"


def _trim_model(m):
    arch = m.get("architecture") or {}
    params = m.get("supported_parameters") or []
    pricing = m.get("pricing") or {}
    return {
        "id": m["id"],
        "canonical_slug": m.get("canonical_slug"),
        "name": m.get("name") or m["id"],
        "created": m.get("created"),
        "context_length": m.get("context_length"),
        "in": per_million(pricing.get("prompt")),
        "out": per_million(pricing.get("completion")),
        "cache_read": per_million(pricing.get("input_cache_read")),
        "input_modalities": arch.get("input_modalities") or [],
        "output_modalities": arch.get("output_modalities") or [],
        "tools": "tools" in params,
        "reasoning": "reasoning" in params or "include_reasoning" in params,
        "expiration": m.get("expiration_date"),
        "benchmarks": m.get("benchmarks") or None,
        "hf": m.get("hugging_face_id") or None,   # set for open-weight models
    }


def _trim_endpoint(e):
    p = e.get("pricing") or {}
    return {
        "provider": e.get("provider_name"),
        "tag": e.get("tag"),
        "tier": e.get("service_tier"),
        "quant": e.get("quantization"),
        "status": e.get("status"),
        "uptime": num(e.get("uptime_last_1d")),
        "in": per_million(p.get("prompt")),
        "out": per_million(p.get("completion")),
        "cache_read": per_million(p.get("input_cache_read")),
        "ctx": e.get("context_length"),
        "latency": num(e.get("latency_last_30m")),        # only returned to authenticated callers today
        "throughput": num(e.get("throughput_last_30m")),
    }


def wants_endpoints(m):
    """Endpoint prices are only needed for purchasable text models."""
    mid = m["id"]
    return (
        not mid.startswith("~")
        and not mid.endswith(":batch")
        and m.get("output_modalities") == ["text"]
        and "embed" not in mid
    )


def fetch(cfg, prev=None):
    models = [_trim_model(m) for m in fetch_json(URL, timeout=90)["data"]]
    ids = [m["id"] for m in models if wants_endpoints(m)]
    old = (prev or {}).get("endpoints", {})
    endpoints, failed = {}, 0

    def one(mid):
        data = fetch_json(ENDPOINTS_URL.format(id=mid), timeout=40, retries=2)["data"]
        return mid, [_trim_endpoint(e) for e in data.get("endpoints", [])]

    workers = int(cfg.get("refresh", {}).get("workers", 8))
    with cf.ThreadPoolExecutor(workers) as pool:
        futs = {pool.submit(one, mid): mid for mid in ids}
        for fut in cf.as_completed(futs):
            mid = futs[fut]
            try:
                endpoints[mid] = fut.result()[1]
            except Exception as exc:  # keep the previous copy for this model
                failed += 1
                if mid in old:
                    endpoints[mid] = old[mid]
                log.warning("openrouter endpoints %s: %s", mid, exc)
    log.info("openrouter: %d models, %d endpoint lists (%d failed)", len(models), len(endpoints), failed)
    return {"fetched_at": time.time(), "models": models, "endpoints": endpoints, "endpoint_failures": failed}


def count(raw):
    return len(raw.get("models", []))
