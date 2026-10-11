#!/usr/bin/env python3
"""Build data/parishes.json (the file the website loads) from the per-diocese files in data/states/.

    python3 tools/build_parishes.py            # merge data/states/**/*.json into data/parishes.json
    python3 tools/build_parishes.py --check    # only report whether parishes.json is up to date
    python3 tools/build_parishes.py --split    # one-time: split an existing parishes.json into data/states/

The merged file is sorted by state, diocese, city, name, then id (case-insensitive, accents ignored), so it
does not depend on how the files are split. Edit the files under data/states/, then run this.
"""

from __future__ import annotations

import argparse
import json
import sys

import parish_files as pf


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="report only; change nothing")
    ap.add_argument("--split", action="store_true", help="split parishes.json into data/states/")
    args = ap.parse_args()

    if args.split:
        if pf.state_files():
            sys.exit(f"{pf.STATES_DIR} already has files; refusing to overwrite them")
        records = json.loads(pf.COMBINED.read_text(encoding="utf-8"))
        if args.check:
            print(f"would split {len(records)} parishes")
            return 0
        written = pf.write_states(records)
        print(f"wrote {len(written)} file(s) under {pf.STATES_DIR.relative_to(pf.ROOT)}")
        return 0

    records = pf.load_all()
    ids = [r.get("id") for r in records]
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    if dupes:
        sys.exit(f"duplicate id(s) across files: {', '.join(dupes)}")
    stale = not pf.COMBINED.exists() or pf.COMBINED.read_text(encoding="utf-8") != pf.dump(records)
    if args.check:
        print(f"{pf.COMBINED.relative_to(pf.ROOT)} is {'OUT OF DATE' if stale else 'up to date'} ({len(records)} parishes)")
        return 1 if stale else 0
    pf.write_combined(records)
    print(f"{'wrote' if stale else 'already up to date:'} {pf.COMBINED.relative_to(pf.ROOT)} ({len(records)} parishes from {len(pf.state_files())} files)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
