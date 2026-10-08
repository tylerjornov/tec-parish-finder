# Parish Checker: Instructions

This checks the facts in `data/parishes.json` (phones, emails, addresses, clergy, service times) against each parish's website. It tells you what matches and what doesn't. It never changes your data file.

## Set up (once)

1. Open the **Terminal** app.
2. Type `cd ` (with a space after it). Drag this folder from Finder into the Terminal window. Press Return.
3. Run:

```bash
chmod +x run.command
```

4. In Finder, right-click `run.command`, choose **Open**, then click **Open**. (After this, double-clicking works.)

## Run it

1. Double-click `run.command`.
2. The first time, it installs things and downloads the AI model. This takes 10 minutes or more.
3. It shows a plan and a time estimate. Type `y` and press Return to start. Type `n` to quit.
4. When it finishes, a folder opens. Open `needs_review.csv` in Numbers or Excel.

To stop: press **Ctrl+C**. Run it again later and it continues where it stopped.

## Try one parish first

In Terminal, in this folder, use part of any `id` or `name` from `parishes.json`:

```bash
./run.command --only st-thaddeus
```

## Read the results

Open `needs_review.csv`. Each row is one field of one parish.

| Status | Meaning |
|---|---|
| CORRECT | Your data matches the website. |
| DISCREPANCY | Both have a value, and they differ. |
| PARTIAL | Some items match, some don't. |
| JSON_ONLY | Your data has it; the website doesn't. |
| SITE_ONLY | The website has it; your data is blank. |
| BOTH_MISSING | Neither has it. |
| UNCLEAR | Could not tell. The `reason` column says why. |
| NOT_CHECKABLE | A field this tool skips (id, lat, lon...). |

Useful columns: `json_value` (your data), `site_value` (the website), `source_url` (the page), `evidence` (the words on the page).

`proposed_updates.json` is a copy of your data with blank fields filled in from the websites. Check it before you use it. The original is never touched.

## What it checks

It checks facts a website states plainly: name, diocese, address, phones, emails, website, livestream link, rector, bishop, service times, languages, music, childcare, formation, parking, accessibility.

It does **not** check opinion-type columns (churchmanship, clergy gender or LGBT status, ordination, same-sex marriage, alignment). Those are copied through unchanged.

## Settings

All settings are in `config.yaml`. Open it in TextEdit. Lines starting with `#` are notes.

- `input_json:` where `parishes.json` is. If you move this folder, change this line.
- `model:` the AI model.
- `time_format:` `12h` or `24h`.
- `max_pages_per_site:` how many pages to read per website.

Facebook is off if you add `--no-facebook` (for example `./run.command --no-facebook`), or set `mode: skip` under `facebook.com:` in `config.yaml`.

## If something goes wrong

- **"Can't reach Ollama"**: open the Ollama app, wait a few seconds, run again.
- **"Model is not downloaded"**: run `ollama pull qwen2.5:3b`, or let `run.command` do it.
- **"Permission denied"**: repeat the `chmod` step above.
- **Many UNCLEAR rows**: read the `reason` column. Common causes: the website blocks automated visitors, or Facebook asked for a login.
- **Slow**: expect 2 to 5 minutes per website. You can leave it running. Ctrl+C is safe.
- **Start over**: `./run.command --fresh`
- **Test the tool itself**: `.venv/bin/python selftest.py` should end with `ALL TESTS PASSED`.

## Limits

- Facebook pages are often blocked.
- Service times written only in images or PDFs are missed.
- The Episcopal Asset Map can be out of date.
- A small AI model makes mistakes. Treat the results as a to-do list to review, not as the final answer.
- Everything this tool creates stays inside this folder (`output/` and `.venv/`).
