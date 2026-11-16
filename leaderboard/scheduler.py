"""Refresh cycle shared by the CLI and the server: fetch stale sources, rebuild latest.json."""

import threading
import time

from . import CONFIG_PATH, LATEST
from .build import build_and_write
from .sources import SOURCES
from .store import forceable_sources, load_config, refresh_sources, stale_sources
from .util import log


def needs_rebuild(refreshed):
    """Rebuild after any fetch, when no output exists yet, or when config.json was edited."""
    if refreshed or not LATEST.exists():
        return True
    try:
        return CONFIG_PATH.stat().st_mtime > LATEST.stat().st_mtime
    except OSError:
        return True


def run_cycle(cfg=None, names=None, force=False, progress=None, respect_min_age=False):
    """One pass. names=None -> only stale sources; force -> all of them (or, with
    respect_min_age, all that are older than refresh.min_force_minutes)."""
    cfg = cfg or load_config()
    if names is None:
        if force:
            names = forceable_sources(cfg) if respect_min_age else list(SOURCES)
        else:
            names = stale_sources(cfg)
    results = refresh_sources(cfg, names, progress) if names else {}
    if needs_rebuild(bool(results)):
        if progress:
            progress("building")
        build_and_write(cfg)
    return results


class Scheduler(threading.Thread):
    """Background thread: wakes every `check_seconds`, refreshes whatever is stale."""

    def __init__(self):
        super().__init__(daemon=True, name="scheduler")
        self._wake = threading.Event()
        self._force = False
        self.state = {"running": False, "step": None, "last_cycle_at": None, "last_error": None}

    def trigger(self, force=True):
        self._force = self._force or force
        self._wake.set()

    def _progress(self, step):
        self.state["step"] = step

    def run(self):
        while True:
            try:
                cfg = load_config()
                force, self._force = self._force, False
                self.state.update(running=True, step="starting", last_error=None)
                run_cycle(cfg, force=force, progress=self._progress, respect_min_age=True)
                self.state["last_cycle_at"] = time.time()
            except Exception as exc:  # noqa: BLE001 - keep the loop alive no matter what
                log.exception("refresh cycle failed")
                self.state["last_error"] = f"{type(exc).__name__}: {exc}"[:300]
                cfg = {"refresh": {"check_seconds": 60}}
            finally:
                self.state.update(running=False, step=None)
            self._wake.wait(timeout=float(cfg["refresh"].get("check_seconds", 60)))
            self._wake.clear()
