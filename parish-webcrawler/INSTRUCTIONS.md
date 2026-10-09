# Parish Checker

Checks each parish's phones, emails, address, clergy and service times against its website. It never changes your JSON file.

**Install once:** [Python 3.9+](https://www.python.org/downloads/) and [Ollama](https://ollama.com/download). Open Ollama once after installing.

## Run it

1. Double-click **`run.command`** in this folder.
   - If macOS blocks it: go to **System Settings › Privacy & Security**, click **Open Anyway**, then double-click it again.
2. Choose your parish JSON file (for example `data/parishes.json`).
3. Wait. A progress bar shows how far along it is and the time left. The first run downloads about 2 GB first.
   - To stop: press **Control + C**. Double-click `run.command` again later to continue where it stopped.
4. When it's done, **`Parish Check Results.xlsx`** opens. It's also saved in the `output` folder.
   - The same folder also gets `results.json`, `results.csv` and `suggested_patch.json`. Those are for scripts (or Claude) to read; you can ignore them.
   - While Excel has the results open you'll also see **`~$Parish Check Results.xlsx`**. That's Excel's lock file. It's harmless and disappears when you close Excel.

**Re-check only some parishes or fields** (from Terminal, after `run.command` has set things up once):
`.venv/bin/python scraper.py --input ../data/parishes.json --ids-file ids.txt --fields rector_name,sunday_services`
`--ids-file` takes a text file with one parish id per line. `--fields` takes field names separated by commas. `--only NAME` still works too.

## Read the results

Start on the **To review** sheet. Each row is one field of one parish, colour-coded:
**Different on website** · **Missing from your data** · **Not found on website** · **Partly matches** · **Couldn't tell**.
Compare **Your data** with **Website says**, and click **Source page** to check. The **Key** sheet explains each label.
The **Id** column tells apart parishes with the same name. **Confidence** and **Evidence** show how sure the tool is and the words it found.
The AI makes mistakes, so check the source page before you change your data.

The **Summary** sheet starts with when the run was made, from which file, with which AI model. Its **Site problem** column flags websites that
look expired or taken over, and addresses that now lead to a different website. Check those by hand.

`suggested_patch.json` lists only the most certain changes: values missing from your data or different on the website, found with high confidence.

## If something goes wrong

- **"Ollama is not running"**: open the Ollama app, wait 10 seconds, and run it again.
- **Lots of "Couldn't tell"**: read the Notes column. Usually the site blocks automated visitors or Facebook wants a login.
- **Start over from scratch**: delete the `output` folder, then run it again.
