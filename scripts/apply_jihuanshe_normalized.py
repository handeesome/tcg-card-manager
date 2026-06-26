#!/usr/bin/env python3
"""Apply normalized JiHuanShe capture results to a portfolio card."""

import argparse
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
DEFAULT_PORTFOLIO = DATA_DIR / "portfolio.json"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json(path: Path, default):
    if not path.exists():
        return default
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def price_rank(row: dict) -> int:
    current = row.get("price_current") or {}
    kind = current.get("kind") or ""
    confidence = row.get("confidence") or ""
    rank = {
        "latest_sold": 100,
        "lowest_listing": 85,
        "market_reference": 75,
        "card_detail_reference": 75,
        "trend_latest": 60,
        "average_reference": 50,
        "price_reference": 40,
    }.get(kind, 0)
    if confidence == "high":
        rank += 10
    elif confidence == "medium":
        rank += 5
    return rank


def combine_rows(rows: list[dict], card_id: str) -> dict:
    usable = [row for row in rows if row.get("source") == "jihuanshe"]
    ok_rows = [row for row in usable if row.get("status") == "ok"]
    unresolved = [row for row in usable if row.get("status") == "raw_data_unresolved"]

    best_price = None
    for row in ok_rows:
        if row.get("price_current") and (best_price is None or price_rank(row) > price_rank(best_price)):
            best_price = row

    best_history = []
    for row in ok_rows:
        history = row.get("price_history") or []
        if len(history) > len(best_history):
            best_history = history

    combined = {
        "card_id": card_id,
        "source": "jihuanshe",
        "market_group": "jp",
        "price_current": (best_price or {}).get("price_current"),
        "price_history": best_history,
        "sold_records": [],
        "confidence": (best_price or {}).get("confidence") or ("low" if best_history else "none"),
        "status": "ok" if (best_price or best_history) else ("raw_data_unresolved" if unresolved else "no_price"),
        "message": "combined from normalized JiHuanShe captures",
        "collected_at": now_iso(),
        "raw_ref": {
            "combined_rows": len(usable),
            "ok_rows": len(ok_rows),
            "raw_data_unresolved_rows": len(unresolved),
            "captures": [
                (row.get("raw_ref") or {}).get("capture_file")
                for row in usable
                if (row.get("raw_ref") or {}).get("capture_file")
            ],
        },
    }

    if best_price:
        combined["raw_ref"]["selected_price"] = {
            "kind": (best_price.get("price_current") or {}).get("kind"),
            "amount": (best_price.get("price_current") or {}).get("amount"),
            "price_key": (best_price.get("raw_ref") or {}).get("price_key"),
            "price_path": (best_price.get("raw_ref") or {}).get("price_path"),
        }
    if best_history:
        combined["raw_ref"]["history_points"] = len(best_history)

    return combined


def merge_collector_source(existing, result: dict) -> list:
    rows = [row for row in (existing or []) if row.get("source") != result.get("source")]
    rows.append(result)
    return rows


def apply_to_portfolio(portfolio: dict, card_id: str, result: dict) -> dict:
    cards = portfolio.get("cards") or []
    target = None
    for card in cards:
        if card.get("id") == card_id:
            target = card
            break
    if target is None:
        raise ValueError(f"card_id not found in portfolio: {card_id}")

    prices = target.setdefault("current_prices", {})
    sources_detail = prices.setdefault("sources_detail", {})
    sources_detail["collector_jihuanshe"] = result
    prices["collector_sources"] = merge_collector_source(prices.get("collector_sources"), result)

    current = result.get("price_current") or {}
    if current.get("currency") == "CNY" and current.get("amount"):
        prices["jihuanshe_cny"] = current["amount"]
        prices["jihuanshe_price_kind"] = current.get("kind")
    prices["last_updated"] = now_iso()
    portfolio["last_updated"] = now_iso()
    return portfolio


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply normalized JiHuanShe results to a portfolio card.")
    parser.add_argument("normalized_json", help="Output from normalize_jihuanshe_captures.py")
    parser.add_argument("--card-id", required=True, help="Portfolio card id to update.")
    parser.add_argument("--portfolio", default=str(DEFAULT_PORTFOLIO))
    parser.add_argument("--out", default="", help="Write updated portfolio copy here.")
    parser.add_argument("--write", action="store_true", help="Overwrite --portfolio in place.")
    args = parser.parse_args()

    normalized = load_json(Path(args.normalized_json), {})
    rows = normalized.get("collector_sources") or []
    combined = combine_rows(rows, args.card_id)

    portfolio_path = Path(args.portfolio)
    portfolio = load_json(portfolio_path, {})
    updated = apply_to_portfolio(portfolio, args.card_id, combined)

    if args.write:
        backup = portfolio_path.with_suffix(portfolio_path.suffix + f".jihuanshe.{datetime.now().strftime('%Y%m%d%H%M%S')}.bak")
        if portfolio_path.exists():
            shutil.copy2(portfolio_path, backup)
        out_path = portfolio_path
        write_json(out_path, updated)
    elif args.out:
        out_path = Path(args.out)
        write_json(out_path, updated)
        backup = None
    else:
        out_path = None
        backup = None

    print(json.dumps({
        "card_id": args.card_id,
        "status": combined.get("status"),
        "price_current": combined.get("price_current"),
        "history_points": len(combined.get("price_history") or []),
        "combined_rows": (combined.get("raw_ref") or {}).get("combined_rows"),
        "out": str(out_path) if out_path else "",
        "backup": str(backup) if backup else "",
        "write_required": not bool(out_path),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
