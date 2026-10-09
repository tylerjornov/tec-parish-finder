#!/usr/bin/env python3
"""One pass that applies every format rule to the parish JSON files.

Phones: every US number in a phone field or other_contact becomes (XXX) XXX-XXXX. Separators of any kind
(dots, dashes, spaces, slashes, none), a leading +1 / 1, stray invisible characters and extensions
("ext. 18") are handled, and text around a number is kept. A phone value with no readable number is left
alone and listed at the end so it can be fixed by hand. The site applies the same rule when it shows a
number (formatPhones in js/app.js); this script fixes the data itself.

Clergy (rector_name, diocesan_bishop, other_clergy) use only these titles:
    Rev.   Mtr.   Fr.   Rt. Rev.   Very Rev.
so "Reverend", "Revd", "Rev" -> Rev.; "Right Reverend", "Rt Rev" -> Rt. Rev.; "Very Reverend" -> Very Rev.;
"Father", "Fr" -> Fr.; "Mother", "Mthr", "Mtr" -> Mtr. "Dr.", "Canon" and the rest of the name are kept. A name
that starts with Rev., Rt. Rev. or Very Rev. always starts with "The" ("Rev. Ann Lee" -> "The Rev. Ann Lee");
Fr. and Mtr. do not get one. "Father" / "Mother" are only changed when a capitalised name follows.
  - rector_name and diocesan_bishop: a role after the name is dropped (", Rector", ", Priest-in-Charge",
    ", Bishop Provisional"); generational suffixes (", Jr.", ", IV") are kept.
  - other_clergy: a "; "-separated list. Each person may keep a role, written in parentheses at the end:
    "The Rev. Ann Lee, Associate Rector" -> "The Rev. Ann Lee (Associate Rector)".
Values such as "N/A", "None - No Rector" and "Sede vacante (...)" are left as they are, apart from the
spelling of the title.

Emails (church_email, rector_email, and any address in other_contact) are lowercased.

URLs (website, livestream_url): a bare domain gets a trailing slash ("https://x.org" -> "https://x.org/").
http:// is not upgraded, because not every site serves https.

Text: curly quotes become straight ones in every field. In the free-text fields, an em dash after a service
time is dropped ("11:00 AM — Holy Eucharist" -> "11:00 AM Holy Eucharist"), and time, day and month ranges
use an en dash ("9:15-11:45 AM" -> "9:15–11:45 AM", "Mon-Fri" -> "Mon–Fri", "Sept - May" -> "Sept–May").

    python3 tools/normalize_data.py              # fix data/parishes.json and data/wip-dioceses/**.json
    python3 tools/normalize_data.py --check      # only report what would change
    python3 tools/normalize_data.py FILE ...     # fix just these files
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

TITLE_FIELDS = ("rector_name", "diocesan_bishop")
CLERGY_LIST_FIELDS = ("other_clergy",)
EMAIL_FIELDS = ("church_email", "rector_email")
URL_FIELDS = ("website", "livestream_url")
PROSE_FIELDS = (
    "sunday_services", "weekday_services", "rite_details", "service_languages", "music_style",
    "accessibility", "parking", "childcare", "formation", "notes",
)

# Order matters: the longer titles go first so "Right Reverend" is not left as "Right Rev.".
_TITLES = [
    (re.compile(r"\b(?:Right|Rt\.?)\s*(?:Reverend|Revd\.?|Rev\b\.?)", re.I), "Rt. Rev."),
    (re.compile(r"\bVery\s+(?:Reverend|Revd\.?|Rev\b\.?)", re.I), "Very Rev."),
    (re.compile(r"\b(?:Reverend|Revd\.?|Rev\b\.?)", re.I), "Rev."),
    (re.compile(r"\b(?:Father|Fr\b\.?)(?=\s+[A-Z])"), "Fr."),
    (re.compile(r"\b(?:Mother|Mthr\b\.?|Mtr\b\.?)(?=\s+[A-Z])"), "Mtr."),
]
_SKIP = ("N/A", "None", "Sede vacante")
_GENERATION = re.compile(r"(?:Jr|Sr)\.?|II|III|IV|V|VI", re.I)
_THE_TITLE = re.compile(r"(?:Rt\. Rev\.|Very Rev\.|Rev\.)")

_QUOTES = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"'})
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_BARE_DOMAIN = re.compile(r"https?://[^/?#\s]+", re.I)
_TIME = r"\d{1,2}(?::\d\d)?\s?[AP]M"
_TIME_RANGE = re.compile(r"(\d{1,2}(?::\d\d)?(?:\s?[AP]M)?)\s*[-—]\s*(?=" + _TIME + r"\b)")
_NAMES = r"(?:Mon|Tues?|Wed|Thu(?:rs?)?|Fri|Sat|Sun|Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sept?|Oct|Nov|Dec)"
_NAME_RANGE = re.compile(r"\b(" + _NAMES + r")\.?\s*[-—]\s*(?=" + _NAMES + r"\b)")
_TIME_EM_DASH = re.compile(r"\b([AP]M)\s+—\s+")


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


def _respell(value: str) -> str:
    for pat, repl in _TITLES:
        value = pat.sub(repl, value)
    return value


def _split_role(person: str) -> tuple[str, str]:
    """"The Rev. Ann Lee, IV, Associate Rector" -> ("The Rev. Ann Lee, IV", "Associate Rector")."""
    person = person.strip()
    m = re.fullmatch(r"(.*?)\s*\(([^()]*)\)", person)
    if m and m.group(1):
        return m.group(1), m.group(2).strip()
    head, *rest = [part.strip() for part in person.split(",")]
    kept = []
    while rest and _GENERATION.fullmatch(rest[0]):
        kept.append(rest.pop(0))
    return ", ".join([head, *kept]), ", ".join(p for p in rest if p)


def _with_the(name: str) -> str:
    name = re.sub(r"^the\s+", "", name, flags=re.I)
    return f"The {name}" if _THE_TITLE.match(name) else name


def format_title(value: str) -> str:
    """One person, no role: "Rev. Ann Lee, Rector" -> "The Rev. Ann Lee"."""
    if not isinstance(value, str) or not value:
        return value
    out = _respell(value)
    if out.startswith(_SKIP):
        return out
    return _with_the(_split_role(out)[0])


def format_clergy_list(value: str) -> str:
    """Several people, each with an optional role in parentheses, separated by "; "."""
    if not isinstance(value, str) or not value or value.startswith(_SKIP):
        return value
    people = []
    for person in filter(None, (p.strip() for p in value.split(";"))):
        name, role = _split_role(_respell(person))
        people.append(_with_the(name) + (f" ({role})" if role else ""))
    return "; ".join(people)


def format_email(value: str) -> str:
    return _EMAIL.sub(lambda m: m.group(0).lower(), value) if isinstance(value, str) else value


def format_url(value: str) -> str:
    if isinstance(value, str) and _BARE_DOMAIN.fullmatch(value.strip()):
        return value.strip() + "/"
    return value


def format_prose(value: str) -> str:
    if not isinstance(value, str) or not value:
        return value
    value = _TIME_EM_DASH.sub(r"\1 ", value)
    value = _TIME_RANGE.sub(r"\1–", value)
    return _NAME_RANGE.sub(r"\1–", value)


def normalize_record(r: dict, path: Path, unreadable: list[str]) -> list[tuple[str, object, object]]:
    """Apply every rule to one record in place; return (field, old, new) for each change."""
    changes = []

    def put(k: str, new: object) -> None:
        if new != r[k]:
            changes.append((k, r[k], new))
            r[k] = new

    for k, v in r.items():
        if isinstance(v, str) and v.translate(_QUOTES) != v:
            put(k, v.translate(_QUOTES))
    for k in phone_keys(r):
        v = r[k]
        if not isinstance(v, str) or not v or v.startswith("N/A"):
            continue
        if "phone" in k.lower() and not has_phone(v):
            unreadable.append(f"{path.relative_to(ROOT)}  {r.get('id', r.get('name', '?'))}  {k}: {v!r}")
            continue
        put(k, format_phones(v))
    rules = [
        (TITLE_FIELDS, format_title),
        (CLERGY_LIST_FIELDS, format_clergy_list),
        (EMAIL_FIELDS + ("other_contact",), format_email),
        (URL_FIELDS, format_url),
        (PROSE_FIELDS, format_prose),
    ]
    for fields, fn in rules:
        for k in fields:
            if k in r:
                put(k, fn(r[k]))
    return changes


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("files", nargs="*", type=Path)
    ap.add_argument("--check", action="store_true", help="report only; change nothing")
    args = ap.parse_args()

    total, unreadable = 0, []
    for path in [p.resolve() for p in args.files] or default_files():
        records = json.loads(path.read_text(encoding="utf-8-sig"))
        changed = 0
        for r in records:
            for k, old, new in normalize_record(r, path, unreadable):
                if args.check and "phone" not in k:
                    print(f"  {r.get('id', '?')}  {k}: {old!r} -> {new!r}")
                changed += 1
        total += changed
        print(f"{'would fix' if args.check else 'fixed'} {changed:4d} change(s) in {path.relative_to(ROOT)}")
        if changed and not args.check:
            path.write_text(json.dumps(records, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"\n{total} change(s) {'pending' if args.check else 'made'}.")
    if unreadable:
        print(f"\n{len(unreadable)} phone value(s) with no readable number (left as they are, fix by hand):")
        print("\n".join("  " + u for u in unreadable))
    return 0


if __name__ == "__main__":
    sys.exit(main())
