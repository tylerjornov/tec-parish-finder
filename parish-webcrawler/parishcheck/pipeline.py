"""The whole run, start to finish: load -> plan -> crawl + extract -> compare -> write the Excel file."""

from __future__ import annotations

import json
import logging
import sys
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from . import models as M
from .assetmap import parse_listing
from .config import Config, ConfigError
from .crawler import crawl_site
from .extract import (
    ExtractContext, config_fingerprint, extract_asset_map, extract_facebook, extract_own_site, new_result,
)
from .facebook import FB_NOTICE, crawl_facebook
from .fetcher import Fetcher, StopRequested
from .llm import OllamaClient, OllamaUnavailable
from .merge import RecordView, build_view
from .normalizers import host_of, name_hint_words, normalize_url
from .report import Row, compare_record, summarize_record, write_excel, write_results_files
from .state import State
from .urls import (
    KIND_ASSET_MAP, KIND_FACEBOOK, KIND_INVALID, KIND_MISSING, KIND_OWN, KIND_UNSUPPORTED, classify_url,
    facebook_page_key, site_key,
)

log = logging.getLogger("parishcheck")


# --------------------------------------------------------------------------------------
# Logging
# --------------------------------------------------------------------------------------
def setup_logging(work_dir: Path) -> None:
    """Everything goes to run.log; only warnings reach the screen (the progress bar does the talking)."""
    work_dir.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger("parishcheck")
    root.setLevel(logging.DEBUG)
    for h in list(root.handlers):
        root.removeHandler(h)
    fh = logging.FileHandler(work_dir / "run.log", encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.WARNING)
    ch.setFormatter(logging.Formatter("%(message)s"))
    root.addHandler(fh)
    root.addHandler(ch)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("trafilatura").setLevel(logging.ERROR)
    logging.getLogger("htmldate").setLevel(logging.ERROR)
    root.propagate = False


def say(msg: str = "") -> None:
    """A line for run.log only."""
    log.info(msg)


def tell(msg: str = "") -> None:
    """A line for the screen (and run.log)."""
    log.info(msg)
    print(msg, flush=True)


class Progress:
    """One line that redraws itself:  Checking websites [#####.....] 32/81 · about 1 h 5 min left · now: x.org"""

    WIDTH = 28

    def __init__(self, total: int):
        self.total = max(1, total)
        self.done = 0
        self.timed = 0                 # sources actually worked on (not resumed), for the time estimate
        self.started = time.monotonic()
        self.current = ""
        self.tty = sys.stdout.isatty()
        self._last_plain = -1

    def step(self, name: str = "", worked: bool = True) -> None:
        self.done = min(self.total, self.done + 1)
        if worked:
            self.timed += 1
        self.draw(name)

    def set_total(self, total: int) -> None:
        self.total = max(self.done, total, 1)

    def draw(self, name: str = "") -> None:
        if name:
            self.current = name
        left = self.total - self.done
        if self.done >= self.total:
            eta = "finishing up"
        elif self.timed >= 2:
            secs = (time.monotonic() - self.started) / self.timed * left
            eta = f"about {_hms(secs) if secs < 120 else _hms(round(secs / 60) * 60).replace(' 0 s', '')} left"
        else:
            eta = "estimating time left..."
        filled = int(self.WIDTH * self.done / self.total)
        bar = "█" * filled + "░" * (self.WIDTH - filled)
        line = f"Checking websites [{bar}] {self.done}/{self.total} · {eta}"
        if self.current and self.done < self.total:
            line += f" · now: {self.current[:30]}"
        if self.tty:
            sys.stdout.write("\r\033[K" + line)
            sys.stdout.flush()
        elif self.done != self._last_plain:      # not a terminal: plain lines, one per step
            self._last_plain = self.done
            print(line, flush=True)

    def finish(self) -> None:
        if self.tty:
            sys.stdout.write("\n")
            sys.stdout.flush()


