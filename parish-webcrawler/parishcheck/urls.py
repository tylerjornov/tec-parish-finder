"""URL helpers: what kind of address is this, should we skip it, how interesting is it?"""

from __future__ import annotations

import re
from typing import Optional
from urllib.parse import parse_qsl, urlsplit

from .normalizers import host_of, normalize_url

FACEBOOK_HOSTS = {"facebook.com", "m.facebook.com", "web.facebook.com", "mbasic.facebook.com", "fb.com",
                  "fb.me", "touch.facebook.com", "business.facebook.com"}
ASSET_MAP_HOST = "episcopalassetmap.org"

KIND_OWN = "own_site"
KIND_FACEBOOK = "facebook"
KIND_ASSET_MAP = "asset_map"
KIND_UNSUPPORTED = "unsupported"   # Instagram, LinkedIn, Google Maps...: not a source we can read
KIND_MISSING = "missing"
KIND_INVALID = "invalid"

_NON_HTML_EXT = re.compile(
    r"\.(?:jpe?g|png|gif|webp|svg|ico|bmp|tiff?|mp3|mp4|m4a|wav|mov|avi|wmv|webm|zip|gz|tgz|rar|7z|"
    r"docx?|xlsx?|pptx?|csv|rtf|epub|css|js|json|xml|rss|atom|woff2?|ttf|eot|exe|dmg|apk)(?:$|\?)", re.I)
_PDF_EXT = re.compile(r"\.pdf(?:$|\?)", re.I)

# Query parameters that make endless variants of the same page (calendars, sorting, searching...).
_TRAP_PARAMS = {
    "ical", "outlook-ical", "eventdisplay", "tribe-bar-date", "tribe_events", "date", "month", "year", "day",
    "s", "search", "q", "sort", "orderby", "order", "filter", "print", "format", "replytocom", "share",
    "action", "do", "view_mode", "paged", "page", "pagenum", "offset", "cal", "calendar", "mode",
}


def domain_matches(host: str, pattern: str) -> bool:
    return host == pattern or host.endswith("." + pattern)


def classify_url(url: str, skip_domains: Optional[list] = None) -> str:
    """own_site / facebook / asset_map / unsupported / missing / invalid"""
    if url is None or not str(url).strip():
        return KIND_MISSING
    n = normalize_url(str(url))
    if not n:
        return KIND_INVALID
    host = host_of(n)
    if "." not in host:
        return KIND_INVALID
    if host in FACEBOOK_HOSTS or domain_matches(host, "facebook.com"):
        return KIND_FACEBOOK
    if domain_matches(host, ASSET_MAP_HOST):
        return KIND_ASSET_MAP
    for d in skip_domains or []:
        if domain_matches(host, d):
            return KIND_UNSUPPORTED
    return KIND_OWN


def is_pdf(url: str) -> bool:
    return bool(_PDF_EXT.search(urlsplit(url).path + ("?" + urlsplit(url).query if urlsplit(url).query else "")))


def is_non_html(url: str) -> bool:
    return bool(_NON_HTML_EXT.search(urlsplit(url).path))


def is_excluded(url: str, exclude_regexes: list) -> bool:
    """True for URL traps (calendars, tag/category pages, feeds, logins, carts, ...)."""
    for rx in exclude_regexes:
        if rx.search(url):
            return True
    try:
        qs = parse_qsl(urlsplit(url).query, keep_blank_values=True)
    except ValueError:
        return True
    for k, _ in qs:
        if k.lower() in _TRAP_PARAMS:
            return True
    if len(qs) > 3:
        return True
    return False


def same_site(url: str, allowed_hosts: set[str]) -> bool:
    """www and non-www are the same site; sub-domains are not."""
    return host_of(url) in allowed_hosts


def score_url(url: str, anchor_text: str, keywords: list[str], depth: int = 0) -> float:
    """How promising a link looks.  Keyword hits in the path count most, then in the link text.
    Short, shallow addresses and shallow crawl depth are preferred."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return -99.0
    path = parts.path.lower()
    text = (anchor_text or "").lower()
    score = 0.0
    for kw in keywords:
        if kw in path:
            score += 3.0
        if kw in text:
            score += 1.5
    segments = [s for s in path.split("/") if s]
    score -= 0.4 * max(0, len(segments) - 1)
    score -= 0.5 * depth
    if parts.query:
        score -= 2.0
    if not segments:
        score += 5.0     # the home page
    return score


def facebook_page_key(url: str) -> str:
    """A stable name for a Facebook page: 'ststephenswyandotte' or 'id:100063...'.  '' if unrecognised."""
    n = normalize_url(url)
    if not n:
        return ""
    p = urlsplit(n)
    segs = [s for s in p.path.split("/") if s]
    if not segs:
        return ""
    qs = dict(parse_qsl(p.query))
    if segs[0] == "profile.php" and qs.get("id"):
        return "id:" + qs["id"]
    if segs[0] in ("pages", "p") and len(segs) >= 2:
        # /pages/Name/12345 or /p/Name-12345
        last = segs[-1]
        m = re.search(r"(\d{6,})$", last)
        return ("id:" + m.group(1)) if m else segs[1].lower()
    if segs[0] in ("groups", "events", "watch", "sharer", "sharer.php", "login", "l.php", "dialog", "plugins",
                   "share", "hashtag", "marketplace", "gaming", "reel", "stories"):
        return ""
    return segs[0].lower()


def facebook_base_url(page_key: str) -> str:
    if page_key.startswith("id:"):
        return f"https://www.facebook.com/profile.php?id={page_key[3:]}"
    return f"https://www.facebook.com/{page_key}"


# --------------------------------------------------------------------------------------
# Websites shared by many parishes (a diocese's site, a college's site)
# --------------------------------------------------------------------------------------
# Paths that are just another way to write the home page, so they do not mark a shared site.
_HOME_PATHS = {"/home", "/index", "/welcome", "/main", "/default", "/en", "/en-us"}


def site_scope(url: str, shared_host_domains: Optional[list] = None) -> tuple[str, str]:
    """(host, scope). scope is '' for an ordinary church website (read the whole site). For a parish page on a
    shared site it is the page's path ('/st-davids-church': read that page, pages below it, and at most 3 pages
    it links to); for the home page of a listed shared host (a college) it is '/' (that page and 3 links)."""
    n = normalize_url(url)
    host = host_of(n)
    path = urlsplit(n).path if n else ""
    path = re.sub(r"\.(?:html?|php|aspx?)$", "", path, flags=re.I).rstrip("/")
    if path.lower() in _HOME_PATHS:
        path = ""
    if path:
        return host, path
    if any(domain_matches(host, d) for d in shared_host_domains or []):
        return host, "/"
    return host, ""


def in_scope(url: str, host: str, scope: str) -> bool:
    """Is `url` inside the part of the site this parish owns?  ('/' = only the start page itself.)"""
    if host_of(url) != host:
        return False
    if not scope:
        return True
    path = re.sub(r"\.(?:html?|php|aspx?)$", "", urlsplit(normalize_url(url)).path, flags=re.I).rstrip("/")
    if scope == "/":
        return path in ("",) or path.lower() in _HOME_PATHS
    return path == scope or path.startswith(scope + "/")


def site_key(url: str, shared_host_domains: Optional[list] = None) -> str:
    """The progress-database key for a church website. Parishes on one shared host get one key each."""
    host, scope = site_scope(url, shared_host_domains)
    return "site:" + host + (scope if scope not in ("", "/") else "")
