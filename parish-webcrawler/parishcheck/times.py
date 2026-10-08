"""Times, weekdays and service schedules.

This file knows how to:
  * find clock times inside free text ("8:30 AM", "noon", "10 a.m.", "9-10:30 am")
  * print them in the style you asked for (12-hour "8:30 AM" / "12:00 noon", or 24-hour "08:30")
  * turn a services string such as "Wed 5:30 PM Healing Mass; 1st & 3rd Thu 10 AM"
    into a SET of (day, ordinal, minutes-after-midnight) slots so two schedules can be compared.

No network, no AI: pure text rules, so selftest.py can test it offline.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Optional

# --------------------------------------------------------------------------------------
# Weekdays
# --------------------------------------------------------------------------------------
DAY_ORDER = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]

# Pattern that matches one weekday word in many spellings.
_DAY_WORD = (
    r"(?:mon(?:day)?s?|tue(?:s|sday)?s?|wed(?:nesday)?s?|thu(?:r|rs|rsday)?s?|"
    r"fri(?:day)?s?|sat(?:urday)?s?|sun(?:day)?s?)"
)
# Words that stand for several days at once.
_MULTI_DAY = {
    "weekdays": ["mon", "tue", "wed", "thu", "fri"],
    "weekday": ["mon", "tue", "wed", "thu", "fri"],
    "daily": list(DAY_ORDER),
    "every day": list(DAY_ORDER),
    "everyday": list(DAY_ORDER),
    "each day": list(DAY_ORDER),
    "weekends": ["sat", "sun"],
}

_ORD_WORD = r"(?:1st|2nd|3rd|4th|5th|first|second|third|fourth|fifth|last)"
_ORD_MAP = {
    "1st": 1, "first": 1, "2nd": 2, "second": 2, "3rd": 3, "third": 3,
    "4th": 4, "fourth": 4, "5th": 5, "fifth": 5, "last": -1,
}
_ORD_LABEL = {1: "1st", 2: "2nd", 3: "3rd", 4: "4th", 5: "5th", -1: "last"}

# One "day group": optional ordinals, then one or more days joined by commas, "&", "and",
# "/" (a list) or "-", "to", "through" (a range).  Examples it matches:
#   "Wed"   "Wednesdays"   "Mon-Fri"   "1st & 3rd Wed"   "every 4th Wednesday"   "Sat, Sun"
_DAYGROUP_RE = re.compile(
    r"""
    (?<![A-Za-z])
    (?:(?:every|each)\s+)?
    (?P<ords>""" + _ORD_WORD + r"""(?:\s*(?:,|&|and|/)\s*""" + _ORD_WORD + r""")*\s+(?:and\s+)?)?
    (?P<days>
        (?:""" + _DAY_WORD + r""")
        (?:\s*(?:,|&|/|\band\b|-|–|—|\bto\b|\bthrough\b|\bthru\b)\s*(?:""" + _DAY_WORD + r"""))*
    )
    (?![A-Za-z])
    """,
    re.I | re.X,
)
_MULTI_DAY_RE = re.compile(
    r"(?<![A-Za-z])(weekdays|weekday|daily|every day|everyday|each day|weekends)(?![A-Za-z])", re.I
)
_SINGLE_DAY_RE = re.compile(_DAY_WORD, re.I)
_RANGE_CONNECTOR_RE = re.compile(r"^\s*(?:-|–|—|to|through|thru)\s*$", re.I)


def day_token(word: str) -> Optional[str]:
    """'Wednesdays' -> 'wed'.  Returns None when it is not a weekday word."""
    w = word.strip().lower().rstrip(".")
    for d in DAY_ORDER:
        if w.startswith(d):
            return d
    return None


def _expand_day_group(text: str) -> list[str]:
    """Turn 'Mon-Fri' into [mon..fri] and 'Mon, Wed & Fri' into [mon, wed, fri]."""
    words = list(_SINGLE_DAY_RE.finditer(text))
    days: list[str] = []
    for i, m in enumerate(words):
        d = day_token(m.group(0))
        if d is None:
            continue
        if i > 0:
            between = text[words[i - 1].end():m.start()]
            prev = day_token(words[i - 1].group(0))
            if _RANGE_CONNECTOR_RE.match(between) and prev:
                a, b = DAY_ORDER.index(prev), DAY_ORDER.index(d)
                idx = a
                while True:  # walk forward from a to b, wrapping past Sunday if needed
                    idx = (idx + 1) % 7
                    if DAY_ORDER[idx] not in days:
                        days.append(DAY_ORDER[idx])
                    if idx == b:
                        break
                continue
        if d not in days:
            days.append(d)
    return days


# --------------------------------------------------------------------------------------
# Clock times
# --------------------------------------------------------------------------------------
_AP = r"[ap]\.?\s?m(?![a-z])"  # a.m. / am / A M / p.m. ... but not the start of a word like "amen"

TIME_RE = re.compile(
    r"""
    (?<![\w:.])
    (?:
        # 1) 8:30  8:30 AM  8:30 a.m.  12:00 noon
        (?P<h1>\d{1,2}):(?P<m1>\d{2})(?:\s*(?:(?P<ap1>""" + _AP + r""")\.?|(?P<nn1>noon|midnight)))?(?![\d:])
      |
        # 2) 8 AM   8.30 pm
        (?P<h2>\d{1,2})(?:\.(?P<m2>\d{2}))?\s*(?P<ap2>""" + _AP + r""")\.?
      |
        # 3) noon / 12 noon / midnight
        (?:(?P<h3>12)\s+)?(?P<nn3>noon|midnight(?!\s+mass))\b
      |
        # 4) the first number of a range whose end carries am/pm:  "10 - 11 AM", "9:30-10:30 am"
        (?P<h4>\d{1,2})(?::(?P<m4>\d{2}))?(?=\s*(?:-|–|—|to)\s*\d{1,2}(?::\d{2})?\s*""" + _AP + r""")
    )
    """,
    re.I | re.X,
)
_RANGE_JOIN_RE = re.compile(r"^\s*(?:-|–|—|to|until)\s*$", re.I)


@dataclass
class TimeHit:
    minutes: int          # minutes after midnight, 0..1439
    start: int            # position in the text where the match begins
    end: int              # position where it ends
    explicit: bool        # True if the text said AM/PM/noon/midnight itself
    is_noon: bool = False
    is_midnight: bool = False
    range_end: bool = False  # True for the second time of '9-10:30 AM' (a range END, not a start)


def _to_minutes(h: int, m: int, period: Optional[str]) -> Optional[int]:
    if h > 24 or m > 59:
        return None
    if h > 12:
        period = None  # already a 24-hour value; ignore any stray AM/PM
    if period == "am":
        h = 0 if h == 12 else h
    elif period == "pm":
        h = h if h == 12 else h + 12
    else:
        # No AM/PM written. 13-24 is already 24-hour. Otherwise guess what a church means:
        # 7-11 -> morning, 12 -> noon, 1-6 -> afternoon/evening.
        if h >= 13:
            pass
        elif h == 12:
            pass
        elif 1 <= h <= 6:
            h += 12
    if h == 24:
        h = 0
    return h * 60 + m


def find_times(text: str) -> list[TimeHit]:
    """Find every clock time in `text`, in order. Handles ranges ("9-10:30 AM"), noon, midnight."""
    hits: list[TimeHit] = []
    pending_range_start: list[tuple[int, int, int, Optional[int]]] = []  # (h, m, start, end) bare range starts
    for m in TIME_RE.finditer(text or ""):
        g = m.groupdict()
        start, end = m.start(), m.end()
        if g["nn3"]:
            noon = g["nn3"].lower() == "noon"
            hits.append(TimeHit(720 if noon else 0, start, end, True, is_noon=noon, is_midnight=not noon))
            continue
        if g["h4"] is not None:
            pending_range_start.append((int(g["h4"]), int(g["m4"] or 0), start, end))
            continue
        if g["h1"] is not None:
            h, mi = int(g["h1"]), int(g["m1"])
            ap = (g["ap1"] or "")[:1].lower()
            nn = (g["nn1"] or "").lower()
        else:
            h, mi = int(g["h2"]), int(g["m2"] or 0)
            ap = (g["ap2"] or "")[:1].lower()
            nn = ""
        if nn:
            minutes = _to_minutes(h, mi, None)
            if minutes is None:
                continue
            if nn == "noon":
                hits.append(TimeHit(minutes, start, end, True, is_noon=True))
            else:
                hits.append(TimeHit(0 if h in (0, 12, 24) else minutes, start, end, True, is_midnight=True))
            continue
        period = "am" if ap == "a" else "pm" if ap == "p" else None
        minutes = _to_minutes(h, mi, period)
        if minutes is None:
            continue
        hits.append(TimeHit(minutes, start, end, period is not None))

    # Bare range starts ("10 - 11 AM"): borrow AM/PM from the time that follows.
    for h, mi, start, end in pending_range_start:
        nxt = next((t for t in hits if t.start >= end), None)
        if nxt is None or not _RANGE_JOIN_RE.match(text[end:nxt.start]):
            continue
        period = "pm" if nxt.minutes >= 720 else "am"
        first = _to_minutes(h, mi, period)
        if first is not None and first > nxt.minutes:  # "11-1 PM" -> 11 AM, not 11 PM
            first = _to_minutes(h, mi, "am" if period == "pm" else "pm")
        if first is not None:
            hits.append(TimeHit(first, start, end, True))

    hits.sort(key=lambda t: t.start)

    # A time written without AM/PM right before a time that has it ("8:30-9:30 AM") borrows its period.
    for i in range(len(hits) - 1):
        a, b = hits[i], hits[i + 1]
        if not a.explicit and b.explicit and _RANGE_JOIN_RE.match(text[a.end:b.start]):
            h12 = a.minutes % 720
            pm = b.minutes >= 720
            cand = h12 + (720 if pm else 0)
            if cand > b.minutes:
                cand = h12 + (0 if pm else 720)
            a.minutes = cand
            a.explicit = True
    for i in range(len(hits) - 1):
        if _RANGE_JOIN_RE.match(text[hits[i].end:hits[i + 1].start]):
            hits[i + 1].range_end = True
    return hits


def format_time(minutes: int, fmt: str = "12h") -> str:
    """Minutes after midnight -> '8:30 AM', '12:00 noon', '12:00 midnight' or '08:30' (24h)."""
    minutes %= 1440
    h, m = divmod(minutes, 60)
    if fmt == "24h":
        return f"{h:02d}:{m:02d}"
    if minutes == 720:
        return "12:00 noon"
    if minutes == 0:
        return "12:00 midnight"
    suffix = "AM" if h < 12 else "PM"
    h12 = h % 12 or 12
    return f"{h12}:{m:02d} {suffix}"


def restyle_times(text: str, fmt: str = "12h", only_explicit: bool = True) -> str:
    """Rewrite every clock time in `text` into the configured style.

    only_explicit=True leaves bare numbers like 'John 3:16' alone (it only touches times that carry
    AM/PM/noon/midnight).  The services fields pass only_explicit=False because a bare '10:30' there
    really is a time.
    """
    if not text:
        return text
    out, last = [], 0
    for t in find_times(text):
        if only_explicit and not t.explicit:
            continue
        out.append(text[last:t.start])
        out.append(format_time(t.minutes, fmt))
        last = t.end
    out.append(text[last:])
    return "".join(out)


def time_keys(text: str) -> set[tuple[int, int]]:
    """Loose fingerprints of all times in the text: (hour on a 12-hour clock, minute).
    Used to check that a time the model reported really appears on the page."""
    return {((t.minutes // 60) % 12, t.minutes % 60) for t in find_times(text)}


# --------------------------------------------------------------------------------------
# Service schedules
# --------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Slot:
    day: Optional[str]       # 'sun', 'wed', ... or None when the text gives no day
    ordinal: Optional[int]   # None = every week, 1..5 = that week of the month, -1 = last
    minutes: int


def split_top_level(text: str, sep: str = ";") -> list[str]:
    """Split on `sep` but not inside (parentheses). Empty pieces are dropped."""
    parts, depth, cur = [], 0, []
    for ch in text or "":
        if ch in "([":
            depth += 1
        elif ch in ")]" and depth > 0:
            depth -= 1
        if ch == sep and depth == 0:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    parts.append("".join(cur))
    return [p.strip() for p in parts if p.strip()]


def strip_parentheses(text: str) -> str:
    """Remove (...) and [...] notes, e.g. '(Benediction following, every 4th Wednesday)'."""
    prev = None
    while prev != text:
        prev = text
        text = re.sub(r"\([^()]*\)|\[[^\[\]]*\]", " ", text)
    return re.sub(r"[()\[\]]", " ", text)


def parse_service_slots(text: str, default_day: Optional[str] = None) -> set[Slot]:
    """Parse a services string into a set of Slot(day, ordinal, minutes).

    * Items are separated by ';'.  Text in (parentheses) is ignored for day/time purposes.
    * A time uses the closest day-group BEFORE it in the item; if there is none, the first day-group
      AFTER it; if the item has no day at all, `default_day` is used (pass 'sun' for sunday_services).
    * '1st & 3rd Wed 5 PM' becomes two slots (Wed 1st, Wed 3rd).
    * Items with no clock time produce no slots.
    """
    slots: set[Slot] = set()
    for item in split_top_level(text or ""):
        body = strip_parentheses(item)
        events: list[tuple[int, str, object]] = []
        for m in _DAYGROUP_RE.finditer(body):
            days = _expand_day_group(m.group("days"))
            if not days:
                continue
            ords = []
            if m.group("ords"):
                ords = [_ORD_MAP[o.lower()] for o in re.findall(_ORD_WORD, m.group("ords"), re.I)]
            events.append((m.start(), "day", (days, ords)))
        for m in _MULTI_DAY_RE.finditer(body):
            events.append((m.start(), "day", (list(_MULTI_DAY[m.group(1).lower()]), [])))
        for t in find_times(body):
            if not t.range_end:  # for "9-10:30 AM" only the start (9:00) names the service time
                events.append((t.start, "time", t.minutes))
        events.sort(key=lambda e: e[0])

        current: Optional[tuple[list[str], list[int]]] = None
        waiting: list[int] = []  # times seen before any day group
        for _, kind, payload in events:
            if kind == "day":
                current = payload  # type: ignore[assignment]
                for minutes in waiting:
                    _add_slots(slots, current, minutes)
                waiting = []
            else:
                if current is None:
                    waiting.append(payload)  # type: ignore[arg-type]
                else:
                    _add_slots(slots, current, payload)  # type: ignore[arg-type]
        for minutes in waiting:  # no day anywhere in the item
            _add_slots(slots, ([default_day] if default_day else [None], []), minutes)  # type: ignore[list-item]
    return slots


def _add_slots(slots: set[Slot], group: tuple, minutes: int) -> None:
    days, ords = group
    for d in days:
        if ords:
            for o in ords:
                slots.add(Slot(d, o, minutes))
        else:
            slots.add(Slot(d, None, minutes))


def slot_sort_key(s: Slot) -> tuple:
    """Weekday order (Mon..Sun, unknown last), then week-of-month, then time."""
    day = DAY_ORDER.index(s.day) if s.day in DAY_ORDER else 99
    return (day, s.ordinal if s.ordinal is not None else 0, s.minutes)


def describe_slot(s: Slot, fmt: str = "12h") -> str:
    """Human readable slot, e.g. '3rd Wed 5:00 PM'."""
    parts = []
    if s.ordinal is not None:
        parts.append(_ORD_LABEL[s.ordinal])
    if s.day:
        parts.append(s.day.capitalize())
    parts.append(format_time(s.minutes, fmt))
    return " ".join(parts)


def day_time_pairs(slots: Iterable[Slot]) -> set[tuple[Optional[str], int]]:
    """Slots with the ordinal dropped: used to tell 'same service, different frequency' from 'different service'."""
    return {(s.day, s.minutes) for s in slots}