# --------------------------------------------------------------------------------------
# Loading
# --------------------------------------------------------------------------------------
def load_records(cfg: Config) -> list[dict]:
    path = cfg.input_json
    if not path.exists():
        raise ConfigError(
            f"I can't find your parish file: {path}\n"
            "Run it again and choose the parish JSON file.")
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"Your parish file {path.name} is not valid JSON (line {exc.lineno}, column {exc.colno}): {exc.msg}")
    except UnicodeDecodeError:
        raise ConfigError(f"Your parish file {path.name} is not saved as UTF-8 text. Re-save it as UTF-8.")
    if not isinstance(data, list) or not all(isinstance(r, dict) for r in data):
        raise ConfigError(f"{path.name} should be a JSON array (a list) of parish objects, like [ {{...}}, {{...}} ].")
    return data


def record_id(cfg: Config, rec: dict, idx: int) -> str:
    v = rec.get(cfg.id_key)
    return str(v) if v not in (None, "") else f"row-{idx + 1}"


def record_name(cfg: Config, rec: dict) -> str:
    v = rec.get(cfg.name_key)
    return str(v) if v not in (None, "") else ""


# --------------------------------------------------------------------------------------
# Planning
# --------------------------------------------------------------------------------------
@dataclass
class Source:
    key: str
    kind: str                       # M.OWN_SITE / M.FACEBOOK / M.ASSET_MAP
    url: str
    record_idx: list = field(default_factory=list)
    discovered_from: str = ""
    page_key: str = ""
    disabled_reason: str = ""


@dataclass
class RecordPlan:
    idx: int
    rid: str
    name: str
    url: str
    url_kind: str
    source_keys: list = field(default_factory=list)
    pre_failures: list = field(default_factory=list)
    notes: list = field(default_factory=list)
    discovered_site: str = ""


@dataclass
class Plan:
    records: list = field(default_factory=list)                 # list[RecordPlan]
    sources: dict = field(default_factory=dict)                  # key -> Source (insertion order = crawl order)
    am_pages: dict = field(default_factory=dict)                 # key -> (AssetMapListing|None, error)


def select_indices(records: list[dict], cfg: Config, only: str | None, ids: set | None = None) -> list[int]:
    idxs = list(range(len(records)))
    if only:
        needle = only.casefold()
        idxs = [i for i in idxs if needle in record_id(cfg, records[i], i).casefold()
                or needle in record_name(cfg, records[i]).casefold()]
    if ids is not None:
        idxs = [i for i in idxs if record_id(cfg, records[i], i) in ids]
    return idxs


def read_ids_file(path: str) -> list[str]:
    """--ids-file: one parish id per line; blank lines and lines starting with # are ignored."""
    p = Path(path).expanduser()
    try:
        lines = p.read_text(encoding="utf-8-sig").splitlines()
    except OSError as exc:
        raise ConfigError(f"I can't read the ids file {p} ({exc.strerror or exc}).")
    return [ln.strip() for ln in lines if ln.strip() and not ln.strip().startswith("#")]


def _preclassify(cfg: Config, rec: dict) -> tuple[str, str]:
    raw = rec.get(cfg.website_key)
    if cfg.is_missing(raw) or not isinstance(raw, str):
        return KIND_MISSING, ""
    kind = classify_url(raw, cfg.skip_domains)
    n = normalize_url(raw)
    if kind == KIND_OWN:
        return kind, site_key(n, cfg.shared_host_domains)
    if kind == KIND_FACEBOOK:
        pk = facebook_page_key(n)
        return (kind, "fb:" + pk) if pk else (KIND_UNSUPPORTED, "")
    if kind == KIND_ASSET_MAP:
        return kind, "am:" + n
    return kind, ""


