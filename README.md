# Episcopal Compass

## Project Overview

Episcopal Compass is a directory of Episcopal Church parishes in the United States and territories, built to make parish facts easy to filter. 

Users can search for parishes based on location, and filter by details such as churchmanship, women's ordination, and same-sex marriage. Results are sorted by distance, and results can be viewed either on a map or a list. Each parish has a detail view, and entries are marked as either **Verified** or **Unverified** depending on how the information was gathered.

The aim of this project is to be equally useful to people across the theological spectrum, not to campaign for or against any position.

Parish data lives in [parishes.json](data/parishes.json), with the rest of the items in this repo being the structure of the website. As can be seen in [COPYRIGHT.md](COPYRIGHT.md), while the data gathered through verification may not be used without permission, the rest of the code in the repo is free to duplicate and modify, so that other denominations, as well as secular organizations, can more easily build similar websites with Episcopal Compass as a template.

## Code Directory

**Pages**
- `index.html` — main page, shows map and list
- `about.html` — more information on the website and creator
- `institutions.html` — institutions page (coming soon)
- `terms.html` — terms and conditions
- `acknowledgements.html` — data sources and icon artwork credits

**Scripts**
- `js/settings.js` — display settings (theme, contrast, motion)
- `js/nav.js` — site navigation menu
- `js/app.js` — directory logic: filters, search, distance sorting, map, and parish detail dialog

**Styles**
- `css/app.css` — styles for all pages

**Data**
- `data/parishes.json` — parish info
- `data/schema.json` — the choices offered in each filter dropdown

**Other**
- `CNAME` — custom domain for GitHub Pages
- `COPYRIGHT.md` — licensing and reuse terms
- `notes-archive/` — archived planning notes

## Reuse

Some parts of this repo may be freely used with attribution, others require permission. Unless otherwise noted, this means that the code that makes the website function is free to reuse, while the parish data is not. See [COPYRIGHT.md](COPYRIGHT.md) and the [About page](about.html) for more info.
