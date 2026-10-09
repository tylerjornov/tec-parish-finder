"""A compact, numbered digest of a whole site for ONE group of fields.

Instead of sending the model big text windows from each page separately (one call per page), we:
  * split every page into short lines,
  * keep only the lines that mention the group's keywords (plus a line of context on each side), densest first,
  * drop lines already shown (menus, footers and repeated blocks appear only once),
  * number the lines, so the model answers with line numbers instead of copying quotes.

One model call per group per site, a much shorter prompt, and a much shorter answer. The line numbers
also make the evidence check exact: the cited lines ARE the evidence.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urlsplit

from .cleaner import PageDoc
from .normalizers import collapse_ws, norm_for_quote_check

MAX_LINE_CHARS = 280
_SECTION_MARKERS = {"[header and footer text]", "[links found on this page]"}


@dataclass
class DigestLine:
    n: int              # number shown to the model (1, 2, 3 ...)
    text: str
    page: int           # index into Digest.pages


@dataclass
class Digest:
    pages: list = field(default_factory=list)         # list[PageDoc], in the order they are shown
    ranks: list = field(default_factory=list)         # page_rank of each page
    lines: list = field(default_factory=list)         # list[DigestLine]
    text: str = ""                                    # what the model sees

    def line(self, n: int) -> Optional[DigestLine]:
        return self.lines[n - 1] if isinstance(n, int) and 1 <= n <= len(self.lines) else None

    def neighbours(self, n: int) -> list[DigestLine]:
        """The cited line plus the lines right before and after it on the same page (headings, labels)."""
        here = self.line(n)
        if here is None:
            return []
        return [ln for ln in (self.line(n - 1), here, self.line(n + 1)) if ln is not None and ln.page == here.page]


def page_lines(doc: PageDoc) -> list[str]:
    """The page text as short lines. Long paragraphs are cut at sentence ends so each line stays small."""
    out: list[str] = []
    for raw in (doc.text or "").split("\n"):
        line = collapse_ws(raw)
        if not line or line.lower() in _SECTION_MARKERS:
            continue
        while len(line) > MAX_LINE_CHARS:
            cut = max(line.rfind(". ", 0, MAX_LINE_CHARS), line.rfind("; ", 0, MAX_LINE_CHARS))
            if cut < MAX_LINE_CHARS // 3:
                cut = line.rfind(" ", 0, MAX_LINE_CHARS)
            if cut <= 0:
                cut = MAX_LINE_CHARS
            out.append(line[:cut + 1].strip())
            line = line[cut + 1:].strip()
        if line:
            out.append(line)
    return out


def build_digest(ranked_docs: list[tuple[int, PageDoc]], rx: Optional[re.Pattern], budget: int,
                 context: int = 1, notes: Optional[dict] = None, lines_of=page_lines) -> Digest:
    """ranked_docs: [(page_rank, doc)] best page first.  Every line with a keyword hit (and `context` lines on
    each side) is a candidate.  The densest lines win (most different keywords per character, better pages
    first) until `budget` characters are used; they are then shown in page order.  A line is never repeated."""
    # ---- 1. score every candidate line ----
    cands: dict[str, tuple[float, int, int, str]] = {}      # norm key -> (score, page order, line index, text)
    per_page: list[list[str]] = []
    for order, (rank, doc) in enumerate(ranked_docs):
        lines = lines_of(doc)
        per_page.append(lines)
        for i, ln in enumerate(lines):
            found = {m.group(0).lower() for m in rx.finditer(ln)} if rx is not None else set()
            if not found:
                continue
            # distinct keywords, a little extra for short (list-like) lines, and better pages first
            score = len(found) + (1.0 if len(ln) <= 120 else 0.0) - 0.15 * order
            for j in range(max(0, i - context), min(len(lines), i + context + 1)):
                key = norm_for_quote_check(lines[j])
                if not key:
                    continue
                sc = score if j == i else score - 0.5
                if key not in cands or sc > cands[key][0]:
                    cands[key] = (sc, order, j, lines[j])
    # ---- 2. take the best lines that fit the budget ----
    picked: list[tuple[int, int, str]] = []
    used = 0
    for sc, order, j, text in sorted(cands.values(), key=lambda c: (-c[0], c[1], c[2])):
        if used + len(text) > budget:
            continue
        used += len(text) + 6
        picked.append((order, j, text))
    picked.sort()
    # ---- 3. number them, page by page ----
    dg = Digest()
    out_parts: list[str] = []
    for order, (rank, doc) in enumerate(ranked_docs):
        mine = [(j, t) for o, j, t in picked if o == order]
        if not mine:
            continue
        page_idx = len(dg.pages)
        dg.pages.append(doc)
        dg.ranks.append(rank)
        block: list[str] = []
        prev = None
        for j, text in mine:
            if prev is not None and j != prev + 1:
                block.append("  ...")
            prev = j
            n = len(dg.lines) + 1
            dg.lines.append(DigestLine(n, text, page_idx))
            block.append(f"{n}: {text}")
        url = doc.final_url or doc.url
        label = f"=== Page P{page_idx + 1}: {_path(url)}"
        if doc.title:
            label += f'  "{doc.title[:80]}"'
        if (notes or {}).get(id(doc)):
            label += "  (Facebook posts: may be old)"
        out_parts.append(label + " ===\n" + "\n".join(block))
    dg.text = "\n\n".join(out_parts)
    return dg


def _path(url: str) -> str:
    try:
        p = urlsplit(url)
        return (p.netloc + (p.path or "/"))[:90]
    except ValueError:
        return url[:90]
