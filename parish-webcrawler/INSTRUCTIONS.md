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

## Read the results

Start on the **To review** sheet. Each row is one field of one parish, colour-coded:
**Different on website** · **Missing from your data** · **Not found on website** · **Partly matches** · **Couldn't tell**.
Compare **Your data** with **Website says**, and click **Source page** to check. The **Key** sheet explains each label.
The AI makes mistakes, so check the source page before you change your data.

## If something goes wrong

- **"Ollama is not running"**: open the Ollama app, wait 10 seconds, and run it again.
- **Lots of "Couldn't tell"**: read the Notes column. Usually the site blocks automated visitors or Facebook wants a login.
- **Start over from scratch**: delete the `output` folder, then run it again.
