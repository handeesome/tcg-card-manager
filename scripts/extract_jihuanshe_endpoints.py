#!/usr/bin/env python3
"""Extract JiHuanShe endpoint candidates from local APK extraction files."""

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_APK_EXTRACT = PROJECT_ROOT / "apk_extract"
OUT_DIR = PROJECT_ROOT / "data" / "collector_runs" / "jihuanshe_endpoint_discovery"

ASCII_RE = re.compile(rb"[\x20-\x7e]{4,}")
ENDPOINT_RE = re.compile(
    r"(?:https://api\.jihuanshe\.com)?(?P<path>/(?:api/market|market|sellers|entrustedProduct|card-versions|"
    r"get-base-info|price-history|signIn|customerService)[A-Za-z0-9_./?=&%-]*)"
)
PRICE_RE = re.compile(r"(price|history|product|seller|entrusted|card-version|get-base-info|stock|order)", re.IGNORECASE)


def now_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def strings_from_file(path: Path) -> list[str]:
    data = path.read_bytes()
    return [m.decode("utf-8", errors="replace") for m in ASCII_RE.findall(data)]


def normalize_path(raw: str) -> str:
    raw = raw.strip()
    if raw.startswith("https://"):
        parsed = urlparse(raw)
        path = parsed.path
        if parsed.query:
            path += "?" + parsed.query
    else:
        path = raw
    if path.startswith("/market/"):
        path = "/api" + path
    elif path.startswith("/sellers/") or path.startswith("/entrustedProduct/") or path.startswith("/card-versions"):
        path = "/api/market" + path
    elif path.startswith("/get-base-info") or path.startswith("/price-history"):
        path = "/api/market" + path
    return path


def score_endpoint(path: str) -> int:
    score = 0
    low = path.lower()
    if PRICE_RE.search(path):
        score += 20
    if "price" in low or "history" in low:
        score += 30
    if "product" in low or "seller" in low or "entrusted" in low:
        score += 20
    if "card-version" in low or "get-base-info" in low:
        score += 15
    if any(part in low for part in ("batch", "destroy", "orders", "warehouse")):
        score -= 20
    if any(part in low for part in ("verify", "auth", "signin")):
        score -= 10
    return score


def endpoint_kind(path: str) -> str:
    low = path.lower()
    if "price-history" in low or "history" in low:
        return "price_history"
    if "get-base-info" in low or "card-version" in low:
        return "card_detail"
    if "product" in low or "seller" in low or "entrusted" in low:
        return "listing"
    if "auth" in low or "signin" in low:
        return "auth"
    return "other"


def discover(paths: list[Path]) -> list[dict]:
    found = {}
    for base in paths:
        files = [base] if base.is_file() else [p for p in base.rglob("*") if p.is_file()]
        for path in files:
            if path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg"}:
                continue
            try:
                strings = strings_from_file(path)
            except Exception:
                continue
            for text in strings:
                for match in ENDPOINT_RE.finditer(text):
                    endpoint = normalize_path(match.group("path"))
                    if not endpoint.startswith("/api/market") and not endpoint.startswith("/api/signIn"):
                        continue
                    row = found.setdefault(endpoint, {
                        "endpoint": endpoint,
                        "kind": endpoint_kind(endpoint),
                        "score": score_endpoint(endpoint),
                        "sources": [],
                    })
                    source = str(path.relative_to(PROJECT_ROOT)) if path.is_relative_to(PROJECT_ROOT) else str(path)
                    if source not in row["sources"]:
                        row["sources"].append(source)
    return sorted(found.values(), key=lambda r: (-r["score"], r["endpoint"]))


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract JiHuanShe endpoint candidates from APK files.")
    parser.add_argument("paths", nargs="*", help="Files/directories to scan. Defaults to apk_extract.")
    parser.add_argument("--out", default="")
    parser.add_argument("--top", type=int, default=200)
    args = parser.parse_args()

    paths = [Path(p) for p in args.paths] if args.paths else [DEFAULT_APK_EXTRACT]
    rows = discover(paths)
    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scanned": [str(p) for p in paths],
        "total_endpoints": len(rows),
        "endpoints": rows[: args.top],
    }

    if args.out:
        out_path = Path(args.out)
    else:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        out_path = OUT_DIR / f"{now_stamp()}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(json.dumps({
        "out": str(out_path),
        "total_endpoints": output["total_endpoints"],
        "top": [
            {"endpoint": row["endpoint"], "kind": row["kind"], "score": row["score"]}
            for row in output["endpoints"][:20]
        ],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
