#!/usr/bin/env python3
"""Normalize authorized JiHuanShe capture fixtures into collector rows.

This is the bridge from "we captured useful JSON" to "the dashboard can consume
it". It intentionally handles only normal JSON. Opaque raw_data is reported as
decoder work until we have a proven decoder.
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
CAPTURED_DIR = SCRIPT_DIR / "captured_requests"
NORMALIZED_DIR = PROJECT_ROOT / "data" / "collector_runs" / "jihuanshe_capture_normalized"

sys.path.insert(0, str(SCRIPT_DIR))
from analyze_jihuanshe_captures import (  # noqa: E402
    compact_value,
    dict_candidates,
    input_files,
    load_json,
    query_price_candidates,
    safe_number,
    trend_candidates,
)


def now_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def price_key_rank(key: str) -> tuple[int, str, str]:
    text = key.lower()
    if key.startswith("url_query_"):
        return 75, "card_detail_reference", "medium"
    if any(part in text for part in ("latest", "sold", "sale", "final")) or any(part in key for part in ("成交", "售出")):
        return 100, "latest_sold", "high"
    if any(part in text for part in ("lowest", "min")) or "最低" in key:
        return 85, "lowest_listing", "medium"
    if any(part in text for part in ("current", "market", "platform", "estimate", "valuation")):
        return 75, "market_reference", "medium"
    if any(part in text for part in ("avg", "average")) or "均价" in key:
        return 60, "average_reference", "low"
    if "price" in text or "amount" in text or "金额" in key or "价格" in key:
        return 50, "price_reference", "low"
    return 10, "price_reference", "low"


def best_price_candidate(price_rows: list[dict]) -> dict | None:
    best = None
    for row in price_rows:
        for key, payload in (row.get("price_fields") or {}).items():
            number = payload.get("number")
            if number is None or number <= 0:
                continue
            rank, kind, confidence = price_key_rank(key)
            candidate = {
                "rank": rank,
                "path": row.get("path"),
                "key": key,
                "amount": number,
                "kind": kind,
                "confidence": confidence,
                "time_fields": row.get("time_fields") or {},
                "identity_fields": row.get("identity_fields") or {},
            }
            if best is None or candidate["rank"] > best["rank"]:
                best = candidate
    return best


def extract_first_time(row: dict) -> str:
    for value in (row.get("time_fields") or {}).values():
        if value:
            return str(value)
    return ""


def normalize_trend_points(capture_body, trend_rows: list[dict]) -> list[dict]:
    # Re-walk body rather than using only the analyzer summary so we can collect
    # every point from the chosen array.
    from analyze_jihuanshe_captures import is_price_key, walk  # local import avoids exposing internals above

    if not trend_rows:
        return []
    chosen = trend_rows[0]
    chosen_path = chosen.get("path")
    price_key = (chosen.get("price_keys") or [None])[0]
    time_key = (chosen.get("time_keys") or [None])[0]
    if not price_key or not time_key:
        return []

    target = None
    for path, value in walk(capture_body):
        if path == chosen_path:
            target = value
            break
    if not isinstance(target, list):
        return []

    points = []
    for item in target:
        if not isinstance(item, dict):
            continue
        amount = safe_number(item.get(price_key))
        time_value = item.get(time_key)
        if amount is None or not time_value:
            # Try any other price-like key if the preferred one is missing.
            for key, value in item.items():
                if is_price_key(str(key)):
                    amount = safe_number(value)
                    if amount is not None:
                        break
        if amount is None or not time_value:
            continue
        points.append({
            "time": str(time_value),
            "value": amount,
            "price_cny": amount,
            "currency": "CNY",
        })
    return points


def normalize_capture(path: Path) -> dict:
    capture = load_json(path)
    response = capture.get("response") or {}
    body = response.get("body")
    summary = response.get("body_summary") or {}
    request = capture.get("request") or {}

    result = {
        "card_id": "",
        "source": "jihuanshe",
        "market_group": "jp",
        "price_current": None,
        "price_history": [],
        "sold_records": [],
        "confidence": "none",
        "status": "no_price",
        "message": "no normalized JiHuanShe price found",
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "raw_ref": {
            "capture_file": str(path),
            "capture_kind": capture.get("kind"),
            "url": request.get("url"),
            "status_code": response.get("status_code"),
            "body_keys": summary.get("keys") or [],
            "has_raw_data": bool(summary.get("has_raw_data")),
        },
    }

    if summary.get("has_raw_data"):
        result["status"] = "raw_data_unresolved"
        result["message"] = "capture contains raw_data; decoder required before normalization"
        raw = summary.get("raw_data")
        if raw:
            result["raw_ref"]["raw_data"] = raw
        return result

    trends = trend_candidates(body)
    history = normalize_trend_points(body, trends)
    is_history_capture = (
        capture.get("kind") == "price_history_candidate"
        or "price-history" in str(request.get("url") or "")
    )

    if is_history_capture and history:
        latest = history[-1]
        result["price_current"] = {
            "amount": latest["value"],
            "currency": "CNY",
            "kind": "trend_latest",
        }
        result["price_history"] = history
        result["confidence"] = "low"
        result["status"] = "ok"
        result["message"] = "normalized JiHuanShe trend from authorized capture"
        result["raw_ref"]["trend"] = {
            "path": trends[0].get("path"),
            "price_keys": trends[0].get("price_keys"),
            "time_keys": trends[0].get("time_keys"),
            "points": len(history),
        }
        return result

    price_rows = query_price_candidates(request.get("url") or "") + dict_candidates(body)
    best = best_price_candidate(price_rows)
    if best:
        result["price_current"] = {
            "amount": best["amount"],
            "currency": "CNY",
            "kind": best["kind"],
        }
        result["confidence"] = best["confidence"]
        result["status"] = "ok"
        result["message"] = "normalized from authorized JiHuanShe capture"
        result["raw_ref"].update({
            "price_path": best["path"],
            "price_key": best["key"],
            "time_fields": best["time_fields"],
            "identity_fields": best["identity_fields"],
        })
        if extract_first_time(best):
            result["raw_ref"]["price_time"] = extract_first_time(best)

    if history:
        result["price_history"] = history
        result["raw_ref"]["trend"] = {
            "path": trends[0].get("path"),
            "price_keys": trends[0].get("price_keys"),
            "time_keys": trends[0].get("time_keys"),
            "points": len(history),
        }
        if result["status"] != "ok":
            result["status"] = "ok"
            result["message"] = "normalized JiHuanShe trend from authorized capture"
            result["confidence"] = "low"

    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Normalize JiHuanShe capture fixtures into collector rows.")
    parser.add_argument("files", nargs="*", help="Fixture file(s) or directory. Defaults to scripts/captured_requests/*.json.")
    parser.add_argument("--out", default="", help="Optional output JSON path. Defaults to data/collector_runs.")
    args = parser.parse_args()

    rows = []
    for path in input_files(args.files):
        try:
            rows.append(normalize_capture(path))
        except Exception as exc:
            rows.append({
                "source": "jihuanshe",
                "status": "error",
                "message": str(exc)[:200],
                "raw_ref": {"capture_file": str(path)},
            })

    summary = {}
    for row in rows:
        status = row.get("status") or "unknown"
        summary[status] = summary.get(status, 0) + 1

    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_results": len(rows),
        "summary": summary,
        "collector_sources": rows,
    }

    if args.out:
        out_path = Path(args.out)
    else:
        NORMALIZED_DIR.mkdir(parents=True, exist_ok=True)
        out_path = NORMALIZED_DIR / f"{now_stamp()}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(json.dumps({
        "out": str(out_path),
        "total_results": output["total_results"],
        "summary": output["summary"],
        "ok_with_current_price": sum(1 for row in rows if row.get("price_current")),
        "ok_with_history": sum(1 for row in rows if row.get("price_history")),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
