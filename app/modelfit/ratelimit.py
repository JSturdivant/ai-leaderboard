"""In-process rate limiting (token buckets). Good for one app instance; use Redis when running several.

Daily API quotas per plan are metered in the database (db.usage) instead, so they survive restarts.
"""

import threading
import time


class Buckets:
    def __init__(self, rate_per_minute, burst):
        self.rate, self.burst = rate_per_minute / 60.0, burst
        self._b = {}
        self._lock = threading.Lock()

    def allow(self, key, cost=1.0):
        now = time.monotonic()
        with self._lock:
            tokens, last = self._b.get(key, (self.burst, now))
            tokens = min(self.burst, tokens + (now - last) * self.rate)
            ok = tokens >= cost
            self._b[key] = (tokens - cost if ok else tokens, now)
            if len(self._b) > 50_000:  # forget idle keys
                cutoff = now - 3600
                self._b = {k: v for k, v in self._b.items() if v[1] > cutoff}
            return ok


AUTH = Buckets(rate_per_minute=10, burst=10)    # sign-in / sign-up attempts per IP
API = Buckets(rate_per_minute=120, burst=60)    # /v1 calls per key (burst smoothing on top of the daily quota)
GATEWAY = Buckets(rate_per_minute=600, burst=120)  # gateway calls per workspace
WRITES = Buckets(rate_per_minute=600, burst=120)  # settings saves per workspace (slider drags are bursty)