def build_plan(records: list[dict], idxs: list[int], cfg: Config, fetcher: Fetcher, stop: threading.Event) -> Plan:
    plan = Plan()
    am_to_fetch: list[Source] = []
    for i in idxs:
        rec = records[i]
        rp = RecordPlan(i, record_id(cfg, rec, i), record_name(cfg, rec), _fmt_url(rec.get(cfg.website_key)), KIND_MISSING)
        kind, key = _preclassify(cfg, rec)
        rp.url_kind = kind
        raw = rec.get(cfg.website_key)
        url = normalize_url(raw) if isinstance(raw, str) else ""
        if kind == KIND_MISSING:
            rp.pre_failures.append(_failed(M.OWN_SITE, "", "there is no website address in the JSON for this record"))
        elif kind == KIND_INVALID:
            rp.pre_failures.append(_failed(M.OWN_SITE, str(raw), "the website address in the JSON is not a valid web address"))
        elif kind == KIND_UNSUPPORTED:
            rp.pre_failures.append(_failed(M.OWN_SITE, url or str(raw),
                                           "the address is a social-media or map page that this tool cannot read"))
        elif kind == KIND_OWN:
            src = plan.sources.setdefault(key, Source(key, M.OWN_SITE, url))
            src.record_idx.append(i)
            rp.source_keys.append(key)
        elif kind == KIND_FACEBOOK:
            src = plan.sources.setdefault(key, Source(key, M.FACEBOOK, url, page_key=key[3:]))
            if not cfg.facebook_enabled:
                src.disabled_reason = "Facebook is switched off (mode: skip in config.yaml)"
            src.record_idx.append(i)
            rp.source_keys.append(key)
        elif kind == KIND_ASSET_MAP:
            src = plan.sources.setdefault(key, Source(key, M.ASSET_MAP, url))
            src.record_idx.append(i)
            rp.source_keys.append(key)
            if all(src.key != other.key for other in am_to_fetch):
                am_to_fetch.append(src)
        plan.records.append(rp)

    # ---- Asset Map pages: fetch now to find each church's own website ("discovered site") ----
    if am_to_fetch:
        tell(f"Reading {len(am_to_fetch)} Episcopal Asset Map page(s) to find each church's own website...")
    for src in am_to_fetch:
        if stop.is_set():
            raise StopRequested()
        res = fetcher.get(src.url)
        listing, err = None, ""
        if res.ok and res.text:
            listing = parse_listing(res.text, src.url)
            if not listing.ok:
                err = "the page has no church listing on it"
        else:
            err = res.error or f"HTTP {res.status}"
        plan.am_pages[src.key] = (listing, err)
        if listing is not None and listing.ok and listing.website:
            kind = classify_url(listing.website, cfg.skip_domains)
            if kind == KIND_OWN:
                skey = site_key(listing.website, cfg.shared_host_domains)
                site = plan.sources.setdefault(skey, Source(skey, M.OWN_SITE, listing.website,
                                                            discovered_from=src.url))
                for ri in src.record_idx:
                    if ri not in site.record_idx:
                        site.record_idx.append(ri)
                    for rp in plan.records:
                        if rp.idx == ri and skey not in rp.source_keys:
                            rp.source_keys.append(skey)
                            rp.discovered_site = listing.website
                            rp.notes.append(f"discovered site: {listing.website} (found on the Asset Map page)")
    return plan


def _fmt_url(v) -> str:
    return "" if v is None else str(v)


def _failed(kind: str, url: str, reason: str) -> dict:
    r = new_result("none", "none", url)
    r["status"], r["reason"] = "failed", reason
    return r


def shared_site_keys(plan: Plan) -> set[str]:
    return {k for k, s in plan.sources.items() if s.kind == M.OWN_SITE and len(set(s.record_idx)) >= 2}


# --------------------------------------------------------------------------------------
# Environment checks
# --------------------------------------------------------------------------------------
def check_ollama(cfg: Config, client: OllamaClient) -> None:
    ok, err = client.ping()
    if not ok:
        raise ConfigError(
            "I can't reach Ollama (the program that runs the AI model on your Mac).\n"
            "  1. Open the Ollama app (look for the llama icon in your menu bar), or run:  open -a Ollama\n"
            "  2. Wait a few seconds, then run this tool again.\n"
            f"(Technical detail: {err})")
    if not client.has_model():
        raise ConfigError(
            f"The AI model '{cfg.model}' is not downloaded yet.\n"
            f"Download it once by pasting this into Terminal:   ollama pull {cfg.model}\n"
            "Or pick a model you already have by changing 'model:' in config.yaml.\n"
            f"Models on this Mac right now: {', '.join(client.installed_models()) or '(none)'}")


