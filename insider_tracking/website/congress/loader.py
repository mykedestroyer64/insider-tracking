"""
loader.py - reads the Senate's and the House's reports and transactions for the Congress pages.
"""

from datetime import datetime

from ..loading import column_exists, fetch_text, parse_date, parse_int
from .models import Report, Trade

REPORT_SQL = """
select report_path, kind, status, coalesce(first_name, ''), coalesce(last_name, ''),
       coalesce(filer_name, ''), coalesce(filer_type, ''), coalesce(title, ''),
       is_amendment::int::text, to_char(date_received, 'YYYY-MM-DD'), url,
       coalesce(transaction_count, 0)::text,
       to_char(first_seen_at at time zone 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS'),
       chamber, coalesce(state_district, '')
from reports
order by date_received desc nulls last, report_path
"""
# Before the first House collector run the database has no chamber columns yet
REPORT_SQL_SENATE_ONLY = REPORT_SQL.replace("chamber, coalesce(state_district, '')", "'senate', ''")

TRADE_SQL = """
select report_path, row_number::text, to_char(transaction_date, 'YYYY-MM-DD'), owner, ticker,
       asset_name, asset_type, transaction_type, trade_type, amount,
       amount_min::text, amount_max::text, comment
from transactions
order by report_path, row_number
"""

UPDATED_SQL = """
select to_char(max(finished_at) at time zone 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS')
from collector_runs where verdict = 'OK'
"""


def load(db):
    """Every report and its trades, and when the data was last updated: (reports, trades, updated)."""
    reports = {}
    has_chamber = column_exists(db, "reports", "chamber")
    for row in fetch_text(db, REPORT_SQL if has_chamber else REPORT_SQL_SENATE_ONLY):
        report = Report(
            path=row[0], kind=row[1], status=row[2], first_name=row[3], last_name=row[4],
            filer_name=row[5], filer_type=row[6], title=row[7], is_amendment=row[8] == "1",
            received=parse_date(row[9]), url=row[10], transaction_count=parse_int(row[11]) or 0,
            first_seen=datetime.fromisoformat(row[12]) if row[12] else None,
            chamber=row[13] or "senate", state_district=row[14] or "",
        )
        reports[report.path] = report

    trades = []
    for row in fetch_text(db, TRADE_SQL):
        report = reports.get(row[0])
        if report is None:
            continue
        trades.append(Trade(
            report_path=row[0], row=parse_int(row[1]) or 0, traded=parse_date(row[2]),
            owner=row[3], ticker=row[4], asset_name=row[5], asset_type=row[6],
            transaction_type=row[7], trade_type=row[8], amount=row[9],
            amount_min=parse_int(row[10]), amount_max=parse_int(row[11]), comment=row[12],
            received=report.received, report_url=report.url, is_amendment=report.is_amendment,
            chamber=report.chamber,
        ))

    updated_rows = fetch_text(db, UPDATED_SQL)
    updated = datetime.fromisoformat(updated_rows[0][0]) if updated_rows and updated_rows[0][0] else None
    return list(reports.values()), trades, updated
