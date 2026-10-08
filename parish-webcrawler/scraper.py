#!/usr/bin/env python3
"""scraper.py - check a JSON file of parishes against what their websites say.

HOW TO RUN (the easy way): double-click run.command.  The commands below are for when you want more control.

    python scraper.py --config config.yaml [--dry-run] [--only TEXT] [--limit-sites N] [--max-pages N]
                      [--fresh] [--apply-discrepancies] [--no-facebook]

ASSUMPTIONS I MADE (you asked me not to ask questions):
  * The parish file is a JSON list of flat objects (one per parish). Its location is the `input_json` line in
    config.yaml (this folder is meant to sit inside the tec-parish-finder repo, so it points at ../data/parishes.json).
    If your columns are named differently, only config.yaml needs editing.
  * The default model is qwen2.5:3b (set in config.yaml). If it is not on your Mac, run.command downloads it.
  * Times are printed in 12-hour style ("8:30 AM", "12:00 noon") unless config.yaml says 24h.
  * Addresses are written in the house style of your example ("1002 S Main St., Greenville, SC": no ZIP code).
  * "Today" is your Mac's date, used so the model reports services that are in effect now.
  * Your original JSON file is NEVER changed. All results go to the output folder.
  * Facebook and the Episcopal Asset Map are lower-priority, best-effort sources (see INSTRUCTIONS.md).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

if sys.version_info < (3, 9):
    print("This tool needs Python 3.9 or newer. Please install a newer Python from python.org.")
    sys.exit(1)

from parishcheck.config import ConfigError, load_config      # noqa: E402
from parishcheck import pipeline                              # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="Verify a JSON file of parishes against their websites (and Facebook / the Episcopal Asset Map).")
    ap.add_argument("--config", default="config.yaml", help="settings file (default: config.yaml)")
    ap.add_argument("--dry-run", action="store_true",
                    help="show the plan and a time estimate, extract nothing")
    ap.add_argument("--only", metavar="TEXT", help="only parishes whose id or name contains TEXT (good for testing)")
    ap.add_argument("--limit-sites", type=int, metavar="N", help="only the first N different websites")
    ap.add_argument("--max-pages", type=int, metavar="N", help="download at most N pages per website")
    ap.add_argument("--fresh", action="store_true", help="forget saved progress and cached pages; start over")
    ap.add_argument("--apply-discrepancies", action="store_true",
                    help="in proposed_updates.json also overwrite values that DISCREPANCY rows say are different")
    ap.add_argument("--no-facebook", action="store_true", help="do not look at Facebook pages at all")
    return ap


def find_config(arg: str) -> Path:
    p = Path(arg).expanduser()
    if p.is_absolute() or p.exists():
        return p
    return HERE / arg


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        overrides = {"max_pages_per_site": args.max_pages} if args.max_pages else None
        cfg = load_config(find_config(args.config), overrides)
        if args.dry_run:
            return pipeline.dry_run(cfg, args)
        return pipeline.run(cfg, args)
    except ConfigError as exc:
        print("\n" + str(exc))
        return 2
    except KeyboardInterrupt:
        print("\nStopped. Your progress is saved; run the same command again to continue.")
        return 130
    except Exception as exc:                      # last safety net: say what happened in plain words
        import logging
        import traceback
        logging.getLogger("parishcheck").error("Unexpected crash:\n%s", traceback.format_exc())
        print(f"\nSomething unexpected went wrong ({type(exc).__name__}: {str(exc)[:200]}).")
        print("Your progress so far is saved, so running the same command again will continue from there.")
        print("The technical details are in the run.log file inside your output folder.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
