"""Modelfit: live LLM price x benchmark intelligence, and the right model for every alias.

Collectors, scoring and the market snapshot build, plus the multi-tenant SaaS layer (accounts, workspaces,
plans, billing, API keys).
"""

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("MODELFIT_DATA_DIR") or ROOT / "data")  # market data cache + snapshot (disposable)
RAW_DIR = DATA_DIR / "raw"
LATEST = DATA_DIR / "latest.json"
CONFIG_PATH = ROOT / "config.json"
