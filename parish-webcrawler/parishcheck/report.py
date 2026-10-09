"""Comparing each record to what was found, and writing the results workbook.

compare_record()   -> one row per field with a status (CORRECT, DISCREPANCY, ...)
write_excel()      -> output/Parish Check Results.xlsx  (the only output file)
"""

from __future__ import annotations

import logging
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path

from openpyxl import Workbook
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from . import models as M
from .comparators import StatusResult, compare_typed, decide_status
from .config import Config, FieldSpec
from .merge import RecordView
from .normalizers import is_missing

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


# --------------------------------------------------------------------------------------
# The results workbook
# --------------------------------------------------------------------------------------
RESULTS_NAME = "Parish Check Results"

# status -> (plain-English label, fill colour, what it means)
STATUS_STYLE = {
    M.DISCREPANCY:   ("Different on website", "F8D7D5", "Your data and the website both have a value, and they differ."),
    M.PARTIAL:       ("Partly matches", "FCE8C3", "Some items match and some don't. The notes list which."),
    M.SITE_ONLY:     ("Missing from your data", "D6E6FA", "The website has it, but your data is blank. 'Website says' is the value to add."),
    M.JSON_ONLY:     ("Not found on website", "E8DEF5", "Your data has it, but the website doesn't mention it. It may be out of date."),
    M.UNCLEAR:       ("Couldn't tell", "E7E7E7", "The tool could not decide. The notes say why."),
    M.CORRECT:       ("Matches", "DCEFD9", "Your data matches the website."),
    M.BOTH_MISSING:  ("Missing in both", "FFFFFF", "Neither your data nor the website has it."),
    M.NOT_CHECKABLE: ("Not checked", "FFFFFF", "A field this tool skips (id, lat, lon, notes...)."),
}

_HEADER_FILL = PatternFill("solid", fgColor="2F3E4E")
_HEADER_FONT = Font(bold=True, color="FFFFFF")
_WRAP = Alignment(wrap_text=True, vertical="top")
_GROUP_TOP = Border(top=Side(style="medium", color="7F8C99"))
_GREY = Font(color="9AA3AD")
_BOLD = Font(bold=True)
_LINK = Font(color="1F5FBF", underline="single")


def _label(field_key: str) -> str:
    return field_key.replace("_", " ").strip().capitalize()


def _clean(v) -> str:
    v = ILLEGAL_CHARACTERS_RE.sub("", _fmt(v))
    return v[:32000]


def _set(ws, row: int, col: int, value, font=None, fill=None):
    c = ws.cell(row=row, column=col)
    text = _clean(value)
    c.value = text
    if text.startswith("="):
        c.data_type = "s"           # never let Excel treat site text as a formula
    c.alignment = _WRAP
    if font is not None:
        c.font = font
    if fill is not None:
        c.fill = fill
    return c


def _link(ws, row: int, col: int, url: str, fill=None):
    c = _set(ws, row, col, url, fill=fill)
    if url.startswith(("http://", "https://")):
        c.hyperlink = url
        c.font = _LINK
    return c


def _sheet(wb, title: str, headers: list[str], widths: list[int]):
    ws = wb.create_sheet(title)
    for i, (h, w) in enumerate(zip(headers, widths), start=1):
        c = ws.cell(row=1, column=i, value=h)
        c.fill, c.font = _HEADER_FILL, _HEADER_FONT
        c.alignment = Alignment(vertical="center")
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.freeze_panes = "B2"
    ws.row_dimensions[1].height = 22
    return ws


def _notes(r: Row) -> str:
    return "; ".join(x for x in (r.reason, r.detail) if x)


def _check_rows(ws, rows: list[Row], extra) -> None:
    """Rows grouped by parish: the first row of each parish is bold with a line above it."""
    prev = None
    for n, r in enumerate(rows, start=2):
        label, colour, _ = STATUS_STYLE.get(r.status, (r.status, "FFFFFF", ""))
        fill = PatternFill("solid", fgColor=colour)
        first = (r.id != prev)
        prev = r.id
        _set(ws, n, 1, r.name or r.id, font=_BOLD if first else _GREY)
        _set(ws, n, 2, _label(r.field))
        _set(ws, n, 3, label, fill=fill, font=_BOLD)
        _set(ws, n, 4, r.json_value)
        _set(ws, n, 5, r.site_value)
        _set(ws, n, 6, _notes(r))
        _link(ws, n, 7, r.source_url)
        for i, v in enumerate(extra(r), start=8):
            _set(ws, n, i, v)
        if first and n > 2:
            for col in range(1, ws.max_column + 1):
                ws.cell(row=n, column=col).border = _GROUP_TOP
    if rows:
        ws.auto_filter.ref = f"A1:{get_column_letter(ws.max_column)}{len(rows) + 1}"


