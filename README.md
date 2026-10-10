# Episcopal Compass

## Project Overview

Episcopal Compass is an unofficial directory of [PECUSA](https://en.wikipedia.org/wiki/Episcopal_Church_(United_States)) parishes in the United States and territories, built to make it easy to find a parish, with the ability to filter by certain criteria. 

Users can search for parishes based on location, and filter by details such as churchmanship, women's ordination, and same-sex marriage. Results are sorted by distance, and results can be viewed either on a map or a list. Each parish has a detail view, and entries are marked as either **Verified** or **Unverified** depending on how the information was gathered.

The aim of this project is to be equally useful to people across the theological spectrum, not to campaign for or against any position. Although the creator has some very strong beliefs on a variety of the issues mentioned above, his goal was not to push that onto those using Episcopal Compass, but rather to enable people to make informed choices about where to visit.

Parish data lives in [parishes.json](data/parishes.json). Aside from that, the majority of this repo is the structure of the website; The folder [parish-webcrawler](parish-webcrawler/) is a separate tool for checking that data against parish websites. 

As can be seen in [COPYRIGHT.md](COPYRIGHT.md), while the [data](data/parishes.json) gathered through verification may not be used without permission, the rest of the code in the repo is free to duplicate and modify, so that other denominations, as well as secular organizations, can more easily build similar websites with Episcopal Compass as a template.

## Code Directory

**Pages**
- `index.html` — main page, shows map and list
- `definitions.html` — definitions of terms used in the directory
- `about.html` — more information on the website and creator
- `institutions.html` — institutions page (coming soon)
- `terms.html` — terms and conditions
- `acknowledgements.html` — data sources and icon artwork credits

**Scripts**
- `js/settings.js` — display settings menu (theme, contrast, motion, pin size, diocesan boundaries)
- `js/diocesan-boundary-overlay.js` — draws the diocesan boundaries and names on the map
- `js/nav.js` — site navigation menu
- `js/app.js` — directory logic: filters, search, distance sorting, map, and parish detail dialog

**Styles**
- `css/app.css` — styles for all pages

**Data**
- `data/parishes.json` — parish info
- `data/schema.json` — what each field in parishes.json holds, and the choices offered in the filter dropdowns, and the short labels for list-view tags
- `data/diocesan-boundaries.geojson` — diocesan boundary shapes for the map overlay; built by `tools/diocese-boundaries/`, don't edit by hand
- `data/wip-dioceses/` — dioceses still being worked on, one folder per state

**Tools**
- `tools/normalize_data.py` — formats the data files (`--check` to preview): phone numbers as (XXX) XXX-XXXX; clergy names in `rector_name`, `diocesan_bishop` and `other_clergy` with the titles Rev., Ven., Mtr., Fr., Rt. Rev. or Very Rev. ("The" before Rev. titles), no ", Rector"-style role after the rector or bishop, and roles in parentheses for other clergy; lowercase emails; a trailing slash on bare-domain URLs; straight quotes; en dashes in time, day and month ranges. The full rules are at the top of the script. The site applies the same phone rule when it shows a number.
- `tools/import_parishes.py` — merges parishes from other JSON files into `data/parishes.json`, checks them against `schema.json`, and keeps the file sorted by state, diocese, city, name and id (`--dry-run` to preview, `--sort-only` to just re-sort).
- `tools/diocese-boundaries/` — builds `data/diocesan-boundaries.geojson`. Each diocese's counties are listed in `dioceses.json`; edit that, then run `npm install && npm run build` in that folder. Navajoland uses the Navajo Nation reservation outline (`navajo-nation.geojson`) instead of counties. `npm run check` then looks for overlapping dioceses, blank land between them, unexplained enclaves/exclaves, and parish pins that sit in a different diocese than the one they're listed under; known, legitimate cases are listed in `check.js` (land parts) and `parish-exceptions.json` (parishes).

**Images**
- `img/compass-rose-tec.svg` — site logo and favicon

**Parish checker**

Checks a parish JSON file against each parish's own website with fixed rules (no AI): phones, emails, address, rector, livestream link and diocesan bishop. It never changes the file it checks. Double-click `run.command` and pick the file (the live `parishes.json`, or a file of candidate additions); results land in `parish-webcrawler/output/`:

- `corrections.json` — only the changes the websites make certain (fill a blank, or replace a value the site no longer shows), written so a script or an AI can apply them exactly
- `Corrections.xlsx` — the same changes, for people
- `Website Problems.xlsx` — parishes whose website is blocked, gone, expired or unreadable, colour-coded

Files:
- `run.command` — double-click launcher; installs what's needed on first run, runs the check, opens both Excel files
- `parish_check.py` — the whole tool (settings at the top; `--selftest` checks its rules offline)

**Other**
- `CNAME` — custom domain for GitHub Pages
- `_config.yml` — GitHub Pages settings; keeps the notes, README, COPYRIGHT, parish checker and tools off the live site
- `COPYRIGHT.md` — licensing and reuse terms
- `notes-archive/` — archived planning notes

## Reuse

Some parts of this repo may be freely used with attribution, others require permission. Unless otherwise noted, this means that the code that makes the website function is free to reuse, while the parish data is not. See [COPYRIGHT.md](COPYRIGHT.md) and the [About page](about.html) for more info.