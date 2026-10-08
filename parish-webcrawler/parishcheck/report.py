"""Comparing each record to what was found, and writing the output files.

compare_record()   -> one row per field with a status (CORRECT, DISCREPANCY, ...)
write_*()          -> verification_report.csv, needs_review.csv, verification_summary.csv,
                      proposed_updates.json, results_raw.json, pdfs_found.txt
"""

from __future__ import annotations

import copy
import csv
import json
import logging
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Optional

from . import models as M
from .comparators import StatusResult, compare_typed, decide_status
from .config import Config, FieldSpec
from .merge import KIND_LABEL, RecordView
from .normalizers import collapse_ws, derive_rite, is_missing

log = logging.getLogger("parishcheck")

REPORT_COLUMNS = ["id", "name", "field", "status", "json_value", "site_value", "detail", "reason", "method",
                  "source_type", "source_url", "confidence", "evidence"]


@dataclass
class Row:
    id: str
    name: str
    field: str
    status: str
    json_value: str = ""
    site_value: str = ""
    detail: str = ""
    reason: str = ""
    method: str = ""
    source_type: str = ""
    source_url: str = ""
    confidence: str = ""
    evidence: str = ""

    def as_list(self) -> list:
        d = asdict(self)
        return [d[c] for c in REPORT_COLUMNS]


def _fmt(v) -> str:
    if v is None:
        return ""
    if isinstance(v, (list, tuple)):
        return "; ".join(str(x) for x in v)
    return str(v)


def _ftype(spec: FieldSpec, cfg: Config) -> str:
    if spec.address_part == "state":
        return "state"
    d = (cfg.derived_fields or {}).get(spec.key) or {}
    if "from" in d:
        return "contactlist"
    return spec.type


def compare_record(record: dict, cfg: Config, view: RecordView, rid: str, name: str) -> list[Row]:
    """One row per field. A problem inside one field becomes an UNCLEAR row; it never stops the run."""
    rows: list[Row] = []
    for spec in cfg.fields:
        try:
            rows.append(_compare_field(record, cfg, view, rid, name, spec))
        except Exception as exc:                # pragma: no cover - safety net
            log.exception("Could not compare %s for %s", spec.key, rid)
            rows.append(Row(rid, name, spec.key, M.UNCLEAR, _fmt(record.get(spec.key)), "", "",
                            f"internal problem while comparing this field ({type(exc).__name__}: {str(exc)[:100]}); see run.log"))
    # keys in uncheckable_fields that are not in the fields table still get a row
    listed = {s.key for s in cfg.fields}
    for key in cfg.uncheckable_fields:
        if key not in listed and key in record:
            rows.append(Row(rid, name, key, M.NOT_CHECKABLE, _fmt(record.get(key)), "", "listed under uncheckable_fields; passed through"))
    return rows