def _by_parish(rows: list[Row]) -> list[Row]:
    order = {st: i for i, st in enumerate(STATUS_STYLE)}
    return sorted(rows, key=lambda r: ((r.name or "").casefold(), str(r.id), order.get(r.status, 99)))


def write_excel(cfg: Config, rows: list[Row], summary: list[dict]) -> Path:
    """Write output/Parish Check Results.xlsx and return its path."""
    wb = Workbook()
    wb.remove(wb.active)
    cols = ["Parish", "Field", "Problem", "Your data", "Website says", "Notes", "Source page"]
    widths = [30, 18, 22, 40, 40, 45, 35]

    review = _by_parish([r for r in rows if r.status in M.REVIEW_STATUSES])
    ws = _sheet(wb, "To review", cols, widths)
    _check_rows(ws, review, lambda r: [])

    ws = _sheet(wb, "Summary", ["Parish", "Website", "To review", "Matches", "Couldn't tell", "Pages read", "Notes"],
                [34, 35, 11, 10, 13, 11, 70])
    review_fill = PatternFill("solid", fgColor=STATUS_STYLE[M.DISCREPANCY][1])
    ok_fill = PatternFill("solid", fgColor=STATUS_STYLE[M.CORRECT][1])
    for n, s in enumerate(sorted(summary, key=lambda s: (s["name"] or "").casefold()), start=2):
        c = s["counts"]
        to_review = sum(c.get(st, 0) for st in M.REVIEW_STATUSES)
        notes = s["notes"]
        if s.get("pdfs"):
            notes = (notes + " ; " if notes else "") + "PDFs found (not read): " + ", ".join(s["pdfs"][:10])
        _set(ws, n, 1, s["name"] or s["id"], font=_BOLD)
        _link(ws, n, 2, s.get("website", ""))
        ws.cell(row=n, column=3, value=to_review).fill = review_fill if to_review else ok_fill
        ws.cell(row=n, column=4, value=c.get(M.CORRECT, 0))
        ws.cell(row=n, column=5, value=c.get(M.UNCLEAR, 0))
        ws.cell(row=n, column=6, value=s["pages_crawled"])
        _set(ws, n, 7, notes)
    if summary:
        ws.auto_filter.ref = f"A1:G{len(summary) + 1}"

    ws = _sheet(wb, "All checks", cols[:2] + ["Result"] + cols[3:] + ["Found by", "Confidence", "Evidence (words on the page)"],
                widths + [10, 12, 60])
    _check_rows(ws, _by_parish(rows), lambda r: [r.method, r.confidence, r.evidence])

    ws = _sheet(wb, "Key", ["Label", "What it means"], [26, 90])
    ws.freeze_panes = "A2"
    for n, (label, colour, meaning) in enumerate(STATUS_STYLE.values(), start=2):
        _set(ws, n, 1, label, font=_BOLD, fill=PatternFill("solid", fgColor=colour))
        _set(ws, n, 2, meaning)
    n = len(STATUS_STYLE) + 3
    for line in ("'To review' lists only the rows that need a look. 'All checks' has every field of every parish.",
                 "Your JSON file was not changed. Edit it yourself using 'Website says' and the source page link.",
                 "The AI makes mistakes: treat this as a to-do list, and check the source page before changing anything."):
        _set(ws, n, 1, line)
        ws.merge_cells(start_row=n, start_column=1, end_row=n, end_column=2)
        n += 1

    cfg.output_dir.mkdir(parents=True, exist_ok=True)
    path = cfg.output_dir / f"{RESULTS_NAME}.xlsx"
    try:
        wb.save(path)
    except PermissionError:             # the old results are open in Excel
        path = cfg.output_dir / f"{RESULTS_NAME} (new).xlsx"
        wb.save(path)
    return path
