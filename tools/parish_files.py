"""Shared helpers for the parish data layout.

The parish data is edited as one file per state and diocese:

    data/states/<state>/<diocese>.json        e.g. data/states/georgia/atlanta.json

and data/parishes.json, the single file the website loads, is generated from those files (see
tools/build_parishes.py). A diocese that spans several states has a file in each state's folder, holding
just the parishes in that state. Never edit data/parishes.json by hand: the next build overwrites it.
"""

from __future__ import annotations

import json
import re
import sys
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATES_DIR = ROOT / "data" / "states"
COMBINED = ROOT / "data" / "parishes.json"

STATE_NAMES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California",
    "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware", "DC": "District of Columbia",
    "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois",
    "IN": "Indiana", "IA": "Iowa", "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana",
    "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota",
    "MS": "Mississippi", "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada",
    "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York",
    "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon",
    "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota",
    "TN": "Tennessee", "TX": "Texas", "UT": "Utah", "VT": "Vermont", "VA": "Virginia",
    "WA": "Washington", "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming",
    "PR": "Puerto Rico", "VI": "Virgin Islands", "GU": "Guam", "AS": "American Samoa",
    "MP": "Northern Mariana Islands",
}


def fold(s: object) -> str:
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode()
    return s.casefold().strip()


def slug(s: object, fallback: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", fold(s)).strip("-") or fallback


def sort_key(r: dict) -> tuple:
    return tuple(fold(r.get(k)) for k in ("state", "diocese", "city", "name", "id"))


def rel_path(rec: dict) -> Path:
    """Where a record lives under STATES_DIR."""
    state = str(rec.get("state") or "").strip()
    return Path(slug(STATE_NAMES.get(state.upper(), state), "unknown-state")) / (
        slug(rec.get("diocese"), "no-diocese") + ".json"
    )


def dump(records: list[dict]) -> str:
    return json.dumps(records, indent=2, ensure_ascii=False) + "\n"


def state_files() -> list[Path]:
    return sorted(STATES_DIR.rglob("*.json"))


def load_all() -> list[dict]:
    """Every record from the state/diocese files, sorted into the order of parishes.json."""
    records = []
    for path in state_files():
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, list) or not all(isinstance(r, dict) for r in data):
            sys.exit(f"{path}: expected a JSON array of parish objects")
        records += data
    return sorted(records, key=sort_key)


def write_states(records: list[dict]) -> list[Path]:
    """Write records into their state/diocese files; delete files that no longer hold any. Returns the
    paths written or removed."""
    groups: dict[Path, list[dict]] = {}
    for r in sorted(records, key=sort_key):
        groups.setdefault(rel_path(r), []).append(r)
    touched = []
    for rel, recs in groups.items():
        path = STATES_DIR / rel
        text = dump(recs)
        if not path.exists() or path.read_text(encoding="utf-8") != text:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
            touched.append(path)
    for path in state_files():
        if path.relative_to(STATES_DIR) not in groups:
            path.unlink()
            touched.append(path)
    for d in sorted(STATES_DIR.glob("*/"), reverse=True):
        if d.is_dir() and not any(d.iterdir()):
            d.rmdir()
    return touched


def write_combined(records: list[dict]) -> bool:
    """Write data/parishes.json. Returns True if the file changed."""
    text = dump(sorted(records, key=sort_key))
    if COMBINED.exists() and COMBINED.read_text(encoding="utf-8") == text:
        return False
    COMBINED.write_text(text, encoding="utf-8")
    return True
