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
- `tools/format_phones.py` — rewrites every phone number in the data files as (XXX) XXX-XXXX (`--check` to preview). The site applies the same rule when it shows a number.
- `tools/diocese-boundaries/` — builds `data/diocesan-boundaries.geojson`. Each diocese's counties are listed in `dioceses.json`; edit that, then run `npm install && npm run build` in that folder. Navajoland uses the Navajo Nation reservation outline (`navajo-nation.geojson`) instead of counties.

**Images**
- `img/compass-rose-tec.svg` — site logo and favicon

**Parish checker**

Checks the facts in `parishes.json` against each parish's website, Facebook page, and Episcopal Asset Map listing, using a local AI model (Ollama). It writes one Excel file, `output/Parish Check Results.xlsx`, and never changes `parishes.json`. See [INSTRUCTIONS.md](parish-webcrawler/INSTRUCTIONS.md) for how to run it.

- `run.command` — double-click launcher; installs what's needed on first run, asks for the JSON file, runs the check, opens the results
- `scraper.py` — command-line entry point (`--input FILE`, optional `--only TEXT`, `--fresh`)
- `config.yaml` — advanced settings: which fields to check, AI model, page limits
- `style_example.json` — formatting examples (time style, punctuation) for the AI to follow
- `requirements.txt` — Python libraries the tool needs
- `selftest.py` — tests the tool's own logic offline
- `INSTRUCTIONS.md` — plain-language setup and usage guide
- `parishcheck/` — the tool's code:
  - `pipeline.py` — runs the whole check, start to finish
  - `config.py` — reads `config.yaml` and `style_example.json`
  - `crawler.py` — finds and downloads the pages of a parish website
  - `fetcher.py` — downloads pages politely (delays, retries, caching, optional headless browser)
  - `robots.py` — reads each site's robots.txt rules
  - `facebook.py` — reads public Facebook pages, logged out, best effort
  - `assetmap.py` — reads Episcopal Asset Map listings
  - `cleaner.py` — turns web pages into plain text
  - `rules.py` — finds phones, emails, addresses, and livestream links without the AI
  - `digest.py` — boils a site down to short numbered lines for each group of fields, so the AI reads less
  - `llm.py` — asks the local AI model for the rest (one question per group per site) and checks its answers
  - `extract.py` — collects everything found on one source
  - `merge.py` — combines all sources into one answer per field
  - `normalizers.py` — puts phones, emails, addresses, names, and lists in a comparable form
  - `times.py` — reads and formats service times and schedules
  - `comparators.py` — decides whether the data and the website agree
  - `report.py` — compares each field and writes the Excel results file
  - `state.py` — saves progress so a stopped run can resume
  - `urls.py` — sorts and ranks web addresses
  - `models.py` — shared building blocks

**Other**
- `CNAME` — custom domain for GitHub Pages
- `_config.yml` — GitHub Pages settings; keeps the notes, README, COPYRIGHT, parish checker and tools off the live site
- `COPYRIGHT.md` — licensing and reuse terms
- `notes-archive/` — archived planning notes

## Reuse

Some parts of this repo may be freely used with attribution, others require permission. Unless otherwise noted, this means that the code that makes the website function is free to reuse, while the parish data is not. See [COPYRIGHT.md](COPYRIGHT.md) and the [About page](about.html) for more info.