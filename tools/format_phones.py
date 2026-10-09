#!/usr/bin/env python3
"""Rewrite every US phone number in the parish JSON files as (XXX) XXX-XXXX.

Fixes church_phone, rector_phone, any other key with "phone" in its name, and other_contact
("phone | email"). Separators of any kind (dots, dashes, spaces, slashes, no separator at all), a
leading +1 / 1, stray invisible characters and extensions ("ext. 18") are all handled. Text around a
number ("Call ... or Text ...") is kept. A value with no readable phone number is left alone and listed
at the end so it can be fixed by hand.

The site applies the same rule when it shows a number (formatPhone in js/app.js), so a badly formatted
number never reaches the screen; this script fixes the data itself.

    python3 tools/format_phones.py              # fix data/parishes.json and data/wip-dioceses/**.json
    python3 tools/format_phones.py --check      # only report what would change
    python3 tools/format_phones.py FILE ...     # fix just these files
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Zero-width spaces, direction marks, byte-order marks: invisible, but they break matching and tel: links.
_INVISIBLE = re.compile(r"[​-‏‪-‮⁠﻿]")
_SEP = r"[\s.\-=/]*"
PHONE_RE = re.compile(
    r"(?<![\d\w])(?:\+?1" + _SEP + r")?\(?(\d{3})\)?" + _SEP + r"(\d{3})" + _SEP + r"(\d{4})(?!\d)"
    r"(?:\s*,?\s*(?:ext\.?|extension|x)\s*(\d{1,6}))?\.?",
    re.I,
)


def format_phones(value: str) -> str:
    """Every phone number in `value` as (XXX) XXX-XXXX; everything else kept, with tidy spacing."""
    if not isinstance(value, str) or not value or value.startswith("N/A"):
        return value
    text = _INVISIBLE.sub("", value).replace("\xa0", " ")

    def one(m: re.Match) -> str:
        out = f"({m.group(1)}) {m.group(2)}-{m.group(3)}"
        return out + (f" ext. {m.group(4)}" if m.group(4) else "")

    text = PHONE_RE.sub(one, text)
    # Two numbers with only spaces or a slash between them: "(607) 267-4523 / (607) 746-2826"
    text = re.sub(r"(\d{4}(?: ext\. \d+)?)\s*/?\s*(?=\(\d{3}\) \d{3}-\d{4})", r"\1 / ", text)
    return re.sub(r"\s+", " ", text).strip()


def has_phone(value: str) -> bool:
    return bool(PHONE_RE.search(_INVISIBLE.sub("", value or "")))


def phone_keys(record: dict) -> list[str]:
    return [k for k in record if "phone" in k.lower() or k == "other_contact"]


def default_files() -> list[Path]:
    return [ROOT / "data" / "parishes.json"] + sorted((ROOT / "data" / "wip-dioceses").rglob("*.json"))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("files", nargs="*", type=Path)
    ap.add_argument("--check", action="store_true", help="report only; change nothing")
    args = ap.parse_args()

    total, unreadable = 0, []
    for path in args.files or default_files():
        records = json.loads(path.read_text(encoding="utf-8-sig"))
        changed = 0
        for r in records:
            for k in phone_keys(r):
                v = r[k]
                if not isinstance(v, str) or not v or v.startswith("N/A"):
                    continue
                if "phone" in k.lower() and not has_phone(v):
                    unreadable.append(f"{path.relative_to(ROOT)}  {r.get('id', r.get('name', '?'))}  {k}: {v!r}")
                    continue
                new = format_phones(v)
                if new != v:
                    r[k] = new
                    changed += 1
        total += changed
        print(f"{'would fix' if args.check else 'fixed'} {changed:4d} value(s) in {path.relative_to(ROOT)}")
        if changed and not args.check:
            path.write_text(json.dumps(records, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"\n{total} value(s) {'would change' if args.check else 'changed'}.")
    if unreadable:
        print(f"\n{len(unreadable)} phone value(s) with no readable number (left as they are, fix by hand):")
        print("\n".join("  " + u for u in unreadable))
    return 0


if __name__ == "__main__":
    sys.exit(main())
