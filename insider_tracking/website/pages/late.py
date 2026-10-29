"""late.py - the Late filings page: reports filed after the 45-day limit, and who files late most."""


class LatePage:
    def build(self, site):
        ds = site.congress
        late = ds.late_reports()
        rows = {}
        for report in late:
            filer = ds.filer_by_report.get(report.path)
            if not filer:
                continue
            row = rows.setdefault(filer.key, {"filer": filer, "late": 0, "late_trades": 0, "longest": 0})
            row["late"] += 1
            row["late_trades"] += sum(1 for t in report.trades if t.late)
            row["longest"] = max(row["longest"], report.delay)
        for row in rows.values():
            total = len(row["filer"].filed_reports)
            row["total"] = total
            row["share"] = round(100 * row["late"] / total) if total else 0
        by_filer = sorted(rows.values(), key=lambda r: (-r["late"], -r["share"], r["filer"].name))
        year_ago = ds.filed_reports(365)
        site.writer.page("late.html", "/late/index.html", page="late", late=late, by_filer=by_filer,
                         hist=ds.delay_histogram(), filer_of=ds.filer_by_report,
                         late_12m=sum(1 for r in year_ago if r.late), reports_12m=len(year_ago),
                         late_trades_12m=sum(1 for r in year_ago for t in r.trades if t.late))

    # ---- data files, feed, sitemap