def _hms(seconds: float) -> str:
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h} h {m} min"
    if m:
        return f"{m} min {s} s"
    return f"{s} s"


# --------------------------------------------------------------------------------------
# The run
# --------------------------------------------------------------------------------------
def run(cfg: Config, args) -> int:
    records = load_records(cfg)
    _tidy_old_outputs(cfg)
    setup_logging(cfg.work_dir)
    started = time.monotonic()
    run_started = datetime.now().astimezone()
    today = date.today()
    wanted_ids = read_ids_file(args.ids_file) if getattr(args, "ids_file", None) else None
    idxs = select_indices(records, cfg, args.only, set(wanted_ids) if wanted_ids is not None else None)
    if wanted_ids is not None:
        known = {record_id(cfg, r, i) for i, r in enumerate(records)}
        missing = [i for i in wanted_ids if i not in known]
        if missing:
            tell(f"{len(missing)} id(s) in {Path(args.ids_file).name} are not in {cfg.input_json.name}: {', '.join(missing[:5])}"
                 + (" ..." if len(missing) > 5 else ""))
    if not idxs:
        tell("No parish matches " + " and ".join(x for x in (f"'{args.only}'" if args.only else "",
                                                            f"the ids in {Path(args.ids_file).name}" if wanted_ids is not None else "") if x)
             + ", so there is nothing to do.")
        return 1
    if cfg.only_fields:
        tell(f"Checking only these fields: {', '.join(f.key for f in cfg.fields if f.key in cfg.only_fields)}.")
    tell(f"Checking {len(idxs)} parish(es) from {cfg.input_json.name}. You can press Control+C at any time; progress is saved.")
    stop = threading.Event()
    state = State(cfg.work_dir / "progress.sqlite", fresh=args.fresh)
    if args.fresh:
        _clear_cache(cfg)
        tell("Starting fresh: old progress and saved pages were cleared.")
    fetcher = Fetcher(cfg, stop)
    client = OllamaClient(cfg, stop)
    interrupted = False
    progress = None
    try:
        check_ollama(cfg, client)
        if cfg.facebook_enabled and any(_preclassify(cfg, records[i])[0] == KIND_FACEBOOK for i in idxs):
            say(FB_NOTICE)
        ctx = ExtractContext(cfg=cfg, client=client, state=state, today=today, stop=stop)
        plan = build_plan(records, idxs, cfg, fetcher, stop)
        fp = config_fingerprint(cfg)

        def fp_of(key: str) -> str:
            src = plan.sources.get(key)
            return source_fingerprint(fp, cfg, [records[i] for i in (src.record_idx if src else [])])

        def only_record(src: Source):
            return records[src.record_idx[0]] if len(set(src.record_idx)) == 1 else None
        say(f"{len(plan.sources)} source(s) to check ({sum(1 for s in plan.sources.values() if s.kind == M.OWN_SITE)} websites, "
            f"{sum(1 for s in plan.sources.values() if s.kind == M.FACEBOOK)} Facebook, "
            f"{sum(1 for s in plan.sources.values() if s.kind == M.ASSET_MAP)} Asset Map).")
        progress = Progress(len(plan.sources))
        progress.draw()

        # ---- Asset Map listings (already downloaded while planning) ----
        for src in list(plan.sources.values()):
            if src.kind != M.ASSET_MAP:
                continue
            if state.get_source(src.key, fp_of(src.key)) is not None:
                progress.step(worked=False)
                continue
            progress.draw(_short(src.url))
            listing, err = plan.am_pages.get(src.key, (None, "page was not fetched"))
            try:
                r = extract_asset_map(src.key, src.url, listing, err, ctx, record=only_record(src))
            except (OllamaUnavailable, KeyboardInterrupt):
                raise
            except Exception as exc:
                log.exception("Asset Map step crashed for %s", src.key)
                r = new_result(M.ASSET_MAP, src.key, src.url)
                r["status"], r["reason"] = "failed", f"unexpected problem ({type(exc).__name__}: {str(exc)[:100]})"
            state.put_source(src.key, fp_of(src.key), r)
            _log_source_result(src.key, r)
            progress.step()

        # ---- Facebook pages next, one at a time: their About page may name the church's own website ----
        for src in list(plan.sources.values()):
            if src.kind != M.FACEBOOK:
                continue
            r = state.get_source(src.key, fp_of(src.key))
            worked = r is None and not src.disabled_reason
            if r is None and src.disabled_reason:
                r = new_result(M.FACEBOOK, src.key, src.url)
                r["status"], r["reason"] = "failed", src.disabled_reason
                state.put_source(src.key, fp_of(src.key), r)
            elif r is None:
                progress.draw(f"Facebook: {src.page_key}")
                try:
                    crawl = crawl_facebook(src.page_key, cfg, fetcher, stop)
                    r = extract_facebook(src.key, src.url, crawl, ctx, record=only_record(src))
                except StopRequested:
                    raise KeyboardInterrupt()
                except (OllamaUnavailable, KeyboardInterrupt):
                    raise
                except Exception as exc:
                    log.exception("Facebook step crashed for %s", src.key)
                    r = new_result(M.FACEBOOK, src.key, src.url)
                    r["status"], r["reason"] = "failed", f"unexpected problem ({type(exc).__name__}: {str(exc)[:100]})"
                state.put_source(src.key, fp_of(src.key), r)
                _log_source_result(src.key, r)
            if _adopt_discovered_site(plan, cfg, src, r):
                progress.set_total(len(plan.sources))
            progress.step(worked=worked)

        # ---- the websites: crawl in threads, extract here one at a time ----
        threaded: list[Source] = []
        for src in plan.sources.values():
            if src.kind != M.OWN_SITE:
                continue
            saved = state.get_source(src.key, fp_of(src.key))
            if saved is not None and not str(saved.get("reason", "")).startswith("unexpected problem"):
                progress.step(worked=False)
                continue
            threaded.append(src)            # not done yet, or it crashed last time (a bug, so try again)

        pending = deque(threaded)
        inflight: dict = {}
        pool = ThreadPoolExecutor(max_workers=cfg.fetch_concurrency, thread_name_prefix="crawl")
        try:
            def submit_more() -> None:
                while pending and len(inflight) < cfg.fetch_concurrency + 1:
                    s = pending.popleft()
                    say(f"starting {s.key} ...")
                    hints = sorted({w for i in s.record_idx for w in name_hint_words(record_name(cfg, records[i]))})
                    inflight[pool.submit(_crawl_job, s, cfg, fetcher, stop, hints)] = s
                    if progress.current == "" or len(inflight) == 1:
                        progress.draw(_short(s.url))

            submit_more()
            while inflight:
                done, _ = wait(list(inflight), timeout=0.5, return_when=FIRST_COMPLETED)
                for fut in done:
                    src = inflight.pop(fut)
                    progress.draw(_short(src.url))
                    try:
                        crawl = fut.result()
                    except StopRequested:
                        raise KeyboardInterrupt()
                    except Exception as exc:         # a crashed crawl must not stop the run
                        log.exception("Crawl crashed for %s", src.key)
                        r = new_result(src.kind, src.key, src.url)
                        r["status"], r["reason"] = "failed", f"unexpected problem while reading it ({type(exc).__name__}: {str(exc)[:100]})"
                        state.put_source(src.key, fp_of(src.key), r)
                        progress.step()
                        continue
                    try:
                        r = extract_own_site(src.key, src.url, crawl, ctx, record=only_record(src))
                    except (OllamaUnavailable, KeyboardInterrupt):
                        raise
                    except Exception as exc:
                        log.exception("Extraction crashed for %s", src.key)
                        r = new_result(src.kind, src.key, src.url)
                        r["status"], r["reason"] = "failed", f"unexpected problem while extracting ({type(exc).__name__}: {str(exc)[:100]})"
                    r["crawl_seconds"] = round(getattr(crawl, "seconds", 0.0), 1)
                    state.put_source(src.key, fp_of(src.key), r)
                    _log_source_result(src.key, r)
                    del crawl
                    progress.step()
                submit_more()
        except KeyboardInterrupt:
            interrupted = True
            stop.set()
        finally:
            if pending or inflight:      # leaving early (Ctrl+C or an error): tell the crawl threads to wrap up
                stop.set()
            pool.shutdown(wait=True, cancel_futures=True)
        if interrupted:
            raise KeyboardInterrupt()
        progress.finish()
        progress = None

        # ---- compare every record to what was found ----
        say("All sources are read. Comparing the JSON with what was found...")
        shared = shared_site_keys(plan)
        all_rows: list[Row] = []
        summary: list[dict] = []
        for rp in plan.records:
            rec = records[rp.idx]
            results = [state.get_source(k, fp_of(k)) for k in rp.source_keys]
            results = [r for r in results if r is not None] + list(rp.pre_failures)
            is_shared = any(k in shared for k in rp.source_keys)
            try:
                view = build_view(rec, cfg, results, shared=is_shared)
            except Exception as exc:                 # never let one record stop the report
                log.exception("Could not combine the results for %s", rp.rid)
                view = RecordView(all_failed_reason=f"internal problem while combining results ({type(exc).__name__}: {str(exc)[:100]}); see run.log")
            if rp.discovered_site:
                view.notes.append(f"discovered site: {rp.discovered_site}")
            rows = compare_record(rec, cfg, view, rp.rid, rp.name)
            all_rows.extend(rows)
            s = summarize_record(rp.rid, rp.name, view, rows)
            s["website"] = rp.url
            s["pdfs"] = [u for r in results for u in (r.get("pdfs") or [])]
            summary.append(s)

        meta = run_meta(cfg, args, run_started, len(plan.records))
        path = write_excel(cfg, all_rows, summary, meta)
        write_results_files(cfg, all_rows, meta)
        n_review = sum(1 for r in all_rows if r.status in M.REVIEW_STATUSES)
        n_parishes = len({r.id for r in all_rows if r.status in M.REVIEW_STATUSES})
        saved = {k: state.get_source(k, fp_of(k)) for k in plan.sources}
        _log_timings(saved)
        tell("")
        tell(f"Done in {_hms(time.monotonic() - started)}. {n_review} item(s) to review across {n_parishes} parish(es).")
        tell(f"Results: {path}  (also results.json, results.csv and suggested_patch.json in the same folder)")
        return 0
    except KeyboardInterrupt:
        stop.set()
        if progress:
            progress.finish()
        tell("Stopped. Your progress is saved; run it again to pick up where it left off.")
        return 130
    except OllamaUnavailable as exc:
        if progress:
            progress.finish()
        tell(f"The AI model stopped responding: {exc}")
        tell("Your progress is saved. Open the Ollama app, then run this again to continue.")
        return 3
    finally:
        stop.set()
        try:
            fetcher.close()
        except Exception:
            pass
        client.unload()        # give the model's memory back to your Mac
        client.close()
        state.close()


