"""Rule-based extraction: facts that can be found WITHOUT the AI.

Phones, emails, livestream links, street addresses (and JSON-LD data) are found with patterns and link
detection.  Phone numbers and emails are labelled "rector" or "church" using the words right next to them.
"""

from __future__ import annotations

import re
from typing import Optional
from urllib.parse import unquote, urlsplit

from . import models as M
from .cleaner import PageDoc, find_jsonld_orgs
from .models import Candidate
from .normalizers import (
    PHONE_TEXT_RE, STATE_ABBR, collapse_ws, extract_emails, format_phone, host_of, norm_email, normalize_url,
    norm_state, phone_digits,
)
from .urls import domain_matches

# --------------------------------------------------------------------------------------
# Which kind of page is it?  (lower rank number = more likely to hold the answer)
# --------------------------------------------------------------------------------------
CONTACT_HINTS = ["contact", "visit", "location", "directions", "about", "staff", "clergy"]


def page_rank(url: str, title: str, hints: list[str]) -> int:
    """Index of the first hint found in the address or page title; the home page counts as middling."""
    path = urlsplit(url).path.lower()
    low_title = (title or "").lower()
    for i, h in enumerate(hints):
        if h in path or h in low_title:
            return i
    if path in ("", "/"):
        return min(len(hints), 3)
    return len(hints) + 5


# --------------------------------------------------------------------------------------
# Labelling a phone number / email as the rector's or the church's
# --------------------------------------------------------------------------------------
_RECTOR_WORDS = re.compile(r"\b(?:rector|vicar|priest|dean|father|fr|rev|revd|reverend|canon|pastor|clergy|"
                           r"mother|deacon|priest[- ]in[- ]charge)\b\.?", re.I)
_CHURCH_WORDS = re.compile(r"\b(?:office|church|parish|front desk|reception|secretary|administrator|admin|"
                           r"general|information|info)\b", re.I)
# Generic words ("Phone:", "Call us") only tell us the number is the church's when nothing better is nearby.
_WEAK_WORDS = re.compile(r"\b(?:phone|telephone|tel|call us|call|contact us|main)\b", re.I)
_RECTOR_LOCAL = re.compile(r"rector|vicar|priest|father|dean|pastor|revd|reverend|\bfr\b|^rev|padre", re.I)
_CHURCH_LOCAL = re.compile(r"office|info|admin|parish|secretary|hello|welcome|contact|church|frontdesk", re.I)


def label_role(text: str, start: int, end: int) -> Optional[str]:
    """Look at the line the number is on (and the line before) and pick the NEAREST label word."""
    line_start = text.rfind("\n", 0, start) + 1
    prev_start = text.rfind("\n", 0, max(line_start - 1, 0)) + 1
    line_end = text.find("\n", end)
    line_end = len(text) if line_end == -1 else line_end
    window_start = max(prev_start, start - 160)
    window = text[window_start:line_end]
    s_rel, e_rel = start - window_start, end - window_start

    def nearest(rx: re.Pattern) -> int:
        best = 10 ** 6
        for m in rx.finditer(window):
            if m.end() <= s_rel:
                dist = s_rel - m.end()
                if "\n" in window[m.end():s_rel]:
                    dist += 40                      # a label on the previous line is weaker
            elif m.start() >= e_rel:
                dist = m.start() - e_rel + 15       # words AFTER the number count a little less
            else:
                dist = 0
            best = min(best, dist)
        return best

    d_rector, d_church = nearest(_RECTOR_WORDS), nearest(_CHURCH_WORDS)
    if min(d_rector, d_church) <= 140:
        return "rector" if d_rector < d_church else "church"
    if nearest(_WEAK_WORDS) <= 40:
        return "church"
    return None


def label_email_role(email: str, text: str, start: int, end: int) -> Optional[str]:
    local = email.split("@")[0]
    if _RECTOR_LOCAL.search(local):
        return "rector"
    if _CHURCH_LOCAL.search(local):
        return "church"
    return label_role(text, start, end)


def _snippet(text: str, start: int, end: int, pad: int = 70) -> str:
    return collapse_ws(text[max(0, start - pad): end + pad])


# --------------------------------------------------------------------------------------
# Street addresses
# --------------------------------------------------------------------------------------
_SUFFIX = (r"(?i:street|st|avenue|ave|av|road|rd|drive|dr|boulevard|blvd|lane|ln|highway|hwy|way|court|ct|"
           r"place|pl|circle|cir|parkway|pkwy|square|sq|terrace|ter|trail|trl|pike|route|rte|plaza|row|loop|alley)")
