"""Comparators: decide how a JSON value relates to what the website says.

`compare_typed()` does the type-aware comparison (phone, email, url, address, person, services, list,
text, rite) and returns CORRECT / PARTIAL / DISCREPANCY plus the items on each side.

`decide_status()` wraps it with the rules about missing data and doubt:
    BOTH_MISSING, SITE_ONLY, JSON_ONLY, UNCLEAR  (and NOT_CHECKABLE is handled by the report code).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Optional

from rapidfuzz import fuzz

from . import models as M
from .normalizers import (
    addresses_match, derive_rite, emails_in_json_value, host_of, is_missing, livestream_key, norm_email,
    norm_rite, norm_state, norm_text, persons_match, phones_in_json_value, split_items, url_key,
)
from .times import Slot, day_time_pairs, describe_slot, parse_service_slots, slot_sort_key

FUZZY_THRESHOLD = 80  # rapidfuzz token_set_ratio needed to call two list items "the same"


@dataclass
class CompareResult:
    status: str                               # CORRECT / PARTIAL / DISCREPANCY
    json_only: list = field(default_factory=list)
    site_only: list = field(default_factory=list)
    detail: str = ""


@dataclass
class StatusResult:
    status: str
    json_only: list = field(default_factory=list)
    site_only: list = field(default_factory=list)
    detail: str = ""
    reason: str = ""


# --------------------------------------------------------------------------------------
# Small building blocks
# --------------------------------------------------------------------------------------
def _fuzzy_same(a: str, b: str, threshold: int = FUZZY_THRESHOLD) -> bool:
    na, nb = norm_text(a), norm_text(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    return fuzz.token_set_ratio(na, nb) >= threshold


def _set_status(json_items: list, site_items: list, matcher) -> CompareResult:
    """Generic set comparison. `matcher(a, b)` says whether a JSON item and a site item are 'the same'."""
    json_found = [j for j in json_items if any(matcher(j, s) for s in site_items)]
    site_found = [s for s in site_items if any(matcher(j, s) for j in json_items)]
    json_only = [j for j in json_items if j not in json_found]
    site_only = [s for s in site_items if s not in site_found]
    if not json_found:
        return CompareResult(M.DISCREPANCY, json_only, site_only, "no items in common")
    if not json_only and not site_only:
        return CompareResult(M.CORRECT)
    return CompareResult(M.PARTIAL, json_only, site_only, "some items agree")


# --------------------------------------------------------------------------------------
# Type-aware comparison
# --------------------------------------------------------------------------------------
def compare_typed(
    ftype: str,
    json_value: str,
    site_values: list[str],
    *,
    default_day: Optional[str] = None,
    url_kind: Optional[str] = None,
    time_format: str = "12h",
    list_sep: str = ";",
) -> CompareResult:
    """Compare one JSON value to the site's value(s). Both sides are assumed to be present."""
    json_value = str(json_value)
    site_values = [str(v) for v in site_values if str(v).strip()]

    if ftype == "phone":
        jn = phones_in_json_value(json_value)
        sn = [p for v in site_values for p in phones_in_json_value(v)]
        if not jn:
            return CompareResult(M.UNCLEAR, detail="the phone number in the JSON could not be read")
        if any(j in sn for j in jn):
            return CompareResult(M.CORRECT)
        return CompareResult(M.DISCREPANCY, detail="different number")

    if ftype == "email":
        jn = [norm_email(e) for e in emails_in_json_value(json_value)] or [norm_email(json_value)]
        sn = [norm_email(e) for v in site_values for e in (emails_in_json_value(v) or [v])]
        if any(j in sn for j in jn):
            return CompareResult(M.CORRECT)
        return CompareResult(M.DISCREPANCY, detail="different email")

    if ftype == "url":
        if url_kind == "livestream":
            jk, sk = livestream_key(json_value), [livestream_key(v) for v in site_values]
        elif url_kind == "website":
            # For the website itself only the host matters: /home vs / is not a difference.
            jk, sk = host_of(json_value), [host_of(v) for v in site_values]
        else:
            jk, sk = url_key(json_value), [url_key(v) for v in site_values]
        if jk and jk in sk:
            return CompareResult(M.CORRECT)
        return CompareResult(M.DISCREPANCY, detail="different web address")

    if ftype == "address":
        if any(addresses_match(json_value, s) for s in site_values):
            return CompareResult(M.CORRECT)
        return CompareResult(M.DISCREPANCY, detail="different street address")

    if ftype == "person":
        j_people = split_items(json_value) or [json_value]
        s_people = []
        for v in site_values:
            s_people.extend(split_items(v) or [v])
        expl: list[str] = []

        def _m(a: str, b: str) -> bool:
            ok, why = persons_match(a, b)
            if not ok:
                expl.append(why)
            return ok

        res = _set_status(j_people, s_people, _m)
        if res.status == M.DISCREPANCY and expl:
            res.detail = "; ".join(dict.fromkeys(expl))
        return res

    if ftype == "services":
        jslots = parse_service_slots(json_value, default_day)
        sslots: set[Slot] = set()
        for v in site_values:
            sslots |= parse_service_slots(v, default_day)
        if not jslots or not sslots:
            # No clock times to compare on one side: fall back to comparing the text items.
            j_items, s_items = split_items(json_value), [i for v in site_values for i in split_items(v)]
            res = _set_status(j_items, s_items, _fuzzy_same)
            res.detail = (res.detail + "; " if res.detail else "") + "compared as text because no clock times could be read from one side"
            return res
        common = jslots & sslots
        if jslots == sslots:
            return CompareResult(M.CORRECT)
        j_only = sorted(jslots - sslots, key=slot_sort_key)
        s_only = sorted(sslots - jslots, key=slot_sort_key)
        j_only_t = [describe_slot(s, time_format) for s in j_only]
        s_only_t = [describe_slot(s, time_format) for s in s_only]
        if common:
            return CompareResult(M.PARTIAL, j_only_t, s_only_t, "some service times agree")
        # Same weekday and time but a different week-of-month pattern still counts as partly the same.
        if day_time_pairs(jslots) & day_time_pairs(sslots):
            return CompareResult(M.PARTIAL, j_only_t, s_only_t, "same day and time, but how often differs")
        return CompareResult(M.DISCREPANCY, j_only_t, s_only_t, "no service times in common")

    if ftype == "state":
        j = norm_state(json_value) or norm_text(json_value)
        if any(j == (norm_state(v) or norm_text(v)) for v in site_values):
            return CompareResult(M.CORRECT)
        return CompareResult(M.DISCREPANCY, detail="different state")

    if ftype == "rite":
        j = norm_rite(json_value)
        s = {norm_rite(v) for v in site_values}
        if j in s:
            return CompareResult(M.CORRECT)
        return CompareResult(M.DISCREPANCY, detail="different rite")

    if ftype == "contactlist":
        # other_contact: items like phone numbers and emails joined with " | "
        def canon(x: str) -> str:
            ph = phones_in_json_value(x)
            if ph:
                return ph[0]
            em = emails_in_json_value(x)
            return em[0] if em else norm_text(x)

        j_items = [i.strip() for i in str(json_value).split(list_sep) if i.strip()]
        s_items = [i.strip() for v in site_values for i in v.split(list_sep) if i.strip()]
        return _set_status(j_items, s_items, lambda a, b: canon(a) == canon(b))

    # list / text (and anything unknown): split on ';', fuzzy-match items
    j_items = split_items(json_value, list_sep)
    s_items = [i for v in site_values for i in split_items(v, list_sep)]
    if not j_items:
        j_items = [json_value]
    if not s_items:
        s_items = site_values
    return _set_status(j_items, s_items, _fuzzy_same)