def _compare_field(record: dict, cfg: Config, view: RecordView, rid: str, name: str, spec: FieldSpec) -> Row:
    key = spec.key
    json_value = record.get(key)
    if not spec.checkable:
        return Row(rid, name, key, M.NOT_CHECKABLE, _fmt(json_value), "", "listed under uncheckable_fields; passed through")
    sv = view.values.get(key)
    ftype = _ftype(spec, cfg)
    sep = ((cfg.derived_fields or {}).get(key) or {}).get("separator", ";")
    unclear = ""
    if view.all_failed_reason:
        unclear = view.all_failed_reason
    elif (sv is None or not sv.values) and spec.type == "url" and not spec.url_kind:
        unclear = "this kind of web-address field is not collected automatically"
    elif (sv is None or not sv.values) and view.issues.get(key):
        unclear = _issue_reason(view.issues[key])
    site_values = list(sv.values) if sv else []
    st: StatusResult = decide_status(
        ftype, json_value, site_values, missing_markers=cfg.missing_markers,
        site_confidence=(sv.confidence if sv else "high"), unclear_reason=unclear,
        json_only_note="; ".join(n for n in (view.limited_note, view.cap_note) if n), default_day=spec.default_day, url_kind=spec.url_kind,
        time_format=cfg.time_format, list_sep=sep,
    )
    status, reason, detail_bits = st.status, st.reason, []
    if st.detail:
        detail_bits.append(st.detail)
    if status in (M.DISCREPANCY, M.PARTIAL) or st.json_only or st.site_only:
        if st.json_only:
            detail_bits.append("json_only_items: " + _fmt(st.json_only))
        if st.site_only:
            detail_bits.append("site_only_items: " + _fmt(st.site_only))

    # Phone/email that exist on the site but are labelled for the other role.
    if sv and sv.alt_values and status in (M.DISCREPANCY, M.JSON_ONLY) and not is_missing(json_value, cfg.missing_markers):
        if compare_typed(ftype, str(json_value), sv.alt_values).status == M.CORRECT:
            status, detail_bits = M.CORRECT, [f"found on the site, but labelled as the {'church' if spec.role == 'rector' else 'rector'}'s, not the {spec.role}'s"]

    if status in (M.DISCREPANCY, M.PARTIAL, M.SITE_ONLY) and sv:
        if sv.conflict:
            status, reason = M.UNCLEAR, "the site itself gives different answers: " + " / ".join(sv.values)
        elif view.unlocated or sv.unlocated:
            status, reason = M.UNCLEAR, ("shared website with several locations, and no page showing this record's "
                                         "street address gave this value, so it may belong to another location")
    if sv:
        detail_bits.extend(sv.notes)
    note = "; ".join(n for n in (view.limited_note, view.cap_note) if n)
    if status == M.JSON_ONLY and note and note not in detail_bits:
        detail_bits.append(note)
    elif status in (M.DISCREPANCY, M.PARTIAL, M.SITE_ONLY, M.UNCLEAR) and view.limited_note and view.limited_note not in detail_bits:
        detail_bits.append(view.limited_note)

    site_display = sv.best if sv else ""
    return Row(
        rid, name, key, status, _fmt(json_value), site_display,
        "; ".join(b for b in detail_bits if b), reason if status == M.UNCLEAR else "",
        (sv.method if sv else ""),
        _source_label(sv.source_type if sv and sv.source_type else view.primary_type),
        (sv.source_url if sv and sv.source_url else view.primary_url),
        (sv.confidence if sv else ""), (sv.evidence if sv else ""),
    )


def _source_label(kind: str) -> str:
    return {"own_site": M.OWN_SITE, M.OWN_SITE: M.OWN_SITE, "facebook": M.FACEBOOK, M.FACEBOOK: M.FACEBOOK,
            "asset_map": M.ASSET_MAP, M.ASSET_MAP: M.ASSET_MAP}.get(kind, kind or "")


def _issue_reason(issues: list[str]) -> str:
    if any(i.startswith("rejected_unverified") for i in issues):
        return ("the model gave an answer but it could not be verified in the page text, so I threw it away "
                f"({issues[0].split(': ', 1)[-1][:140]})")
    return "the model could not give a readable answer for this field"


# --------------------------------------------------------------------------------------
# Writing files
# --------------------------------------------------------------------------------------
def _write_csv(path: Path, header: list[str], rows: list[list]) -> None:
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:   # utf-8-sig so Excel/Numbers show accents correctly
        w = csv.writer(fh)
        w.writerow(header)
        w.writerows(rows)


def write_report_csvs(cfg: Config, rows: list[Row]) -> tuple[Path, Path]:
    out = cfg.output_dir
    out.mkdir(parents=True, exist_ok=True)
    p1 = out / "verification_report.csv"
    _write_csv(p1, REPORT_COLUMNS, [r.as_list() for r in rows])
    review = [r for r in rows if r.status in M.REVIEW_STATUSES]
    review.sort(key=lambda r: ((r.name or "").casefold(), str(r.id)))
    p2 = out / "needs_review.csv"
    _write_csv(p2, REPORT_COLUMNS, [r.as_list() for r in review])
    return p1, p2


