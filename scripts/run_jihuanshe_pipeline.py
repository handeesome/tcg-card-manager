#!/usr/bin/env python3
"""One-command JiHuanShe capture pipeline.

Inputs can be:
- raw HAR/mitmproxy JSON files, which will be imported and sanitized first;
- an already-sanitized fixture directory such as scripts/captured_requests/.

The pipeline runs analyze -> normalize -> optional portfolio preview/apply.
"""

import argparse
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
DATA_DIR = PROJECT_ROOT / "data"
PIPELINE_DIR = DATA_DIR / "collector_runs" / "jihuanshe_pipeline"
DEFAULT_CAPTURED_DIR = SCRIPT_DIR / "captured_requests"

sys.path.insert(0, str(SCRIPT_DIR))
from analyze_jihuanshe_captures import classify_capture, input_files as analyzed_input_files, load_json  # noqa: E402
from apply_jihuanshe_normalized import apply_to_portfolio, combine_rows, load_json as load_json_default, write_json  # noqa: E402
from import_jihuanshe_capture import import_file  # noqa: E402
from normalize_jihuanshe_captures import normalize_capture  # noqa: E402


def now_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def is_sanitized_fixture(path: Path) -> bool:
    if not path.is_file() or path.suffix.lower() != ".json":
        return False
    try:
        data = load_json(path)
    except Exception:
        return False
    return isinstance(data, dict) and "request" in data and "response" in data and "kind" in data


def collect_fixture_files(inputs: list[str], keep_raw_data: bool, import_dir: Path) -> list[Path]:
    if not inputs:
        return sorted(DEFAULT_CAPTURED_DIR.glob("*.json")) if DEFAULT_CAPTURED_DIR.exists() else []

    fixtures = []
    imports_needed = []
    for value in inputs:
        path = Path(value)
        if path.is_dir():
            json_files = sorted(path.glob("*.json"))
            if json_files and all(is_sanitized_fixture(p) for p in json_files):
                fixtures.extend(json_files)
            else:
                imports_needed.extend(json_files)
        elif is_sanitized_fixture(path):
            fixtures.append(path)
        else:
            imports_needed.append(path)

    if imports_needed:
        import_dir.mkdir(parents=True, exist_ok=True)
        for path in imports_needed:
            import_file(path, import_dir, keep_raw_data=keep_raw_data)
        fixtures.extend(sorted(import_dir.glob("*.json")))

    # Keep order stable and remove duplicates.
    seen = set()
    unique = []
    for path in fixtures:
        key = str(path.resolve())
        if key not in seen:
            seen.add(key)
            unique.append(path)
    return unique


def analyze_fixtures(files: list[Path]) -> dict:
    captures = []
    for path in files:
        try:
            row = classify_capture(load_json(path))
            row["file"] = str(path)
            captures.append(row)
        except Exception as exc:
            captures.append({"file": str(path), "error": str(exc)[:200]})

    by_kind = {}
    raw_data_files = []
    for row in captures:
        kind = row.get("kind") or "unknown"
        by_kind[kind] = by_kind.get(kind, 0) + 1
        if row.get("has_raw_data"):
            raw_data_files.append(row.get("file"))
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_files": len(captures),
        "by_kind": by_kind,
        "raw_data_files": raw_data_files,
        "price_candidate_count": sum(row.get("price_candidate_count", 0) for row in captures),
        "trend_candidate_count": sum(row.get("trend_candidate_count", 0) for row in captures),
        "captures": captures,
    }


def normalize_fixtures(files: list[Path]) -> dict:
    rows = []
    for path in files:
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
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_results": len(rows),
        "summary": summary,
        "collector_sources": rows,
    }


def apply_preview(normalized: dict, card_id: str, portfolio_path: Path, out_path: Path, write: bool) -> dict:
    combined = combine_rows(normalized.get("collector_sources") or [], card_id)
    portfolio = load_json_default(portfolio_path, {})
    updated = apply_to_portfolio(portfolio, card_id, combined)

    backup = None
    if write:
        backup = portfolio_path.with_suffix(portfolio_path.suffix + f".jihuanshe.{datetime.now().strftime('%Y%m%d%H%M%S')}.bak")
        if portfolio_path.exists():
            shutil.copy2(portfolio_path, backup)
        write_json(portfolio_path, updated)
        final_path = portfolio_path
    else:
        write_json(out_path, updated)
        final_path = out_path

    return {
        "card_id": card_id,
        "status": combined.get("status"),
        "price_current": combined.get("price_current"),
        "history_points": len(combined.get("price_history") or []),
        "combined_rows": (combined.get("raw_ref") or {}).get("combined_rows"),
        "portfolio_out": str(final_path),
        "backup": str(backup) if backup else "",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run JiHuanShe capture import/analyze/normalize/apply pipeline.")
    parser.add_argument("inputs", nargs="*", help="HAR/mitmproxy JSON files or sanitized fixture dirs/files. Defaults to scripts/captured_requests.")
    parser.add_argument("--card-id", default="", help="Portfolio card id to preview/apply.")
    parser.add_argument("--portfolio", default=str(DATA_DIR / "portfolio.json"))
    parser.add_argument("--portfolio-out", default="", help="Preview portfolio output. Defaults under data/collector_runs.")
    parser.add_argument("--keep-raw-data", action="store_true")
    parser.add_argument("--write", action="store_true", help="Overwrite --portfolio after backup.")
    args = parser.parse_args()

    run_dir = PIPELINE_DIR / now_stamp()
    import_dir = run_dir / "captured"
    run_dir.mkdir(parents=True, exist_ok=True)

    fixture_files = collect_fixture_files(args.inputs, keep_raw_data=args.keep_raw_data, import_dir=import_dir)
    analysis = analyze_fixtures(fixture_files)
    normalized = normalize_fixtures(fixture_files)

    analysis_path = run_dir / "analysis.json"
    normalized_path = run_dir / "normalized.json"
    write_json(analysis_path, analysis)
    write_json(normalized_path, normalized)

    apply_result = None
    if args.card_id:
        portfolio_out = Path(args.portfolio_out) if args.portfolio_out else run_dir / "portfolio.preview.json"
        apply_result = apply_preview(normalized, args.card_id, Path(args.portfolio), portfolio_out, args.write)

    summary = {
        "run_dir": str(run_dir),
        "fixture_count": len(fixture_files),
        "analysis": {
            "out": str(analysis_path),
            "price_candidate_count": analysis["price_candidate_count"],
            "trend_candidate_count": analysis["trend_candidate_count"],
            "raw_data_files": len(analysis["raw_data_files"]),
        },
        "normalized": {
            "out": str(normalized_path),
            "summary": normalized["summary"],
            "ok_with_current_price": sum(1 for row in normalized["collector_sources"] if row.get("price_current")),
            "ok_with_history": sum(1 for row in normalized["collector_sources"] if row.get("price_history")),
        },
        "apply": apply_result,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
