# insider-tracking

**insider-tracking** is a free, non-commercial website about the stock trades of US senators and representatives, built from the official financial disclosure sites of the Senate ([eFD](https://efdsearch.senate.gov)) and the House ([the Clerk's site](https://disclosures-clerk.house.gov/FinancialDisclosure)). This repository holds the two collectors, which save the reports and trades in a database, and the website, which is built from that database and published on GitHub Pages.

| File | What it does |
|---|---|
| `collect.py` | the Senate collector: runs every 3 hours on GitHub Actions and saves reports and trades in the Supabase database |
| `collect_house.py` | the House collector: runs every 3 hours too and saves House members' reports and trades in the same database |
| `house_site.py`, `house_parsing.py` | download the House Clerk's yearly lists and trade reports (PDFs), and read them |
| `collect_sec.py` | the company-insider collector: officers' and directors' buys and sells from SEC Form 4, every 3 hours |
| `sec_site.py`, `sec_parsing.py` | talk to the SEC's sites and turn their quarterly files and filings into clean records |
| `collect_sweden.py` | the Swedish executives collector: buys and sells of their company's shares from Finansinspektionen's register, every 3 hours |
| `sweden_site.py`, `sweden_parsing.py`, `ecb_rates.py` | talk to the register and the European Central Bank, read the register's export and the euro exchange rates |
| `efd_site.py` | talks to eFD politely (agreement, search, report pages) and stops if it is blocked |
| `parsing.py` | turns eFD pages into clean data |
| `database.py` | creates the tables and saves the data |
| `site/build.py` | builds the website from the database (with `site/data.py`, `site/people.py`, `site/charts.py`) |
| `site/templates/`, `site/static/` | the pages' templates, the stylesheet, the fonts and the small amount of JavaScript |
| `site/overrides.json` | fixes for members of Congress whose party and state the build can't work out by name |
| `.github/workflows/collect.yml`, `collect-house.yml`, `collect-sec.yml`, `collect-sweden.yml` | run the four collectors every 3 hours (the Senate one twice a day while eFD refuses it) |
| `.github/workflows/site.yml` | builds and publishes the website after every collector run |
| `efd_check.py` and `.github/workflows/efd-check.yml` | the step 1 access test, still handy for checking eFD by hand |
| `house_check.py`, `sec_check.py`, `sweden_check.py`, `netherlands_check.py` and `.github/workflows/new-sources-check.yml` | access tests for new sources (type the names of the checks to run when starting the workflow; results go to the `check-results` branch) |
| `requirements.txt` | the Python libraries needed |
| `status/heartbeat.txt` | a date the Senate collector saves once a week (see "Good to know") |

## The website

After every successful collector run, GitHub builds the website from the database and publishes it to GitHub Pages at `https://<your GitHub name>.github.io/insider-tracking/`. Visitors never touch the database: the site is plain pages, so it is fast and free to host.

Pages: an overview with charts and the latest trades, every trade with filters (including Senate or House), a Congress page listing every member who reported a trade, a page per member, a page per stock, an Insiders section (all markets, and one page per market: `/insiders/united-states/`, `/insiders/sweden/`) with a page per insider, late filings, and a feed (`feed.xml`) that any news reader can follow. European companies' stock pages live under their country code (`/stocks/se/investor/`); amounts stay in the trade's currency with a euro figure alongside. Party, state and House district come from the public-domain [congress-legislators](https://github.com/unitedstates/congress-legislators) dataset; members the build can't match by name are listed in the build log and can be added to `site/overrides.json`.

### Switching it on (once)

1. In the repository, open **Settings > Pages**. Under **Build and deployment**, set **Source** to **GitHub Actions**.
2. Open the **Actions** tab, click **Publish the website**, then **Run workflow**. The first build takes a minute or two.
3. The run's Summary shows the address of the site. From then on, the site is rebuilt after every collector run.

### Building it on your computer

With the two Supabase secrets in your environment (see part B above) and the requirements installed:

```
python site/build.py --out _site
```

Open `_site/index.html` in a browser. Links assume the site lives under `/insider-tracking/`; run with `--base ""` for links that work straight from the folder.

### Colors and fonts

Every color is defined once, at the top of `site/static/site.css`. The fonts (Libre Franklin and Newsreader, both under the SIL Open Font License) are served from `site/static/fonts/`, so no outside service is contacted when someone visits.

## How the Senate collector works

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

## How the House collector works

`collect_house.py` runs every 3 hours, half an hour after the Senate collector, with the same three secrets (nothing new to set up). Every run:

1. connects to the database (the first run adds a `chamber` column, so every report says `senate` or `house`)
2. downloads this year's and last year's lists of House filings, but only if they changed since the last run
3. reads older yearly lists, three years per run, back to 2013
4. downloads electronic trade reports that haven't been read yet (PDF files), newest first, reads the table in each one and saves its transactions

Scanned paper reports (about one in ten) are saved with the status `not_read`, like the Senate's. Each run sends at most 200 requests to the House site, 1.5-2.5 seconds apart. The verdict line works the same way as the Senate collector's.

To start it by hand: **Actions > insider-tracking House collector > Run workflow**.

## How the company-insider collector works

`collect_sec.py` covers company insiders: officers and directors who buy or sell their own company's stock must report it to the SEC on Form 4 within two business days. It runs every 3 hours with the same three secrets. Every run:

1. connects to the database (the first run adds the `insider_filings` and `insider_trades` tables)
2. loads the SEC's quarterly data files, newest first, two per run, back to the start of 2021 (each holds every Form 4 of a quarter)
3. reads EDGAR's daily lists of filings for the days after the newest quarterly file, downloads each new Form 4 and saves it. EDGAR publishes no list on weekends and US federal holidays, so the collector first reads the quarter's listing of daily lists (`index.json`) and skips the days that aren't in it: asking the SEC for a list that doesn't exist gets "403 Access Denied" from its storage, which looks exactly like a refusal (this happened on Columbus Day, 12 Oct 2026)

Only open-market buys and sells (codes P and S) by officers and directors are kept: not stock awards, option exercises, gifts, tax withholding, or trades by 10% owners (mostly funds). A few filings carry impossible numbers; those rows are marked `suspect` and left out of totals. Each run sends at most 1,200 requests to the SEC, 0.3-0.6 seconds apart, with a User-Agent naming the project and your email, as the SEC asks.

To start it by hand: **Actions > insider-tracking SEC collector > Run workflow**.

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

- **Failure emails:** when a scheduled run fails, GitHub emails you. Exception: while eFD refuses the Senate collector (since 23 Oct 2026), a refusal is recorded on the run (a yellow warning and the verdict note) but doesn't count as a failed run, so it sends no email; every other kind of failure still does. `QUIET_WHEN_BLOCKED` in `collect.yml` switches this off.
- **Heartbeat:** GitHub switches off scheduled workflows in public repositories after 60 days without any change, so once a week the collector saves today's date in `status/heartbeat.txt`. Those small commits are expected.
- **Supabase free plan:** Supabase pauses a free project after a week without activity. Runs every 3 hours keep it awake, and the data fits easily in the free 500 MB.
- **Who can see the database:** only you, in Supabase. Row Level Security is switched on for every table and no read access is granted; the website build connects with your password and publishes plain pages, so visitors never reach the database.
- **Reading failed reports again** (after a collector has been fixed): run this in Supabase's **SQL Editor** (use `'house'` for House reports):
  ```sql
  update reports set status = 'to_read', note = null where status = 'failed' and chamber = 'senate';
  ```
- **Changing the pace:** `MAX_REQUESTS` in `collect.py` sets the requests per run, and the `cron` line in `collect.yml` sets how often it runs (twice a day since 24 Oct 2026, while eFD refuses the collector; every 3 hours before that).

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