# --------------------------------------------------------------------------------------
# Status assignment
# --------------------------------------------------------------------------------------
def decide_status(
    ftype: str,
    json_value,
    site_values: list[str],
    *,
    missing_markers: Iterable[str],
    site_confidence: str = "high",
    unclear_reason: str = "",
    json_only_note: str = "",
    default_day: Optional[str] = None,
    url_kind: Optional[str] = None,
    time_format: str = "12h",
    list_sep: str = ";",
) -> StatusResult:
    """The one place where a field's final status is chosen.

    Order of rules:
      1. The source was blocked / failed / ambiguous / its evidence was rejected -> UNCLEAR (with reason)
      2. Both sides empty                -> BOTH_MISSING
      3. JSON empty, site has a value    -> SITE_ONLY   (but UNCLEAR if the site value is low confidence)
      4. JSON has a value, site doesn't  -> JSON_ONLY   (with a note about page cap / limited source)
      5. Both have values                -> compare; anything but CORRECT with low confidence -> UNCLEAR
    """
    markers = list(missing_markers)
    json_missing = is_missing(json_value, markers)
    site_values = [v for v in (site_values or []) if str(v).strip()]

    if unclear_reason and not site_values:
        return StatusResult(M.UNCLEAR, reason=unclear_reason)

    if json_missing and not site_values:
        return StatusResult(M.BOTH_MISSING)

    if json_missing:
        if site_confidence == "low":
            return StatusResult(M.UNCLEAR, reason="the website value is low confidence, so I did not trust it")
        return StatusResult(M.SITE_ONLY)

    if not site_values:
        return StatusResult(M.JSON_ONLY, detail=json_only_note)

    cmp = compare_typed(
        ftype, str(json_value), site_values, default_day=default_day, url_kind=url_kind,
        time_format=time_format, list_sep=list_sep,
    )
    if cmp.status == M.UNCLEAR:
        return StatusResult(M.UNCLEAR, cmp.json_only, cmp.site_only, cmp.detail, reason=cmp.detail)
    if cmp.status != M.CORRECT and (site_confidence == "low" or unclear_reason):
        why = unclear_reason or "the website value is low confidence, so I did not trust the difference"
        return StatusResult(M.UNCLEAR, cmp.json_only, cmp.site_only, cmp.detail, reason=why)
    return StatusResult(cmp.status, cmp.json_only, cmp.site_only, cmp.detail)
