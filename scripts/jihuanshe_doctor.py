#!/usr/bin/env python3
"""Diagnose the local JiHuanShe collection setup."""

import argparse
import json
import shutil
import socket
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
DATA_DIR = PROJECT_ROOT / "data"
CAPTURED_DIR = SCRIPT_DIR / "captured_requests"
BINARY_BODY_DIR = CAPTURED_DIR / "_bodies"
PIPELINE_DIR = DATA_DIR / "collector_runs" / "jihuanshe_pipeline"

sys.path.insert(0, str(SCRIPT_DIR))
from analyze_jihuanshe_captures import input_files as capture_input_files  # noqa: E402
from collector_config import get_platform_token  # noqa: E402
from run_jihuanshe_pipeline import analyze_fixtures, normalize_fixtures  # noqa: E402


def local_ips() -> list[str]:
    ips = set()
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            ips.add(sock.getsockname()[0])
    except Exception:
        pass
    try:
        for item in socket.gethostbyname_ex(socket.gethostname())[2]:
            if item and not item.startswith("127."):
                ips.add(item)
    except Exception:
        pass
    return sorted(ips)


def command_paths(names: list[str]) -> dict:
    return {name: shutil.which(name) or "" for name in names}


def latest_dirs(path: Path, limit: int = 3) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    for item in sorted(path.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)[:limit]:
        rows.append({
            "path": str(item),
            "is_dir": item.is_dir(),
        })
    return rows


def capture_status() -> dict:
    files = capture_input_files([str(CAPTURED_DIR)]) if CAPTURED_DIR.exists() else []
    binary_bodies = sorted(BINARY_BODY_DIR.iterdir()) if BINARY_BODY_DIR.exists() else []
    analysis = analyze_fixtures(files) if files else None
    normalized = normalize_fixtures(files) if files else None
    return {
        "dir": str(CAPTURED_DIR),
        "exists": CAPTURED_DIR.exists(),
        "file_count": len(files),
        "binary_body_count": len(binary_bodies),
        "recent_binary_bodies": [str(path) for path in binary_bodies[-5:]],
        "recent_files": [str(path) for path in files[-5:]],
        "analysis": {
            "price_candidate_count": analysis["price_candidate_count"],
            "trend_candidate_count": analysis["trend_candidate_count"],
            "raw_data_files": len(analysis["raw_data_files"]),
        } if analysis else None,
        "normalized": {
            "summary": normalized["summary"],
            "ok_with_current_price": sum(1 for row in normalized["collector_sources"] if row.get("price_current")),
            "ok_with_history": sum(1 for row in normalized["collector_sources"] if row.get("price_history")),
        } if normalized else None,
    }


def recommended_next(checks: dict) -> list[str]:
    steps = []
    if not checks["token"]["present"]:
        steps.append("Add your own JiHuanShe token to data/api_tokens.json or JIHUANSHE_TOKEN.")
    if not checks["mitmproxy"]["mitmdump"] and not checks["mitmproxy"]["mitmproxy"]:
        steps.append("Install mitmproxy so scripts/start_jihuanshe_mitm.ps1 can capture App traffic.")
    if not checks["network"]["local_ips"]:
        steps.append("Connect this PC to the same LAN/Wi-Fi as the phone, then rerun the doctor.")
    if checks["captures"]["file_count"] == 0:
        steps.append("Start scripts/start_jihuanshe_mitm.ps1, set the phone proxy to this PC, then open the JiHuanShe price page.")
    elif checks["captures"]["normalized"] and checks["captures"]["normalized"]["ok_with_current_price"] == 0:
        if checks["captures"]["analysis"]["raw_data_files"]:
            steps.append("Captured responses contain raw_data only; keep raw_data and work on decoder samples.")
        else:
            steps.append("Captured responses did not expose obvious price fields; capture card detail/products/price-history pages.")
    else:
        steps.append("Run scripts/run_jihuanshe_pipeline.py scripts/captured_requests --card-id <portfolio-card-id> to create a preview.")
    return steps


def main() -> int:
    parser = argparse.ArgumentParser(description="Diagnose JiHuanShe collection readiness.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON only.")
    args = parser.parse_args()

    checks = {
        "token": {
            "present": bool(get_platform_token("jihuanshe")),
            "value": "<redacted>" if get_platform_token("jihuanshe") else "",
        },
        "mitmproxy": command_paths(["mitmdump", "mitmproxy", "mitmweb"]),
        "network": {
            "local_ips": local_ips(),
            "proxy_port": 8080,
        },
        "captures": capture_status(),
        "pipeline_runs": latest_dirs(PIPELINE_DIR),
        "paths": {
            "project_root": str(PROJECT_ROOT),
            "apk_extract": str(PROJECT_ROOT / "apk_extract"),
            "captured_requests": str(CAPTURED_DIR),
        },
    }
    checks["recommended_next"] = recommended_next(checks)

    if args.json:
        print(json.dumps(checks, ensure_ascii=False, indent=2))
        return 0

    print("JiHuanShe Doctor")
    print("================")
    print(f"Token configured: {'yes' if checks['token']['present'] else 'no'}")
    print("mitmproxy:")
    for name, path in checks["mitmproxy"].items():
        print(f"  {name}: {path or 'not found'}")
    print("Local proxy addresses:")
    if checks["network"]["local_ips"]:
        for ip in checks["network"]["local_ips"]:
            print(f"  {ip}:{checks['network']['proxy_port']}")
    else:
        print("  none detected")
    print(f"Captured fixtures: {checks['captures']['file_count']} in {checks['captures']['dir']}")
    print(f"  binary bodies: {checks['captures']['binary_body_count']}")
    if checks["captures"]["analysis"]:
        analysis = checks["captures"]["analysis"]
        normalized = checks["captures"]["normalized"]
        print(f"  price candidates: {analysis['price_candidate_count']}")
        print(f"  trend candidates: {analysis['trend_candidate_count']}")
        print(f"  raw_data files: {analysis['raw_data_files']}")
        print(f"  normalized current prices: {normalized['ok_with_current_price']}")
        print(f"  normalized histories: {normalized['ok_with_history']}")
    print("Next:")
    for idx, step in enumerate(checks["recommended_next"], start=1):
        print(f"  {idx}. {step}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
