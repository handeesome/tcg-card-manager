#!/usr/bin/env python3
"""Probe JiHuanShe endpoints and save sanitized response shapes.

This script is intentionally small and conservative:
- it uses the local JiHuanShe token if configured;
- it never prints or writes the token;
- it stores only endpoint status, keys, small scalar samples, and raw_data metadata.
"""

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, urlencode, urlparse, urlunparse, parse_qsl

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
DATA_DIR = PROJECT_ROOT / "data"
PROBE_DIR = DATA_DIR / "collector_runs" / "jihuanshe_probe"

sys.path.insert(0, str(SCRIPT_DIR))
from chinese_platform_api import JiHuanSheAPI  # noqa: E402


SENSITIVE_KEYS = {
    "authorization",
    "token",
    "access_token",
    "refresh_token",
    "cookie",
    "set-cookie",
    "phone",
    "mobile",
    "email",
    "openid",
    "unionid",
}


def now_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def safe_scalar(value):
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        if len(value) <= 160:
            return value
        return value[:160] + "...<truncated>"
    return None


def summarize_value(value, depth: int = 0):
    if depth > 3:
        return {"type": type(value).__name__}

    if isinstance(value, dict):
        summary = {
            "type": "dict",
            "keys": list(value.keys())[:40],
        }
        sample = {}
        for key, item in list(value.items())[:12]:
            if str(key).lower() in SENSITIVE_KEYS:
                sample[key] = "<redacted>"
            elif key == "raw_data" and isinstance(item, str):
                sample[key] = {
                    "type": "raw_data",
                    "length": len(item),
                    "prefix": item[:24],
                    "sha256": hashlib.sha256(item.encode("utf-8", errors="ignore")).hexdigest()[:16],
                }
            elif isinstance(item, (dict, list)):
                sample[key] = summarize_value(item, depth + 1)
            else:
                sample[key] = safe_scalar(item)
        summary["sample"] = sample
        return summary

    if isinstance(value, list):
        summary = {
            "type": "list",
            "length": len(value),
        }
        if value:
            summary["first"] = summarize_value(value[0], depth + 1)
        return summary

    return {"type": type(value).__name__, "value": safe_scalar(value)}


def classify(resp: dict) -> str:
    if not isinstance(resp, dict):
        return "invalid_response"
    code = resp.get("code")
    error = str(resp.get("error") or "")
    message = str(resp.get("msg") or resp.get("message") or "")
    if code in (401, 403):
        return "auth_required"
    if error == "MARKET_PERMISSION_DENY" or "无权限" in message:
        return "permission_denied"
    if error == "SYSTEM_UPGRADED" or "升级App" in message or "升级 App" in message:
        return "app_upgrade_required"
    if "raw_data" in resp:
        return "raw_data_unresolved"
    if code and code not in (0, 200):
        return "error"
    return "reachable"


def run_probe(client: JiHuanSheAPI, name: str, endpoint: str, method: str = "GET", data: dict = None) -> dict:
    resp = client._call(endpoint, method=method, data=data)
    return {
        "name": name,
        "endpoint": endpoint,
        "method": method,
        "status": classify(resp),
        "response": summarize_value(resp),
    }


def candidate_endpoints(query: str, game_key: str) -> list:
    encoded_query = quote(query)
    endpoints = [
        ("users_public", "/api/market/users?page=1", "GET", None),
        ("activities", f"/api/market/activities?game_key={quote(game_key)}", "GET", None),
        ("card_versions", f"/api/market/card-versions?game_key={quote(game_key)}", "GET", None),
        ("card_version_search", f"/api/market/card-versions/search?keyword={encoded_query}", "GET", None),
        ("cards", "/api/market/cards", "GET", None),
        ("products", f"/api/market/products?game_key={quote(game_key)}", "GET", None),
        ("seller_products", f"/api/market/sellers/products?game_key={quote(game_key)}", "GET", None),
        ("entrusted_prices", "/api/market/entrustedProduct/cardVersionPrices", "GET", None),
    ]

    # Endpoints seen in local collector notes/capture naming. These may need a
    # privileged account or more exact ids, but probing their shape is useful.
    endpoints.extend([
        ("base_info_query_get", f"/api/market/products/get-base-info?keyword={encoded_query}", "GET", None),
        ("price_history_query_get", f"/api/market/price-history/products?keyword={encoded_query}", "GET", None),
    ])
    return endpoints


def fill_endpoint(endpoint: str, query: str, game_key: str) -> str:
    parsed = urlparse(endpoint)
    path = parsed.path or endpoint
    query_pairs = dict(parse_qsl(parsed.query, keep_blank_values=True))
    low = path.lower()
    if "card-versions" in low or "products" in low or "get-base-info" in low:
        if "keyword" not in query_pairs and "search" in low:
            query_pairs["keyword"] = query
        if "game_key" not in query_pairs and ("card-versions" in low or "products" in low):
            query_pairs["game_key"] = game_key
    if "page" not in query_pairs and any(part in low for part in ("products", "users", "orders")):
        query_pairs["page"] = "1"
    new_query = urlencode(query_pairs, doseq=True)
    return urlunparse(("", "", path, "", new_query, ""))


def endpoints_from_file(path: Path, query: str, game_key: str, limit: int) -> list:
    with open(path, "r", encoding="utf-8") as f:
        payload = json.load(f)
    rows = payload.get("endpoints") if isinstance(payload, dict) else payload
    endpoints = []
    seen = set()
    for idx, row in enumerate(rows or [], start=1):
        endpoint = row.get("endpoint") if isinstance(row, dict) else str(row)
        if not endpoint or endpoint in seen:
            continue
        seen.add(endpoint)
        if any(part in endpoint.lower() for part in ("destroy", "batchstore", "batchupdate", "batchshow", "orders")):
            continue
        endpoints.append((
            f"discovered_{idx:03d}_{row.get('kind', 'endpoint') if isinstance(row, dict) else 'endpoint'}",
            fill_endpoint(endpoint, query, game_key),
            "GET",
            None,
        ))
        if limit and len(endpoints) >= limit:
            break
    return endpoints


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe JiHuanShe API endpoint shapes.")
    parser.add_argument("--query", default="ポケるんTVのピカチュウと仲間たち S-P")
    parser.add_argument("--game-key", default="pokemon")
    parser.add_argument("--endpoints-file", default="", help="Optional endpoint discovery JSON from extract_jihuanshe_endpoints.py")
    parser.add_argument("--limit", type=int, default=40, help="Maximum discovered endpoints to probe.")
    parser.add_argument("--out", default="")
    args = parser.parse_args()

    client = JiHuanSheAPI()
    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "has_token": bool(client.token),
        "query": args.query,
        "game_key": args.game_key,
        "probes": [],
    }

    if args.endpoints_file:
        endpoints = endpoints_from_file(Path(args.endpoints_file), args.query, args.game_key, args.limit)
    else:
        endpoints = candidate_endpoints(args.query, args.game_key)

    for name, endpoint, method, data in endpoints:
        output["probes"].append(run_probe(client, name, endpoint, method=method, data=data))

    PROBE_DIR.mkdir(parents=True, exist_ok=True)
    out_path = Path(args.out) if args.out else PROBE_DIR / f"{now_stamp()}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    summary = {}
    for row in output["probes"]:
        summary[row["status"]] = summary.get(row["status"], 0) + 1
    print(json.dumps({
        "out": str(out_path),
        "has_token": output["has_token"],
        "summary": summary,
        "statuses": [{"name": r["name"], "status": r["status"]} for r in output["probes"]],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
