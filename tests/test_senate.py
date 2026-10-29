"""
The Senate collector (python -m insider_tracking collect senate) against a stand-in eFD with
15 years of made-up reports, and a real PostgreSQL test database: a full backfill run after
run, then sessions that expire, a block, an outage, changed pages, a database failure, a bug.
"""
import random
import sys
import tempfile
from datetime import date
from pathlib import Path

from insider_tracking.db import connection
from insider_tracking.runs.command import CollectorCommand
from insider_tracking.sources import congress
from insider_tracking.sources.senate import parsing as senate_parsing
from insider_tracking.sources.senate import site as efd_site
from insider_tracking.sources.senate.collector import SenateSource

from .sites import efd as site
from .support.checks import Checks
from .support.commands import run as run_command
from .support.database import FailingConnection, TestDatabase
from .support.platforms import SUPABASE_SETTINGS, test_platform

DB = TestDatabase("it_senate").fresh()
BASE = site.start()
ENDPOINTS = efd_site.EfdEndpoints(BASE)
PACING = efd_site.PACING.without_waiting()
ENV = {"EFD_CONTACT_EMAIL": "tester@example.com", **SUPABASE_SETTINGS}
FAILURE = {"on": None}          # statements containing this text fail (a simulated database problem)
check = Checks()


def failing(connection_):
    return FailingConnection(connection_, FAILURE["on"]) if FAILURE["on"] else connection_


OUTPUT_DIR = Path(tempfile.mkdtemp())


def run(max_requests=200, env=None, args=(), read_page=None):
    environ = {**(ENV if env is None else env), "GITHUB_ACTIONS": "true"}
    source = SenateSource(ENDPOINTS, PACING, read_page or senate_parsing.parse_report_page)
    platform = test_platform(DB, environ, output_dir=OUTPUT_DIR, wrap=failing)
    before = site.STATE["requests"]
    result = run_command(CollectorCommand(source), ["--max-requests", str(max_requests), *args], platform)
    return result.code, result.out, result.last, site.STATE["requests"] - before


def q(sql):
    return DB.query(sql)


def reset():
    q("drop table if exists transactions, report_pages, reports, collector_runs, collector_state cascade")
    site.CONFIG.update(expire_every=0, block_after=0, down=False, all_broken=False)
    site.STATE.update(requests=0, sessions={}, session_counter=0)
    FAILURE["on"] = None


# ---------------------------------------------------------------- unit checks
print("== connection string parsing")
p = connection.parse_db_url(ENV["SUPABASE_DB_URL"], "pa@ss:w/rd#1")
check("placeholder URL + separate password", p == {"user": "postgres.abcdefgh", "password": "pa@ss:w/rd#1",
      "host": "aws-0-us-east-1.pooler.supabase.com", "port": 5432, "database": "postgres"}, p)
p = connection.parse_db_url("postgresql://postgres.x:p%40ss@aws-1-eu-west-2.pooler.supabase.com:5432/postgres")
check("password inside URL (encoded)", p["password"] == "p@ss" and p["host"].startswith("aws-1"), p)
p = connection.parse_db_url("postgresql://postgres.x:p@ss@host.example.com:6543/postgres?sslmode=require")
check("raw @ in password, query string, port", p["password"] == "p@ss" and p["port"] == 6543 and p["host"] == "host.example.com", p)
for bad, why in (("https://example.com", "not a connection string"), (ENV["SUPABASE_DB_URL"], "no password")):
    try:
        connection.parse_db_url(bad, None)
        check(f"rejects: {why}", False)
    except connection.DatabaseError as e:
        check(f"rejects: {why} -> {e}", True)
installed = {name: sys.modules.pop(name) for name in list(sys.modules) if name.split(".")[0] == "pg8000"}
sys.modules["pg8000"] = None          # as if pg8000 weren't installed (nothing is sent anywhere)
try:
    connection.connect(ENV["SUPABASE_DB_URL"], "x")
    check("missing pg8000 gives a clear error", False)
except connection.DatabaseError as e:
    check(f"missing pg8000 gives a clear error -> {e}", "pg8000" in str(e))
finally:
    del sys.modules["pg8000"]
    sys.modules.update(installed)

print("\n== parsing helpers")
check("amount range", congress.parse_amount("$1,001 - $15,000") == (1001, 15000))
check("amount over", congress.parse_amount("Over $50,000,000") == (50000001, None))
check("amount spouse over", congress.parse_amount("Spouse/DC Over $1,000,000") == (1000001, None))
check("amount empty", congress.parse_amount("--") == (None, None))
check("trade types", [congress.trade_type(t) for t in ["Purchase", "Sale (Full)", "Sale (Partial)", "Exchange", "--", "Gift"]]
      == ["purchase", "sale_full", "sale_partial", "exchange", None, "other"])
