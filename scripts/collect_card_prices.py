#!/usr/bin/env python3
"""Unified price collection for Pokemon holdings.

v1 intentionally uses direct platform clients instead of Scrapy. The goal is
to stabilize data access and result shape before adding a crawling framework.
"""
import argparse
import hashlib
import json
import os
import shutil
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
DATA_DIR = PROJECT_ROOT / "data"
PORTFOLIO_PATH = DATA_DIR / "portfolio.json"
BACKUP_DIR = DATA_DIR / "backups"
PRICE_HISTORY_DIR = DATA_DIR / "price_history"
COLLECTOR_RUN_DIR = DATA_DIR / "collector_runs"
CAPTURED_DIR = SCRIPT_DIR / "captured_requests"
USD2CNY = 7.2

sys.path.insert(0, str(SCRIPT_DIR))
from chinese_platform_api import BiuCardAPI, CardHobbyAPI, JiHuanSheAPI  # noqa: E402
from collector_config import get_api_key  # noqa: E402


FAIL_REASONS = {
    "auth_required",
    "permission_denied",
    "app_upgrade_required",
    "raw_data_unresolved",
    "no_match",
    "no_price",
    "reference_only",
    "rate_limited",
    "error",
    "skipped",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def today_key() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def load_json(path: Path, default):
    if not path.exists():
        return default
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def backup_portfolio() -> Path:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d%H%M%S")
    backup = BACKUP_DIR / f"portfolio.json.collector.{ts}.bak"
    shutil.copy2(PORTFOLIO_PATH, backup)
    return backup


def normalize_card_number(value: str) -> str:
    return (value or "").strip().upper()


def grade_key(card: dict) -> str:
    grading = card.get("grading") or {}
    grader = str(grading.get("grader") or "").strip().lower()
    grade = grading.get("grade")
    if grader and grade:
        grade_text = str(grade).replace(".", "_").lower()
        return f"{grader}{grade_text}"
    return "raw"


def biaoka_grade_key(card: dict) -> str:
    key = grade_key(card)
    if key.startswith("ace10"):
        return "psa10"
    return key if key != "raw" else "raw"


def card_language(card: dict) -> str:
    return str(card.get("language") or "").strip().lower()


def is_japanese_card(card: dict) -> bool:
    return card_language(card) in ("jp", "ja", "jpn", "日版")


def market_group_for_source(card: dict, source: str) -> str:
    if source in ("jihuanshe", "pricecharting_jp", "jp_market_reference", "cardmarket_jp"):
        return "jp"
    if source in ("biaoka", "tcgpricelookup", "pokeprice", "serpapi_ebay"):
        return "us"
    if source in ("cardhobby", "xianyu"):
        return "cn"
    return "jp" if is_japanese_card(card) else "us"


def card_query(card: dict, prefer_japanese: bool = False) -> str:
    names = []
    if prefer_japanese:
        names = [card.get("name_jp"), card.get("name_cn"), card.get("name_en")]
    else:
        names = [card.get("name_en"), card.get("name_cn"), card.get("name_jp")]
    name = next((str(x).strip() for x in names if x), "")
    number = str(card.get("card_number") or "").strip()
    return f"{name} {number}".strip() if number else name


def clean_query_name(name: str) -> str:
    cleaned = (name or "").replace("☆", " Star ").replace("★", " Star ")
    cleaned = cleaned.replace("’", "'")
    return " ".join(cleaned.split())


def biaoka_search_queries(card: dict) -> list:
    """Build high-recall BiaoKa search queries for Pokemon cards."""
    name_en = clean_query_name(card.get("name_en") or "")
    name_cn = clean_query_name(card.get("name_cn") or "")
    name_jp = clean_query_name(card.get("name_jp") or "")
    number = str(card.get("card_number") or "").strip()
    series = str(card.get("series") or "")
    series_lower = series.lower()
    queries = []

    prefixed_number = number
    if number and "swsh" in series_lower and not number.upper().startswith("SWSH"):
        prefixed_number = f"SWSH{number}"

    for n in [name_en, name_cn, name_jp]:
        if not n:
            continue
        if prefixed_number and prefixed_number != number:
            queries.append(f"{n} {prefixed_number}")
        if number:
            queries.append(f"{n} {number}")
        if series and name_en == n:
            queries.append(f"{n} {series}")
        queries.append(n)

    if "Star" in name_en and number:
        queries.append(f"{name_en.replace(' Star', '')} {number}")
        queries.append(f"{name_en.replace(' Star', ' Gold Star')} {number}")

    return list(dict.fromkeys(q.strip() for q in queries if q.strip()))


def base_result(card: dict, source: str, status: str, message: str = "") -> dict:
    return {
        "card_id": card.get("id"),
        "source": source,
        "market_group": market_group_for_source(card, source),
        "price_current": None,
        "price_history": [],
        "sold_records": [],
        "grade": {
            "key": grade_key(card),
            "grader": (card.get("grading") or {}).get("grader"),
            "grade": (card.get("grading") or {}).get("grade"),
        },
        "currency": "CNY",
        "fetched_at": now_iso(),
        "confidence": "none",
        "status": status,
        "message": message,
        "raw_ref": {},
    }


def safe_float(value):
    try:
        if value in ("", None):
            return None
        return float(value)
    except Exception:
        return None


def extract_search_items(resp: dict) -> list:
    data = resp.get("data") if isinstance(resp, dict) else None
    if isinstance(data, dict):
        items = data.get("data") or data.get("items") or []
        return items if isinstance(items, list) else []
    return []


def item_serial(item: dict) -> str:
    card = item.get("card") or {}
    return str(card.get("serial_number") or item.get("serial_number") or "").strip()


def serial_matches(wanted: str, actual: str) -> bool:
    wanted = normalize_card_number(wanted)
    actual = normalize_card_number(actual)
    if not wanted or not actual:
        return False
    if wanted == actual or wanted in actual:
        return True
    if "/" in wanted and wanted.split("/")[0].lstrip("0") == actual.lstrip("0"):
        return True
    return wanted.lstrip("0") == actual.lstrip("0")


def pick_biaoka_match(items: list, card: dict) -> dict:
    number = card.get("card_number") or ""
    if number:
        for item in items:
            if serial_matches(number, item_serial(item)):
                return item
    return items[0] if items else {}


def compact_trend(values) -> list:
    if not isinstance(values, list):
        return []
    out = []
    window = values[-60:]
    end_date = datetime.now(timezone.utc).date()
    for idx, point in enumerate(window):
        if isinstance(point, dict):
            price = safe_float(point.get("price") or point.get("value") or point.get("avg"))
            date = point.get("date") or point.get("day") or point.get("time")
        else:
            price = safe_float(point)
            date = None
        if price is not None:
            time_value = date or (end_date - timedelta(days=(len(window) - idx - 1))).isoformat()
            row = {
                "index": idx,
                "time": time_value,
                "value": price,
                "price_cny": price,
                "currency": "CNY",
            }
            if date:
                row["date"] = date
            else:
                row["date"] = None
                row["time_inferred"] = True
            out.append(row)
    return out


def extract_detail_trend(detail: dict) -> list:
    """Best-effort trend extraction from BiaoKa card detail responses."""
    data = detail.get("data") if isinstance(detail, dict) else None
    candidates = []
    if isinstance(data, dict):
        candidates.extend([
            data.get("estimated_trend"),
            data.get("trend"),
            data.get("price_trend"),
            data.get("price_history"),
        ])
        card = data.get("card") if isinstance(data.get("card"), dict) else {}
        candidates.extend([
            card.get("estimated_trend"),
            card.get("trend"),
            card.get("price_trend"),
            card.get("price_history"),
        ])
    for candidate in candidates:
        trend = compact_trend(candidate)
        if trend:
            return trend
    return []


def recent_sales_from_grading(data: dict) -> list:
    rows = []
    for sale in data.get("recent_sales") or []:
        amount = safe_float(sale.get("price_cny") or sale.get("final_price_cny"))
        if amount is None or amount <= 0:
            continue
        rows.append({
            "price_cny": amount,
            "market": sale.get("market") or sale.get("source") or "",
            "sold_at": sale.get("sold_at") or "",
            "title": sale.get("title") or "",
            "grade": sale.get("grading") or "",
        })
    return rows


def collect_biaoka(card: dict, client: BiuCardAPI, live: bool = True) -> dict:
    result = base_result(card, "biaoka", "skipped", "not collected")
    if (card.get("game") or "").lower() != "pokemon":
        result["message"] = "non-pokemon card skipped in v1"
        return result
    if not live:
        result["message"] = "live collection disabled"
        return result
    if not client.token:
        return base_result(card, "biaoka", "auth_required", "missing biaoka token")

    query = ""
    search = {}
    items = []
    attempted_queries = biaoka_search_queries(card)
    for candidate in attempted_queries:
        query = candidate
        search = client.search_cards(query, category="pokemon", page=1, page_size=10, include_trend=True)
        if search.get("code") == 401:
            return base_result(card, "biaoka", "auth_required", search.get("msg", "auth required"))
        if search.get("code") == 429:
            return base_result(card, "biaoka", "rate_limited", "rate limited")
        if search.get("code") != 200:
            continue
        items = extract_search_items(search)
        if items:
            break

    if search.get("code") not in (None, 200) and not items:
        err = base_result(card, "biaoka", "error", search.get("msg", "search failed"))
        err["raw_ref"] = {"endpoint": "search-cards", "query": query, "code": search.get("code"), "attempted_queries": attempted_queries}
        return err

    if not items:
        miss = base_result(card, "biaoka", "no_match", "no matching card")
        miss["raw_ref"] = {"endpoint": "search-cards", "query": query, "attempted_queries": attempted_queries}
        return miss

    best = pick_biaoka_match(items, card)
    card_obj = best.get("card") or {}
    series = best.get("series") or {}
    card_set = best.get("card_set") or {}
    card_id = card_obj.get("id")
    price_psa10 = safe_float(best.get("price"))
    price_raw = safe_float(best.get("raw_card_price"))
    trend = compact_trend(best.get("estimated_trend"))

    detail = client.get_card_detail(card_id, include_trend=True) if card_id else {}
    detail_trend = extract_detail_trend(detail)

    grading_key = biaoka_grade_key(card)
    grading = client.get_grading_price(card_id, grading=grading_key) if card_id else {}
    sales = recent_sales_from_grading(grading)
    latest = sales[0] if sales else None

    current = None
    confidence = "none"
    if latest:
        current = {
            "amount": latest["price_cny"],
            "currency": "CNY",
            "kind": "latest_sold",
        }
        confidence = "high"
    elif grading_key == "psa10" and price_psa10:
        current = {"amount": price_psa10, "currency": "CNY", "kind": "psa10_estimate"}
        confidence = "medium"
    elif price_raw:
        current = {"amount": price_raw, "currency": "CNY", "kind": "raw_estimate"}
        confidence = "low"

    if not current:
        no_price = base_result(card, "biaoka", "no_price", "matched card but no usable price")
        no_price["raw_ref"] = {
            "endpoint": "search-cards",
            "query": query,
            "attempted_queries": attempted_queries,
            "card_id": card_id,
            "detail_code": detail.get("code"),
            "detail_keys": list((detail.get("data") or {}).keys())[:20] if isinstance(detail.get("data"), dict) else [],
            "detail_trend_points": len(detail_trend),
            "matched_name": card_obj.get("card_name") or card_obj.get("chinese_name"),
            "matched_serial": card_obj.get("serial_number"),
            "series": series.get("chinese_name") or series.get("name"),
            "card_set": card_set.get("chinese_name") or card_set.get("name"),
            "total_results": (search.get("data") or {}).get("total_count"),
            "grade_key": grading_key,
            "sale_count": grading.get("sale_count", 0),
            "psa10_price_cny": price_psa10,
            "raw_price_cny": price_raw,
            "summary": grading.get("summary"),
        }
        return no_price

    result = base_result(card, "biaoka", "ok", "collected")
    result.update({
        "price_current": current,
        "price_history": trend or detail_trend,
        "sold_records": sales,
        "confidence": confidence,
        "raw_ref": {
            "query": query,
            "attempted_queries": attempted_queries,
            "card_id": card_id,
            "detail_code": detail.get("code"),
            "detail_keys": list((detail.get("data") or {}).keys())[:20] if isinstance(detail.get("data"), dict) else [],
            "detail_trend_points": len(detail_trend),
            "matched_name": card_obj.get("card_name") or card_obj.get("chinese_name"),
            "matched_serial": card_obj.get("serial_number"),
            "series": series.get("chinese_name") or series.get("name"),
            "card_set": card_set.get("chinese_name") or card_set.get("name"),
            "total_results": (search.get("data") or {}).get("total_count"),
            "grade_key": grading_key,
            "sale_count": grading.get("sale_count", 0),
            "psa10_price_cny": price_psa10,
            "raw_price_cny": price_raw,
            "summary": grading.get("summary"),
        },
    })
    return result


def third_party_result(card: dict, source: str, payload: dict) -> dict:
    result = base_result(card, source, "ok", "fallback collected")
    result["raw_ref"] = {"payload": payload}

    if source == "cardhobby":
        amount = safe_float(payload.get("lowest_cny"))
        if amount:
            result["price_current"] = {"amount": amount, "currency": "CNY", "kind": "lowest_listing"}
            result["confidence"] = "medium" if payload.get("price_count", 0) else "low"
        else:
            result["status"] = "no_price"
            result["message"] = "cardhobby returned no usable CNY price"
    elif source == "tcgpricelookup":
        amount = safe_float(payload.get("raw_near_mint_market_usd"))
        if amount:
            result["price_current"] = {"amount": round(amount * USD2CNY, 2), "currency": "CNY", "kind": "raw_nm_usd_converted"}
            result["confidence"] = "low"
        else:
            result["status"] = "no_price"
            result["message"] = "tcgpricelookup returned no raw market price"
    elif source == "pokeprice":
        amount = safe_float(payload.get("raw_market_usd"))
        if amount:
            result["price_current"] = {"amount": round(amount * USD2CNY, 2), "currency": "CNY", "kind": "raw_usd_converted"}
            result["confidence"] = "low"
        else:
            result["status"] = "no_price"
            result["message"] = "pokeprice returned no raw market price"
    elif source == "serpapi_ebay":
        amount = safe_float(payload.get("price_usd"))
        if amount:
            result["price_current"] = {"amount": round(amount * USD2CNY, 2), "currency": "CNY", "kind": "ebay_sold_usd_converted"}
            result["confidence"] = "medium"
        else:
            result["status"] = "no_price"
            result["message"] = "serpapi returned no sold price"

    return result


def http_json(url: str, params: dict = None, headers: dict = None, timeout: int = 15) -> dict:
    if params:
        url = f"{url}?{urlencode(params)}"
    req = Request(url, headers=headers or {})
    with urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def fetch_fallback_cardhobby(card: dict) -> dict:
    keyword = card.get("name_cn") or card.get("name_en") or ""
    if not keyword:
        return {}
    return CardHobbyAPI().get_lowest_price(keyword)


def fetch_fallback_tcgpricelookup(card: dict) -> dict:
    key = get_api_key("tcgpricelookup")
    if not key:
        return {}
    query = card_query(card)
    data = http_json(
        "https://api.tcgpricelookup.com/v1/cards/search",
        params={"q": query, "game": "pokemon"},
        headers={"X-API-Key": key, "Accept": "application/json"},
    )
    items = data.get("data") or []
    if not items:
        return {}
    best = pick_generic_card_match(items, card)
    prices = best.get("prices") or {}
    raw_nm = ((prices.get("raw") or {}).get("near_mint") or {}).get("tcgplayer") or {}
    return {
        "source": "tcgpricelookup.com",
        "card_name": best.get("name") or best.get("card_name"),
        "card_number": best.get("number") or best.get("card_number"),
        "raw_near_mint_market_usd": safe_float(raw_nm.get("market") or raw_nm.get("market_price")),
    }


def fetch_fallback_pokeprice(card: dict) -> dict:
    key = get_api_key("pokeprice")
    if not key:
        return {}
    data = http_json(
        "https://www.pokemonpricetracker.com/api/v2/cards",
        params={"search": card.get("name_en") or card.get("name_cn") or "", "limit": 10},
        headers={"Authorization": f"Bearer {key}", "Accept": "application/json"},
    )
    items = data.get("data") or []
    if not items:
        return {}
    best = pick_generic_card_match(items, card)
    prices = best.get("prices") or {}
    return {
        "source": "pokemonpricetracker.com",
        "card_name": best.get("name"),
        "card_number": best.get("cardNumber") or best.get("number"),
        "raw_market_usd": safe_float(prices.get("market") or prices.get("raw_market")),
    }


def fetch_fallback_serpapi_ebay(card: dict) -> dict:
    key = get_api_key("serpapi")
    if not key:
        return {}
    grading = card.get("grading") or {}
    query = " ".join(
        str(part).strip()
        for part in [
            card.get("name_en") or card.get("name_cn") or "",
            card.get("card_number") or "",
            f"{grading.get('grader')} {grading.get('grade')}" if grading.get("grader") and grading.get("grade") else "",
        ]
        if part
    )
    data = http_json(
        "https://serpapi.com/search",
        params={"engine": "ebay", "_nkw": query, "ebay_domain": "ebay.com", "show_only": "Sold", "api_key": key},
        headers={"Accept": "application/json"},
        timeout=20,
    )
    prices = []
    samples = []
    for item in (data.get("organic_results") or [])[:10]:
        extracted = (item.get("price") or {}).get("extracted")
        amount = safe_float(extracted)
        if amount:
            prices.append(amount)
            samples.append({"title": (item.get("title") or "")[:80], "price_usd": amount})
    if not prices:
        return {}
    prices.sort()
    return {
        "source": "serpapi.com (eBay sold)",
        "search_query": query,
        "price_usd": prices[len(prices) // 2],
        "samples": samples[:5],
    }


def pricecharting_query(card: dict) -> str:
    parts = [
        "Pokemon",
        "Japanese" if is_japanese_card(card) else "",
        card.get("name_en") or card.get("name_jp") or card.get("name_cn") or "",
        card.get("card_number") or "",
        card.get("series") or "",
    ]
    return " ".join(str(part).strip() for part in parts if str(part).strip())


def pricecharting_usd(value):
    amount = safe_float(value)
    if amount is None or amount <= 0:
        return None
    # PriceCharting API prices are returned in cents.
    return round(amount / 100.0, 2)


def fetch_pricecharting_jp(card: dict) -> dict:
    token = get_api_key("pricecharting")
    if not token:
        return {"auth_required": True}
    query = pricecharting_query(card)
    data = http_json(
        "https://www.pricecharting.com/api/product",
        params={"t": token, "q": query},
        headers={"Accept": "application/json"},
        timeout=20,
    )
    status = str(data.get("status") or "").lower()
    if status and status not in ("success", "ok"):
        return {
            "api_status": data.get("status"),
            "error": data.get("error-message") or data.get("message") or "pricecharting request failed",
            "query": query,
        }

    loose_usd = pricecharting_usd(data.get("loose-price"))
    graded_usd = pricecharting_usd(data.get("graded-price"))
    new_usd = pricecharting_usd(data.get("new-price"))
    grading = card.get("grading") or {}
    grader = str(grading.get("grader") or "").lower()
    grade = safe_float(grading.get("grade"))
    selected = loose_usd
    selected_kind = "loose_price"
    confidence = "medium" if loose_usd else "none"

    if grader == "psa" and grade and grade >= 8 and graded_usd:
        selected = graded_usd
        selected_kind = "graded_price"
        confidence = "medium"
    elif grader == "ace" and grade and grade >= 10 and graded_usd:
        selected = round(graded_usd * 0.30, 2)
        selected_kind = "graded_price_ace_discount"
        confidence = "low"

    product_url = data.get("url") or data.get("product-url")
    if isinstance(product_url, str) and product_url.startswith("/"):
        product_url = "https://www.pricecharting.com" + product_url

    return {
        "source": "pricecharting.com",
        "query": query,
        "product_name": data.get("product-name") or data.get("product_name"),
        "console_name": data.get("console-name") or data.get("console_name"),
        "product_url": product_url,
        "loose_usd": loose_usd,
        "graded_usd": graded_usd,
        "new_usd": new_usd,
        "selected_usd": selected,
        "selected_kind": selected_kind,
        "confidence": confidence,
    }


def build_japanese_market_links(card: dict) -> list:
    name_jp = card.get("name_jp") or ""
    name_en = card.get("name_en") or ""
    name_cn = card.get("name_cn") or ""
    number = str(card.get("card_number") or "").strip()
    jp_query = " ".join(part for part in [name_jp or name_en or name_cn, number, "ポケカ"] if part)
    en_query = " ".join(part for part in [name_en or name_jp or name_cn, number, "Pokemon Japanese"] if part)
    return [
        {
            "label": "Aucfan 落札相場",
            "kind": "sold_reference",
            "url": "https://aucfan.com/search1/q-" + quote(jp_query) + "/s-mix/",
        },
        {
            "label": "Mercari JP",
            "kind": "listing_reference",
            "url": "https://jp.mercari.com/search?keyword=" + quote(jp_query),
        },
        {
            "label": "Yahoo!オークション",
            "kind": "auction_reference",
            "url": "https://auctions.yahoo.co.jp/search/search?p=" + quote(jp_query),
        },
        {
            "label": "magi",
            "kind": "tcg_marketplace",
            "url": "https://magi.camp/search?keyword=" + quote(jp_query),
        },
        {
            "label": "SNKRDUNK",
            "kind": "tcg_marketplace",
            "url": "https://snkrdunk.com/search/result?keyword=" + quote(jp_query),
        },
        {
            "label": "CardRush",
            "kind": "shop_ask",
            "url": "https://www.cardrush-pokemon.jp/product-list?keyword=" + quote(jp_query),
        },
        {
            "label": "PriceCharting",
            "kind": "global_price_reference",
            "url": "https://www.pricecharting.com/search-products?q=" + quote(en_query) + "&type=prices",
        },
    ]


def collect_japanese_reference_sources(card: dict, live: bool = True) -> list:
    if not is_japanese_card(card) or (card.get("game") or "").lower() != "pokemon":
        return []

    rows = []
    pricecharting = base_result(card, "pricecharting_jp", "skipped", "not collected")
    if not live:
        pricecharting["message"] = "live collection disabled"
    else:
        try:
            payload = fetch_pricecharting_jp(card)
            pricecharting["raw_ref"] = {"payload": payload}
            if payload.get("auth_required"):
                pricecharting["status"] = "auth_required"
                pricecharting["message"] = "missing PriceCharting token"
            elif payload.get("error"):
                pricecharting["status"] = "error"
                pricecharting["message"] = str(payload.get("error"))[:200]
            elif payload.get("selected_usd"):
                amount_cny = round(payload["selected_usd"] * USD2CNY, 2)
                pricecharting["status"] = "ok"
                pricecharting["message"] = "PriceCharting current price"
                pricecharting["price_current"] = {
                    "amount": amount_cny,
                    "currency": "CNY",
                    "kind": payload.get("selected_kind") or "pricecharting_current",
                }
                pricecharting["confidence"] = payload.get("confidence") or "low"
            else:
                pricecharting["status"] = "no_price"
                pricecharting["message"] = "PriceCharting returned no usable Japanese-card price"
        except Exception as exc:
            pricecharting["status"] = "error"
            pricecharting["message"] = str(exc)[:200]
    rows.append(pricecharting)

    refs = base_result(card, "jp_market_reference", "reference_only", "manual Japanese market verification links")
    refs["confidence"] = "reference"
    refs["raw_ref"] = {
        "query": card_query(card, prefer_japanese=True),
        "links": build_japanese_market_links(card),
        "note": "These sources are intentionally exposed as verification links until a stable public API/import path is available.",
    }
    rows.append(refs)
    return rows


def pick_generic_card_match(items: list, card: dict) -> dict:
    number = normalize_card_number(card.get("card_number") or "")
    if number:
        for item in items:
            item_number = normalize_card_number(
                item.get("number") or item.get("card_number") or item.get("cardNumber") or ""
            )
            if serial_matches(number, item_number):
                return item
    return items[0] if items else {}


def collect_third_party_fallbacks(card: dict, live: bool = True) -> list:
    if not live or (card.get("game") or "").lower() != "pokemon":
        return []

    if is_japanese_card(card):
        collectors = [
            ("cardhobby", fetch_fallback_cardhobby),
        ]
    else:
        collectors = [
            ("cardhobby", fetch_fallback_cardhobby),
            ("tcgpricelookup", fetch_fallback_tcgpricelookup),
            ("pokeprice", fetch_fallback_pokeprice),
            ("serpapi_ebay", fetch_fallback_serpapi_ebay),
        ]
    rows = []
    for source, fn in collectors:
        try:
            payload = fn(card)
        except Exception as exc:
            err = base_result(card, source, "error", str(exc)[:200])
            rows.append(err)
            continue
        if payload:
            rows.append(third_party_result(card, source, payload))
        else:
            miss = base_result(card, source, "no_price", "fallback source returned no data")
            rows.append(miss)
    return rows


def scan_jihuanshe_captures(card: dict) -> list:
    if not CAPTURED_DIR.exists():
        return []
    needles = [
        str(card.get("name_cn") or ""),
        str(card.get("name_jp") or ""),
        str(card.get("name_en") or ""),
        quote(str(card.get("name_cn") or "")),
        quote(str(card.get("name_jp") or "")),
        quote(str(card.get("name_en") or "")),
    ]
    needles = [n for n in needles if n]
    samples = []
    for path in CAPTURED_DIR.glob("*jihuanshe*.json"):
        name = path.name
        if needles and not any(n in name for n in needles):
            continue
        try:
            data = load_json(path, {})
            body = ((data.get("response") or {}).get("body") or {})
            body_keys = list(body.keys()) if isinstance(body, dict) else []
            samples.append({
                "file": path.name,
                "status_code": (data.get("response") or {}).get("status_code"),
                "url": (data.get("request") or {}).get("url", "")[:240],
                "body_keys": body_keys[:12],
                "has_raw_data": "raw_data" in body_keys,
            })
        except Exception:
            continue
        if len(samples) >= 5:
            break
    return samples


def classify_jihuanshe_capture(path: Path, data: dict, body: dict) -> dict:
    raw = body.get("raw_data") if isinstance(body, dict) else None
    url = (data.get("request") or {}).get("url", "")
    endpoint = url.split("?")[0].replace("https://api.jihuanshe.com", "")
    item = {
        "file": path.name,
        "status_code": (data.get("response") or {}).get("status_code"),
        "url": url[:240],
        "endpoint": endpoint,
        "body_keys": list(body.keys())[:12] if isinstance(body, dict) else [],
        "has_raw_data": bool(raw),
    }
    if raw:
        item["raw_length"] = len(raw)
        item["raw_prefix"] = raw[:24]
        item["raw_sha256"] = hashlib.sha256(raw.encode("utf-8", errors="ignore")).hexdigest()[:16]
        if "price-history" in endpoint:
            item["kind"] = "price_history_candidate"
        elif "products" in endpoint or "grading-products" in endpoint:
            item["kind"] = "listing_candidate"
        elif "get-base-info" in endpoint or "card-versions/" in endpoint:
            item["kind"] = "card_detail_candidate"
        else:
            item["kind"] = "raw_data_candidate"
    return item


def audit_jihuanshe_raw_data_samples(limit: int = 12) -> list:
    if not CAPTURED_DIR.exists():
        return []
    patterns = [
        "*price-history_raw_data*",
        "*products_raw_data*",
        "*grading-products*",
        "*get-base-info_raw_data*",
        "*card-versions_*raw_data*",
    ]
    seen = set()
    rows = []
    for pattern in patterns:
        for path in CAPTURED_DIR.glob(pattern):
            if path.name in seen:
                continue
            seen.add(path.name)
            try:
                data = load_json(path, {})
                body = ((data.get("response") or {}).get("body") or {})
                rows.append(classify_jihuanshe_capture(path, data, body))
            except Exception:
                continue
            if len(rows) >= limit:
                return rows
    return rows


def collect_jihuanshe_audit(card: dict, client: JiHuanSheAPI, live: bool = True) -> dict:
    result = base_result(card, "jihuanshe", "skipped", "not collected")
    if (card.get("game") or "").lower() != "pokemon":
        result["message"] = "non-pokemon card skipped in v1"
        return result
    if (card.get("language") or "").lower() not in ("jp", "ja", "jpn", "日版"):
        result["message"] = "jihuanshe v1 is reserved for Japanese cards"
        return result

    samples = scan_jihuanshe_captures(card)
    result["raw_ref"] = {"captured_samples": samples}
    if any(s.get("has_raw_data") for s in samples):
        result["status"] = "raw_data_unresolved"
        result["message"] = "captured endpoint returns raw_data; decoder not yet proven"
        return result

    if not live:
        result["message"] = "live collection disabled"
        return result
    if not client.token:
        result["status"] = "auth_required"
        result["message"] = "missing jihuanshe token"
        return result

    query = card_query(card, prefer_japanese=True)
    resp = client.search_card_versions(query)
    code = resp.get("code")
    error_key = str(resp.get("error") or resp.get("code_name") or "")
    message = resp.get("msg") or resp.get("message") or ""
    if code in (401, 403):
        result["status"] = "auth_required"
        result["message"] = "jihuanshe auth required"
    elif error_key == "MARKET_PERMISSION_DENY" or "无权限" in str(message):
        result["status"] = "permission_denied"
        result["message"] = message or "jihuanshe market permission denied"
    elif "raw_data" in resp:
        result["status"] = "raw_data_unresolved"
        result["message"] = "live endpoint returned raw_data; decoder not yet proven"
    elif code and code != 200:
        result["status"] = "error"
        result["message"] = message or "jihuanshe request failed"
    else:
        result["status"] = "no_price"
        result["message"] = "jihuanshe endpoint reachable but no normalized price mapping exists"
    result["raw_ref"]["query"] = query
    result["raw_ref"]["endpoint"] = "card-versions/search"
    return result


def update_card_from_biaoka(card: dict, result: dict) -> None:
    if result.get("status") != "ok":
        return
    prices = card.setdefault("current_prices", {})
    sources = prices.setdefault("sources_detail", {})
    raw = result.get("raw_ref") or {}
    current = result.get("price_current") or {}
    latest = (result.get("sold_records") or [{}])[0]

    sources["collector_biaoka"] = result
    if raw.get("psa10_price_cny"):
        prices["biaoka_psa10_cny"] = raw["psa10_price_cny"]
    if current.get("kind") == "latest_sold":
        prices["biaoka_latest_sold_cny"] = current.get("amount")
        prices["biaoka_latest_sold_grade"] = latest.get("grade") or raw.get("grade_key", "")
        prices["biaoka_latest_sold_source"] = latest.get("market", "")
    prices["collector_sources"] = merge_collector_source(prices.get("collector_sources"), result)
    prices["last_updated"] = now_iso()


def update_card_from_fallback(card: dict, result: dict) -> None:
    if result.get("status") != "ok":
        return
    prices = card.setdefault("current_prices", {})
    sources = prices.setdefault("sources_detail", {})
    payload = (result.get("raw_ref") or {}).get("payload") or {}
    source = result.get("source")
    current = result.get("price_current") or {}

    sources[f"collector_{source}"] = result
    if source == "cardhobby" and current.get("amount"):
        prices["cardhobby_cny"] = current["amount"]
    elif source == "tcgpricelookup":
        if payload.get("raw_near_mint_market_usd"):
            prices["tcgapi_usd"] = payload["raw_near_mint_market_usd"]
            prices["raw_usd"] = payload["raw_near_mint_market_usd"]
    elif source == "pokeprice":
        if payload.get("raw_market_usd"):
            prices["pokemonpricetracker_usd"] = payload["raw_market_usd"]
            prices.setdefault("raw_usd", payload["raw_market_usd"])
    elif source == "serpapi_ebay":
        if payload.get("price_usd"):
            prices["ebay_usd"] = payload["price_usd"]
    elif source == "pricecharting_jp":
        if payload.get("selected_usd"):
            prices["pricecharting_jp_usd"] = payload["selected_usd"]
            prices["pricecharting_jp_cny"] = round(payload["selected_usd"] * USD2CNY, 2)


def merge_collector_source(existing, result: dict) -> list:
    source = result.get("source")
    rows = [r for r in (existing or []) if r.get("source") != source]
    old = next((r for r in (existing or []) if r.get("source") == source), None)

    def has_usable_price(row) -> bool:
        current = (row or {}).get("price_current") or {}
        return bool(current.get("amount")) or (row or {}).get("status") == "ok"

    # Keep a previous usable source when the new run only produced a transient
    # no_price/error/auth result.
    if old and has_usable_price(old) and not has_usable_price(result):
        rows.append(old)
    else:
        rows.append(result)
    return rows


def update_card_from_result(card: dict, result: dict) -> None:
    prices = card.setdefault("current_prices", {})
    prices["collector_sources"] = merge_collector_source(prices.get("collector_sources"), result)
    if result.get("status") != "ok":
        return
    if result.get("source") == "biaoka":
        update_card_from_biaoka(card, result)
    else:
        update_card_from_fallback(card, result)
        prices["last_updated"] = now_iso()


def build_price_history_entry(card: dict, results: list) -> dict:
    prices = card.get("current_prices") or {}
    current_cny = None
    for result in results:
        current = result.get("price_current") or {}
        if current.get("currency") == "CNY" and current.get("amount"):
            current_cny = current["amount"]
            break
    if current_cny is None:
        if prices.get("biaoka_latest_sold_cny"):
            current_cny = prices.get("biaoka_latest_sold_cny")
        elif prices.get("biaoka_psa10_cny"):
            current_cny = prices.get("biaoka_psa10_cny")
        elif prices.get("cardhobby_cny"):
            current_cny = prices.get("cardhobby_cny")
        elif prices.get("pricecharting_jp_cny"):
            current_cny = prices.get("pricecharting_jp_cny")
        elif prices.get("tcgapi_usd"):
            current_cny = round(prices["tcgapi_usd"] * USD2CNY, 2)

    return {
        "cny": current_cny,
        "sources": {
            "biaoka_latest_sold_cny": prices.get("biaoka_latest_sold_cny"),
            "biaoka_psa10_cny": prices.get("biaoka_psa10_cny"),
            "cardhobby_cny": prices.get("cardhobby_cny"),
            "pricecharting_jp_cny": prices.get("pricecharting_jp_cny"),
            "pricecharting_jp_usd": prices.get("pricecharting_jp_usd"),
            "tcgapi_usd": prices.get("tcgapi_usd"),
            "pokemonpricetracker_usd": prices.get("pokemonpricetracker_usd"),
        },
        "collector_sources": results,
        "source_statuses": {r.get("source"): r.get("status") for r in results},
    }


def select_cards(cards: list, args) -> list:
    selected = []
    wanted_ids = set(args.card_id or [])
    for card in cards:
        if wanted_ids and card.get("id") not in wanted_ids:
            continue
        if args.pokemon_only and (card.get("game") or "").lower() != "pokemon":
            continue
        selected.append(card)
        if args.limit and len(selected) >= args.limit:
            break
    return selected


def summarize_run(results_by_card: dict) -> dict:
    total = sum(len(v) for v in results_by_card.values())
    by_status = {}
    by_source = {}
    for rows in results_by_card.values():
        for row in rows:
            by_status[row["status"]] = by_status.get(row["status"], 0) + 1
            by_source[row["source"]] = by_source.get(row["source"], 0) + 1
    return {"total_results": total, "by_status": by_status, "by_source": by_source}


def main() -> int:
    parser = argparse.ArgumentParser(description="Collect Pokemon price data from BiaoKa and JiHuanShe audit sources.")
    parser.add_argument("--limit", type=int, default=0, help="Limit cards processed.")
    parser.add_argument("--card-id", action="append", help="Process a specific card id. Repeatable.")
    parser.add_argument("--dry-run", action="store_true", help="Do not write portfolio or history files.")
    parser.add_argument("--no-live", action="store_true", help="Skip live API calls; only produce local/audit statuses.")
    parser.add_argument("--no-fallback", action="store_true", help="Do not call third-party fallback sources.")
    parser.add_argument("--pokemon-only", action="store_true", default=True, help="Only process Pokemon cards.")
    args = parser.parse_args()

    portfolio = load_json(PORTFOLIO_PATH, {"cards": []})
    cards = select_cards(portfolio.get("cards", []), args)
    if not cards:
        print("No cards selected.")
        return 1

    live = not args.no_live
    biaoka = BiuCardAPI()
    jihuanshe = JiHuanSheAPI()
    results_by_card = {}

    for card in cards:
        card_id = card.get("id")
        print(f"Collecting {card_id}: {card_query(card)}")
        rows = [
            collect_biaoka(card, biaoka, live=live),
            collect_jihuanshe_audit(card, jihuanshe, live=live),
        ]
        if is_japanese_card(card):
            rows.extend(collect_japanese_reference_sources(card, live=live))
        has_primary_price = any(
            r.get("status") == "ok" and (r.get("price_current") or {}).get("amount")
            for r in rows
            if r.get("source") in ("biaoka", "jihuanshe", "pricecharting_jp")
        )
        if not has_primary_price and not args.no_fallback:
            rows.extend(collect_third_party_fallbacks(card, live=live))
        results_by_card[card_id] = rows
        if not args.dry_run:
            for row in rows:
                update_card_from_result(card, row)

    run = {
        "generated_at": now_iso(),
        "scope": {
            "pokemon_only": args.pokemon_only,
            "live": live,
            "dry_run": args.dry_run,
            "limit": args.limit,
            "card_id": args.card_id or [],
        },
        "summary": summarize_run(results_by_card),
        "jihuanshe_raw_data_audit": audit_jihuanshe_raw_data_samples(),
        "results": results_by_card,
    }

    if args.dry_run:
        print(json.dumps(run["summary"], ensure_ascii=False, indent=2))
        return 0

    backup = backup_portfolio()
    portfolio["last_updated"] = now_iso()
    write_json(PORTFOLIO_PATH, portfolio)

    COLLECTOR_RUN_DIR.mkdir(parents=True, exist_ok=True)
    run_path = COLLECTOR_RUN_DIR / f"{datetime.now().strftime('%Y%m%dT%H%M%S')}.json"
    write_json(run_path, run)

    history_path = PRICE_HISTORY_DIR / f"{today_key()}.json"
    history = load_json(history_path, {"date": today_key(), "timestamp": now_iso(), "prices": {}})
    history["timestamp"] = now_iso()
    for card in cards:
        history["prices"][card.get("id")] = build_price_history_entry(card, results_by_card.get(card.get("id"), []))
    write_json(history_path, history)

    print(f"Portfolio backup: {backup}")
    print(f"Collector run: {run_path}")
    print(f"Price history: {history_path}")
    print(json.dumps(run["summary"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
