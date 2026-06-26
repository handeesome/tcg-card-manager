"""mitmproxy addon: capture JiHuanShe traffic as sanitized fixtures.

Usage:
  mitmdump -s scripts/jihuanshe_mitm_addon.py --listen-host 0.0.0.0 --listen-port 8080

Environment:
  JHS_CAPTURE_DIR     Optional output directory. Defaults to scripts/captured_requests.
  JHS_KEEP_RAW_DATA   Set to 1 to keep raw_data strings for decoder work.
"""

from __future__ import annotations

import json
import os
import sys
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
DEFAULT_CAPTURE_DIR = SCRIPT_DIR / "captured_requests"

sys.path.insert(0, str(SCRIPT_DIR))
from import_jihuanshe_capture import (  # noqa: E402
    body_summary,
    header_list_to_dict,
    normalize_entry,
    parse_body,
    sanitize_json,
    slugify,
)


def now_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def header_items(headers) -> list[dict]:
    return [{"name": str(k), "value": str(v)} for k, v in headers.items()]


def safe_text(message) -> str:
    if message is None:
        return ""
    try:
        return message.get_text(strict=False)
    except Exception:
        try:
            return message.content.decode("utf-8", errors="replace")
        except Exception:
            return ""


def header_value(headers, name: str) -> str:
    for key, value in headers.items():
        if str(key).lower() == name.lower():
            return str(value)
    return ""


def looks_binary(message) -> bool:
    content = getattr(message, "raw_content", None) or getattr(message, "content", b"") or b""
    content_type = header_value(message.headers, "content-type").lower()
    disposition = header_value(message.headers, "content-disposition").lower()
    if "attachment" in disposition:
        return True
    text_types = (
        "application/json",
        "application/javascript",
        "application/x-javascript",
        "application/xml",
        "text/",
    )
    if any(item in content_type for item in text_types):
        return False
    if not content:
        return False
    sample = content[:2048]
    if b"\x00" in sample:
        return True
    control = sum(1 for byte in sample if byte < 32 and byte not in b"\r\n\t")
    return control / max(len(sample), 1) > 0.08


def binary_extension(response, url: str) -> str:
    disposition = header_value(response.headers, "content-disposition")
    if "filename=" in disposition:
        filename = disposition.split("filename=", 1)[1].strip().strip('"')
        suffix = Path(filename).suffix
        if suffix:
            return suffix[:16]
    content_type = header_value(response.headers, "content-type").lower()
    if "zip" in content_type:
        return ".zip"
    if "zstd" in content_type:
        return ".zst"
    suffix = Path(urlparse(url).path).suffix
    return suffix[:16] if suffix else ".bin"


class JiHuanSheCaptureAddon:
    def __init__(self):
        self.capture_dir = Path(os.environ.get("JHS_CAPTURE_DIR") or DEFAULT_CAPTURE_DIR)
        self.keep_raw_data = os.environ.get("JHS_KEEP_RAW_DATA") == "1"
        self.capture_all_hosts = os.environ.get("JHS_CAPTURE_ALL_HOSTS") == "1"
        self.body_dir = self.capture_dir / "_bodies"
        self.counter = 0

    def normalize_observed_entry(self, entry: dict) -> dict:
        request = entry.get("request") or {}
        response = entry.get("response") or {}
        resp_body = response.get("body")
        parsed_body = None
        if isinstance(resp_body, (dict, list)):
            parsed_body = sanitize_json(resp_body, keep_raw_data=False)
        elif isinstance(resp_body, str):
            stripped = resp_body.lstrip()
            if stripped.startswith("{") or stripped.startswith("["):
                parsed_body = sanitize_json(parse_body(resp_body), keep_raw_data=False)

        return {
            "imported_at": datetime.now(timezone.utc).isoformat(),
            "source_file": "mitmproxy-live",
            "kind": "observed_host_candidate",
            "request": {
                "method": request.get("method") or "GET",
                "url": request.get("url") or "",
                "path": urlparse(request.get("url") or "").path,
                "headers": header_list_to_dict(request.get("headers") or {}),
                "body": sanitize_json(parse_body(request.get("body") or ""), keep_raw_data=False),
            },
            "response": {
                "status_code": response.get("status_code"),
                "headers": header_list_to_dict(response.get("headers") or {}),
                "body": parsed_body,
                "body_summary": body_summary(parsed_body),
            },
        }

    def response(self, flow):
        request = flow.request
        response = flow.response
        if response is None:
            return
        host = request.pretty_host or request.host or ""
        url = request.pretty_url or request.url or ""
        is_jihuanshe = "jihuanshe.com" in host or "jihuanshe.com" in url
        content_type = header_value(response.headers, "content-type").lower()
        path = urlparse(url).path
        is_jsonish = "json" in content_type
        is_probable_api = "/api/" in path or "api" in host
        if not is_jihuanshe and not (self.capture_all_hosts and (is_jsonish or is_probable_api)):
            return

        self.capture_dir.mkdir(parents=True, exist_ok=True)
        self.counter += 1
        endpoint = urlparse(url).path.replace("/api/market/", "")
        prefix = "jihuanshe" if is_jihuanshe else "observed"
        stem = f"{prefix}_{now_stamp()}_{self.counter:04d}_{slugify(endpoint)}"

        response_body = safe_text(response)
        if looks_binary(response):
            content = response.raw_content or response.content or b""
            self.body_dir.mkdir(parents=True, exist_ok=True)
            binary_name = f"{stem}{binary_extension(response, url)}"
            binary_path = self.body_dir / binary_name
            binary_path.write_bytes(content)
            response_body = {
                "binary_saved": True,
                "path": str(binary_path),
                "file": binary_name,
                "length": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
                "content_type": header_value(response.headers, "content-type"),
                "content_disposition": header_value(response.headers, "content-disposition"),
            }

        entry = {
            "request": {
                "method": request.method,
                "url": url,
                "headers": header_items(request.headers),
                "body": safe_text(request),
            },
            "response": {
                "status_code": response.status_code,
                "headers": header_items(response.headers),
                "body": response_body,
            },
        }
        if is_jihuanshe:
            normalized = normalize_entry(entry, "mitmproxy-live", keep_raw_data=self.keep_raw_data)
        else:
            normalized = self.normalize_observed_entry(entry)
        if not normalized:
            return

        name = f"{stem}.json"
        out_path = self.capture_dir / name
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(normalized, f, ensure_ascii=False, indent=2)
        print(f"[jihuanshe] captured {response.status_code} {url} -> {out_path}")


addons = [JiHuanSheCaptureAddon()]
