#!/usr/bin/env python3
"""Import authorized JiHuanShe captures into sanitized local fixtures.

Accepted inputs:
- HAR files exported by browser/Charles/Proxyman/HTTP Toolkit.
- JSON files shaped like mitmproxy exports with request/response objects.

The output goes to scripts/captured_requests/, which is ignored by Git.
Sensitive request headers are always redacted. Response JSON is recursively
redacted for common personal fields. raw_data is kept only with
--keep-raw-data because it may contain opaque account-specific data.
"""

import argparse
import base64
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

SCRIPT_DIR = Path(__file__).resolve().parent
CAPTURED_DIR = SCRIPT_DIR / "captured_requests"

SENSITIVE_HEADER_KEYS = {
    "authorization",
    "cookie",
    "set-cookie",
    "x-csrf-token",
    "x-xsrf-token",
    "token",
}

SENSITIVE_JSON_KEYS = {
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
    "id_card",
    "idcard",
    "real_name",
    "realname",
    "address",
}


def now_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def slugify(value: str) -> str:
    value = re.sub(r"^https?://", "", value)
    value = re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("_")
    return value[:120] or "capture"


def read_json(path: Path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def header_list_to_dict(headers) -> dict:
    if isinstance(headers, dict):
        items = headers.items()
    else:
        items = []
        for row in headers or []:
            if isinstance(row, dict):
                items.append((row.get("name") or row.get("key") or "", row.get("value") or ""))
    out = {}
    for key, value in items:
        if not key:
            continue
        if str(key).lower() in SENSITIVE_HEADER_KEYS:
            out[str(key)] = "<redacted>"
        else:
            out[str(key)] = value
    return out


def decode_har_text(content: dict) -> str:
    text = content.get("text") or ""
    if content.get("encoding") == "base64":
        try:
            return base64.b64decode(text).decode("utf-8", errors="replace")
        except Exception:
            return ""
    return text


def parse_body(text: str):
    if not text:
        return None
    if isinstance(text, (dict, list)):
        return text
    if not isinstance(text, str):
        return text
    try:
        return json.loads(text)
    except Exception:
        return text[:2000]


def sanitize_json(value, keep_raw_data: bool):
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            key_l = str(key).lower()
            if key_l in SENSITIVE_JSON_KEYS:
                out[key] = "<redacted>"
            elif key == "raw_data" and isinstance(item, str) and not keep_raw_data:
                out[key] = {
                    "redacted": True,
                    "length": len(item),
                    "prefix": item[:24],
                    "sha256": hashlib.sha256(item.encode("utf-8", errors="ignore")).hexdigest()[:16],
                }
            else:
                out[key] = sanitize_json(item, keep_raw_data)
        return out
    if isinstance(value, list):
        return [sanitize_json(item, keep_raw_data) for item in value]
    return value


def body_summary(body) -> dict:
    if isinstance(body, dict):
        raw = body.get("raw_data")
        has_raw_data = "raw_data" in body
        summary = {
            "type": "dict",
            "keys": list(body.keys())[:40],
            "has_raw_data": has_raw_data,
        }
        if isinstance(raw, str):
            summary["raw_data"] = {
                "length": len(raw),
                "prefix": raw[:24],
                "sha256": hashlib.sha256(raw.encode("utf-8", errors="ignore")).hexdigest()[:16],
            }
        elif isinstance(raw, dict):
            summary["raw_data"] = raw
        return summary
    if isinstance(body, list):
        return {"type": "list", "length": len(body)}
    if isinstance(body, str):
        return {"type": "text", "length": len(body), "prefix": body[:120]}
    return {"type": type(body).__name__}


def endpoint_kind(url: str, body) -> str:
    path = urlparse(url).path
    if "price-history" in path:
        return "price_history_candidate"
    if "get-base-info" in path or "card-versions" in path:
        return "card_detail_candidate"
    if "products" in path or "sellers" in path or "entrustedProduct" in path:
        return "listing_candidate"
    if isinstance(body, dict) and "raw_data" in body:
        return "raw_data_candidate"
    return "jihuanshe_candidate"


def normalize_entry(entry: dict, source_name: str, keep_raw_data: bool) -> dict | None:
    request = entry.get("request") or {}
    response = entry.get("response") or {}

    url = request.get("url") or ""
    method = request.get("method") or "GET"
    req_headers = request.get("headers") or {}
    req_body = request.get("postData", {}).get("text") if isinstance(request.get("postData"), dict) else request.get("body")

    status = response.get("status") or response.get("status_code")
    resp_headers = response.get("headers") or {}
    resp_body = None
    if isinstance(response.get("content"), dict):
        resp_body = decode_har_text(response["content"])
    elif "body" in response:
        resp_body = response.get("body")

    if "jihuanshe.com" not in url:
        return None

    parsed_body = sanitize_json(parse_body(resp_body), keep_raw_data)
    normalized = {
        "imported_at": datetime.now(timezone.utc).isoformat(),
        "source_file": source_name,
        "request": {
            "method": method,
            "url": url,
            "path": urlparse(url).path,
            "headers": header_list_to_dict(req_headers),
            "body": sanitize_json(parse_body(req_body), keep_raw_data=False),
        },
        "response": {
            "status_code": status,
            "headers": header_list_to_dict(resp_headers),
            "body": parsed_body,
            "body_summary": body_summary(parsed_body),
        },
    }
    normalized["kind"] = endpoint_kind(url, parsed_body)
    return normalized


def iter_entries(payload):
    if isinstance(payload, dict) and isinstance(payload.get("log"), dict):
        for entry in payload["log"].get("entries") or []:
            yield entry
    elif isinstance(payload, dict) and "request" in payload and "response" in payload:
        yield payload
    elif isinstance(payload, list):
        for item in payload:
            if isinstance(item, dict):
                if "request" in item and "response" in item:
                    yield item
                elif isinstance(item.get("log"), dict):
                    yield from iter_entries(item)


def import_file(path: Path, out_dir: Path, keep_raw_data: bool) -> list:
    payload = read_json(path)
    rows = []
    for idx, entry in enumerate(iter_entries(payload), start=1):
        normalized = normalize_entry(entry, path.name, keep_raw_data)
        if not normalized:
            continue
        endpoint = normalized["request"]["path"].replace("/api/market/", "")
        name = f"jihuanshe_{now_stamp()}_{idx:03d}_{slugify(endpoint)}.json"
        out_path = out_dir / name
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(normalized, f, ensure_ascii=False, indent=2)
        rows.append({
            "file": str(out_path),
            "kind": normalized["kind"],
            "url": normalized["request"]["url"],
            "status_code": normalized["response"]["status_code"],
            "body_summary": normalized["response"]["body_summary"],
        })
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description="Import and sanitize authorized JiHuanShe captures.")
    parser.add_argument("files", nargs="+", help="HAR or JSON capture file(s).")
    parser.add_argument("--out-dir", default=str(CAPTURED_DIR))
    parser.add_argument("--keep-raw-data", action="store_true", help="Preserve raw_data strings for decoder work.")
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    imported = []
    for file_name in args.files:
        imported.extend(import_file(Path(file_name), out_dir, keep_raw_data=args.keep_raw_data))

    summary = {}
    for row in imported:
        summary[row["kind"]] = summary.get(row["kind"], 0) + 1

    print(json.dumps({
        "imported_count": len(imported),
        "out_dir": str(out_dir),
        "summary": summary,
        "files": [row["file"] for row in imported],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
