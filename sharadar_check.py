"""
sharadar_check.py - learns the format of Sharadar's API before the subscription starts,
with the public test key Sharadar gave us (it covers a small free sample, e.g. AAPL).

Requests (about six): the column lists of the tickers, stocks and funds tables (no key
needed), the tickers-table row for AAPL, and a short AAPL date range with only the date
column, sent with the key in a header. The notes show formats and counts, never prices:
the run notes of a public repository are public, and Sharadar's licence keeps prices
off the site. Delete the run afterwards anyway (it shows Sharadar's metadata for AAPL).
"""

import csv
import io
import json
import time

import requests

API = "https://api.sharadar.com/v1.0/"
TEST_KEY = "test-api-key"           # the public test key in Sharadar's email of 23 Oct 2026
METADATA = ("ticker", "permaticker", "table", "category", "isdelisted", "exchange", "relatedtickers",
            "secfilings", "firstpricedate", "lastpricedate", "firstadded", "currency", "cusips")


def note(text):
    print("::notice title=SHARADAR check::" + text.replace("\n", " ")[:3000])


def rows_of(response):
    text = response.content.decode("utf-8-sig", errors="replace").lstrip()
    if text.startswith(("{", "[")):
        data = json.loads(text)
        if isinstance(data, dict):
            for key in ("data", "datatable", "rows"):
                if isinstance(data.get(key), list):
                    data = data[key]
                    break
        if isinstance(data, list) and data and isinstance(data[0], dict):
            return list(data[0]), [list(row.values()) for row in data]
        return [], []
    rows = list(csv.reader(io.StringIO(text)))
    return (rows[0], rows[1:]) if rows else ([], [])


def main():
    web = requests.Session()
    web.headers["User-Agent"] = "insider-tracking/0.1 (non-commercial public transparency project)"

    for table in ("tickers", "stocks", "funds", "actions"):
        response = web.get(API + f"schema/{table}", timeout=60)
        note(f"schema/{table}: HTTP {response.status_code}, {response.headers.get('Content-Type')}: "
             + response.text[:1500])
        time.sleep(1)

    response = web.get(API + "data/tickers", params={"ticker": "AAPL", "format": "csv"},
                       headers={"x-api-key": TEST_KEY}, timeout=60)
    note(f"tickers row for AAPL: HTTP {response.status_code}")
    if response.status_code == 200:
        header, rows = rows_of(response)
        note(f"tickers columns: {', '.join(header)}; {len(rows)} rows")
        for row in rows[:3]:
            record = dict(zip(header, row))
            note("AAPL metadata: " + json.dumps({k: record.get(k) for k in METADATA if k in record}))
    else:
        note("answer: " + response.text[:300])
    time.sleep(1)

    response = web.get(API + "data/stocks", params={"ticker": "AAPL", "from": "2026-09-01", "to": "2026-09-30",
                                                    "fields": "date", "format": "csv"},
                       headers={"x-api-key": TEST_KEY}, timeout=60)
    header, rows = rows_of(response) if response.status_code == 200 else ([], [])
    dates = sorted(row[0] for row in rows) if rows else []
    note(f"stocks AAPL Sep 2026, date column only, key in a header: HTTP {response.status_code}, "
         f"columns {header}, {len(rows)} rows" + (f", {dates[0]} to {dates[-1]}" if dates else "")
         + ("" if response.status_code == 200 else "; answer: " + response.text[:300]))


def collector_requests():
    """The exact requests collect_returns.py sends, with the test key: counts and dates only."""
    from datetime import date
    import returns_calc
    import sharadar_site
    web = sharadar_site.SharadarSession(TEST_KEY, "insider-tracking/0.1 (non-commercial public transparency project)", 10)
    for table, ticker in (("stocks", "AAPL"), ("funds", "SPY")):
        try:
            series = returns_calc.Series.from_csv(web.closes(table, ticker, date(2026, 9, 1), date(2026, 9, 30)))
            note(f"collector request {table}/{ticker} Sep 2026: {len(series)} closes, {series.days[0] if len(series) else '-'}"
                 f" to {series.last_day}")
        except sharadar_site.SiteStop as stop:
            note(f"collector request {table}/{ticker}: {stop.status} - {stop.detail}")
    try:
        web.table_file("tickers")
        note("tickers file: downloaded (unexpected with the test key)")
    except sharadar_site.SiteStop as stop:
        note(f"tickers file with the test key: {stop.status} - {stop.detail[:200]}")


if __name__ == "__main__":
    collector_requests()     # first: GitHub keeps only the first 10 notes of a step
    main()
