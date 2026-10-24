"""
charts.py - the numbers behind the site's charts, and the SVG for the static ones.

Charts are plain SVG: thin black lines, gray gridlines, no colors. The home
page's activity chart gets one set of paths per time range (3M, 1Y, 5Y, All)
and a little JavaScript swaps them; everything else is drawn here.
"""

import math
from datetime import date
from html import escape

MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

# Plot area of the 840 x 300 charts
LEFT, RIGHT, TOP, BASE = 48, 816, 20, 252
TICK_XS = [48, 201.6, 355.2, 508.8, 662.4, 816]


def nice_max(value):
    """A round number a little above the highest value, for the top of the y axis."""
    value = max(value, 4)
    for step in (4, 8, 10, 20, 40, 60, 80, 100, 120, 160, 200, 240, 300, 400, 500, 600, 800, 1000):
        if step >= value * 1.05:
            return step
    return math.ceil(value * 1.05 / 400) * 400


def fmt_tick(value):
    return f"{value:g}" if value != int(value) else str(int(value))


def line_chart(series, labels, caption, partial_last=True):
    """Paths and labels for one time range of the activity chart.

    series: list of counts; labels: a label per point (only 6 are shown).
    """
    if len(series) < 2:
        series = (series + [0, 0])[:2]
        labels = (labels + ["", ""])[:2]
    top = nice_max(max(series))
    k = len(series)
    xs = [LEFT + i * (RIGHT - LEFT) / (k - 1) for i in range(k)]
    ys = [BASE - v / top * (BASE - TOP) for v in series]
    points = " L".join(f"{x:.1f} {y:.1f}" for x, y in zip(xs, ys))
    peak = max(range(k), key=lambda i: series[i])
    anchor = "end" if xs[peak] > 760 else ("start" if xs[peak] < 100 else "middle")
    end_y = ys[-1]
    return {
        "line": "M" + points,
        "area": f"M{xs[0]:.1f} {BASE} L" + points + f" L{xs[-1]:.1f} {BASE} Z",
        "endX": round(xs[-1], 1), "endY": round(end_y, 1),
        "endLabelY": round(end_y - 10 if end_y > 44 else end_y + 20, 1),
        "endText": f"{series[-1]} so far" if partial_last else str(series[-1]),
        "peakX": round(xs[peak], 1), "peakY": round(max(ys[peak] - 12, 12), 1),
        "peakText": "" if peak == k - 1 else str(series[peak]), "peakAnchor": anchor,
        "y": [fmt_tick(top), fmt_tick(top * 0.75), fmt_tick(top * 0.5), fmt_tick(top * 0.25), "0"],
        "t": [labels[round(j * (k - 1) / 5)] for j in range(6)],
        "caption": caption,
    }


def activity_charts(dataset):
    """The four time ranges of 'trades disclosed over time'."""
    monthly = dataset.monthly_counts()
    weekly = dataset.weekly_counts(13)
    today = dataset.today

    def month_label(day, with_year):
        return f"{MONTHS[day.month - 1]} {day.year}" if with_year else MONTHS[day.month - 1]

    def span(points, with_year=True):
        if not points:
            return ""
        first, last = points[0][0], points[-1][0]
        return (f"{MONTHS[first.month - 1]} {first.year} to {MONTHS[last.month - 1]} {last.year} "
                f"({MONTHS[last.month - 1]} so far)")

    last12, last60 = monthly[-12:], monthly[-60:]
    charts = {
        "3M": line_chart([v for _, v in weekly], [f"{MONTHS[d.month - 1]} {d.day}" for d, _ in weekly],
                         "Weekly, last 13 weeks (this week so far)"),
        "1Y": line_chart([v for _, v in last12], [month_label(d, False) for d, _ in last12],
                         "Monthly, " + span(last12)),
        "5Y": line_chart([v for _, v in last60], [month_label(d, True) for d, _ in last60],
                         "Monthly, " + span(last60)),
        "All": line_chart([v for _, v in monthly], [str(d.year) for d, _ in monthly],
                          "Monthly, " + span(monthly)),
    }
    return charts


