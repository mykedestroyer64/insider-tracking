"""
congress.py - Senate and House trade reports (tables reports, transactions, report_pages).

A report is stored once when it is first listed (status to_read for electronic reports,
not_read for scanned paper ones) and updated when it has been read: its transactions are
saved together with the new status, all or nothing.
"""

import gzip
import json

from .database import chunks, row_slots

REPORT_COLUMNS = ["report_path", "report_uuid", "kind", "status", "first_name", "last_name",
                  "filer_name", "filer_type", "title", "is_amendment", "date_received", "url",
                  "chamber", "state_district"]
REPORT_DEFAULTS = {"chamber": "senate", "state_district": None}
TRANSACTION_COLUMNS = ["report_path", "row_number", "transaction_date", "owner", "ticker",
                       "asset_name", "asset_type", "transaction_type", "trade_type", "amount",
                       "amount_min", "amount_max", "comment", "raw"]
STATUS_FOR_KIND = {"electronic": "to_read", "paper": "not_read"}
QUEUE_COLUMNS = ["report_path", "url", "first_name", "last_name", "date_received"]


class CongressReports:
    def __init__(self, db):
        self.db = db

    def counts(self, chamber="senate"):
        """How many of a chamber's reports there are in each state, and how many transactions."""
        with self.db.transaction() as cursor:
            cursor.execute("""
        select count(*),
               count(*) filter (where status = 'read'),
               count(*) filter (where status = 'to_read'),
               count(*) filter (where status = 'not_read'),
               count(*) filter (where status = 'failed')
        from reports where chamber = %s""", (chamber,))
            reports, read, to_read, paper, failed = (int(value) for value in cursor.fetchone())
            cursor.execute("""
        select count(*) from transactions t join reports r on r.report_path = t.report_path
        where r.chamber = %s""", (chamber,))
            transactions = int(cursor.fetchone()[0])
        return {"reports": reports, "read": read, "to_read": to_read, "paper_not_read": paper,
                "failed": failed, "transactions": transactions}

    def add(self, reports):
        """Store reports we haven't seen before. Returns the ones that were new."""
        new_paths = set()
        with self.db.transaction() as cursor:
            for chunk in chunks(reports, 100):
                params = []
                for report in chunk:
                    values = dict(REPORT_DEFAULTS, **report)
                    values["status"] = STATUS_FOR_KIND.get(report["kind"], "other")
                    params += [values[column] for column in REPORT_COLUMNS]
                cursor.execute(
                    f"insert into reports ({', '.join(REPORT_COLUMNS)}) values "
                    + ", ".join([row_slots(len(REPORT_COLUMNS))] * len(chunk))
                    + " on conflict (report_path) do nothing returning report_path", params)
                new_paths.update(str(row[0]) for row in cursor.fetchall())
        return [report for report in reports if report["report_path"] in new_paths]

    def to_read(self, limit, chamber="senate"):
        """A chamber's electronic reports that haven't been read yet, newest first."""
        with self.db.transaction() as cursor:
            cursor.execute("""
        select report_path, url, first_name, last_name, date_received
        from reports
        where status = 'to_read' and chamber = %s
        order by date_received desc nulls last, report_path
        limit %s""", (chamber, int(limit)))
            rows = cursor.fetchall()
        return [dict(zip(QUEUE_COLUMNS, row)) for row in rows]

    def save_read(self, report_path, transactions, page, note=None, is_amendment=None):
        """Save a report's transactions (and page, if given) and mark it as read - all or nothing."""
        with self.db.transaction() as cursor:
            cursor.execute("delete from transactions where report_path = %s", (report_path,))
            slots = row_slots(len(TRANSACTION_COLUMNS), last="%s::jsonb")
            for chunk in chunks(transactions, 100):
                params = []
                for transaction in chunk:
                    values = dict(transaction, report_path=report_path,
                                  raw=json.dumps(transaction["raw"], ensure_ascii=False))
                    params += [values[column] for column in TRANSACTION_COLUMNS]
                cursor.execute(f"insert into transactions ({', '.join(TRANSACTION_COLUMNS)}) values "
                               + ", ".join([slots] * len(chunk)), params)
            if page is not None:
                _store_page(cursor, report_path, page)
            cursor.execute("""
        update reports set status = 'read', read_at = now(), transaction_count = %s, note = %s,
                           is_amendment = coalesce(%s, is_amendment)
        where report_path = %s""", (len(transactions), note, is_amendment, report_path))

    def mark_failed(self, report_path, reason, page=None):
        """Mark a report that couldn't be read, with the reason (and the page, if we got one)."""
        with self.db.transaction() as cursor:
            if page:
                _store_page(cursor, report_path, page)
            cursor.execute("""
        update reports set status = 'failed', read_at = now(), note = %s
        where report_path = %s""", (reason[:500], report_path))

    def mark_scanned(self, report_path, reason):
        """A report listed as electronic turned out to be a scan: keep it as not read (no text)."""
        with self.db.transaction() as cursor:
            cursor.execute("""
        update reports set status = 'not_read', kind = 'paper', read_at = now(), note = %s
        where report_path = %s""", (reason[:500], report_path))


def _store_page(cursor, report_path, page):
    cursor.execute("""
        insert into report_pages (report_path, html_gzip, fetched_at) values (%s, %s, now())
        on conflict (report_path) do update
            set html_gzip = excluded.html_gzip, fetched_at = now()""",
                   (report_path, gzip.compress(page)))
