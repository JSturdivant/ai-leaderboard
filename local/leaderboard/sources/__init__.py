"""Data collectors. Each module exposes NAME, LABEL, URL and fetch(cfg, prev) -> JSON-able payload."""

from . import arena, epoch, litellm, nous, openrouter

SOURCES = {m.NAME: m for m in (openrouter, nous, litellm, arena, epoch)}
