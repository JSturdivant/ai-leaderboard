"""POST to the alias-sync webhook when any alias's #1 pick changes."""

import json
import urllib.request

from .util import log

HOOK_TIMEOUT = 5


def _top_picks(recs):
    """{alias: #1 model id} from a recommendations dict; skip aliases with no picks."""
    out = {}
    for alias, entry in ((recs or {}).get("aliases") or {}).items():
        picks = (entry or {}).get("picks") or []
        if picks:
            out[alias] = picks[0]["model"]
    return out


def notify_if_changed(prev_recs, new_recs, cfg):
    """Fire the webhook when at least one alias's #1 pick changed.

    Returns the list of changed alias names (possibly []). Never raises.
    """
    try:
        hook = (cfg or {}).get("webhook") or {}
        url, token = hook.get("url"), hook.get("token")
        before = _top_picks(prev_recs)
        after = _top_picks(new_recs)
        changed = {a: (before.get(a), after.get(a)) for a in after if before.get(a) != after.get(a)}
        # an alias that vanished: present before, gone now
        changed.update({a: (m, None) for a, m in before.items() if a not in after})
        if not changed:
            return []
        if not url:
            return sorted(changed)
        payload = json.dumps({
            "changed": {a: {"from": f, "to": t} for a, (f, t) in changed.items()},
            "generated_at": (new_recs or {}).get("generated_at"),
        }).encode("utf-8")
        req = urllib.request.Request(url, data=payload, headers={
            "Content-Type": "application/json",
            "X-Sync-Token": token or "",
        })
        urllib.request.urlopen(req, timeout=HOOK_TIMEOUT).read()
        log.info("webhook fired: %d alias pick(s) changed", len(changed))
        return sorted(changed)
    except Exception as exc:
        log.error("webhook notify failed: %s", exc)
        return []