_STATE_NAMES = "|".join(sorted((n.title() for n in STATE_ABBR), key=len, reverse=True))
ADDRESS_RE = re.compile(
    r"(?<![\w/#])(?P<num>\d{1,6}[A-Za-z]?)\s+"
    r"(?P<street>(?:(?:N|S|E|W|NE|NW|SE|SW|North|South|East|West)\.?\s+)?"
    r"(?:[A-Z0-9][\w'’.\-]*\s+){0,4}?" + _SUFFIX + r"\b\.?(?:\s+(?:NE|NW|SE|SW|N|S|E|W)\b\.?)?)"
    r"(?:\s*,?\s*(?i:suite|ste|unit|apt|room|rm|floor|fl|bldg)\.?\s*[\w-]+)?"
    r"\s*(?:[,\n|•·]\s*|\s+)"
    r"(?P<city>[A-Z][a-z][A-Za-z.'’\-]*(?:\s+[A-Z][a-z][A-Za-z.'’\-]*){0,3})"
    r"[\s,]+(?P<state>[A-Z]{2}|" + _STATE_NAMES + r")\b"
    r"(?:[\s,]*(?P<zip>\d{5}(?:-\d{4})?))?"
)


def find_addresses(text: str) -> list[tuple[str, str]]:
    """[(address in house style 'street, City, ST', matched text)]"""
    out = []
    seen = set()
    for m in ADDRESS_RE.finditer(text or ""):
        state = norm_state(m.group("state"))
        if not state:
            continue
        street = collapse_ws(f"{m.group('num')} {m.group('street')}")
        city = collapse_ws(m.group("city"))
        formatted = f"{street}, {city}, {state}"
        key = formatted.lower()
        if key not in seen:
            seen.add(key)
            out.append((formatted, collapse_ws(m.group(0))))
    return out


# --------------------------------------------------------------------------------------
# Livestream links
# --------------------------------------------------------------------------------------
_LIVE_WORDS = re.compile(r"live|stream|watch|online|broadcast|worship|service|video|sermon", re.I)
_STRONG_HOSTS = ("boxcast.com", "online.church", "livestream.com", "restream.io", "resi.io", "churchstreaming.tv",
                 "subsplash.com")
_WEAK_HOSTS = ("youtube.com", "youtu.be", "vimeo.com")          # a channel link in the footer is a hint, not proof
_NEEDS_LABEL_HOSTS = ("facebook.com", "fb.watch", "zoom.us")


def livestream_candidates(doc: PageDoc, livestream_hosts: list[str]) -> list[tuple[str, str, str]]:
    """[(url, 'high'|'low', evidence)] from the links on one page."""
    out = []
    seen = set()
    for href, text in doc.links:
        if href.lower().startswith(("tel:", "mailto:", "sms:")):
            continue
        host = host_of(href)
        if not host or not any(domain_matches(host, d) for d in livestream_hosts):
            continue
        norm = normalize_url(href)
        if not norm or norm in seen:
            continue
        path = urlsplit(norm).path.lower()
        if domain_matches(host, "facebook.com") and (path in ("", "/") or path.count("/") <= 1) \
                and not _LIVE_WORDS.search(path + " " + text):
            continue
        labelled = bool(_LIVE_WORDS.search(unquote(path) + " " + (text or "")))
        if any(domain_matches(host, d) for d in _STRONG_HOSTS):
            conf = "high"
        elif any(domain_matches(host, d) for d in _NEEDS_LABEL_HOSTS):
            if not labelled:
                continue
            conf = "high"
        elif any(domain_matches(host, d) for d in _WEAK_HOSTS):
            conf = "high" if labelled else "low"
        else:
            conf = "high" if labelled else "low"
        seen.add(norm)
        out.append((norm, conf, f'link text "{text}" -> {norm}' if text else f"link -> {norm}"))
    return out


# --------------------------------------------------------------------------------------
# Everything for one page
# --------------------------------------------------------------------------------------
def _jsonld_values(blocks: list[dict]) -> dict:
    """Pull phones, emails, addresses and sameAs links out of JSON-LD organization blocks."""
    found = {"phones": [], "emails": [], "addresses": [], "same_as": [], "urls": [], "names": []}

    def add_addr(a):
        if isinstance(a, str):
            found["addresses"].append(a)
        elif isinstance(a, dict):
            street = a.get("streetAddress") or ""
            city = a.get("addressLocality") or ""
            region = a.get("addressRegion") or ""
            if street and (city or region):
                state = norm_state(str(region)) or str(region)
                found["addresses"].append(", ".join(x for x in (collapse_ws(str(street)), collapse_ws(str(city)), state) if x))
        elif isinstance(a, list):
            for x in a:
                add_addr(x)

    for org in find_jsonld_orgs(blocks):
        if org.get("telephone"):
            found["phones"].append(str(org["telephone"]))
        if org.get("email"):
            found["emails"].append(str(org["email"]))
        if org.get("name"):
            found["names"].append(str(org["name"]))
        if org.get("url"):
            found["urls"].append(str(org["url"]))
        add_addr(org.get("address"))
        sa = org.get("sameAs")
        if isinstance(sa, str):
            sa = [sa]
        found["same_as"].extend([str(x) for x in (sa or [])])
        cps = org.get("contactPoint")
        for cp in (cps if isinstance(cps, list) else [cps] if cps else []):
            if isinstance(cp, dict):
                if cp.get("telephone"):
                    found["phones"].append(str(cp["telephone"]))
                if cp.get("email"):
                    found["emails"].append(str(cp["email"]))
    return found


