"""Turning everything found on the sources into ONE answer per field for ONE record.

Rules (from your spec):
  * Scalar fields: take the value from the highest-priority source (own website, then Facebook, then
    Asset Map), then from the best kind of page (contact / services pages first), then the most frequent.
  * List-like fields (services and lists): the de-duplicated union.
  * Lower-priority sources that disagree are only mentioned in the `detail` column.
  * Rector name/email/phone are never taken from an Asset Map contact unless the title says Rector, Vicar,
    Priest-in-Charge, Dean or Interim Rector (that filtering happens in assetmap.py).
  * Shared websites (several records, one website): each record is matched to the page(s) that show ITS
    street address.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Optional

from rapidfuzz import fuzz

from . import models as M
from .comparators import compare_typed
from .config import Config, FieldSpec
from .models import Candidate, SiteValue
from .normalizers import (
    addresses_match, collapse_ws, derive_rite, format_phone, host_of, livestream_key, norm_state, norm_text,
    parse_person, phones_in_json_value, split_city_state, split_items, url_key,
)
from .times import DAY_ORDER, parse_service_slots

SOURCE_KEY = {M.OWN_SITE: "own_site", M.FACEBOOK: "facebook", M.ASSET_MAP: "asset_map"}
KIND_LABEL = {M.OWN_SITE: "the church website", M.FACEBOOK: "Facebook", M.ASSET_MAP: "the Episcopal Asset Map"}


@dataclass
class RecordView:
    values: dict = field(default_factory=dict)             # field key -> SiteValue
    sources_used: list = field(default_factory=list)       # e.g. ["own site: https://..."]
    notes: list = field(default_factory=list)              # record-level notes
    all_failed_reason: str = ""                            # set when no source could be read
    limited_note: str = ""                                 # set when a better source failed (shown on every uncertain row)
    cap_note: str = ""                                     # set when the crawl hit the page cap (shown on JSON_ONLY rows)
    cap_hit: bool = False
    pages_crawled: int = 0
    unlocated: bool = False                                # shared website, but this record's page not found
    issues: dict = field(default_factory=dict)             # field key -> [reason, ...]
    primary_type: str = ""                                 # source type shown when a field has no value of its own
    primary_url: str = ""


# --------------------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------------------
def _pages(cs: list[Candidate]) -> int:
    return len({c.source_url for c in cs})


def _best_of(cs: list[Candidate]) -> Candidate:
    """The most useful single candidate in a cluster: high confidence, then best page, then in the footer."""
    return sorted(cs, key=lambda c: (c.confidence != "high", c.page_rank, not c.in_footer))[0]


def _cluster(cands: list[Candidate], same) -> list[list[Candidate]]:
    clusters: list[list[Candidate]] = []
    for c in cands:
        for cl in clusters:
            if same(cl[0].value, c.value):
                cl.append(c)
                break
        else:
            clusters.append([c])
    return clusters


def _prefer_good(cands: list[Candidate]) -> tuple[list[Candidate], str]:
    """Drop stale and low-confidence candidates when better ones exist.  Returns (kept, confidence)."""
    fresh = [c for c in cands if not c.stale] or cands
    high = [c for c in fresh if c.confidence == "high"]
    if high:
        return high, "high"
    return fresh, "low"


def _sv_from(c: Candidate, values: list[str], conf: str = "") -> SiteValue:
    return SiteValue(values=values, best=values[0] if values else "", confidence=conf or c.confidence,
                     source_type=c.source_type, source_url=c.source_url, method=c.method, evidence=c.evidence)


# --------------------------------------------------------------------------------------
# per-type merging
# --------------------------------------------------------------------------------------
def _merge_contact(spec: FieldSpec, cands: list[Candidate], kind: str) -> Optional[SiteValue]:
    """Phones and emails. `kind` is 'phone' or 'email'. Role labels decide which field gets which."""
    groups: dict[str, list[Candidate]] = defaultdict(list)
    for c in cands:
        key = (phones_in_json_value(c.value) or [c.value])[0] if kind == "phone" else c.value.lower()
        groups[key].append(c)
    info = []
    for key, cs in groups.items():
        votes = Counter(c.role for c in cs if c.role)
        role = None
        if votes:
            top = votes.most_common()
            role = top[0][0] if len(top) == 1 or top[0][1] > top[1][1] else "church"
        llm_only = bool(votes) and all(c.method == "llm" for c in cs if c.role)
        info.append({"key": key, "cs": cs, "role": role, "llm_only": llm_only,
                     "pages": _pages(cs), "rank": min(c.page_rank for c in cs), "footer": any(c.in_footer for c in cs)})
    want = spec.role or "church"
    if want == "rector":
        eligible = [g for g in info if g["role"] == "rector"]
    else:
        eligible = [g for g in info if g["role"] in ("church", None)]
    other = [g for g in info if g not in eligible]
    eligible.sort(key=lambda g: (g["role"] != want, -g["pages"], g["rank"], not g["footer"]))
    if not eligible and not other:
        return None
    values = [(format_phone(g["key"]) if kind == "phone" else g["key"]) for g in eligible]
    alts = [(format_phone(g["key"]) if kind == "phone" else g["key"]) for g in other]
    if not eligible:
        sv = SiteValue(values=[], best="", alt_values=alts, confidence="high")
        sv.source_type, sv.source_url = other[0]["cs"][0].source_type, other[0]["cs"][0].source_url
        return sv
    top = eligible[0]
    c0 = _best_of(top["cs"])
    sv = _sv_from(c0, values, "low" if top["llm_only"] else "high")
    sv.alt_values = alts
    if len(eligible) > 1:
        sv.notes.append(f"{len(eligible)} {kind}s found on the site: " + ", ".join(values[:5]))
    return sv


def _merge_address(spec: FieldSpec, cands: list[Candidate]) -> Optional[SiteValue]:
    clusters = _cluster(cands, addresses_match)
    if not clusters:
        return None
    clusters.sort(key=lambda cl: (-_pages(cl), min(c.page_rank for c in cl), not any(c.in_footer for c in cl)))
    values, reps = [], []
    for cl in clusters[:6]:
        # Prefer a JSON-LD spelling when there is one, otherwise the most common spelling.
        jl = [c for c in cl if c.evidence.startswith("JSON-LD")]
        counts = Counter(c.value for c in cl)
        rep = jl[0] if jl else _best_of([c for c in cl if c.value == counts.most_common(1)[0][0]])
        values.append(rep.value)
        reps.append(rep)
    sv = _sv_from(reps[0], values, "high")
    if len(values) > 1:
        sv.notes.append("other addresses seen on the site: " + "; ".join(values[1:3]))
    return sv


def _address_part(spec: FieldSpec, addr: Optional[SiteValue]) -> Optional[SiteValue]:
    if addr is None or not addr.values:
        return None
    outs = []
    for a in addr.values[:3]:
        city, state = split_city_state(a)
        v = city if spec.address_part == "city" else state
        if v and v not in outs:
            outs.append(v)
    if not outs:
        return None
    sv = SiteValue(values=outs, best=outs[0], confidence=addr.confidence, source_type=addr.source_type,
                   source_url=addr.source_url, method="rule (from address)", evidence=addr.evidence)
    sv.derived_from = ["address"]
    return sv


def _merge_scalar(spec: FieldSpec, cands: list[Candidate], same) -> Optional[SiteValue]:
    kept, conf = _prefer_good(cands)
    if not kept:
        return None
    clusters = _cluster(kept, same)
    clusters.sort(key=lambda cl: (min(c.page_rank for c in cl), -_pages(cl)))
    top = clusters[0]
    rep = _best_of(top)
    sv = _sv_from(rep, [rep.value], conf)
    # A tie between different answers on equally good pages = the site contradicts itself.
    rivals = [cl for cl in clusters[1:] if (min(c.page_rank for c in cl), -_pages(cl)) == (min(c.page_rank for c in top), -_pages(top))]
    if rivals:
        sv.conflict = True
        sv.values = [rep.value] + [_best_of(cl).value for cl in rivals]
        sv.notes.append("the site gives different answers: " + " / ".join(sv.values))
    elif len(clusters) > 1:
        sv.notes.append("other answers seen: " + "; ".join(_best_of(cl).value for cl in clusters[1:3]))
    return sv


def _person_same(a: str, b: str) -> bool:
    pa, pb = parse_person(a), parse_person(b)
    return bool(pa.surname) and pa.surname == pb.surname


def _merge_services(spec: FieldSpec, cands: list[Candidate], fmt: str) -> Optional[SiteValue]:
    kept, conf = _prefer_good(cands)
    if not kept:
        return None
    items: list[dict] = []
    for c in sorted(kept, key=lambda c: (c.page_rank, c.confidence != "high")):
        for it in split_items(c.value):
            slots = parse_service_slots(it, spec.default_day)
            for ex in items:
                if (slots and ex["slots"] == slots) or (not slots and fuzz.token_set_ratio(norm_text(ex["text"]), norm_text(it)) >= 92):
                    break
            else:
                items.append({"text": it, "slots": slots, "c": c})

    def sort_key(i):
        if not i["slots"]:
            return (99, 0)
        return (min((DAY_ORDER.index(s.day) if s.day in DAY_ORDER else 7) for s in i["slots"]),
                min(s.minutes for s in i["slots"]))

    items.sort(key=sort_key)       # stable: untimed items keep their order at the end
    value = "; ".join(i["text"] for i in items)
    sv = _sv_from(kept[0], [value], conf)
    sv.source_url = items[0]["c"].source_url if items else sv.source_url
    sv.evidence = " | ".join(dict.fromkeys(i["c"].evidence for i in items))[:300]
    return sv


def _merge_list(spec: FieldSpec, cands: list[Candidate]) -> Optional[SiteValue]:
    kept, conf = _prefer_good(cands)
    if not kept:
        return None
    items: list[tuple[str, Candidate]] = []
    for c in sorted(kept, key=lambda c: c.page_rank):
        for it in split_items(c.value):
            for idx, (ex, exc) in enumerate(items):
                if fuzz.token_set_ratio(norm_text(ex), norm_text(it)) >= 90:
                    if len(it) > len(ex):
                        items[idx] = (it, c)
                    break
            else:
                items.append((it, c))
    value = "; ".join(i for i, _ in items)
    sv = _sv_from(kept[0], [value], conf)
    sv.evidence = " | ".join(dict.fromkeys(c.evidence for _, c in items))[:300]
    return sv


def _merge_livestream(cands: list[Candidate]) -> Optional[SiteValue]:
    groups: dict[str, list[Candidate]] = defaultdict(list)
    for c in cands:
        groups[livestream_key(c.value)].append(c)
    ranked = sorted(groups.values(), key=lambda cs: (all(c.confidence != "high" for c in cs), -_pages(cs)))
    values = [_best_of(cs).value for cs in ranked]
    if not values:
        return None
    best_group = ranked[0]
    conf = "high" if any(c.confidence == "high" for cs in ranked for c in cs) else "low"
    sv = _sv_from(_best_of(best_group), values, conf)
    return sv


# --------------------------------------------------------------------------------------
# one field, from the candidates of ONE source
# --------------------------------------------------------------------------------------
def merge_field(spec: FieldSpec, cands: list[Candidate], cfg: Config) -> Optional[SiteValue]:
    t = spec.type
    if t == "phone":
        return _merge_contact(spec, [c for c in cands if c.field == "@phone"], "phone")
    if t == "email":
        return _merge_contact(spec, [c for c in cands if c.field == "@email"], "email")
    if t == "address":
        return _merge_address(spec, [c for c in cands if c.field == "@address"])
    if t == "url":
        if spec.url_kind == "livestream":
            return _merge_livestream([c for c in cands if c.field == "@livestream"])
        if spec.url_kind == "website":
            ws = [c for c in cands if c.field == "@website"]
            return _sv_from(ws[0], [ws[0].value], "high") if ws else None
        return None
    own = [c for c in cands if c.field == spec.key]
    if t == "person":
        extra = [c for c in cands if c.field == "@clergy"] if spec.role == "rector" else []
        return _merge_scalar(spec, own + extra, _person_same)
    if t == "services":
        return _merge_services(spec, own, cfg.time_format)
    if t == "list":
        return _merge_list(spec, own)
    if t == "text" and spec.key == cfg.name_key:
        own = own + [c for c in cands if c.field == "@jsonld_name"]
    return _merge_scalar(spec, own, lambda a, b: fuzz.token_set_ratio(norm_text(a), norm_text(b)) >= 90)


# --------------------------------------------------------------------------------------
# the whole record
# --------------------------------------------------------------------------------------
def _derive(cfg: Config, values: dict) -> None:
    """other_contact and rite are built from other fields, in code."""
    for key, how in (cfg.derived_fields or {}).items():
        spec = cfg.get_field(key)
        if spec is None:
            continue
        how = how or {}
        if "from_services" in how or spec.type == "rite":
            srcs = how.get("from_services") or []
            texts = [values[k].best for k in srcs if k in values and values[k].best]
            r = derive_rite(*texts)
            if r:
                first = next((values[k] for k in srcs if k in values and values[k].best), None)
                sv = SiteValue(values=[r], best=r, confidence=first.confidence if first else "high",
                               source_type=first.source_type if first else "", source_url=first.source_url if first else "",
                               method="rule (derived from services text)", evidence="; ".join(texts)[:300])
                sv.derived_from = list(srcs)
                values[key] = sv
        elif "from" in how:
            sep = how.get("separator", " | ")
            parts = [values[k].best for k in how["from"] if k in values and values[k].best]
            if parts:
                first = values[[k for k in how["from"] if k in values and values[k].best][0]]
                sv = SiteValue(values=[sep.join(parts)], best=sep.join(parts), confidence=first.confidence,
                               source_type=first.source_type, source_url=first.source_url,
                               method="rule (derived)", evidence="built from " + " and ".join(how["from"]))
                sv.derived_from = list(how["from"])
                values[key] = sv


def _locate(cands_by_result: list[tuple[dict, list[Candidate]]], record: dict, cfg: Config, shared: bool):
    """For shared websites: prefer candidates from pages that show THIS record's street address.
    The address comes from the JSON; if the JSON has none, from the record's Facebook / Asset Map page.
    Returns ([(result, preferred candidates, all candidates)], located?)."""
    if not shared:
        return [(res, cands, cands) for res, cands in cands_by_result], True
    ref_addrs: list[str] = []
    for f in cfg.fields:
        if f.type == "address" and not cfg.is_missing(record.get(f.key)):
            ref_addrs.append(str(record.get(f.key)))
            break
    if not ref_addrs:
        for res, cands in cands_by_result:
            if res["kind"] != M.OWN_SITE:
                ref_addrs.extend(c.value for c in cands if c.field == "@address")
    out, located = [], False
    for res, cands in cands_by_result:
        if res["kind"] != M.OWN_SITE or not ref_addrs:
            out.append((res, cands, cands))
            continue
        loc_pages = {c.source_url for c in cands if c.field == "@address"
                     and any(addresses_match(a, c.value) for a in ref_addrs)}
        if loc_pages:
            located = True
            out.append((res, [c for c in cands if c.source_url in loc_pages or c.field == "@website"], cands))
        else:
            out.append((res, cands, cands))
    return out, located


def build_view(record: dict, cfg: Config, results: list[dict], shared: bool = False) -> RecordView:
    view = RecordView()
    results = sorted(results, key=lambda r: cfg.priority_rank(SOURCE_KEY.get(r["kind"], "")))
    ok = [r for r in results if r["status"] == "ok"]
    bad = [r for r in results if r["status"] != "ok"]

    for r in results:
        label = KIND_LABEL.get(r["kind"], r["kind"])
        if r["kind"] == "none":
            continue                      # "no source at all" is explained by all_failed_reason
        if r["status"] == "ok":
            view.sources_used.append(f"{r['kind']}: {r['url']} ({r['pages_crawled']} page(s))")
            view.pages_crawled += r["pages_crawled"]
            view.cap_hit = view.cap_hit or bool(r.get("cap_hit"))
        else:
            view.notes.append(f"{label} could not be used: {r['reason']}")
        view.notes.extend(r.get("notes", []))

    if bad and bad[0]["kind"] != "none":
        view.primary_type, view.primary_url = bad[0]["kind"], bad[0]["url"]
    if ok:
        view.primary_type, view.primary_url = ok[0]["kind"], ok[0]["url"]
    if not ok:
        view.all_failed_reason = (bad[0]["reason"] if bad
                                  else "no usable website, Facebook page or Asset Map page is listed for this record")
        return view

    if bad and cfg.priority_rank(SOURCE_KEY.get(bad[0]["kind"], "")) < cfg.priority_rank(SOURCE_KEY.get(ok[0]["kind"], "")):
        view.limited_note = (f"limited source: {KIND_LABEL.get(bad[0]['kind'], bad[0]['kind'])} could not be used "
                             f"({bad[0]['reason']}), so only {KIND_LABEL.get(ok[0]['kind'], ok[0]['kind'])} was checked")
    if view.cap_hit:
        view.cap_note = "the crawl stopped at the page limit, so the information may be on a page that was not read"

    per_source = [(r, [Candidate.from_dict(d) for d in r["candidates"]]) for r in ok]
    per_source, located = _locate(per_source, record, cfg, shared)
    if shared:
        view.notes.append("shared website: multiple locations" + ("" if located else
                          " (this record's street address was not found on any page, so values may belong to another location)"))
        view.unlocated = not located
    for r in ok:
        for field_key, kind, text, url in r.get("issues", []):
            view.issues.setdefault(field_key, []).append(f"{kind}: {text}")

    # ---- fields ----
    addr_sv_by_source: dict[int, Optional[SiteValue]] = {}
    for spec in cfg.fields:
        if not spec.checkable or spec.derived:
            continue
        found = []   # [(result, SiteValue)] in priority order
        for idx, (res, cands, all_cands) in enumerate(per_source):
            if spec.address_part:
                if idx not in addr_sv_by_source:
                    asp = next((f for f in cfg.fields if f.type == "address"), None)
                    addr_sv_by_source[idx] = merge_field(asp, cands, cfg) if asp else None
                sv = _address_part(spec, addr_sv_by_source[idx])
            else:
                sv = merge_field(spec, cands, cfg)
            if (sv is None or not (sv.present or sv.alt_values)) and all_cands is not cands:
                # Nothing for this field on this record's own location page: fall back to the rest of the shared
                # website, but remember that the value may belong to another location.
                sv = merge_field(spec, all_cands, cfg)
                if sv is not None:
                    sv.unlocated = True
                    sv.notes.append("shared website: this value is from a page that does not show this record's address")
            if sv is not None and sv.present:
                found.append((res, sv))
            elif sv is not None and sv.alt_values:
                found.append((res, sv))
        if not found:
            continue
        winner_res, winner = found[0]
        # a source with real values beats one that only has "other role" numbers
        for res, sv in found:
            if sv.values:
                winner_res, winner = res, sv
                break
        for res, sv in found:
            if sv is winner or not sv.best:
                continue
            same = compare_typed(("state" if spec.address_part == "state" else spec.type), winner.best, [sv.best],
                                 default_day=spec.default_day,
                                 url_kind=spec.url_kind, time_format=cfg.time_format)
            if winner.best and same.status != M.CORRECT:
                winner.notes.append(f"{KIND_LABEL.get(res['kind'], res['kind'])} says: {sv.best}")
        view.values[spec.key] = winner
    _derive(cfg, view.values)
    return view
