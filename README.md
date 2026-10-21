# insider-tracking: eFD access check (step 1)

This is step 1 of **insider-tracking**, a free, non-commercial website about US senators' stock trades.

`efd_check.py` is a small test. It checks whether a script can read the Senate's official financial disclosure site, [eFD](https://efdsearch.senate.gov), by doing what a person does in a browser:

1. open the eFD home page
2. accept the site's usage agreement
3. search for Periodic Transaction Reports received in the last 30 days
4. pick the newest electronic report (scanned paper reports are skipped)
5. open it and read its transactions table

It waits 2-3 seconds between requests, puts your contact email in its User-Agent, and stops as soon as the site blocks it. It never tries to get around a block.

The last line it prints is the **verdict**:

| Verdict | Meaning |
|---|---|
| `OK` | It worked. |
| `BLOCKED` | eFD refused the script (HTTP 403 or Akamai "Access Denied"). The line includes Akamai's Reference #. |
| `SITE_DOWN` | eFD had a server error or showed a maintenance page. Try again later. |
| `CHANGED` | A page did not look as expected, so eFD may work differently now. See the last section. |
| `NOT_RUN` | No contact email was given, so nothing was sent to eFD. |

| File | What it is |
|---|---|
| `efd_check.py` | the test script |
| `requirements.txt` | the two Python libraries it needs |
| `.github/workflows/efd-check.yml` | tells GitHub Actions how to run the script |
| `.gitignore` | tells Git which folders never to upload |

## A. Run it on your computer (Windows, VPN on)

1. **Check that Python is installed.** Open PowerShell (press the Windows key, type `PowerShell`, press Enter) and type:
   ```
   py --version
   ```
   You need Python 3.11 or newer. If PowerShell doesn't recognise `py`, install Python from https://www.python.org/downloads/ with the default options (if it asks whether to install a Python runtime, say yes). Then close PowerShell, open it again and repeat this step.
2. **Put the files in a folder**, for example `Documents\insider-tracking`. It should hold `efd_check.py`, `requirements.txt`, `README.md`, `.gitignore` and a `.github` folder.
3. **Open PowerShell in that folder.** Open the folder in File Explorer, click the address bar at the top, type `powershell` and press Enter.
4. **Set up the project (first time only).** Type these two lines, pressing Enter after each:
   ```
   py -m venv .venv
   .venv\Scripts\python -m pip install -r requirements.txt
   ```
   The first line makes a `.venv` folder: a private copy of Python just for this project. The second installs the two libraries into it.
5. **Turn your US VPN on.** Check that https://efdsearch.senate.gov/search/home/ opens in your browser (not "Access Denied").
6. **Run the check** with your own email in place of `you@example.com`:
   ```
   .venv\Scripts\python efd_check.py --email you@example.com
   ```
   It takes about 15-30 seconds and prints one line per step.
7. **Read the last line.** `VERDICT: OK - ...` means it worked. The results are in the new `output` folder.

Next time you only need steps 3, 5 and 6.

## B. Put the files on GitHub (public repository)

1. Sign in at https://github.com (create a free account if you don't have one).
2. Click **+** (top right) and choose **New repository**. Name it `insider-tracking`, choose **Public**, leave the README option off, and click **Create repository**.
3. On the new repository's page, click the link **uploading an existing file**. (If you don't see it, click **Add file > Upload files**.)
4. In File Explorer, select `efd_check.py`, `requirements.txt`, `README.md`, `.gitignore` and the `.github` folder, and drag them onto the upload area. Don't drag the `.venv` or `output` folders. Click **Commit changes**.
5. Check that the file list now shows a `.github` folder. If it doesn't, click **Add file > Create new file**, type `.github/workflows/efd-check.yml` as the file name (each `/` makes a folder), open your `efd-check.yml` in Notepad, copy all of it into the big box, and click **Commit changes**.
6. **Store your email as a secret.** Click the **Settings** tab, then **Secrets and variables > Actions** in the left menu, then **New repository secret**. Name: `EFD_CONTACT_EMAIL`. Secret: your email. Click **Add secret**. Nobody can read a secret back, and GitHub hides it in logs.

## C. Run the check on GitHub Actions

1. Click the **Actions** tab. (If GitHub asks, click the button to enable workflows.)
2. In the list on the left, click **eFD access check**.
3. Click **Run workflow** on the right, then the green **Run workflow** button.
4. A new run appears after a few seconds (refresh the page if it doesn't). It takes about a minute. A green tick means `OK`; a red cross means any other verdict.

## D. Find the verdict and download the results

1. Click the run. Its **Summary** page shows the verdict line and the five step lines.
2. Scroll down to **Artifacts** and click **efd-check-output** to download a zip. It holds the same files as the `output` folder, plus `console.log` (everything the script printed).
3. To see the full printout online, click the **efd-check** job on the left and open the **Run the eFD check** step.

Anyone can see the runs and downloads of a public repository. Your email is in neither.

## What's in the output folder

- `search_results.json`: the search results eFD sent back
- `report.html`: the report page, exactly as downloaded
- `transactions.json`: the transactions read from that report
- `summary.json`: the verdict, each step's status and HTTP code, counts, the time and where it ran
- `problem_page.html`: only when a step failed, the page that caused it

## If the verdict is CHANGED

eFD probably works differently from what the script expects. To see what your browser sends:

1. Turn the VPN on and open https://efdsearch.senate.gov/search/home/ in Chrome or Edge.
2. Press **F12** to open the developer tools, click the **Network** tab and tick **Preserve log**.
3. Tick the agreement checkbox. On the search page, tick **Periodic Transactions**, pick a date range and click **Search Reports**.
4. In the Network list, click the request named **data/**, open its **Payload** tab and click **view source**. (If the problem was at step 1 or 2, also do this for the request named **home/**.)
5. Copy those form fields and paste them into your chat with Claude, together with `output/summary.json` and `output/problem_page.html`.
