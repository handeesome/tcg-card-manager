#!/usr/bin/env python3
"""Local-only configuration helpers for price collectors.

Secrets are read from environment variables first, then data/api_tokens.json.
This keeps API keys and captured account tokens out of source files.
"""
import json
import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
TOKEN_FILE = DATA_DIR / "api_tokens.json"


def _load_tokens() -> dict:
    if not TOKEN_FILE.exists():
        return {}
    try:
        with open(TOKEN_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def get_secret(section: str, key: str = "token", env: str = "") -> str:
    """Return a secret from env or data/api_tokens.json without logging it."""
    if env and os.environ.get(env):
        return os.environ[env].strip()

    tokens = _load_tokens()
    section_data = tokens.get(section, {})
    if isinstance(section_data, dict):
        value = section_data.get(key, "")
    else:
        value = section_data if key == "token" else ""
    return str(value).strip() if value else ""


def get_platform_token(platform: str) -> str:
    env_map = {
        "biaoka": "BIAOKA_TOKEN",
        "jihuanshe": "JIHUANSHE_TOKEN",
    }
    return get_secret(platform, "token", env=env_map.get(platform, ""))


def get_api_key(name: str) -> str:
    env_map = {
        "tcgapi": "TCGAPI_KEY",
        "serpapi": "SERPAPI_KEY",
        "tcgpricelookup": "TCGPRICELOOKUP_KEY",
        "pokeprice": "POKEPRICE_KEY",
        "pricecharting": "PRICECHARTING_TOKEN",
    }
    key_map = {
        "tcgapi": "tcgapi_key",
        "serpapi": "serpapi_key",
        "tcgpricelookup": "tcgpricelookup_key",
        "pokeprice": "pokeprice_key",
        "pricecharting": "pricecharting_token",
    }
    return get_secret("external_apis", key_map.get(name, name), env=env_map.get(name, ""))
