"""Finding and downloading the pages of one church website.

Plan for each site:
  1. Fetch the home page (always).
  2. Look for a sitemap (robots.txt "Sitemap:" lines, then /sitemap.xml, including nested and .gz sitemaps).
  3. Rank every candidate address by how many `priority_keywords` it contains, and download the best ones
     until `max_pages_per_site` is reached.  If there is no (useful) sitemap, follow links from the pages
     instead, best-looking first.
"""

from __future__ import annotations

import heapq
import itertools
import logging
import threading
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urlsplit

import re

from lxml import etree

from .cleaner import PageDoc, clean_html
from .config import Config
from .fetcher import FetchResult, Fetcher, StopRequested
from .normalizers import host_of, normalize_url
from .urls import is_excluded, is_non_html, is_pdf, same_site, score_url

log = logging.getLogger("parishcheck")


@dataclass
class CrawlResult:
    start_url: str
    status: str = "ok"                 # ok | failed
    reason: str = ""
    pages: list = field(default_factory=list)          # list[PageDoc]
    pdfs: list = field(default_factory=list)
    cap_hit: bool = False
    sitemap_used: bool = False
    candidates_found: int = 0
    home_final_url: str = ""
    errors: list = field(default_factory=list)
    allowed_hosts: set = field(default_factory=set)
    robots_blocked: int = 0
    used_browser_pages: int = 0


# --------------------------------------------------------------------------------------
# Sitemaps
# --------------------------------------------------------------------------------------
_SKIP_SITEMAP_WORDS = ("taxonom", "users", "author", "tag", "category", "attachment", "event", "product",
                       "image", "video", "news", "location")


def _parse_sitemap(data: bytes) -> tuple[str, list[str]]:
    """Returns ('index' | 'urls', [locs])."""
    try:
        parser = etree.XMLParser(recover=True, resolve_entities=False, no_network=True, huge_tree=False)
        root = etree.fromstring(data, parser=parser)
    except Exception:
        return "urls", []
    if root is None:
        return "urls", []
    kind = "index" if etree.QName(root).localname.lower() == "sitemapindex" else "urls"
    locs = []
    for el in root.iter("{*}loc"):
        if el.text and el.text.strip():
            locs.append(el.text.strip())
    return kind, locs


def collect_sitemap_urls(home_url: str, fetcher: Fetcher, stop: threading.Event, max_urls: int = 4000) -> list[str]:
    parts = urlsplit(home_url)
    origin = f"{parts.scheme}://{parts.netloc}"
    seeds: list[str] = []
    try:
        seeds.extend(fetcher.robots_for(origin + "/").sitemaps)
    except StopRequested:
        raise
    except Exception:
        pass
    seeds.append(origin + "/sitemap.xml")
    site_host = host_of(origin)
    urls: list[str] = []
    seen_maps: set[str] = set()
    tried_fallbacks = False
    queue = list(dict.fromkeys(seeds))
    fetched = 0
    while queue and fetched < 15 and len(urls) < max_urls:
        if stop.is_set():
            raise StopRequested()
        sm = queue.pop(0)
        key = normalize_url(sm)
        if not key or key in seen_maps:
            continue
        seen_maps.add(key)
        data = fetcher.get_sitemap_bytes(sm)
        fetched += 1
        if data:
            kind, locs = _parse_sitemap(data)
            if kind == "index":
                kids = [l for l in locs if host_of(l) == site_host and not any(w in l.lower() for w in _SKIP_SITEMAP_WORDS)]
                kids.sort(key=lambda l: (0 if "page" in l.lower() else 1))
                queue = kids + queue
            else:
                urls.extend(locs)
        if not queue and not urls and not tried_fallbacks:
            tried_fallbacks = True
            queue = [origin + "/sitemap_index.xml", origin + "/wp-sitemap.xml"]
    return urls[:max_urls]


