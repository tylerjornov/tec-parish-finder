# Optional prompt for Grok Build / another generator

Use this only if you want a prettier UI pass. Do not let it replace `data/` or `scripts/xlsx_to_json.py`.

```
Build a static GitHub Pages site (HTML/CSS/JS only, no backend) for a TEC parish directory.

Must load ./data/parishes.json and ./data/schema.json already in the repo.
Do not invent a CMS or database.

UI:
- Dark theme
- Left: filters
- Center: list (Finder-like rows)
- Right: Leaflet + OpenStreetMap map
- Mobile: stack filters / list / map

Filters from schema.list_options plus state, diocese, text search, verified status.
Location box: address, city+state, or lat,lon. Sort results by haversine miles. Never show “nothing near you.” Always list nearest matches even if far. Parishes without lat/lon stay in the list, skip distance.

Each card: name, address, miles if known, churchmanship, verified, website link.

Keep about.html copy as-is (value-neutral disclosure).

Do not change JSON field names:
name, website, address, state, diocese, churchmanship, women_serve_priests, wo_affirmed, lgbt_serve_priests, lgbt_ordination_affirmed, ssm, spectrum, contact, notes, verified, lat, lon, asa.
```