def source_fingerprint(fp: str, cfg: Config, recs: list[dict]) -> str:
    """The settings fingerprint plus the JSON values of the parish(es) using a source: a group confirmed by rule
    from the JSON's values must be redone when those values change."""
    import hashlib
    vals = [[r.get(f.key) for f in cfg.fields if f.checkable] for r in recs]
    return fp + ":" + hashlib.sha1(json.dumps(vals, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:16]


def run_meta(cfg: Config, args, started: datetime, n_parishes: int) -> dict:
    """What tells one run's results apart from another's (Summary sheet and results.json)."""
    return {
        "run_at": started.isoformat(timespec="seconds"),
        "input_file": cfg.input_json.name if cfg.input_json else "",
        "model": cfg.model,
        "parishes": n_parishes,
        "fields": sorted(cfg.only_fields) if cfg.only_fields else [],
        "ids_file": Path(args.ids_file).name if getattr(args, "ids_file", None) else "",
        "only": getattr(args, "only", None) or "",
    }


def _crawl_job(src: Source, cfg: Config, fetcher: Fetcher, stop: threading.Event, hints: list):
    return crawl_site(src.url, cfg, fetcher, stop, hint_words=hints)


def _adopt_discovered_site(plan: Plan, cfg: Config, src: Source, result: dict | None) -> bool:
    """If a Facebook page lists the church's own website, crawl that website too ("discovered site")."""
    if not result or result.get("status") != "ok":
        return False
    for d in result.get("candidates", []):
        if d.get("field") != "@website" or not d.get("value"):
            continue
        if classify_url(d["value"], cfg.skip_domains) != KIND_OWN:
            continue
        skey = site_key(d["value"], cfg.shared_host_domains)
        site = plan.sources.get(skey)
        if site is None:
            site = plan.sources[skey] = Source(skey, M.OWN_SITE, d["value"], discovered_from=src.url)
        for ri in src.record_idx:
            if ri not in site.record_idx:
                site.record_idx.append(ri)
            for rp in plan.records:
                if rp.idx == ri and skey not in rp.source_keys:
                    rp.source_keys.append(skey)
                    rp.discovered_site = d["value"]
                    rp.notes.append(f"discovered site: {d['value']} (found on the Facebook page)")
        return True
    return False


def _short(url: str) -> str:
    return host_of(url) or url


def _seconds(r: dict) -> float:
    return float(r.get("crawl_seconds") or 0) + float(r.get("elapsed") or 0)


def _log_source_result(key: str, r: dict) -> None:
    took = f" [{_hms(_seconds(r))}: reading {_hms(r.get('crawl_seconds') or 0)}, extracting {_hms(r.get('elapsed') or 0)}]"
    if r["status"] == "ok":
        extra = " (hit the page limit)" if r.get("cap_hit") else ""
        say(f"{key}: read {r['pages_crawled']} page(s){extra}; {len(r['candidates'])} item(s) found; "
            f"{r['llm_calls']} model call(s), {r['llm_cache_hits']} reused, {r.get('llm_skipped', 0)} not needed"
            + (f"; {sum(1 for i in r['issues'] if i[1] == 'rejected_unverified')} answer(s) rejected as unverifiable" if r["issues"] else "")
            + took)
    else:
        say(f"{key}: could not use it: {r['reason']}" + took)


def _log_timings(saved: dict) -> None:
    """End of run.log: model calls skipped, and the 10 slowest sites (to see where the time goes)."""
    done = {k: r for k, r in saved.items() if r}
    say(f"Model calls not needed (your data's values were already on the page): "
        f"{sum(int(r.get('llm_skipped') or 0) for r in done.values())}; "
        f"model calls made: {sum(int(r.get('llm_calls') or 0) for r in done.values())}; "
        f"answers reused from earlier runs: {sum(int(r.get('llm_cache_hits') or 0) for r in done.values())}.")
    slow = sorted(done.items(), key=lambda kv: -_seconds(kv[1]))[:10]
    if slow:
        say("Slowest sites (reading + extracting):")
        for k, r in slow:
            say(f"  {_hms(_seconds(r)):>12}  {k}  ({r.get('pages_crawled', 0)} page(s), {r.get('llm_calls', 0)} model call(s))")


def _tidy_old_outputs(cfg: Config) -> None:
    """Older versions wrote several files straight into output/. Move what is still useful into output/.work/
    (so saved progress survives) and delete the rest, leaving output/ with just the Excel file."""
    import shutil
    out, work = cfg.output_dir, cfg.work_dir
    work.mkdir(parents=True, exist_ok=True)
    for name in ("cache", "progress.sqlite"):
        old, new = out / name, work / name
        if old.exists() and not new.exists():
            try:
                shutil.move(str(old), str(new))
            except OSError:
                pass
    for name in ("cache", "progress.sqlite", "run.log", "verification_report.csv", "needs_review.csv",
                 "verification_summary.csv", "proposed_updates.json", "results_raw.json", "pdfs_found.txt"):
        p = out / name
        try:
            shutil.rmtree(p) if p.is_dir() else p.unlink()
        except OSError:
            pass


def _clear_cache(cfg: Config) -> None:
    import shutil
    try:
        shutil.rmtree(cfg.cache_dir)
    except FileNotFoundError:
        pass
    except OSError as exc:
        log.warning("Could not clear the page cache: %s", exc)
