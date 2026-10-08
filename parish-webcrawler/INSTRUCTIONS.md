# Parish Checker: Instructions

This checks the facts in `data/parishes.json` (phones, emails, addresses, clergy, service times) against each parish's website. It tells you what matches and what doesn't. It never changes your data file.

## Before you start

You need two programs on your Mac:

- **Python 3.9 or newer.** To check, open Terminal, type `python3 --version`, and press Return. If it says "command not found" or a number below 3.9, download the macOS installer from https://www.python.org/downloads/ and run it.
- **Ollama.** Download it from https://ollama.com/download, move it to Applications, and open it once.

## Set up (once)

1. Open the **Terminal** app.
2. In Terminal, type `cd` followed by a space. Then drag the `parish-webcrawler` folder from Finder into the Terminal window. Its path should appear right after `cd `. Check that the line reads `cd /Users/...parish-webcrawler`, then press Return.
3. Type this line and press Return:

```bash
chmod +x run.command
```

Every Terminal command below assumes you have done step 2 in that Terminal window. If you open a new window, do step 2 again.

## Run it

1. In Finder, open the `parish-webcrawler` folder and double-click `run.command`. A Terminal window opens.
   - If macOS says it can't be opened: open **System Settings** > **Privacy & Security**, scroll down, click **Open Anyway** next to `run.command`, then double-click it again.
2. The first time, it installs things and downloads the AI model (about 2 GB). This takes 10 minutes or more.
3. It shows a plan and a time estimate, then asks `Proceed with the full run? (y/n)`. Type `y` and press Return to start. Type `n` and press Return to quit.
4. When it finishes, the `output` folder opens. Open `needs_review.csv` in Numbers or Excel.
5. Press any key to close the Terminal window.

To stop early: press **Control + C** (the Control key, not Command). Your progress is saved. Double-click `run.command` again later and it continues where it stopped.

## Try one parish first

In Terminal (after set-up step 2), type `./run.command --only` followed by a space and part of any `id` or `name` from `parishes.json`, then press Return. Example:

```bash
./run.command --only st-thaddeus
```

It still shows the plan and asks `y/n` first.

## Read the results

All results are in the `output` folder inside `parish-webcrawler`.

- `needs_review.csv`: only the rows you need to look at (DISCREPANCY, PARTIAL, JSON_ONLY, SITE_ONLY, UNCLEAR). Start here.
- `verification_report.csv`: every row, including CORRECT, BOTH_MISSING and NOT_CHECKABLE.

Each row is one field of one parish.

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

`proposed_updates.json` is a copy of your data with blank fields filled in from the websites. Check it before you use it. Your original `parishes.json` is never changed.

## What it checks

It checks facts a website states plainly: name, diocese, address, phones, emails, website, livestream link, rector, bishop, service times, languages, music, childcare, formation, parking, accessibility.

It does **not** check opinion-type columns (churchmanship, clergy gender or LGBT status, ordination, same-sex marriage, alignment). Those are copied through unchanged.

## Settings

All settings are in `config.yaml` in the `parish-webcrawler` folder. To edit it: right-click it, choose **Open With** > **TextEdit**. Lines starting with `#` are notes. Keep the colon and the space after each setting name. Use spaces, never Tab. Save with Command + S.

- `input_json:` where `parishes.json` is. If you move the `parish-webcrawler` folder out of `tec-parish-finder`, change this to the full path of `parishes.json` (for example `/Users/you/Desktop/parishes.json`).
- `model:` the AI model.
- `time_format:` `12h` or `24h`.
- `max_pages_per_site:` how many pages to read per website.

To skip Facebook for one run, type `./run.command --no-facebook` in Terminal. To skip it always, in `config.yaml` find the `facebook.com:` section and change `mode: best_effort` to `mode: skip`.

## If something goes wrong

- **"I could not reach Ollama"**: open the Ollama app, wait 10 seconds, then double-click `run.command` again.
- **"I could not find the 'ollama' command"** or **"The model download failed"**: in Terminal, type `ollama pull qwen2.5:3b` and press Return. (If you changed `model:` in `config.yaml`, use that name instead.) Then double-click `run.command` again.
- **"Permission denied" when running `run.command`**: do set-up steps 2 and 3 again.
- **"Permission denied" with a folder path and no `cd`**: you typed the folder path without `cd` in front of it. Type `cd` and a space first, then the path.
- **Many UNCLEAR rows**: read the `reason` column. Common causes: the website blocks automated visitors, or Facebook asked for a login.
- **Slow**: expect 2 to 5 minutes per website. You can leave it running. Control + C is safe.
- **Start over** (forget saved progress and downloaded pages): in Terminal, type `./run.command --fresh` and press Return.
- **Test the tool itself** (only after the first run): in Terminal, type `.venv/bin/python selftest.py` and press Return. The last line should be `ALL TESTS PASSED`.

## Limits

- Facebook pages are often blocked.
- Service times written only in images or PDFs are missed.
- The Episcopal Asset Map can be out of date.
- A small AI model makes mistakes. Treat the results as a to-do list to review, not as the final answer.
- Everything this tool creates stays inside the `parish-webcrawler` folder (`output/` and `.venv/`).
