#!/usr/bin/env python3
"""scraper.py - check a JSON file of parishes against what their websites say.

The easy way to run it is to double-click run.command. From Terminal:

    python scraper.py --input path/to/parishes.json [--only TEXT] [--ids-file FILE] [--fields a,b,c] [--fresh]

The results are output/Parish Check Results.xlsx (for people) and results.json, results.csv and
suggested_patch.json (for scripts) in the same folder. Your JSON file is never changed.
Settings (model, crawl limits, which fields to check) are in config.yaml.
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

from parishcheck.config import ConfigError, limit_fields, load_config      # noqa: E402
from parishcheck import pipeline                              # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="Verify a JSON file of parishes against their websites (and Facebook / the Episcopal Asset Map).")
    ap.add_argument("--input", required=True, metavar="FILE", help="the parish JSON file to check")
    ap.add_argument("--config", default="config.yaml", help="settings file (default: config.yaml)")
    ap.add_argument("--only", metavar="TEXT", help="only parishes whose id or name contains TEXT (good for testing)")
    ap.add_argument("--ids-file", metavar="FILE", help="only the parishes whose ids are listed in FILE (one id per line)")
    ap.add_argument("--fields", metavar="A,B,C", help="only check these fields, e.g. rector_name,sunday_services")
    ap.add_argument("--fresh", action="store_true", help="forget saved progress and cached pages; start over")
    return ap


def find_config(arg: str) -> Path:
    p = Path(arg).expanduser()
    if p.is_absolute() or p.exists():
        return p
    return HERE / arg


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        cfg = load_config(find_config(args.config))
        cfg.input_json = Path(args.input).expanduser().resolve()
        if args.fields:
            limit_fields(cfg, args.fields)
        return pipeline.run(cfg, args)
    except ConfigError as exc:
        print("\n" + str(exc))
        return 2
    except KeyboardInterrupt:
        print("\nStopped. Your progress is saved; run it again to continue.")
        return 130
    except Exception as exc:                      # last safety net: say what happened in plain words
        import logging
        import traceback
        logging.getLogger("parishcheck").error("Unexpected crash:\n%s", traceback.format_exc())
        print(f"\nSomething unexpected went wrong ({type(exc).__name__}: {str(exc)[:200]}).")
        print("Your progress so far is saved, so running it again will continue from there.")
        print("The technical details are in output/.work/run.log.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