def extract_rule_candidates(doc: PageDoc, livestream_hosts: list[str], source_type: str = M.OWN_SITE) -> list[Candidate]:
    """All rule-based candidates from one cleaned page."""
    url = doc.final_url or doc.url
    text = doc.text
    contact_rank = page_rank(url, doc.title, CONTACT_HINTS)
    cands: list[Candidate] = []

    def mk(field: str, value: str, evidence: str, role: Optional[str] = None, conf: str = "high",
           in_footer: bool = False) -> Candidate:
        return Candidate(field=field, value=value, source_type=source_type, source_url=url, method="rule",
                         confidence=conf, evidence=evidence[:300], role=role, page_rank=contact_rank,
                         in_footer=in_footer)

    # ---- phones in running text ----
    seen_phone: set[tuple[str, Optional[str]]] = set()
    for m in PHONE_TEXT_RE.finditer(text):
        d = phone_digits(m.group(0))
        if not d:
            continue
        before = text[text.rfind("\n", 0, m.start()) + 1: m.start()]
        if re.search(r"\bfax\b[^\n]{0,25}$", before, re.I):   # a fax number is not a phone number
            continue
        role = label_role(text, m.start(), m.end())
        key = (d, role)
        if key in seen_phone:
            continue
        seen_phone.add(key)
        cands.append(mk("@phone", format_phone(d), _snippet(text, m.start(), m.end()), role,
                        in_footer=m.group(0) in doc.hf_text))
    # ---- phones from tel: links (these work even when the number has no spaces or dashes) ----
    for href, link_text in doc.links:
        if not href.lower().startswith("tel:"):
            continue
        d = phone_digits(unquote(href[4:]))
        if not d:
            continue
        role = None
        if link_text:
            role = "rector" if _RECTOR_WORDS.search(link_text) else "church" if _CHURCH_WORDS.search(link_text) else None
        if role is None:                     # judge by the same number written out elsewhere on the page
            for m in PHONE_TEXT_RE.finditer(text):
                if phone_digits(m.group(0)) == d:
                    role = label_role(text, m.start(), m.end())
                    break
        if (d, role) not in seen_phone:
            seen_phone.add((d, role))
            cands.append(mk("@phone", format_phone(d), f"tel: link {unquote(href[4:])} ({link_text})", role))

    # ---- emails in text and mailto: links ----
    seen_email: set[tuple[str, Optional[str]]] = set()
    for m in re.finditer(r"(?<![\w.+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,24}(?![\w-])", text):
        found = extract_emails(m.group(0))
        if not found:
            continue
        e = found[0]
        role = label_email_role(e, text, m.start(), m.end())
        if (e, role) in seen_email:
            continue
        seen_email.add((e, role))
        cands.append(mk("@email", e, _snippet(text, m.start(), m.end()), role, in_footer=e in doc.hf_text.lower()))

    # ---- livestream ----
    for u, conf, ev in livestream_candidates(doc, livestream_hosts):
        cands.append(mk("@livestream", u, ev, conf=conf))

    # ---- street addresses in text ----
    for formatted, ev in find_addresses(text):
        cands.append(mk("@address", formatted, ev, in_footer=ev in doc.hf_text))

    # ---- JSON-LD ----
    if doc.jsonld:
        j = _jsonld_values(doc.jsonld)
        for ph in j["phones"]:
            d = phone_digits(ph)
            if d:
                cands.append(mk("@phone", format_phone(d), "JSON-LD telephone: " + ph, "church"))
        for em in j["emails"]:
            for e in extract_emails(em) or [norm_email(em)]:
                if "@" in e:
                    cands.append(mk("@email", e, "JSON-LD email: " + em, "church"))
        for a in j["addresses"]:
            fa = find_addresses(a.replace("\n", ", "))
            value = fa[0][0] if fa else collapse_ws(a)
            if not re.match(r"^\s*\d+", value):        # "Greenville, SC 29601 USA" has no street: not usable
                continue
            cands.append(mk("@address", value, "JSON-LD address: " + a))
        for name in j["names"][:1]:
            cands.append(mk("@jsonld_name", collapse_ws(name), "JSON-LD name: " + name))
        for sa in j["same_as"]:
            h = host_of(sa)
            if any(domain_matches(h, d) for d in livestream_hosts) and ("live" in sa.lower() or "youtube" in h):
                cands.append(mk("@livestream", normalize_url(sa) or sa, "JSON-LD sameAs: " + sa, conf="low"))
    return cands