# --------------------------------------------------------------------------------------
# Crawling
# --------------------------------------------------------------------------------------
def _alternate_urls(url: str) -> list[str]:
    """If the address we were given doesn't load, try http/https and www/non-www variants."""
    n = normalize_url(url)
    if not n:
        return []
    p = urlsplit(n)
    host = p.hostname or ""
    hosts = [host]
    hosts.append(host[4:] if host.startswith("www.") else "www." + host)
    out = []
    for scheme in (p.scheme, "http" if p.scheme == "https" else "https"):
        for h in hosts:
            u = f"{scheme}://{h}{p.path or '/'}"
            if u not in out and u != n:
                out.append(u)
    return out


def _fetch_doc(url: str, cfg: Config, fetcher: Fetcher) -> tuple[Optional[PageDoc], FetchResult]:
    """Download one page and clean it; use the helper browser when the page has almost no text."""
    res = fetcher.get(url)
    if res.robots_blocked or not res.ok:
        return None, res
    if res.content_type and "html" not in res.content_type and "xml" not in res.content_type and "text" not in res.content_type:
        res.error = f"not a web page (type {res.content_type})"
        return None, res
    final = res.final_url or url
    doc = clean_html(res.text, url, cfg.livestream_hosts, final_url=final, status=res.status)
    if cfg.use_browser_fallback and doc.main_len < cfg.min_text_chars_before_browser:
        try:
            rr = fetcher.get_rendered(url)
        except StopRequested:
            raise
        if rr.ok and rr.text:
            doc2 = clean_html(rr.text, url, cfg.livestream_hosts, via_browser=True, final_url=rr.final_url or final)
            if len(doc2.text) > len(doc.text):
                doc = doc2
    return doc, res


_DEAD_SITE = re.compile(
    r"account (?:has been )?suspended|temporarily (?:disabled|unavailable)|(?:site|website) (?:is )?(?:currently )?"
    r"(?:disabled|unavailable|suspended|expired)|domain (?:is |may be )?(?:for sale|parked)|buy this domain|"
    r"(?:is )?under construction|coming soon|default web ?page|welcome to nginx|apache2? .{0,20}default page|"
    r"index of /|bandwidth limit exceeded|page not found|404 not found|this site can.t be reached|"
    r"hosting (?:account|service) (?:has|is)", re.I)


def dead_site_reason(doc: PageDoc, final_url: str) -> str:
    """'' for a normal website. Otherwise why the 'website' is really a placeholder (suspended, parked, ...)."""
    url_low = (final_url or "").lower()
    if any(w in url_low for w in ("tempdisabled", "suspended", "/parked", "domain-for-sale")):
        return "the website address leads to a 'suspended / disabled' placeholder page"
    if len(doc.main_text) < 700:
        m = _DEAD_SITE.search(doc.text[:3000])
        if m:
            return f"the website is only a placeholder page (it says '{m.group(0)[:50]}')"
    return ""


