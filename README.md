# TEC Parish Directory (static)

GitHub Pages site. No server, no database.

## What this is

- `index.html` — list + map + filters
- `about.html` — value-neutral disclosure
- `data/parishes.json` — generated from your spreadsheet
- `data/schema.json` — field names and allowed values
- `scripts/xlsx_to_json.py` — pipeline: Excel → JSON

The sample JSON has three fake parishes so the page runs before the real list exists.

## Local preview

Open `index.html` in a browser, or:

```bash
python3 -m http.server 8080
```

Then http://localhost:8080

## Publish on GitHub Pages

1. New public repo (example: `tec-directory`).
2. Upload this folder as the repo root.
3. Settings → Pages → Deploy from branch → `main` / `/ (root)`.
4. Site URL: `https://YOURUSER.github.io/tec-directory/`

Later: Settings → Pages → Custom domain.

## Update the church list

1. Keep column headers exactly as in `data/schema.json`.
2. Optional extra columns the site already reads: `Diocese`, `Lat`, `Lon`, `ASA` (average Sunday attendance).
3. Run:

```bash
python3 scripts/xlsx_to_json.py path/to/Episcopal_Churches_US.xlsx
```

4. Commit `data/parishes.json`.

Addresses without `Lat`/`Lon` still appear in the list. They are omitted from distance sort until geocoded.
