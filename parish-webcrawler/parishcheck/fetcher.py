"""Downloading pages politely.

* `Fetcher.get()`      - plain HTTP download (httpx) with timeouts, retries, per-domain delay, robots.txt,
                         and an on-disk cache so a re-run does not hit the websites again.
* `BrowserWorker`      - an optional real (headless) Chromium browser via Playwright, used when a page has
                         almost no text (JavaScript sites) and always for Facebook.  Playwright must stay on
                         one thread, so it lives on its own worker thread and other threads wait for it.

Nothing here ever logs in, solves CAPTCHAs, or tries to get around a block.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import logging
import queue
import threading
import time
import zlib
from concurrent.futures import Future
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
from urllib.parse import urlsplit

import httpx

from .config import Config
from .normalizers import host_of, normalize_url
from .robots import RobotsRules

log = logging.getLogger("parishcheck")

MAX_BODY_BYTES = 3_000_000
MAX_SITEMAP_BYTES = 12_000_000


@dataclass
class FetchResult:
    url: str
    final_url: str = ""
    status: int = 0
    content_type: str = ""
    text: str = ""                 # decoded HTML (empty for non-HTML)
    data: bytes = b""              # raw bytes (sitemaps)
    error: str = ""
    from_cache: bool = False
    via_browser: bool = False
    robots_blocked: bool = False
    truncated: bool = False
    body_text: str = ""            # visible text from the browser (Facebook)
    last_modified: str = ""        # the Last-Modified header, when the server sends one

    @property
    def ok(self) -> bool:
        return self.status == 200 and not self.error


class StopRequested(Exception):
    """Raised inside workers when Ctrl+C was pressed, so they unwind quickly."""


# --------------------------------------------------------------------------------------
# Per-domain politeness
# --------------------------------------------------------------------------------------
class Throttle:
    """Makes sure two requests to the same domain are at least `delay` seconds apart, even when several
    threads ask at once (each caller reserves the next free time slot)."""

    def __init__(self, stop: threading.Event):
        self._lock = threading.Lock()
        self._next: dict[str, float] = {}
        self._stop = stop

    def wait(self, domain: str, delay: float) -> None:
        with self._lock:
            now = time.monotonic()
            slot = max(now, self._next.get(domain, 0.0))
            self._next[domain] = slot + delay
        while True:
            remaining = slot - time.monotonic()
            if remaining <= 0:
                return
            if self._stop.is_set():
                raise StopRequested()
            time.sleep(min(remaining, 0.25))


# --------------------------------------------------------------------------------------
# Browser worker (Playwright)
# --------------------------------------------------------------------------------------
@dataclass
class BrowserResult:
    final_url: str = ""
    status: int = 0
    html: str = ""
    body_text: str = ""
    error: str = ""


class BrowserWorker:
    def __init__(self, stop: threading.Event):
        self._stop = stop
        self._q: "queue.Queue[Optional[tuple]]" = queue.Queue()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._ready = threading.Event()
        self.available: Optional[bool] = None
        self.error = ""

    def _ensure_started(self) -> None:
        with self._lock:
            if self._thread is None:
                self._thread = threading.Thread(target=self._run, name="browser-worker", daemon=True)
                self._thread.start()
        self._ready.wait(timeout=90)

    def _run(self) -> None:
        pw = browser = None
        try:
            from playwright.sync_api import sync_playwright
            pw = sync_playwright().start()
            browser = pw.chromium.launch(headless=True)
            self.available = True
        except Exception as exc:  # not installed / browser missing
            self.available = False
            self.error = f"the helper browser could not start ({type(exc).__name__}: {str(exc)[:160]})"
            log.warning("Browser fallback unavailable: %s", self.error)
        finally:
            self._ready.set()
        if not self.available:
            self._drain_with_errors()
            return
        try:
            while True:
                item = self._q.get()
                if item is None:
                    break
                url, timeout_s, fut = item
                try:
                    fut.set_result(self._render(browser, url, timeout_s))
                except Exception as exc:
                    fut.set_result(BrowserResult(error=f"browser error: {type(exc).__name__}: {str(exc)[:200]}"))
        finally:
            try:
                browser.close()
                pw.stop()
            except Exception:
                pass

    def _drain_with_errors(self) -> None:
        while True:
            item = self._q.get()
            if item is None:
                return
            item[2].set_result(BrowserResult(error=self.error))

    @staticmethod
    def _render(browser, url: str, timeout_s: int) -> BrowserResult:
        ctx = browser.new_context(viewport={"width": 1280, "height": 1000}, locale="en-US")
        try:
            page = ctx.new_page()
            # Skip pictures, video and fonts: we only want text, and it keeps memory use low.
            page.route("**/*", lambda route: route.abort()
                       if route.request.resource_type in ("image", "media", "font") else route.continue_())
            resp = page.goto(url, wait_until="domcontentloaded", timeout=timeout_s * 1000)
            try:
                page.wait_for_load_state("networkidle", timeout=4000)
            except Exception:
                pass
            page.wait_for_timeout(700)
            html = page.content()
            try:
                body_text = page.inner_text("body", timeout=3000)
            except Exception:
                body_text = ""
            return BrowserResult(final_url=page.url, status=resp.status if resp else 200, html=html, body_text=body_text)
        finally:
            ctx.close()

    def render(self, url: str, timeout_s: int = 45) -> BrowserResult:
        if self._stop.is_set():
            raise StopRequested()
        self._ensure_started()
        if not self.available:
            return BrowserResult(error=self.error or "the helper browser is not available")
        fut: Future = Future()
        self._q.put((url, timeout_s, fut))
        while True:
            try:
                return fut.result(timeout=0.5)
            except Exception:
                if self._stop.is_set():
                    raise StopRequested()
                if fut.done():
                    return fut.result()

    def close(self) -> None:
        if self._thread is not None:
            self._q.put(None)
            self._thread.join(timeout=15)
            self._thread = None


# --------------------------------------------------------------------------------------
# The fetcher
# --------------------------------------------------------------------------------------
class Fetcher:
    def __init__(self, cfg: Config, stop: threading.Event):
        self.cfg = cfg
        self.stop = stop
        self.throttle = Throttle(stop)
        self.browser = BrowserWorker(stop) if cfg.use_browser_fallback or cfg.facebook_enabled else None
        self._robots: dict[str, RobotsRules] = {}
        self._robots_lock = threading.Lock()
        self.cache_dir = cfg.cache_dir / "http"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._client = httpx.Client(
            follow_redirects=True,
            max_redirects=8,
            timeout=httpx.Timeout(25.0, connect=12.0),
            headers={
                "User-Agent": cfg.user_agent,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.5",
                "Accept-Language": "en-US,en;q=0.9",
            },
            limits=httpx.Limits(max_connections=16, max_keepalive_connections=8),
        )
        self._insecure_client: Optional[httpx.Client] = None
        self.stats = {"http_requests": 0, "cache_hits": 0, "browser_renders": 0}
        self._stats_lock = threading.Lock()

    # ---- housekeeping ----------------------------------------------------------------
    def close(self) -> None:
        try:
            self._client.close()
            if self._insecure_client:
                self._insecure_client.close()
        finally:
            if self.browser:
                self.browser.close()

    def _bump(self, key: str) -> None:
        with self._stats_lock:
            self.stats[key] += 1

    # ---- cache -----------------------------------------------------------------------
    def _cache_paths(self, key_text: str) -> tuple[Path, Path]:
        h = hashlib.sha1(key_text.encode("utf-8")).hexdigest()
        return self.cache_dir / f"{h}.json", self.cache_dir / f"{h}.bin"

    def _cache_get(self, key_text: str) -> Optional[FetchResult]:
        meta_p, body_p = self._cache_paths(key_text)
        try:
            if not meta_p.exists():
                return None
            age_days = (time.time() - meta_p.stat().st_mtime) / 86400
            if age_days > self.cfg.cache_max_age_days:
                return None
            meta = json.loads(meta_p.read_text(encoding="utf-8"))
            data = body_p.read_bytes() if body_p.exists() else b""
            if data[:2] == b"\x1f\x8b":                    # cached bodies are stored gzip-compressed
                data = gzip.decompress(data)
        except (OSError, json.JSONDecodeError, EOFError, zlib.error):
            return None
        res = FetchResult(
            url=meta.get("url", ""), final_url=meta.get("final_url", ""), status=int(meta.get("status", 0)),
            content_type=meta.get("content_type", ""), error=meta.get("error", ""), from_cache=True,
            via_browser=bool(meta.get("via_browser")), truncated=bool(meta.get("truncated")),
            body_text=meta.get("body_text", ""), last_modified=meta.get("last_modified", ""),
        )
        if meta.get("is_text"):
            res.text = data.decode("utf-8", errors="replace")
        else:
            res.data = data
        self._bump("cache_hits")
        return res

    def _cache_put(self, key_text: str, res: FetchResult) -> None:
        meta_p, body_p = self._cache_paths(key_text)
        try:
            is_text = bool(res.text)
            meta = {
                "url": res.url, "final_url": res.final_url, "status": res.status, "content_type": res.content_type,
                "error": res.error, "via_browser": res.via_browser, "truncated": res.truncated,
                "is_text": is_text, "body_text": res.body_text[:200000], "fetched_at": time.time(),
                "last_modified": res.last_modified,
            }
            raw = res.text.encode("utf-8") if is_text else res.data
            body_p.write_bytes(gzip.compress(raw, compresslevel=4) if raw else b"")
            meta_p.write_text(json.dumps(meta), encoding="utf-8")
        except OSError as exc:
            log.debug("Could not write cache: %s", exc)

    # ---- robots.txt ------------------------------------------------------------------
    def robots_for(self, url: str) -> RobotsRules:
        parts = urlsplit(normalize_url(url))
        origin = f"{parts.scheme}://{parts.netloc}"
        with self._robots_lock:
            rules = self._robots.get(origin)
        if rules is not None:
            return rules
        robots_url = origin + "/robots.txt"
        cached = self._cache_get("robots|" + robots_url)
        if cached is not None and cached.text is not None and cached.status:
            text = cached.text if cached.status == 200 else ""
        else:
            text = ""
            try:
                self.throttle.wait(host_of(robots_url), self.cfg.delay_seconds)
                r = self._http_get(robots_url, max_bytes=500_000, accept_any=True)
                if r.status == 200:
                    text = r.text
                if r.status in (200, 401, 403, 404, 410):   # don't remember temporary trouble
                    self._cache_put("robots|" + robots_url, FetchResult(url=robots_url, status=r.status, text=text or " ",
                                                                         content_type="text/plain"))
            except StopRequested:
                raise
            except Exception as exc:
                log.debug("robots.txt not readable for %s: %s", origin, exc)
        rules = RobotsRules(text)
        with self._robots_lock:
            self._robots[origin] = rules
        return rules

    def allowed_by_robots(self, url: str) -> bool:
        if not self.cfg.respect_robots:
            return True
        parts = urlsplit(normalize_url(url))
        rules = self.robots_for(url)
        return rules.allowed((parts.path or "/") + (("?" + parts.query) if parts.query else ""))

    def delay_for(self, url: str) -> float:
        host = host_of(url)
        delay = self.cfg.delay_seconds
        for dom, spec in self.cfg.special_sources.items():
            if host == dom or host.endswith("." + dom):
                delay = max(delay, float(spec.get("delay_seconds", delay)))
        try:
            origin_rules = self._robots.get(f"{urlsplit(normalize_url(url)).scheme}://{urlsplit(normalize_url(url)).netloc}")
            if origin_rules and origin_rules.crawl_delay:
                delay = max(delay, min(origin_rules.crawl_delay, 10.0))
        except Exception:
            pass
        return delay

    def path_is_forbidden_by_config(self, url: str) -> bool:
        """special_sources can list paths we must never touch (Asset Map: /map, /search, /user ...)."""
        host = host_of(url)
        parts = urlsplit(url)
        target = (parts.path or "/") + (("?" + parts.query) if parts.query else "")
        import re
        for dom, spec in self.cfg.special_sources.items():
            if host == dom or host.endswith("." + dom):
                for pat in spec.get("never_paths", []) or []:
                    if re.search(pat, target, re.I):
                        return True
        return False

    # ---- low-level HTTP --------------------------------------------------------------
    def _http_get(self, url: str, max_bytes: int = MAX_BODY_BYTES, accept_any: bool = False) -> FetchResult:
        """One download with retries.  Never raises for ordinary network problems; sets `error` instead."""
        res = FetchResult(url=url)
        last_error = ""
        client = self._client
        for attempt in range(1, 4):
            if self.stop.is_set():
                raise StopRequested()
            try:
                self._bump("http_requests")
                with client.stream("GET", url) as resp:
                    res.status = resp.status_code
                    res.final_url = str(resp.url)
                    res.content_type = resp.headers.get("content-type", "").split(";")[0].strip().lower()
                    res.last_modified = resp.headers.get("last-modified", "")
                    if resp.status_code in (429, 500, 502, 503, 504) and attempt < 3:
                        wait = 1.5 * attempt
                        ra = resp.headers.get("retry-after", "")
                        if ra.isdigit():
                            wait = min(float(ra), 30.0)
                        last_error = f"HTTP {resp.status_code}"
                        self._sleep(wait)
                        continue
                    chunks, size = [], 0
                    is_htmlish = accept_any or "html" in res.content_type or "xml" in res.content_type \
                        or "text" in res.content_type or not res.content_type or "gzip" in res.content_type \
                        or "octet-stream" in res.content_type
                    if resp.status_code == 200 and is_htmlish:
                        for chunk in resp.iter_bytes():
                            chunks.append(chunk)
                            size += len(chunk)
                            if size >= max_bytes:
                                res.truncated = True
                                break
                    body = b"".join(chunks)
                    res.data = body
                    if body[:2] != b"\x1f\x8b" and body and (
                            "html" in res.content_type or "text" in res.content_type or not res.content_type
                            or accept_any):
                        header_has_charset = "charset" in resp.headers.get("content-type", "").lower()
                        enc = (resp.encoding or "utf-8") if header_has_charset else (_sniff_charset(body) or "utf-8")
                        try:
                            res.text = body.decode(enc, errors="replace")
                        except LookupError:
                            res.text = body.decode("utf-8", errors="replace")
                    return res
            except httpx.TooManyRedirects:
                res.error = "redirect loop (the site keeps redirecting)"
                return res
            except httpx.TimeoutException:
                last_error = "timed out"
            except httpx.ConnectError as exc:
                msg = str(exc)
                if "CERTIFICATE" in msg.upper() or "SSL" in msg.upper():
                    # Expired or misconfigured certificate: we only READ public text and send no secrets,
                    # so retry once without certificate checking.
                    if self._insecure_client is None:
                        self._insecure_client = httpx.Client(
                            verify=False, follow_redirects=True, max_redirects=8,
                            timeout=httpx.Timeout(25.0, connect=12.0), headers=dict(self._client.headers))
                    if client is not self._insecure_client:
                        client = self._insecure_client
                        last_error = "certificate problem (retrying without certificate check)"
                        continue
                last_error = f"could not connect ({msg[:100]})"
            except (httpx.TransportError, httpx.InvalidURL, httpx.DecodingError) as exc:
                last_error = f"{type(exc).__name__}: {str(exc)[:100]}"
            except UnicodeError as exc:
                res.error = f"bad web address ({exc})"
                return res
            if attempt < 3:
                self._sleep(1.5 * attempt)
        res.error = last_error or f"HTTP {res.status}"
        if res.status in (429, 500, 502, 503, 504):
            res.error = f"HTTP {res.status} (site is busy or refusing; gave up after 3 tries)"
        return res

    def _sleep(self, seconds: float) -> None:
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if self.stop.is_set():
                raise StopRequested()
            time.sleep(0.2)

    # ---- public: a web page ------------------------------------------------------------
    def get(self, url: str, *, use_cache: bool = True) -> FetchResult:
        """Download an HTML page (cached, polite, robots-aware)."""
        nu = normalize_url(url)
        if not nu:
            return FetchResult(url=url, error="not a valid web address")
        if self.path_is_forbidden_by_config(nu):
            return FetchResult(url=nu, error="skipped: this part of the site is off-limits by settings", robots_blocked=True)
        if use_cache:
            hit = self._cache_get("page|" + nu)
            if hit is not None:
                return hit
        try:
            if not self.allowed_by_robots(nu):
                return FetchResult(url=nu, error="blocked by robots.txt (the site asked robots not to fetch this)",
                                   robots_blocked=True)
            self.throttle.wait(host_of(nu), self.delay_for(nu))
        except StopRequested:
            raise
        res = self._http_get(nu)
        res.url = nu
        if res.final_url:
            res.final_url = normalize_url(res.final_url) or res.final_url
        # Cache pages and permanent "not found" answers; don't cache temporary network trouble.
        if res.status == 200 or res.status in (404, 410):
            self._cache_put("page|" + nu, res)
        return res

    def get_rendered(self, url: str, *, use_cache: bool = True, facebook: bool = False) -> FetchResult:
        """Download a page through the helper browser (runs JavaScript)."""
        nu = normalize_url(url)
        if not nu:
            return FetchResult(url=url, error="not a valid web address", via_browser=True)
        if self.browser is None:
            return FetchResult(url=nu, error="helper browser is switched off in config.yaml", via_browser=True)
        if use_cache:
            hit = self._cache_get("js|" + nu)
            if hit is not None:
                return hit
        if not facebook:
            if self.path_is_forbidden_by_config(nu):
                return FetchResult(url=nu, error="skipped: off-limits by settings", robots_blocked=True, via_browser=True)
            if not self.allowed_by_robots(nu):
                return FetchResult(url=nu, error="blocked by robots.txt", robots_blocked=True, via_browser=True)
        self.throttle.wait(host_of(nu), self.delay_for(nu))
        self._bump("browser_renders")
        br = self.browser.render(nu)
        res = FetchResult(url=nu, final_url=normalize_url(br.final_url) or br.final_url, status=br.status,
                          text=br.html, error=br.error, via_browser=True, body_text=br.body_text,
                          content_type="text/html")
        if res.ok and res.text:
            self._cache_put("js|" + nu, res)
        return res

    # ---- public: raw bytes (sitemaps) ----------------------------------------------------
    def get_sitemap_bytes(self, url: str) -> Optional[bytes]:
        """Download a sitemap (.xml or .xml.gz) and return the unzipped bytes (None if unavailable)."""
        nu = normalize_url(url)
        if not nu:
            return None
        hit = self._cache_get("sm|" + nu)
        if hit is not None:
            return hit.data or None
        try:
            if not self.allowed_by_robots(nu):
                return None
            self.throttle.wait(host_of(nu), self.delay_for(nu))
        except StopRequested:
            raise
        r = self._http_get(nu, max_bytes=MAX_SITEMAP_BYTES, accept_any=True)
        data = r.data if r.status == 200 else b""
        data = _maybe_gunzip(data)
        self._cache_put("sm|" + nu, FetchResult(url=nu, status=r.status or 404, data=data))
        return data or None


def _sniff_charset(body: bytes) -> str:
    """Look for <meta charset=...> in the first 3 KB when the server didn't say which encoding it uses."""
    import re
    m = re.search(rb"<meta[^>]+charset=[\"']?\s*([A-Za-z0-9_\-]+)", body[:3000], re.I)
    return m.group(1).decode("ascii", "ignore") if m else ""


def _maybe_gunzip(data: bytes, limit: int = 40_000_000) -> bytes:
    """Unzip .gz data safely (stops at `limit` bytes so a 'zip bomb' can't eat memory)."""
    if data[:2] != b"\x1f\x8b":
        return data
    try:
        d = zlib.decompressobj(16 + zlib.MAX_WBITS)
        return d.decompress(data, limit)
    except zlib.error:
        try:
            return gzip.decompress(data)[:limit]
        except Exception:
            return b""
