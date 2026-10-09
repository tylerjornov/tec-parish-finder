#!/usr/bin/env python3
"""Merge parishes from other JSON files into data/parishes.json and keep it in a fixed order.

Order of data/parishes.json: state, then diocese, then city, then name, then id (all case-insensitive,
accents ignored). The id is the final tie-break, so the order never depends on input order.

Each incoming record is checked against data/schema.json (keys, key order, types). Missing keys are
filled with "" (or null for asa); unknown keys are an error. Records with no lat/lon (not yet geocoded)
are imported, but counted in the summary because they cannot appear on the map. A record whose id is already in
parishes.json is a duplicate, handled by --on-duplicate:

    fill     (default) copy incoming values only into fields that are blank in the existing record;
             if nothing is blank-and-available, the record counts as skipped
    skip     keep the existing record untouched
    replace  overwrite the existing record with the incoming one

    python3 tools/import_parishes.py data/wip-dioceses/florida/*.json --dry-run
    python3 tools/import_parishes.py data/wip-dioceses/florida/diocese-of-florida.json
    python3 tools/import_parishes.py --sort-only          # just re-sort parishes.json
"""

from __future__ import annotations

import argparse
import json
import sys
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TARGET = ROOT / "data" / "parishes.json"
SCHEMA = ROOT / "data" / "schema.json"


def fold(s: object) -> str:
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode()
    return s.casefold().strip()


def sort_key(r: dict) -> tuple:
    return tuple(fold(r.get(k)) for k in ("state", "diocese", "city", "name", "id"))


def load_list(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list) or not all(isinstance(r, dict) for r in data):
        sys.exit(f"{path}: expected a JSON array of parish objects")
    return data


def normalize(rec: dict, keys: list[str], src: str) -> tuple[dict | None, list[str]]:
    """Return the record in schema key order, plus any problems found."""
    problems = []
    extra = [k for k in rec if k not in keys]
    if extra:
        problems.append(f"unknown field(s): {', '.join(extra)}")
    for k in ("id", "name", "state", "city"):
        if not str(rec.get(k) or "").strip():
            problems.append(f"missing {k}")
    for k in ("lat", "lon"):
        v = rec.get(k)
        if v is not None and (not isinstance(v, (int, float)) or isinstance(v, bool)):
            problems.append(f"{k} must be a number or null")
    if rec.get("asa") is not None and not isinstance(rec.get("asa"), (int, float)):
        problems.append("asa must be a number or null")
    if problems:
        return None, [f"{src} [{rec.get('id', '?')}]: {p}" for p in problems]
    return {k: rec.get(k, None if k == "asa" else "") for k in keys}, []


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("sources", nargs="*", type=Path, help="JSON files (arrays of parishes) to import")
    ap.add_argument("--target", type=Path, default=TARGET)
    ap.add_argument("--on-duplicate", choices=["fill", "skip", "replace"], default="fill")
    ap.add_argument("--sort-only", action="store_true", help="re-sort the target without importing")
    ap.add_argument("--dry-run", action="store_true", help="report what would happen; write nothing")
    args = ap.parse_args()
    if not args.sources and not args.sort_only:
        ap.error("give at least one source file, or --sort-only")

    keys = list(json.loads(SCHEMA.read_text(encoding="utf-8"))["fields"])
    raw = args.target.read_text(encoding="utf-8")
    records = json.loads(raw)
    by_id = {r["id"]: r for r in records}
    if len(by_id) != len(records):
        sys.exit("target already contains duplicate ids; fix those first")

    added = skipped = filled = replaced = ungeocoded = 0
    errors: list[str] = []
    for path in args.sources:
        for rec in load_list(path):
            new, probs = normalize(rec, keys, path.name)
            if probs:
                errors += probs
                continue
            cur = by_id.get(new["id"])
            if new["lat"] is None or new["lon"] is None:
                ungeocoded += 1
            if cur is None:
                by_id[new["id"]] = new
                added += 1
            elif args.on_duplicate == "skip":
                skipped += 1
            elif args.on_duplicate == "replace":
                if cur != new:
                    cur.update(new)
                    replaced += 1
                else:
                    skipped += 1
            else:
                changed = [k for k in keys if cur.get(k) in ("", None) and new[k] not in ("", None)]
                for k in changed:
                    cur[k] = new[k]
                filled += bool(changed)
                skipped += not changed

    for e in errors:
        print("ERROR", e, file=sys.stderr)
    if errors:
        sys.exit(f"{len(errors)} problem(s); nothing written")

    out = sorted(by_id.values(), key=sort_key)
    text = json.dumps(out, indent=2, ensure_ascii=False) + ("\n" if raw.endswith("\n") else "")
    print(f"added {added}, filled {filled}, replaced {replaced}, skipped {skipped}; total {len(out)}")
    if ungeocoded:
        print(f"note: {ungeocoded} incoming record(s) have no lat/lon and will not show on the map")
    if text == raw:
        print("target already up to date")
    elif args.dry_run:
        print("dry run: nothing written")
    else:
        args.target.write_text(text, encoding="utf-8")
        print(f"wrote {args.target}")


if __name__ == "__main__":
    main()