def histogram_svg(hist):
    """The 'how long senators take to file' bar chart (840 x 300): one bar group of reports."""
    counts, labels = hist["counts"], hist["labels"]
    top = nice_max(max(counts) if counts else 0)
    n = len(counts)
    slot = (RIGHT - LEFT) / n
    base, plot_top = 248, 24
    scale = (base - plot_top) / top
    parts = []
    for i, g in enumerate((0, 0.25, 0.5, 0.75, 1)):
        y = base - g * (base - plot_top)
        color = "#C4C4C4" if g == 0 else "#EBEBEB"
        parts.append(f'<line x1="{LEFT}" x2="{RIGHT}" y1="{y:.1f}" y2="{y:.1f}" '
                     f'style="stroke: {color}; stroke-width: 1"></line>')
        parts.append(f'<text x="40" y="{y + 4:.1f}" text-anchor="end" class="chart-label">'
                     f'{fmt_tick(top * g)}</text>')
    for i, (count, label) in enumerate(zip(counts, labels)):
        cx = LEFT + slot * (i + 0.5)
        x, y = cx - 12, base - count * scale
        fill = "#111111" if i >= hist["late_from_index"] else "#8A8A8A"
        if count > 0:
            y = min(y, base - 2)
            d = (f"M{x:.1f} {base} V{y + 4:.1f} A4 4 0 0 1 {x + 4:.1f} {y:.1f} H{x + 20:.1f} "
                 f"A4 4 0 0 1 {x + 24:.1f} {y + 4:.1f} V{base} Z")
            parts.append(f'<path d="{d}" style="fill: {fill}"><title>{escape(label)} days: {count} '
                         f'report{"" if count == 1 else "s"}</title></path>')
        parts.append(f'<text x="{cx:.1f}" y="268" text-anchor="middle" class="chart-label">{escape(label)}</text>')
    boundary = LEFT + slot * hist["late_from_index"]
    parts.append(f'<line x1="{boundary:.1f}" x2="{boundary:.1f}" y1="10" y2="{base}" '
                 'style="stroke: #111111; stroke-width: 1"></line>')
    parts.append(f'<text x="{boundary + 6:.1f}" y="20" class="chart-note">45-day limit</text>')
    if hist["total"]:
        pct = round(100 * hist["late"] / hist["total"])
        late_x = LEFT + slot * (hist["late_from_index"] + 1.5)
        parts.append(f'<text x="{late_x:.1f}" y="204" text-anchor="middle" class="chart-note">'
                     f'{hist["late"]} late ({pct}%)</text>')
    parts.append(f'<text x="{(LEFT + RIGHT) / 2:.0f}" y="292" text-anchor="middle" class="chart-label">'
                 'days from the oldest trade in a report to its filing</text>')
    label = (f"Bar chart of reports by days from their oldest trade to their filing, last 12 months. "
             f"{hist['late']} of {hist['total']} reports were filed after the 45-day limit.")
    return ('<div class="chart-wrap">'
            f'<svg viewBox="0 0 840 300" role="img" aria-label="{escape(label)}" class="chart">'
            + "".join(parts) + "</svg></div>")


def yearly_bars_svg(counts_by_year, title):
    """Trades per year for one member of Congress or one stock (840 x 260)."""
    years = sorted(counts_by_year)
    if not years:
        return ""
    years = list(range(years[0], years[-1] + 1))
    counts = [counts_by_year.get(y, 0) for y in years]
    top = nice_max(max(counts))
    n = len(years)
    slot = (RIGHT - LEFT) / n
    base, plot_top = 212, 24
    scale = (base - plot_top) / top
    width = min(24, max(8, slot - 8))
    parts = []
    for g in (0, 0.25, 0.5, 0.75, 1):
        y = base - g * (base - plot_top)
        color = "#C4C4C4" if g == 0 else "#EBEBEB"
        parts.append(f'<line x1="{LEFT}" x2="{RIGHT}" y1="{y:.1f}" y2="{y:.1f}" '
                     f'style="stroke: {color}; stroke-width: 1"></line>')
        parts.append(f'<text x="40" y="{y + 4:.1f}" text-anchor="end" class="chart-label">{fmt_tick(top * g)}</text>')
    peak = max(range(n), key=lambda i: counts[i])
    step = max(1, math.ceil(n / 8))        # label every year when they fit, else every 2nd/3rd
    for i, (year, count) in enumerate(zip(years, counts)):
        cx = LEFT + slot * (i + 0.5)
        x, y = cx - width / 2, base - count * scale
        if count > 0:
            y = min(y, base - 2)
            r = min(4, width / 2)
            d = (f"M{x:.1f} {base} V{y + r:.1f} A{r} {r} 0 0 1 {x + r:.1f} {y:.1f} H{x + width - r:.1f} "
                 f"A{r} {r} 0 0 1 {x + width:.1f} {y + r:.1f} V{base} Z")
            parts.append(f'<path d="{d}" style="fill: #111111"><title>{year}: {count}</title></path>')
            if i == peak:
                parts.append(f'<text x="{cx:.1f}" y="{y - 8:.1f}" text-anchor="middle" class="chart-note">{count}</text>')
        if (n - 1 - i) % step == 0:
            parts.append(f'<text x="{cx:.1f}" y="232" text-anchor="middle" class="chart-label">{year}</text>')
    label = f"{title}: trades per year from {years[0]} to {years[-1]}. Most in {years[peak]} with {counts[peak]}."
    return ('<div class="chart-wrap">'
            f'<svg viewBox="0 0 840 260" role="img" aria-label="{escape(label)}" class="chart">'
            + "".join(parts) + "</svg></div>")
