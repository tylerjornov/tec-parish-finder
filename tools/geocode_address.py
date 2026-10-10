#!/usr/bin/env python3
"""Find coordinates for a US street address, trying the most precise source first.

Sources, in order (the first one that passes every check wins):
    1. Google Geocoding API   needs GOOGLE_MAPS_API_KEY; accepted only for ROOFTOP or RANGE_INTERPOLATED
    2. US Census geocoder     free, no key; address-range interpolation
    3. OpenStreetMap Nominatim free, no key; accepted only for a house-number match

Every candidate must fall inside North Carolina and agree with the city (and ZIP, when given). A result
that fails is reported with the reason, never written.

If no source finds the address, the script says so. Add a pin by hand in that case: open the parish
in Google Maps, right-click the building and copy the coordinates.

    python3 tools/geocode_address.py "3430 Old US Highway 70, Cleveland, NC 27013"
    python3 tools/geocode_address.py --file data/wip-dioceses/north-carolina/diocese-of-north-carolina.json \
        --id christ-episcopal-church-3430-old-us-hwy-70-cleveland-nc --write
"""

from __future__ import annotations

import argparse
import json
import os
import re
import ssl
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

USER_AGENT = "tec-parish-finder-geocoder/1.0 (tylerajornov@icloud.com)"
NC_BOUNDS = (33.8, 36.6, -84.4, -75.4)  # south, north, west, east
ROOT = Path(__file__).resolve().parent.parent

try:  # some Python installs ship without a CA bundle; certifi supplies one
    import certifi

    _SSL = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    _SSL = ssl.create_default_context()


class Skipped(Exception):
    """A source that cannot run here (no key, not configured). Not an error."""


def _get_json(url: str, params: dict) -> object:
    req = urllib.request.Request(f"{url}?{urllib.parse.urlencode(params)}", headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=30, context=_SSL) as resp:
        return json.loads(resp.read().decode("utf-8"))


def parse_parts(address: str) -> tuple[str, str, str]:
    """"3430 Old US Hwy 70, Cleveland, NC 27013" -> (city, state, zip)."""
    parts = [p.strip() for p in address.split(",")]
    city = parts[-2] if len(parts) >= 3 else ""
    m = re.search(r"\b([A-Z]{2})\b\s*(\d{5})?(?:-\d{4})?\s*$", parts[-1] if parts else "")
    state, zip_code = (m.group(1), m.group(2) or "") if m else ("", "")
    return city, state, zip_code


def in_nc(lat: float, lon: float) -> bool:
    south, north, west, east = NC_BOUNDS
    return south <= lat <= north and west <= lon <= east


def agrees(city: str, zip_code: str, matched: str) -> bool:
    if city and city.lower() not in matched.lower():
        return False
    if zip_code and zip_code not in matched:
        return False
    return True


def from_google(address: str, city: str, zip_code: str) -> dict | None:
    key = os.environ.get("GOOGLE_MAPS_API_KEY")
    if not key:
        raise Skipped("GOOGLE_MAPS_API_KEY not set")
    data = _get_json("https://maps.googleapis.com/maps/api/geocode/json",
                     {"address": address, "components": "country:US", "key": key})
    if not isinstance(data, dict) or data.get("status") != "OK" or not data.get("results"):
        return None
    top = data["results"][0]
    quality = top["geometry"].get("location_type")
    if quality not in ("ROOFTOP", "RANGE_INTERPOLATED"):
        return None
    loc = top["geometry"]["location"]
    if not in_nc(loc["lat"], loc["lng"]) or not agrees(city, zip_code, top["formatted_address"]):
        return None
    return {"lat": loc["lat"], "lon": loc["lng"], "source": f"google ({quality})", "matched": top["formatted_address"]}


def from_census(address: str, city: str, zip_code: str) -> dict | None:
    data = _get_json("https://geocoding.geo.census.gov/geocoder/locations/onelineaddress",
                     {"address": address, "benchmark": "Public_AR_Current", "format": "json"})
    for match in data.get("result", {}).get("addressMatches", []):
        lat, lon = match["coordinates"]["y"], match["coordinates"]["x"]
        if in_nc(lat, lon) and agrees(city, zip_code, match["matchedAddress"]):
            return {"lat": lat, "lon": lon, "source": "census (address range)", "matched": match["matchedAddress"]}
    return None


def from_nominatim(address: str, city: str, zip_code: str) -> dict | None:
    time.sleep(1.1)  # the public Nominatim service allows one request per second
    results = _get_json("https://nominatim.openstreetmap.org/search",
                        {"q": address, "format": "json", "limit": 3, "countrycodes": "us", "addressdetails": 1})
    for r in results if isinstance(results, list) else []:
        lat, lon = float(r["lat"]), float(r["lon"])
        has_number = bool(r.get("address", {}).get("house_number"))
        if has_number and in_nc(lat, lon) and agrees(city, zip_code, r["display_name"]):
            return {"lat": lat, "lon": lon, "source": f"openstreetmap ({r.get('type', '?')})", "matched": r["display_name"]}
    return None


SOURCES = [from_google, from_census, from_nominatim]


def geocode(address: str) -> tuple[dict | None, list[str]]:
    city, _state, zip_code = parse_parts(address)
    notes = []
    for source in SOURCES:
        try:
            found = source(address, city, zip_code)
        except Skipped as why:
            notes.append(f"{source.__name__}: skipped, {why}")
            continue
        except Exception as exc:  # a dead service should not stop the others
            notes.append(f"{source.__name__}: error {exc}")
            continue
        if found:
            return found, notes
        notes.append(f"{source.__name__}: no acceptable match")
    return None, notes


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("address", nargs="?")
    ap.add_argument("--file", type=Path, help="a diocese JSON file")
    ap.add_argument("--id", help="the record to geocode in --file")
    ap.add_argument("--write", action="store_true", help="save the coordinates into --file")
    args = ap.parse_args()

    if args.file:
        path = args.file if args.file.is_absolute() else ROOT / args.file
        records = json.loads(path.read_text(encoding="utf-8"))
        targets = [r for r in records if (not args.id or r["id"] == args.id)]
        if not targets:
            print(f"no record matched {args.id!r}")
            return 1
        for r in targets:
            found, notes = geocode(r["address"])
            print(f"{r['id']}: {r['address']}")
            if found:
                print(f"  {found['lat']:.5f}, {found['lon']:.5f}  via {found['source']}  [{found['matched']}]")
                if args.write:
                    r["lat"], r["lon"] = round(found["lat"], 5), round(found["lon"], 5)
            else:
                print("  not found; " + "; ".join(notes))
        if args.write:
            path.write_text(json.dumps(records, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            print(f"saved {path.relative_to(ROOT)}")
        return 0

    if not args.address:
        ap.error("give an address, or --file with --id")
    found, notes = geocode(args.address)
    if found:
        print(f"{found['lat']:.5f}, {found['lon']:.5f}  via {found['source']}")
        print(f"matched: {found['matched']}")
        return 0
    print("not found")
    print("\n".join("  " + n for n in notes))
    return 2


if __name__ == "__main__":
    sys.exit(main())