row = ["James Conley", "Justice, II", "Justice II, James Conley (Senator)",
       '<a href="/search/view/ptr/ab-12/" target="_blank">Periodic Transaction Report for 09/28/2026 (Amendment 1)</a>', "09/28/2026"]
r = senate_parsing.parse_search_row(row, "https://efdsearch.senate.gov")
check("search row", r["filer_name"] == "Justice II, James Conley" and r["filer_type"] == "Senator" and r["is_amendment"]
      and r["date_received"] == date(2026, 9, 28) and r["report_uuid"] == "ab-12" and r["kind"] == "electronic", r)

# ---------------------------------------------------------------- settings
print("\n== missing settings")
reset()
code, out, last, reqs = run(env={"EFD_CONTACT_EMAIL": "x"})
check("NOT_RUN, exit 1, nothing sent", code == 1 and last.startswith("VERDICT: NOT_RUN") and reqs == 0, last)
check("lists what is missing", "SUPABASE_DB_URL" in out and "EFD_CONTACT_EMAIL" in out)
code, out, last, reqs = run(env={"EFD_CONTACT_EMAIL": "a@b.co", "SUPABASE_DB_URL": ENV["SUPABASE_DB_URL"]})
check("placeholder without password -> NOT_RUN", code == 1 and "SUPABASE_DB_PASSWORD" in out, last)

# ---------------------------------------------------------------- full backfill
print("\n== full history, run after run")
reset()
site.build_data()
truth = site.truth()
print("   fake eFD holds:", truth)
runs, first_out = 0, None
while runs < 40:
    code, out, last, reqs = run()
    runs += 1
    first_out = first_out or out
    ok = code == 0 and last.startswith("VERDICT: OK")
    if not ok or runs <= 2 or "up to date" in last:
        print(f"   run {runs}: {last}  ({reqs} requests)")
    if not ok:
        print(out)
        break
    check(f"run {runs} stays within the request budget", reqs <= 200 + efd_site.MAX_AGREEMENTS, reqs) if reqs > 200 else None
    if "everything is up to date" in last:
        break
check("finishes with 'everything is up to date'", "everything is up to date" in last, last)
print(f"   runs needed: {runs}")
totals = dict(zip(["reports", "read", "to_read", "paper", "failed"],
                  map(int, q("select count(*), count(*) filter (where status='read'), count(*) filter (where status='to_read'), "
                             "count(*) filter (where status='not_read'), count(*) filter (where status='failed') from reports")[0])))
tx = int(q("select count(*) from transactions")[0][0])
print("   database holds:", totals, "transactions:", tx)
check("every report listed once", totals["reports"] == truth["reports"])
check("all readable reports read", totals["read"] == truth["read"] and totals["to_read"] == 0)
check("paper filings kept as not_read", totals["paper"] == truth["paper"])
check("broken + missing pages marked failed", totals["failed"] == truth["failed"])
check("every transaction saved", tx == truth["transactions"], f"{tx} vs {truth['transactions']}")
check("pages stored for read + broken reports",
      int(q("select count(*) from report_pages")[0][0]) == truth["read"] + len(site.BROKEN))
notes = q("select note from reports where status='failed' order by note")
check("failure reasons recorded", all(n[0] for n in notes), notes)
print("   failure notes:", sorted(set(n[0] for n in notes)))

# spot-check one report's transactions against the fake site
uuid = next(u for u in site.TRANSACTIONS if u not in site.BROKEN and u not in site.MISSING
            and any(r[4] == "Stock Option" for r in site.TRANSACTIONS[u]))
rows = q(f"select row_number, transaction_date, owner, ticker, asset_name, asset_type, transaction_type, trade_type, amount, "
         f"amount_min, amount_max, comment, raw->>'Ticker' from transactions where report_path='/search/view/ptr/{uuid}/' order by row_number")
expected = site.TRANSACTIONS[uuid]
good = len(rows) == len(expected)
for got, want in zip(rows, expected):
    m, d, y = want[0].split("/")
    amin, amax = congress.parse_amount(want[6])
    good &= got[1] == f"{y}-{m}-{d}" and got[2] == want[1] and got[3] == ("" if want[2] == "--" else want[2])
    good &= got[6] == want[5] and got[8] == want[6] and got[9] == (str(amin) if amin else "") and got[10] == (str(amax) if amax else "")
    good &= got[11] == ("" if want[7] == "--" else want[7])
