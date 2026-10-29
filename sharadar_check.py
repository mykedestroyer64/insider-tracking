"""
sharadar_check.py - looks at Sharadar's free tickers file (with the public test key
Sharadar gave us) to learn its format before the subscription starts.

Requests: the tickers file, then one small sample of stock prices (SEP) and one of
fund prices (SFP) to see whether the test key gives samples and in what format.
Results: notes on the run, and the tickers file saved (gzipped) in output/sharadar
for the check-results branch.
"""

import csv
import gzip
import io
import json
import time
import zipfile
from pathlib import Path

import requests

BASE = "https://api.sharadar.com/v1.0/data/"
TEST_KEY = "test-api-key"           # the public test key in Sharadar's email of 23 Oct 2026
OUTPUT = Path(__file__).resolve().parent / "output" / "sharadar"
WATCH = ("AAPL", "META", "FB", "GOOGL", "TWTR", "SPY", "BRK.B", "BRK-B")


def note(text):
    print("::notice title=SHARADAR check::" + text.replace("\n", " ")[:3000])


def describe(response):
    kind = response.headers.get("Content-Type", "")
    return f"HTTP {response.status_code}, {kind}, {len(response.content):,} bytes"


def table_from(response):
    """(header, rows) from a CSV, a ZIP holding a CSV, or JSON."""
    content = response.content
    if content[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            name = archive.namelist()[0]
            note(f"zip contains: {archive.namelist()[:5]}")
            content = archive.read(name)
    text = content.decode("utf-8-sig", errors="replace")
    stripped = text.lstrip()
    if stripped.startswith(("{", "[")):
        data = json.loads(stripped)
        if isinstance(data, dict):
            note("JSON keys: " + ", ".join(list(data)[:20]))
            for key in ("datatable", "data"):
                if key in data:
                    inner = data[key]
                    if isinstance(inner, dict) and "columns" in inner:
                        header = [c.get("name") for c in inner["columns"]]
                        return header, inner.get("data", [])
                    if isinstance(inner, list) and inner and isinstance(inner[0], dict):
                        header = list(inner[0])
                        return header, [[row.get(h) for h in header] for row in inner]
        if isinstance(data, list) and data and isinstance(data[0], dict):
            header = list(data[0])
            return header, [[row.get(h) for h in header] for row in data]
        return [], []
    reader = csv.reader(io.StringIO(text))
    rows = list(reader)
    return (rows[0], rows[1:]) if rows else ([], [])


def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    web = requests.Session()
    web.headers["User-Agent"] = "insider-tracking/0.1 (non-commercial public transparency project)"

    response = web.get(BASE + "tickers", params={"api_key": TEST_KEY, "years": "full"}, timeout=120)
    note("tickers file: " + describe(response))
    if response.status_code == 200:
        (OUTPUT / "tickers.raw.gz").write_bytes(gzip.compress(response.content))
        header, rows = table_from(response)
        note(f"tickers: {len(rows):,} rows; columns: {', '.join(map(str, header))}")
        if header:
            index = {name: i for i, name in enumerate(header)}
            ticker_col = index.get("ticker")
            shown = 0
            for row in rows:
                if ticker_col is not None and str(row[ticker_col]).upper() in WATCH and shown < 8:
                    note("row: " + json.dumps(dict(zip(header, row)), default=str)[:1500])
                    shown += 1
            for column in ("table", "category", "exchange", "isdelisted", "currency"):
                if column in index:
                    counts = {}
                    for row in rows:
                        value = row[index[column]]
                        counts[value] = counts.get(value, 0) + 1
                    top = sorted(counts.items(), key=lambda item: -item[1])[:12]
                    note(f"{column} values: " + ", ".join(f"{k}: {v:,}" for k, v in top))
    else:
        note("tickers file answer starts: " + response.text[:400])

    for table, ticker in (("sep", "AAPL"), ("sfp", "SPY")):
        time.sleep(2)
        response = web.get(BASE + table, params={"api_key": TEST_KEY, "ticker": ticker}, timeout=120)
        note(f"{table} sample for {ticker}: " + describe(response))
        if response.status_code == 200:
            try:
                header, rows = table_from(response)
                note(f"{table}: {len(rows):,} rows; columns: {', '.join(map(str, header))}; first rows: "
                     + json.dumps(rows[:3], default=str)[:800])
            except Exception as error:   # the point is to see the format, whatever it is
                note(f"{table}: could not read ({error}); starts: {response.text[:300]}")
        else:
            note(f"{table} answer starts: " + response.text[:400])


if __name__ == "__main__":
    main()
