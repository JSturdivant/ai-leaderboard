"""Local LLM cost-vs-capability leaderboard: collectors, scoring, and build."""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
LATEST = DATA_DIR / "latest.json"
CONFIG_PATH = ROOT / "config.json"
