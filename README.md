# insider-tracking

**insider-tracking** is a free, non-commercial website about the stock trades that people in power
must disclose: US senators and representatives (from the Senate's [eFD](https://efdsearch.senate.gov)
and the [House Clerk's site](https://disclosures-clerk.house.gov/FinancialDisclosure)), and company
insiders in the United States (SEC Form 4) and Sweden (Finansinspektionen's register).

The site: https://mykedestroyer64.github.io/insider-tracking/

## How it works

```
 official sites  ──>  collectors (GitHub Actions)  ──>  database (Supabase)  ──>  website build  ──>  GitHub Pages
```

- **Collectors** run on GitHub's servers on a schedule. Each one reads one official source politely
  (a pause between requests, a limit per run, a User-Agent with a contact email) and saves what's
  new in the database. A refusal stops the run: no collector ever tries to get around a block.
- The **database** is a free Supabase project (PostgreSQL, 500 MB). Only you can reach it.
- After every successful collector run, the **website** is built from the database as plain pages
  and published on GitHub Pages. Visitors never touch the database.

| Workflow (Actions tab) | What it does | When |
|---|---|---|
| insider-tracking collector | Senate reports from eFD | twice a day while eFD refuses the collector (every 3 hours before 24 Oct 2026) |
| insider-tracking House collector | House members' reports | every 3 hours, at :53 |
| insider-tracking SEC collector | company insiders' buys and sells (Form 4) | every 3 hours, at :38 |
| insider-tracking Sweden collector | Swedish executives' buys and sells | every 3 hours, at :08 |
| insider-tracking returns collector | how each traded stock did afterwards (Sharadar prices) | by hand for now |
| Publish the website | builds and publishes the site | after every successful collector run |
| eFD access check | can GitHub read eFD? | by hand |
| New sources check | access checks for other sources, and database tools | by hand |
| Tests | runs every test (see "Tests" below) | on every push |

## The code

Everything is in one Python package, `insider_tracking/`, started the same way for every task:

```
python -m insider_tracking collect senate          one collector run (senate, house, sec, sweden, returns)
python -m insider_tracking check efd               one access check (efd, house, sec, sweden, netherlands)
python -m insider_tracking check sharadar          a probe that only posts notes (sharadar, secdays, libs)
python -m insider_tracking db size                 database tools (size, slim)
python -m insider_tracking site build --out _site  build the website
```

| Folder | Its one job |
|---|---|
| `insider_tracking/core/` | words every part shares: the statuses (OK, BLOCKED, ...), the errors that stop a run, the clock |
| `insider_tracking/web/` | the polite web client: pauses, time limits, one retry, the request budget, telling a refusal from a normal answer |
| `insider_tracking/db/` | the database: connecting, the tables, and one "repository" per group of tables |
| `insider_tracking/runs/` | how every collector run goes: settings check, numbered steps, verdict, the run's record |
| `insider_tracking/sources/` | one folder per data source: its site (addresses and requests), its parsing rules, its collector steps |
| `insider_tracking/checks/` | the access checks and probes |
| `insider_tracking/tools/` | database maintenance |
| `insider_tracking/website/` | the website: loading the data, the rules for what counts, one builder per group of pages |
| `site/` | the pages' templates, the stylesheet, the fonts, the small amount of JavaScript, and `overrides.json` |
| `tests/` | the tests, with stand-in sites and saved examples |
| `.github/workflows/` | the schedules; `_collector.yml` is the run every collector workflow shares |

The code follows a few rules (the "SOLID" principles), so that a change in one place doesn't
break another:

- **One job per part.** Talking to a site, reading its pages and saving the results are separate
  modules, so a site that changes its pages means a change in one parsing module only.
- **New parts plug in.** Each kind of addition has its own place, and the only existing files
  that change are the lists it is added to:
  - a new data source: a folder in `sources/` (its site, parsing, steps and wiring), its tables at
    the end of the list in `db/schema.py` with a repository for them in `db/`, one line in
    `cli.py`, and a small workflow file that calls `_collector.yml` (with its name added to
    `site.yml`, so the site is rebuilt after it runs, and to `tests/test_workflows.py`);
  - a new country of company insiders: its collector, and one `Market` in
    `website/insiders/markets.py` (its words, its currency and how its filings work); the pages
    and the counting rules read everything from there;
  - a new kind of trade with returns: one `ReturnKind` in `sources/returns/kinds.py` and its query
    in `db/returns.py`;
  - a new page: a builder in `website/pages/` and one entry in the `PAGES` list there;
  - a new access check: a module in `checks/`, one line in `cli.py` and a step in
    `new-sources-check.yml` (its results are summed up whatever its name).
- **Parts depend on small interfaces, not on each other's details.** A collector's steps ask for
  "somewhere to save reports" or "a site to read", and the run hands them the real database and
  the real site. The tests hand them stand-ins instead.
- **The outside world comes in from outside.** The environment variables, the database driver,
  the clock and the waiting are given to each command (`runs/platform.py`), so a test can run a
  collector "on" any day, against a local database, without waiting.

## How the Senate collector works

Every run:

1. connects to the database (and creates the tables the first time)
2. opens eFD and accepts its usage agreement
3. saves reports received in the last 60 days that it hasn't seen yet
4. lists older reports, two years per run, back to January 2012
5. reads electronic reports that haven't been read yet, newest first, and saves their transactions

Scanned paper reports are saved with the status `not_read` and their link. Each run sends at most
200 requests to eFD, 2-3 seconds apart, so the full history fills in over several days. Everything
is saved as it goes, so a run that stops early loses nothing.

## How the House collector works

Runs every 3 hours, half an hour after the Senate collector, with the same secrets. Every run:

1. connects to the database (every report says `senate` or `house`)
2. downloads this year's and last year's lists of House filings, but only if they changed since the last run
3. reads older yearly lists, three years per run, back to 2013
4. downloads electronic trade reports that haven't been read yet (PDF files), newest first, reads the table in each one and saves its transactions

Scanned paper reports (about one in ten) are saved with the status `not_read`, like the Senate's.
Each run sends at most 200 requests to the House site, 1.5-2.5 seconds apart.

## How the company-insider collector works

Officers and directors who buy or sell their own company's stock must report it to the SEC on
Form 4 within two business days. Every run:

1. connects to the database
2. loads the SEC's quarterly data files, newest first, two per run, back to the start of 2021 (each holds every Form 4 of a quarter)
3. reads EDGAR's daily lists of filings for the days after the newest quarterly file, downloads each new Form 4 and saves it. EDGAR publishes no list on weekends and US federal holidays, so the collector first reads the quarter's listing of daily lists and skips the days that aren't in it: asking for a list that doesn't exist gets "403 Access Denied", which looks exactly like a refusal (this happened on Columbus Day, 12 Oct 2026)

Only open-market buys and sells (codes P and S) by officers and directors are kept: not stock
awards, option exercises, gifts, tax withholding, or trades by 10% owners (mostly funds). Filings
with impossible numbers are marked `suspect` and left out of totals. Each run sends at most 1,200
requests to the SEC, 0.3-0.6 seconds apart, with a User-Agent naming the project and your email,
as the SEC asks.

## How the Swedish collector works

Finansinspektionen publishes every notification of an executive's or board member's trade in
their company's shares. Every run reads the last 45 days of the register's export, then the
history, 12 months per run, back to July 2016, and keeps the buys and sells of shares. Amounts
stay in the trade's currency (mostly SEK), with a euro value worked out from the European Central
Bank's daily rates. The collector reads the register's robots.txt first and follows it.

## How the returns collector works

For every stock trade by a member of Congress or a US company insider, it works out how the stock
did 1 month, 6 months and 1 year after the trade date (30, 182 and 365 days), next to SPY (an S&P
500 fund) over the same days. It uses Sharadar's closing prices adjusted for splits, dividends and
spinoffs. Sharadar's licence for this site (confirmed by email, 23 and 27 Oct 2026) allows showing
the returns and charts of them, **never the prices**: the prices are not stored anywhere, only the
returns (table `trade_returns`). Every run:

1. connects to the database
2. gets SPY's prices and lists the trades whose returns are missing, or whose period has ended since they were last worked out
3. downloads Sharadar's list of securities and finds the security of each trade: by ticker and date for Congress (a 2019 trade in `FB` is Meta's stock), by the company's SEC number for insiders
4. asks for each security's prices once and works out the returns of all its pending trades, saving as it goes

It needs the `SHARADAR_API_KEY` secret; without it a run does nothing and says so. The key is sent
in a request header, never in an address. At most 2,500 requests per run.

## The verdict

The last line of every run is the **verdict**, also shown on the run's Summary page and as a note:

| Verdict | Meaning |
|---|---|
| `OK` | It worked. |
| `BLOCKED` | The site refused the collector. The next run tries again. |
| `SITE_DOWN` | The site had a server error or a maintenance page. The next run tries again. |
| `CHANGED` | A page didn't look as expected, so the site may work differently now. The collector needs an update. |
| `KEY_PROBLEM` | Sharadar didn't accept the API key (returns collector). |
| `DB_ERROR` | The database couldn't be reached or written to. Check the secrets and the Supabase project. |
| `ERROR` | Something unexpected happened. The log has the details. |
| `NOT_RUN` | A setting is missing, so nothing was done (and nothing was sent anywhere). |

## The website

Pages: an overview with charts and the latest trades, every trade with filters (including Senate
or House), a Congress page listing every member who reported a trade, a page per member, a page
per stock, an Insiders section (all markets, and one page per market: `/insiders/united-states/`,
`/insiders/sweden/`) with a page per insider, late filings, and a feed (`feed.xml`) that any news
reader can follow. Once the returns collector has worked them out, trades show how their stock did
afterwards. Party, state and House district come from the public-domain
[congress-legislators](https://github.com/unitedstates/congress-legislators) dataset; members the
build can't match by name are listed in the build log and can be added to `site/overrides.json`.

### Switching it on (once)

1. In the repository, open **Settings > Pages**. Under **Build and deployment**, set **Source** to **GitHub Actions**.
2. Open the **Actions** tab, click **Publish the website**, then **Run workflow**.
3. The run's Summary shows the address of the site. From then on, the site is rebuilt after every collector run.

### Building it on your computer

With the two Supabase secrets in your environment (see part B below) and the requirements installed:

```
python -m insider_tracking site build --out _site
```

Open `_site/index.html` in a browser. Links assume the site lives under `/insider-tracking/`; add
`--base ""` for links that work straight from the folder.

### Colors and fonts

Every color is defined once, at the top of `site/static/site.css`. The fonts (Libre Franklin and
Newsreader, both under the SIL Open Font License) are served from `site/static/fonts/`, so no
outside service is contacted when someone visits.

## Setting up (once)

### A. Create the database on Supabase

1. Go to https://supabase.com, click **Start your project** and sign in with your GitHub account.
2. Create a new project on the **Free** plan:
   - **Name:** `insider-tracking`
   - **Database password:** click **Generate a password** and copy it somewhere safe, such as a password manager. You need it in part B. Never put it in a file in this repository.
   - **Region:** East US (North Virginia), close to GitHub's servers.
3. Click **Connect** at the top of the project page. Find the **Session pooler** connection string (marked as IPv4 compatible) and copy it. It looks like this:
   ```
   postgresql://postgres.abcdefghijklmnop:[YOUR-PASSWORD]@aws-0-us-east-1.pooler.supabase.com:5432/postgres
   ```
   Leave `[YOUR-PASSWORD]` in it. Don't use the **Direct connection** string: GitHub's servers can't reach it.

### B. Add the secrets on GitHub

In this repository, open **Settings > Secrets and variables > Actions** and click **New repository
secret** for each of these:

| Name | Secret |
|---|---|
| `EFD_CONTACT_EMAIL` | your email: the collectors send it in their User-Agent, so a site can contact you (it is never saved in the output) |
| `SUPABASE_DB_URL` | the Session pooler string from A3, with `[YOUR-PASSWORD]` still in it |
| `SUPABASE_DB_PASSWORD` | your database password |
| `SHARADAR_API_KEY` | the key from sharadar.com/account (returns collector only) |

### C. Start the first runs

Open the **Actions** tab, click a collector on the left, then **Run workflow**. The run's
**Summary** page shows the verdict, for example `VERDICT: OK - 159 new reports, 146 read (514
transactions saved), 13 left to read`. From then on, the collectors run by themselves.

### D. Look at the data

In Supabase, open the **Table Editor**: `reports` and `transactions` (Congress), `insider_filings`
and `insider_trades` (SEC), `mar_trades` and `fx_rates` (Sweden), `trade_returns`, and
`collector_runs` (one row per run, with its verdict).

## Tests

`tests/` holds tests for every collector, check and tool, the website build and the workflows.
They run against stand-in sites and a test database, so nothing is sent to the real sites or to
Supabase. GitHub runs them on every push (the **Tests** workflow); see `tests/README.md` to run
them on your computer.

## Good to know

- **Failure emails:** when a scheduled run fails, GitHub emails you. Exception: while eFD refuses the Senate collector (since 23 Oct 2026), a refusal is recorded on the run (a yellow warning and the verdict note) but doesn't count as a failed run, so it sends no email; every other kind of failure still does. Delete the `blocked-note` line in `.github/workflows/collect.yml` to be emailed about refusals again.
- **Heartbeat:** GitHub switches off scheduled workflows in public repositories after 60 days without any change, so once a week the Senate collector saves today's date in `status/heartbeat.txt`. Those small commits are expected.
- **Supabase free plan:** Supabase pauses a free project after a week without activity. Runs every 3 hours keep it awake. The "New sources check" workflow with `dbsize` shows how much space each table takes; `slim` frees the space of dropped columns.
- **Who can see the database:** only you, in Supabase. Row Level Security is switched on for every table and no read access is granted; the website build connects with your password and publishes plain pages.
- **Reading failed reports again** (after a collector has been fixed): run this in Supabase's **SQL Editor** (use `'house'` for House reports):
  ```sql
  update reports set status = 'to_read', note = null where status = 'failed' and chamber = 'senate';
  ```
- **Changing the pace:** the most requests per run is `max_requests` in each collector's definition (for example `insider_tracking/sources/senate/collector.py`); how often a collector runs is the `cron` line in its workflow file.

## The access checks

An access check tests in one go whether a site can be read by a script, step by step, and saves
real examples in the `output` folder. They are how each collector was built, and they are handy
when a collector reports `BLOCKED` or `CHANGED`.

**On GitHub.** Open **Actions > eFD access check > Run workflow**. For the other sources, open
**Actions > New sources check > Run workflow** and type the names of the checks to run (`house sec
sweden netherlands`, and the tools `libs secdays dbsize sharadar slim`); their results go to the
`check-results` branch.

**On your computer (Windows, VPN on).** Open PowerShell in this folder (type `powershell` in File
Explorer's address bar), then:

```
py -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m insider_tracking check efd --email you@example.com
```

The first two lines are needed only once. The results go to the `output` folder.

### If the verdict is CHANGED

eFD probably works differently from what the check expects. To see what your browser sends:

1. With the VPN on, open https://efdsearch.senate.gov/search/home/ in Chrome or Edge.
2. Press **F12**, click the **Network** tab and tick **Preserve log**.
3. Tick the agreement checkbox. On the search page, tick **Periodic Transactions**, pick a date range and click **Search Reports**.
4. Click the request named **data/**, open its **Payload** tab and click **view source**. If the problem was at step 1 or 2, do the same for the request named **home/**.
5. Paste those form fields into your chat with Claude, together with `output/summary.json` and `output/problem_page.html`.