check("a report's transactions match the page (dates, owner, ticker, amounts, comment)", good, rows[:2])
option = [r for r in rows if r[5] == "Stock Option"][0]
check("option details kept in asset_name", "Option Type: Call" in option[4] and "Strike price: $120" in option[4], option[4])
amend = q("select count(*) from reports where is_amendment")[0][0]
check("amendments flagged", int(amend) > 0)
runs_logged = q("select count(*), count(*) filter (where verdict='OK'), count(*) filter (where finished_at is null) from collector_runs")[0]
check("every run logged with its verdict", runs_logged == [str(runs), str(runs), "0"], runs_logged)
state = dict(q("select key, value from collector_state"))
check("history bookmark reached 2012-01-01", state.get("history_listed_back_to") == "2012-01-01", state)
rls = q("select relname, relrowsecurity from pg_class where relname in ('reports','transactions','report_pages','collector_runs','collector_state') order by relname")
check("row level security on for all 5 tables", all(r[1] == "t" for r in rls) and len(rls) == 5, rls)

print("\n== first run output")
print(first_out)

# ---------------------------------------------------------------- new report arrives
print("== a new report today")
uuid = site.add_report(random.Random(1), date.today())
code, out, last, reqs = run()
print("  ", last)
check("1 new report found and read", code == 0 and "- 1 new report, 1 read" in last, last)
code, out, last, reqs = run()
check("next run: up to date, few requests", "everything is up to date" in last and reqs <= 6, f"{last} / {reqs}")

# ---------------------------------------------------------------- sessions that expire
print("\n== sessions expiring every 60 requests")
reset()
site.CONFIG["expire_every"] = 60
code, out, last, reqs = run()
print("  ", last, f"({reqs} requests, {site.STATE['session_counter']} agreements)")
check("run still OK, agreement renewed", code == 0 and site.STATE["session_counter"] >= 3, last)
site.CONFIG["expire_every"] = 5
code, out, last, reqs = run()
print("  ", last)
check("sessions that keep expiring -> CHANGED", code == 1 and last.startswith("VERDICT: CHANGED"), last)

# ---------------------------------------------------------------- blocked mid-run
print("\n== blocked after 50 requests")
reset()
site.CONFIG["block_after"] = 50
code, out, last, reqs = run()
print("  ", last)
saved_before = int(q("select count(*) from reports where status='read'")[0][0])
check("BLOCKED, exit 1, Akamai reference shown", code == 1 and "BLOCKED" in last and "Reference #18.77aa11bb" in last, last)
check("work done before the block is kept", saved_before > 0, saved_before)
check("problem page saved", (OUTPUT_DIR / "problem_page.html").exists())
check("no retry after the block", reqs <= 51, reqs)
site.CONFIG["block_after"] = 0
site.STATE["requests"] = 0
code, out, last, reqs = run()
check("next run carries on", code == 0 and int(q("select count(*) from reports where status='read'")[0][0]) > saved_before, last)

# ---------------------------------------------------------------- site down
print("\n== site down")
site.CONFIG["down"] = True
code, out, last, reqs = run()
print("  ", last)
check("SITE_DOWN at step 2, one retry", code == 1 and "SITE_DOWN at step 2" in last and reqs == 2, f"{last} / {reqs}")
site.CONFIG["down"] = False

# ---------------------------------------------------------------- report pages changed
print("\n== every report page changed")
reset()
site.CONFIG["all_broken"] = True
code, out, last, reqs = run()
print("  ", last)
failed = int(q("select count(*) from reports where status='failed'")[0][0])
check("CHANGED after 3 unreadable reports in a row", code == 1 and "CHANGED at step 5" in last and failed == 3, f"{last} / {failed}")
site.CONFIG["all_broken"] = False

# ---------------------------------------------------------------- database trouble
print("\n== database fails while saving")
reset()
FAILURE["on"] = "insert into transactions"
code, out, last, reqs = run()
print("  ", last)
check("DB_ERROR at step 5, exit 1", code == 1 and "DB_ERROR at step 5" in last, last)
check("run record still closed", q("select verdict from collector_runs order by id desc limit 1")[0][0] == "DB_ERROR")
FAILURE["on"] = None

# ---------------------------------------------------------------- unexpected bug
print("\n== unexpected error")
reset()
code, out, last, reqs = run(read_page=lambda html: (_ for _ in ()).throw(RuntimeError("boom")))
print("  ", last)
check("ERROR with technical details", code == 1 and "VERDICT: ERROR at step 5" in last and "Traceback" in out, last)

# ---------------------------------------------------------------- tiny budget
print("\n== tiny budget (3 requests)")
reset()
code, out, last, reqs = run(max_requests=3)
print("  ", last)
check("ends OK without errors", code == 0 and last.startswith("VERDICT: OK"), last)

sys.exit(check.finish())
