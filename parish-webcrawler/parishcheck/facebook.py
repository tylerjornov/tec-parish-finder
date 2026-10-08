"""Facebook, logged out, best effort.

Rules this file follows:
  * NEVER log in.  NEVER solve a CAPTCHA.  NEVER click through or work around any wall or block.
  * A few pages per Facebook page (About/Info, then the main page for recent posts), 5 seconds apart.
  * The moment a login wall, CAPTCHA or block shows up, stop for that page and report
    "blocked by Facebook login wall" (the fields become UNCLEAR).
  * Results are cached, so a re-run does not ask Facebook again.
We only read the text that the page itself shows.  We do not click anything.
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Optional
from urllib.parse import parse_qs, unquote, urlsplit

from . import models as M
from .cleaner import PageDoc, clean_html
from .config import Config
from .fetcher import Fetcher, StopRequested
from .models import Candidate
from .normalizers import (
    PHONE_TEXT_RE, collapse_ws, extract_emails, format_phone, host_of, norm_state, normalize_url, phone_digits,
)
from .rules import find_addresses, label_role, label_email_role, _snippet
from .urls import KIND_OWN, classify_url, domain_matches, facebook_base_url

WALL_REASON = "blocked by Facebook login wall"

FB_NOTICE = (
    "NOTE ABOUT FACEBOOK: Facebook's terms of use restrict automated collection of its pages. For parishes whose\n"
    "website is a Facebook page, this tool looks only at public pages while logged out, in very low volume (at\n"
    "most 5 pages per Facebook page, 5 seconds apart), and in a best-effort way. It never logs in, never solves\n"
    "CAPTCHAs, and stops the moment it meets a login wall or block. To switch this off, run with --no-facebook\n"
    "or set mode: skip under facebook.com in config.yaml."
)

_STRONG_WALL = (
    "log in to continue", "you must log in", "log in or sign up to view", "to continue, log in",
    "you're temporarily blocked", "you’re temporarily blocked", "confirm you're human", "confirm you’re human",
    "enter the characters you see", "captcha", "we've detected unusual activity", "unusual activity",
    "security check", "your account has been temporarily", "suspicious activity",
)
_UNAVAILABLE = ("this content isn't available right now", "this content isn’t available right now",
                "this page isn't available", "this page isn’t available", "the link you followed may be broken",
                "page not found")
_UI_LINE = re.compile(
    r"^(?:log ?in|log into facebook|email or phone(?: number)?|password|forgot (?:password|account)\??|"
    r"create new account|sign up|see more on facebook|not now|facebook|meta|english(?: \(us\))?|español|"
    r"français|more|about|privacy|terms|ads|ad choices|cookies|help|messenger|watch|marketplace|"
    r"see all|see more|like|comment|share|send message|follow|home|photos|videos|reviews|mentions|"
    r"community|events|posts|all|people|places|groups|contact us|create page|create ad|"
    r"information about this page|learn more|close|back|next|menu|search|notifications|"
    r"recommend|more info)$", re.I)


@dataclass
class FacebookCrawl:
    status: str = "ok"                 # ok | blocked | failed
    reason: str = ""
    pages: list = field(default_factory=list)      # [(PageDoc, kind)]   kind = 'about' | 'posts'
    notes: list = field(default_factory=list)


def fb_subpages(page_key: str, limit: int) -> list[tuple[str, str]]:
    """[(url, kind)] in the order we look: About/Info first (most useful, least noisy), then posts."""
    base = facebook_base_url(page_key)
    if page_key.startswith("id:"):
        urls = [(base + "&sk=about", "about"), (base, "posts")]
    else:
        urls = [(base + "/about", "about"), (base + "/about_contact_and_basic_info", "about"), (base, "posts"),
                (base + "/posts", "posts")]
    return urls[: max(1, min(limit, 5))]


def unwrap_fb_link(href: str) -> str:
    """Facebook wraps outside links as l.facebook.com/l.php?u=<real address>."""
    try:
        p = urlsplit(href)
    except ValueError:
        return href
    if host_of(href) in ("l.facebook.com", "lm.facebook.com") and p.path.startswith("/l.php"):
        u = parse_qs(p.query).get("u")
        if u:
            return unquote(u[0])
    return href


def detect_wall(final_url: str, status: int, body_text: str) -> str:
    """'' if the page has real content; otherwise a plain-language reason.  Looks, never touches."""
    low = (body_text or "").lower()
    path = urlsplit(final_url or "").path.lower()
    if any(x in path for x in ("/login", "/checkpoint", "login.php", "/recover")):
        return WALL_REASON
    if status in (401, 403, 429):
        return WALL_REASON
    if any(p in low for p in _STRONG_WALL):
        return WALL_REASON
    if any(p in low for p in _UNAVAILABLE) and len(clean_fb_text(body_text)) < 600:
        return "Facebook says this page is not available (wrong link, or the page is private)"
    if len(clean_fb_text(body_text)) < 250:
        return WALL_REASON            # only the login box is showing: treat as a wall
    return ""


def clean_fb_text(body_text: str) -> str:
    seen, out = set(), []
    for ln in (body_text or "").splitlines():
        ln = collapse_ws(ln)
        if len(ln) < 3 or _UI_LINE.match(ln) or re.fullmatch(r"[\d.,KkMm ]+", ln):
            continue
        key = ln.lower()
        if key in seen and len(ln) < 80:
            continue
        seen.add(key)
        out.append(ln)
    return "\n".join(out)


def crawl_facebook(page_key: str, cfg: Config, fetcher: Fetcher, stop: threading.Event) -> FacebookCrawl:
    spec = cfg.special("facebook.com")
    limit = int(spec.get("max_pages", 5))
    out = FacebookCrawl()
    for url, kind in fb_subpages(page_key, limit):
        if stop.is_set():
            raise StopRequested()
        res = fetcher.get_rendered(url, facebook=True)
        if res.error and not res.text:
            if not out.pages:
                out.status, out.reason = "failed", f"Facebook page could not be opened ({res.error})"
            else:
                out.notes.append(f"stopped early: {res.error}")
            break
        wall = detect_wall(res.final_url or url, res.status, res.body_text)
        if wall:
            if not out.pages:
                out.status = "blocked" if wall == WALL_REASON else "failed"
                out.reason = wall
            else:
                out.notes.append(f"stopped after {len(out.pages)} page(s): {wall}")
            break
        doc = clean_html(res.text, url, cfg.livestream_hosts, via_browser=True, final_url=res.final_url or url)
        text = clean_fb_text(res.body_text) or doc.text
        # Facebook's HTML is a maze; the visible text is far cleaner.
        doc.text = text
        doc.main_text = text
        doc.links = [(unwrap_fb_link(h), t) for h, t in doc.links]
        out.pages.append((doc, kind))
    if not out.pages and out.status == "ok":
        out.status, out.reason = "failed", "no Facebook page could be read"
    return out


# --------------------------------------------------------------------------------------
# Rule-based facts from a Facebook page
# --------------------------------------------------------------------------------------
_SITE_RE = re.compile(r"(?<![@\w.-])((?:https?://)?(?:www\.)?[a-z0-9][a-z0-9-]*(?:\.[a-z0-9-]+)*\.(?:org|com|net|church|us|info|edu|co)(?:/[^\s\"'<>]*)?)", re.I)
_NOT_A_SITE = ("facebook.com", "fb.com", "fb.me", "instagram.com", "youtube.com", "youtu.be", "twitter.com", "x.com",
               "google.com", "goo.gl", "linkedin.com", "tiktok.com", "messenger.com", "whatsapp.com", "wa.me",
               "apple.com", "gmail.com", "yahoo.com", "outlook.com", "hotmail.com", "icloud.com", "aol.com")


def fb_rule_candidates(doc: PageDoc) -> list[Candidate]:
    url = doc.final_url or doc.url
    text = doc.text
    cands: list[Candidate] = []

    def mk(field_: str, value: str, ev: str, role: Optional[str] = None) -> Candidate:
        return Candidate(field=field_, value=value, source_type=M.FACEBOOK, source_url=url, method="rule",
                         confidence="high", evidence=ev[:300], role=role, page_rank=0)

    seen = set()
    for m in PHONE_TEXT_RE.finditer(text):
        d = phone_digits(m.group(0))
        if d and d not in seen:
            seen.add(d)
            role = label_role(text, m.start(), m.end()) or "church"
            cands.append(mk("@phone", format_phone(d), _snippet(text, m.start(), m.end()), role))
    for e in extract_emails(text):
        i = text.lower().find(e)
        role = label_email_role(e, text, i, i + len(e)) or "church"
        cands.append(mk("@email", e, _snippet(text, i, i + len(e)), role))
    for formatted, ev in find_addresses(text):
        cands.append(mk("@address", formatted, ev))
    # The website listed on the page: outbound links first, then anything that looks like a web address.
    site = ""
    for href, link_text in doc.links:
        h = host_of(href)
        if h and not any(domain_matches(h, d) for d in _NOT_A_SITE) and href.startswith("http"):
            if classify_url(href) == KIND_OWN and "facebook" not in h:
                site = normalize_url(href)
                break
    if not site:
        for m in _SITE_RE.finditer(text):
            cand = m.group(1)
            if not any(domain_matches(host_of(cand), d) for d in _NOT_A_SITE):
                site = normalize_url(cand)
                break
    if site:
        cands.append(mk("@website", site, f"website listed on the Facebook page: {site}"))
    return cands


# --------------------------------------------------------------------------------------
# How old is a post?
# --------------------------------------------------------------------------------------
_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}
_FULL_DATE = re.compile(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(20\d\d)\b", re.I)
_RECENT_REL = re.compile(r"\b(?:just now|yesterday|today|\d{1,2}\s?(?:m|min|mins|h|hr|hrs|d|w|wk|wks))\b", re.I)
_OLD_REL = re.compile(r"\b\d{1,2}\s?(?:y|yr|yrs)\b", re.I)


def assess_recency(window: str, today: date, max_age_days: int = 365) -> str:
    """'recent', 'stale' or 'unknown' for a piece of Facebook text, from any dates written in it."""
    cutoff = today - timedelta(days=max_age_days)
    dates = []
    for m in _FULL_DATE.finditer(window):
        try:
            dates.append(date(int(m.group(3)), _MONTHS[m.group(1).lower()[:3]], int(m.group(2))))
        except ValueError:
            pass
    if dates:
        return "recent" if max(dates) >= cutoff else "stale"
    if _RECENT_REL.search(window):
        return "recent"
    if _OLD_REL.search(window):
        return "stale"
    years = [int(y) for y in re.findall(r"\b(20\d\d)\b", window)]
    if years and max(years) < cutoff.year:
        return "stale"
    return "unknown"
