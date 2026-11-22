"""Shared fixtures: an isolated settings + database + market snapshot per test class."""

import json
import tempfile
from pathlib import Path
from unittest import mock

from modelfit import build as B
from modelfit import db, settings, tenants
from tests.test_build import NOW, RAW, cfg

STATUS = {"openrouter": {"ok": True, "fetched_at": 1763400000.0, "error": None, "records": 3},
          "epoch": {"ok": True, "fetched_at": 1763300000.0, "error": None, "records": 3}}


def snapshot(c=None):
    with mock.patch.object(B, "load_raw", side_effect=lambda n: RAW.get(n)), mock.patch.object(B, "load_status", return_value=STATUS):
        return B.build(c or cfg(), now=NOW)


def test_cfg():
    c = cfg()
    c["workspace_defaults"] = {"starter_aliases": ["Cheap"]}
    c["server"] = {"host": "127.0.0.1", "port": 0}
    return c


class Env:
    """Temp dir with a database, latest.json and config patched in; call stop() to undo."""

    def __init__(self, mode="selfhost", extra_env=None):
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        env = {"MODELFIT_MODE": mode, "MODELFIT_DB": str(d / "t.db"), "MODELFIT_SECRET_KEY": "x" * 40, **(extra_env or {})}
        self.prev = settings._current
        settings.set_current(settings.load(env))
        self.latest = d / "latest.json"
        self.cfg = test_cfg()
        self.snap = snapshot(self.cfg)
        self.latest.write_text(json.dumps(self.snap))
        self.patches = [mock.patch.object(tenants, "LATEST", self.latest), mock.patch.object(tenants, "load_config", return_value=self.cfg),
                        mock.patch("modelfit.weights.legacy_weights_paths", return_value=[d / "no-legacy.json"])]
        for p in self.patches:
            p.start()
        tenants._snap.update(mtime=None, data=None)

    def stop(self):
        db.release()
        for p in self.patches:
            p.stop()
        settings.set_current(self.prev)
        tenants._snap.update(mtime=None, data=None)
        self.tmp.cleanup()
