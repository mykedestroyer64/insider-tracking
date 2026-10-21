# insider-tracking

**insider-tracking** is a free, non-commercial website about US senators' stock trades, built from the Senate's official financial disclosure site, [eFD](https://efdsearch.senate.gov). This repository holds the collector, which saves the reports and trades in a database.

| File | What it does |
|---|---|
| `collect.py` | the collector: runs every 3 hours on GitHub Actions and saves reports and trades in the Supabase database |
| `efd_site.py` | talks to eFD politely (agreement, search, report pages) and stops if it is blocked |
| `parsing.py` | turns eFD pages into clean data |
| `database.py` | creates the tables and saves the data |
| `.github/workflows/collect.yml` | runs the collector every 3 hours |
| `efd_check.py` and `.github/workflows/efd-check.yml` | the step 1 access test, still handy for checking eFD by hand |
| `requirements.txt` | the Python libraries needed |
| `status/heartbeat.txt` | a date the collector saves once a week (see "Good to know") |

## How the collector works

Every run:

1. connects to the database (and creates the tables the first time)
2. opens eFD and accepts its usage agreement
3. saves reports received in the last 60 days that it hasn't seen yet
4. lists older reports, two years per run, back to January 2012
5. reads electronic reports that haven't been read yet, newest first, and saves their transactions

Scanned paper reports are saved with the status `not_read` and their link. Reading them comes in a later step.

Each run sends at most 200 requests to eFD, 2-3 seconds apart, so the full history fills in over several days. Everything is saved as it goes, so a run that stops early loses nothing.

The last line of every run is the **verdict**:

| Verdict | Meaning |
|---|---|
| `OK` | It worked. |
| `BLOCKED` | eFD refused the collector. The next run tries again in 3 hours. |
| `SITE_DOWN` | eFD had a server error or a maintenance page. The next run tries again. |
| `CHANGED` | A page didn't look as expected, so eFD may work differently now. The collector needs an update. |
| `DB_ERROR` | The database couldn't be reached or written to. Check the secrets and the Supabase project. |
| `ERROR` | Something unexpected happened. The log has the details. |
| `NOT_RUN` | A secret is missing, so nothing was done. |

## Setting up the collector (once)

### A. Create the database on Supabase

1. Go to https://supabase.com, click **Start your project** and sign in with your GitHub account.
2. Create a new project on the **Free** plan:
   - **Name:** `insider-tracking`
   - **Database password:** click **Generate a password** and copy it somewhere safe, such as a password manager or a note outside this folder. You need it in part B. Never put it in a file in this repository.
   - **Region:** East US (North Virginia), close to GitHub's servers and to eFD.

   Click **Create new project** and wait a minute or two until it's ready.
3. Click **Connect** at the top of the project page. Find the **Session pooler** connection string (marked as IPv4 compatible) and copy it. It looks like this:
   ```
   postgresql://postgres.abcdefghijklmnop:[YOUR-PASSWORD]@aws-0-us-east-1.pooler.supabase.com:5432/postgres
   ```
   Leave `[YOUR-PASSWORD]` in it. Don't use the **Direct connection** string: GitHub's servers can't reach it.

### B. Add two secrets on GitHub

In this repository, open **Settings > Secrets and variables > Actions** and click **New repository secret** for each of these:

| Name | Secret |
|---|---|
| `SUPABASE_DB_URL` | the Session pooler string from A3, with `[YOUR-PASSWORD]` still in it |
| `SUPABASE_DB_PASSWORD` | your database password |

The `EFD_CONTACT_EMAIL` secret from step 1 stays as it is.

### C. Upload the new files

1. Click **Add file > Upload files**. Drag in `collect.py`, `efd_site.py`, `parsing.py`, `database.py`, `requirements.txt` and `README.md`, then click **Commit changes**. The last two replace the old ones.
2. Add the workflow: click **Add file > Create new file** and type `.github/workflows/collect.yml` as the name (each `/` makes a folder). Open your `collect.yml` in Notepad, copy all of it into the big box, and click **Commit changes**.

### D. Start the first run

1. Open the **Actions** tab, click **insider-tracking collector** on the left, then **Run workflow** and the green **Run workflow** button.
2. It takes about 10 minutes. Click the run: its **Summary** page shows the verdict, for example `VERDICT: OK - 159 new reports, 146 read (514 transactions saved), 13 left to read`.

### E. Look at the data

In Supabase, open the **Table Editor**:

- `reports`: one row per report, with its status (`to_read`, `read`, `failed` or `not_read`)
- `transactions`: the trades read from the reports
- `collector_runs`: one row per run, with its verdict

From now on the collector runs by itself every 3 hours. The history back to 2012 fills in over the next days. Once it's complete, a run with nothing new says `everything is up to date`.

## Good to know

- **Failure emails:** when a scheduled run fails, GitHub emails you.
- **Heartbeat:** GitHub switches off scheduled workflows in public repositories after 60 days without any change, so once a week the collector saves today's date in `status/heartbeat.txt`. Those small commits are expected.
- **Supabase free plan:** Supabase pauses a free project after a week without activity. Runs every 3 hours keep it awake, and the data fits easily in the free 500 MB.
- **Who can see the data:** nobody yet. Row Level Security is switched on for every table and no read access is granted. The website step will open read-only access to the reports and trades.
- **Reading failed reports again** (after the collector has been fixed): run this in Supabase's **SQL Editor**:
  ```sql
  update reports set status = 'to_read', note = null where status = 'failed';
  ```
- **Changing the pace:** `MAX_REQUESTS` in `collect.py` sets the requests per run, and the `cron` line in `collect.yml` sets how often it runs.

## Step 1: the eFD access check

`efd_check.py` checks in one go whether eFD can be reached: agreement, search and one report. It still works on its own.

**On your computer (Windows, VPN on).** Open PowerShell in this folder (type `powershell` in File Explorer's address bar), then:

```
py -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python efd_check.py --email you@example.com
```

The first two lines are needed only once. The results go to the `output` folder.

**On GitHub.** Open **Actions > eFD access check > Run workflow**. The run's Summary page shows the verdict, and its **Artifacts** section has the output files.

**If the verdict is CHANGED**, eFD probably works differently from what the script expects. To see what your browser sends:

1. With the VPN on, open https://efdsearch.senate.gov/search/home/ in Chrome or Edge.
2. Press **F12**, click the **Network** tab and tick **Preserve log**.
3. Tick the agreement checkbox. On the search page, tick **Periodic Transactions**, pick a date range and click **Search Reports**.
4. Click the request named **data/**, open its **Payload** tab and click **view source**. If the problem was at step 1 or 2, do the same for the request named **home/**.
5. Paste those form fields into your chat with Claude, together with `output/summary.json` and `output/problem_page.html`.