def write_summary_csv(cfg: Config, summary: list[dict]) -> Path:
    header = ["id", "name", "sources_used", "pages_crawled", "page_cap_hit"] + M.ALL_STATUSES + ["notes"]
    body = []
    for s in summary:
        counts = s["counts"]
        body.append([s["id"], s["name"], s["sources_used"], s["pages_crawled"], "yes" if s["cap_hit"] else "no"]
                    + [counts.get(st, 0) for st in M.ALL_STATUSES] + [s["notes"]])
    p = cfg.output_dir / "verification_summary.csv"
    _write_csv(p, header, body)
    return p


def write_pdfs(cfg: Config, pdfs: dict[str, list[str]]) -> Path:
    p = cfg.output_dir / "pdfs_found.txt"
    lines = []
    for site, urls in pdfs.items():
        for u in urls:
            lines.append(f"{site}\t{u}")
    p.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    return p


def write_raw(cfg: Config, payload: dict) -> Path:
    p = cfg.output_dir / "results_raw.json"
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return p


# --------------------------------------------------------------------------------------
# proposed_updates.json  (same structure and key order as the input)
# --------------------------------------------------------------------------------------
def build_proposed(records: list[dict], rows_by_index: dict[int, list[Row]], cfg: Config, today: date,
                   apply_discrepancies: bool = False) -> tuple[list[dict], int]:
    """A copy of the input in which only SITE_ONLY fields are filled (and DISCREPANCY fields too, if asked).
    Returns (new records, number of changed records).  The input list is never modified."""
    out = copy.deepcopy(records)
    changed_records = 0
    allowed = {M.SITE_ONLY} | ({M.DISCREPANCY} if apply_discrepancies else set())
    derived = cfg.derived_fields or {}
    for idx, rec in enumerate(out):
        rows = rows_by_index.get(idx)
        if not rows:
            continue
        changed: set[str] = set()
        for r in rows:
            spec = cfg.get_field(r.field)
            if spec is None or r.status not in allowed or not r.site_value or r.reason:
                continue
            if rec.get(r.field) != r.site_value:
                rec[r.field] = r.site_value           # existing key keeps its position; a new key goes at the end
                changed.add(r.field)
        if not changed:
            continue
        # Recompute the derived fields when their inputs changed.
        for key, how in derived.items():
            how = how or {}
            if "from" in how and changed & set(how["from"]) and key not in changed:
                sep = how.get("separator", " | ")
                parts = [str(rec.get(k)) for k in how["from"] if not is_missing(rec.get(k), cfg.missing_markers)]
                new = sep.join(parts)
                if new and rec.get(key) != new:
                    rec[key] = new
            if "from_services" in how and changed & set(how["from_services"]) and key not in changed:
                r_ = derive_rite(*[str(rec.get(k) or "") for k in how["from_services"]
                                   if not is_missing(rec.get(k), cfg.missing_markers)])
                if r_ and rec.get(key) != r_:
                    rec[key] = r_
        if cfg.date_updated_key:
            rec[cfg.date_updated_key] = today.isoformat()
        changed_records += 1
    return out, changed_records


def write_proposed(cfg: Config, proposed: list[dict]) -> Path:
    p = cfg.output_dir / "proposed_updates.json"
    p.write_text(json.dumps(proposed, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def summarize_record(rid: str, name: str, view: RecordView, rows: list[Row]) -> dict:
    counts = Counter(r.status for r in rows)
    notes = list(dict.fromkeys(n for n in view.notes if n))
    for n in (view.limited_note, view.cap_note):
        if n:
            notes.append(n)
    if view.all_failed_reason:
        notes.insert(0, view.all_failed_reason)
    return {
        "id": rid, "name": name, "sources_used": " | ".join(view.sources_used) or "(none)",
        "pages_crawled": view.pages_crawled, "cap_hit": view.cap_hit, "counts": dict(counts),
        "notes": " ; ".join(notes)[:900],
    }
