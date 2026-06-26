#!/usr/bin/env python3
"""Analyze sanitized JiHuanShe capture fixtures for price fields.

Run this after scripts/import_jihuanshe_capture.py. It does not call the
network. The goal is to quickly answer:
- did the authorized capture contain normal JSON prices?
- did it contain trend-like time/value arrays?
- is it still only opaque raw_data that needs decoder work?
"""

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
CAPTURED_DIR = SCRIPT_DIR / "captured_requests"
ANALYSIS_DIR = PROJECT_ROOT / "data" / "collector_runs" / "jihuanshe_capture_analysis"

PRICE_KEY_RE = re.compile(
    r"(price|amount|money|cny|rmb|yuan|lowest|sold|sale|market|platform|final|avg|min|max|"
    r"unit|current|estimate|valuation|成交|价格|售价|金额|最低|均价)",
    re.IGNORECASE,
)
COUNT_KEY_RE = re.compile(r"(count|num|quantity|total|page|size|数量|总数)", re.IGNORECASE)
TIME_KEY_RE = re.compile(r"(time|date|day|month|year|created|updated|sold_at|成交时间|日期)", re.IGNORECASE)
IDENTITY_KEY_RE = re.compile(
    r"(id|uuid|name|title|card|version|number|rarity|grade|grading|game|set|"
    r"名称|标题|编号|稀有|评级)",
    re.IGNORECASE,
)


