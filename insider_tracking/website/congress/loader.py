"""
loader.py - reads the Senate's and the House's reports and transactions for the Congress pages.
"""

from dataclasses import replace
from datetime import datetime

from ..loading import parse_date, parse_int
from ..rows import SiteQuery
from .models import Report, Trade

REPORTS = SiteQuery(
    name="reports",
    columns="""report_path, kind, status, coalesce(first_name, ''), coalesce(last_name, ''),
       coalesce(filer_name, ''), coalesce(filer_type, ''), coalesce(title, ''),
       is_amendment::int::text, to_char(date_received, 'YYYY-MM-DD'), url,
       coalesce(transaction_count, 0)::text,
       to_char(first_seen_at at time zone 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS'),
       chamber, coalesce(state_district, '')""",
    tables="reports", order="date_received desc nulls last, report_path",
    part="to_char(date_received, 'YYYY-MM')", descending=True)
# Before the first House collector run the database has no chamber columns yet
REPORTS_SENATE_ONLY = replace(REPORTS, name="reports (Senate only)",
                              columns=REPORTS.columns.replace("chamber, coalesce(state_district, '')", "'senate', ''"))

TRANSACTIONS = SiteQuery(
    name="transactions",
    columns="""report_path, row_number::text, to_char(transaction_date, 'YYYY-MM-DD'), owner, ticker,
       asset_name, asset_type, transaction_type, trade_type, amount,
       amount_min::text, amount_max::text, comment""",
    tables="transactions", order="report_path, row_number", part="report_path")

UPDATED = SiteQuery(
    name="updated", columns="""to_char(max(finished_at) at time zone 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS')""",
    tables="collector_runs", where="verdict = 'OK'")


def load(source):
    """Every report and its trades, and when the data was last updated: (reports, trades, updated).
    source: a RowSource (website/rows.py)."""
    reports = {}
    has_chamber = source.has_column("reports", "chamber")
    for row in source.rows(REPORTS if has_chamber else REPORTS_SENATE_ONLY):
        report = Report(
            path=row[0], kind=row[1], status=row[2], first_name=row[3], last_name=row[4],
            filer_name=row[5], filer_type=row[6], title=row[7], is_amendment=row[8] == "1",
            received=parse_date(row[9]), url=row[10], transaction_count=parse_int(row[11]) or 0,
            first_seen=datetime.fromisoformat(row[12]) if row[12] else None,
            chamber=row[13] or "senate", state_district=row[14] or "",
        )
        reports[report.path] = report

    trades = []
    for row in source.rows(TRANSACTIONS):
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

    updated_rows = source.rows(UPDATED)
    updated = datetime.fromisoformat(updated_rows[0][0]) if updated_rows and updated_rows[0][0] else None
    return list(reports.values()), trades, updated
