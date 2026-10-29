"""feed.py - the Atom feed of new trade reports (feed.xml), for news readers."""

from collections import defaultdict
from datetime import datetime, timezone
from html import escape

from ..formatting import fmt_date

FEED_ENTRIES = 50


def rfc3339(value):
    if isinstance(value, datetime):
        return value.replace(tzinfo=timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    return f"{value.isoformat()}T00:00:00Z"


class Feed:
    def build(self, site):
        ds = site.congress
        trades_by_report = defaultdict(list)
        for t in ds.trades:
            trades_by_report[t.report_path].append(t)
        reports = [r for r in ds.reports if r.received and r.path in trades_by_report]
        reports.sort(key=lambda r: (r.received, r.first_seen or datetime.min), reverse=True)
        entries = []
        for report in reports[:FEED_ENTRIES]:
            filer = ds.filer_by_report.get(report.path)
            trades = sorted(trades_by_report[report.path], key=lambda t: t.row)
            who = filer.name if filer else report.filer_name
            extra = f" ({filer.party_state})" if filer and filer.party_state else ""
            items = "".join(
                f"<li>{escape(t.label)}: {escape(t.ticker_text if t.is_stock else (t.asset_name or 'asset'))}"
                f"{' (' + escape(t.asset_name) + ')' if t.is_stock and t.asset_name else ''}, "
                f"{escape(t.amount or 'amount not given')}, traded {fmt_date(t.traded) or 'date not given'}"
                f"{', late' if t.late else ''}</li>" for t in trades)
            content = (f"<p>{escape(who)}{escape(extra)} disclosed {len(trades)} trade"
                       f"{'s' if len(trades) != 1 else ''} on {fmt_date(report.received)}"
                       f"{' (amendment)' if report.is_amendment else ''}.</p><ul>{items}</ul>"
                       f"<p><a href=\"{escape(report.url)}\">Official report</a></p>")
            entries.append({
                "title": f"{who}: {len(trades)} trade{'s' if len(trades) != 1 else ''} disclosed "
                         f"{fmt_date(report.received)}",
                "link": f"{site.config.url}/congress/{filer.slug}/" if filer else report.url,
                "id": f"{site.config.url}/reports/{report.path.strip('/').rsplit('/', 1)[-1]}",
                "updated": rfc3339(report.first_seen or report.received),
                "published": rfc3339(report.received),
                "content": content,
            })
        feed_updated = entries[0]["updated"] if entries else rfc3339(site.now.replace(tzinfo=None))
        xml = site.writer.renderer.render("feed.xml", entries=entries, feed_updated=feed_updated)
        site.writer.write("/feed.xml", xml)