def crawl_site(start_url: str, cfg: Config, fetcher: Fetcher, stop: threading.Event, max_pages: Optional[int] = None,
               plan_only: bool = False) -> CrawlResult:
    """Download up to `max_pages` pages of one website.  With plan_only=True only the home page and the
    sitemaps are fetched and `candidates_found` says how many more pages could be downloaded."""
    max_pages = max_pages or cfg.max_pages_per_site
    out = CrawlResult(start_url=start_url)
    start = normalize_url(start_url)
    if not start:
        out.status, out.reason = "failed", "the website address in the JSON is not a valid web address"
        return out

    # ---- 1. home page (with fallbacks) ----
    home_doc, home_res = _fetch_doc(start, cfg, fetcher)
    tried = [start]
    if home_doc is None and not home_res.robots_blocked:
        for alt in _alternate_urls(start):
            if stop.is_set():
                raise StopRequested()
            tried.append(alt)
            d, r = _fetch_doc(alt, cfg, fetcher)
            if d is not None:
                home_doc, home_res = d, r
                break
    if home_doc is None:
        out.status = "failed"
        if home_res.robots_blocked:
            out.reason = "the site's robots.txt asks robots not to visit it"
            out.robots_blocked += 1
        else:
            out.reason = f"the website could not be opened ({home_res.error or 'HTTP ' + str(home_res.status)})"
            if home_res.status in (401, 403):
                out.reason = f"the website refused access (HTTP {home_res.status}); I do not try to get around blocks"
        return out

    home_final = home_doc.final_url or start
    out.home_final_url = home_final
    dead = dead_site_reason(home_doc, home_final)
    if dead:
        out.status, out.reason = "failed", dead
        return out
    allowed = {host_of(start), host_of(home_final)}
    out.allowed_hosts = allowed
    out.pages.append(home_doc)
    if home_doc.via_browser:
        out.used_browser_pages += 1

    visited: set[str] = {normalize_url(start), normalize_url(home_final)}
    queued: dict[str, float] = {}
    heap: list[tuple[float, int, str, int]] = []
    counter = itertools.count()
    query_paths: dict[str, int] = {}
    pdfs: set[str] = set()

    def consider(link: str, anchor: str, depth: int, bonus: float = 0.0) -> None:
        n = normalize_url(link)
        if not n or n in visited:
            return
        if is_pdf(n):
            pdfs.add(n)
            return
        if not same_site(n, allowed) or is_non_html(n) or is_excluded(n, cfg.exclude_regexes):
            return
        sp = urlsplit(n)
        if sp.query:
            if query_paths.get(sp.path, 0) >= 3:
                return
        score = score_url(n, anchor, cfg.priority_keywords, depth) + bonus
        if n in queued and queued[n] >= score:
            return
        if n not in queued and sp.query:
            query_paths[sp.path] = query_paths.get(sp.path, 0) + 1
        queued[n] = score
        heapq.heappush(heap, (-score, next(counter), n, depth))

    # ---- 2. sitemap + links on the home page ----
    sitemap_urls = [] if stop.is_set() else collect_sitemap_urls(home_final, fetcher, stop)
    out.sitemap_used = len(sitemap_urls) > 0
    for u in sitemap_urls:
        consider(u, "", 1)
    for href, text in home_doc.links:
        consider(href, text, 1, bonus=2.0)       # being in the home page navigation is a good sign
    for href, _ in home_doc.links:
        if is_pdf(href):
            pdfs.add(normalize_url(href))
    follow_links = len(sitemap_urls) < 8        # a thin or missing sitemap: keep following links
    out.candidates_found = len(queued)

    if plan_only:
        out.pdfs = sorted(p for p in pdfs if p)
        out.cap_hit = len(queued) + 1 > max_pages
        return out

    # ---- 3. download the best-looking pages first ----
    attempts = 0
    while heap and len(out.pages) < max_pages and attempts < max_pages * 2 + 10:
        if stop.is_set():
            raise StopRequested()
        neg, _, url, depth = heapq.heappop(heap)
        if url in visited:
            continue
        visited.add(url)
        attempts += 1
        try:
            doc, res = _fetch_doc(url, cfg, fetcher)
        except StopRequested:
            raise
        except Exception as exc:                 # one bad page must never stop the site
            out.errors.append(f"{url}: {type(exc).__name__}: {str(exc)[:100]}")
            continue
        if doc is None:
            if res.robots_blocked:
                out.robots_blocked += 1
            else:
                out.errors.append(f"{url}: {res.error or 'HTTP ' + str(res.status)}")
            continue
        final_n = normalize_url(doc.final_url)
        if final_n and final_n != url:
            if final_n in visited or not same_site(final_n, allowed):
                continue                           # redirected to a page we already have, or off-site
            visited.add(final_n)
        if len(doc.text) < 30:
            continue
        out.pages.append(doc)
        if doc.via_browser:
            out.used_browser_pages += 1
        for href, _ in doc.links:
            if is_pdf(href):
                pdfs.add(normalize_url(href))
        if follow_links and depth < cfg.max_crawl_depth:
            for href, text in doc.links:
                consider(href, text, depth + 1)
    out.cap_hit = len(out.pages) >= max_pages and any(e[2] not in visited for e in heap)
    out.pdfs = sorted(p for p in pdfs if p)
    return out