def now_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def load_json(path: Path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def safe_number(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        text = value.replace(",", "").replace("¥", "").replace("￥", "").strip()
        match = re.search(r"-?\d+(?:\.\d+)?", text)
        if match:
            try:
                return float(match.group(0))
            except ValueError:
                return None
    return None


def is_price_key(key: str) -> bool:
    if not PRICE_KEY_RE.search(key):
        return False
    if COUNT_KEY_RE.search(key) and not re.search(r"(price|amount|money|cny|rmb|yuan|价格|金额)", key, re.IGNORECASE):
        return False
    return True


def compact_value(value):
    if isinstance(value, str):
        return value if len(value) <= 120 else value[:120] + "...<truncated>"
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return f"<{type(value).__name__}>"


def walk(value, path: str = "$"):
    yield path, value
    if isinstance(value, dict):
        for key, item in value.items():
            yield from walk(item, f"{path}.{key}")
    elif isinstance(value, list):
        for idx, item in enumerate(value[:200]):
            yield from walk(item, f"{path}[{idx}]")


def dict_candidates(body) -> list:
    rows = []
    for path, value in walk(body):
        if not isinstance(value, dict):
            continue
        price_fields = {}
        time_fields = {}
        identity_fields = {}
        raw_data = value.get("raw_data")

        for key, item in value.items():
            key_s = str(key)
            if key_s == "raw_data":
                continue
            if is_price_key(key_s):
                number = safe_number(item)
                if number is not None:
                    price_fields[key_s] = {
                        "value": compact_value(item),
                        "number": number,
                    }
            elif TIME_KEY_RE.search(key_s):
                time_fields[key_s] = compact_value(item)
            elif IDENTITY_KEY_RE.search(key_s):
                identity_fields[key_s] = compact_value(item)

        if price_fields or raw_data is not None:
            rows.append({
                "path": path,
                "price_fields": price_fields,
                "time_fields": time_fields,
                "identity_fields": identity_fields,
                "has_raw_data": raw_data is not None,
            })
    return rows


def query_price_candidates(url: str) -> list:
    if not url:
        return []
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    rows = []
    price_fields = {}
    identity_fields = {}

    for key, values in query.items():
        value = values[0] if values else ""
        if not value:
            continue
        if is_price_key(key):
            number = safe_number(value)
            if number is not None:
                price_fields[f"url_query_{key}"] = {
                    "value": compact_value(value),
                    "number": number,
                }
        elif IDENTITY_KEY_RE.search(key) or key in {"image", "pack_name_cn"}:
            identity_fields[key] = compact_value(value)

    if price_fields:
        rows.append({
            "path": f"url.query:{parsed.path}",
            "price_fields": price_fields,
            "time_fields": {},
            "identity_fields": identity_fields,
            "has_raw_data": False,
        })
    return rows


def trend_candidates(body) -> list:
    rows = []
    for path, value in walk(body):
        if not isinstance(value, list) or len(value) < 2:
            continue
        sample = [item for item in value[:8] if isinstance(item, dict)]
        if len(sample) < 2:
            continue
        price_keys = set()
        time_keys = set()
        for item in sample:
            for key, val in item.items():
                key_s = str(key)
                if is_price_key(key_s) and safe_number(val) is not None:
                    price_keys.add(key_s)
                if TIME_KEY_RE.search(key_s):
                    time_keys.add(key_s)
        if price_keys and time_keys:
            rows.append({
                "path": path,
                "length": len(value),
                "price_keys": sorted(price_keys),
                "time_keys": sorted(time_keys),
                "first": {k: compact_value(v) for k, v in sample[0].items()},
            })
    return rows


def classify_capture(capture: dict) -> dict:
    request = capture.get("request") or {}
    response = capture.get("response") or {}
    body = response.get("body")
    summary = response.get("body_summary") or {}
    prices = dict_candidates(body)
    url_prices = query_price_candidates(request.get("url") or "")
    trends = trend_candidates(body)

    return {
        "file": "",
        "kind": capture.get("kind"),
        "method": request.get("method"),
        "url": request.get("url"),
        "status_code": response.get("status_code"),
        "body_keys": summary.get("keys") or [],
        "has_raw_data": bool(summary.get("has_raw_data")),
        "price_candidate_count": sum(1 for row in prices + url_prices if row["price_fields"]),
        "trend_candidate_count": len(trends),
        "price_candidates": (url_prices + prices)[:20],
        "trend_candidates": trends[:10],
    }


def input_files(paths: list[str]) -> list[Path]:
    if paths:
        files = []
        for value in paths:
            path = Path(value)
            if path.is_dir():
                files.extend(sorted(path.glob("*.json")))
            else:
                files.append(path)
        return files
    if not CAPTURED_DIR.exists():
        return []
    return sorted(CAPTURED_DIR.glob("*.json"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Analyze sanitized JiHuanShe capture fixtures.")
    parser.add_argument("files", nargs="*", help="Fixture file(s) or directory. Defaults to scripts/captured_requests/*.json.")
    parser.add_argument("--out", default="", help="Optional output JSON path. Defaults to data/collector_runs.")
    args = parser.parse_args()

    analyses = []
    for path in input_files(args.files):
        try:
            capture = load_json(path)
            row = classify_capture(capture)
            row["file"] = str(path)
            analyses.append(row)
        except Exception as exc:
            analyses.append({
                "file": str(path),
                "error": str(exc)[:200],
            })

    by_kind = {}
    raw_data_files = []
    for row in analyses:
        kind = row.get("kind") or "unknown"
        by_kind[kind] = by_kind.get(kind, 0) + 1
        if row.get("has_raw_data"):
            raw_data_files.append(row.get("file"))

    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_files": len(analyses),
        "by_kind": by_kind,
        "raw_data_files": raw_data_files,
        "price_candidate_count": sum(row.get("price_candidate_count", 0) for row in analyses),
        "trend_candidate_count": sum(row.get("trend_candidate_count", 0) for row in analyses),
        "captures": analyses,
    }

    if args.out:
        out_path = Path(args.out)
    else:
        ANALYSIS_DIR.mkdir(parents=True, exist_ok=True)
        out_path = ANALYSIS_DIR / f"{now_stamp()}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(json.dumps({
        "out": str(out_path),
        "total_files": output["total_files"],
        "by_kind": output["by_kind"],
        "raw_data_files": len(output["raw_data_files"]),
        "price_candidate_count": output["price_candidate_count"],
        "trend_candidate_count": output["trend_candidate_count"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
