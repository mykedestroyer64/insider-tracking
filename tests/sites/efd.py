"""A stand-in eFD with 15 years of made-up Periodic Transaction Reports, for the Senate collector.

Imitates: CSRF cookie + hidden field, agreement POST -> 302 /search/ + session cookie,
DataTables JSON search filtered by received date (end day inclusive, like a date compare),
results sorted by last name, electronic report pages with a transactions table.
Switchable failures: sessions that expire, blocks after N requests, broken report pages.
"""
import json
import random
import re
import time
from datetime import date, timedelta
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs

from ..support.web import serve

TODAY = date.today()
SECRET = "Xq2b7Lr0aZcVt9WmPe3sYkN6jHuD4fG1"
MASKED = "m" * 32 + "M" * 32

CONFIG = {"expire_every": 0, "block_after": 0, "down": False, "all_broken": False}
STATE = {"requests": 0, "sessions": {}, "session_counter": 0}
LOG = []

FILERS = [("John", "Fetterman", "Senator"), ("Richard", "Blumenthal", "Senator"),
          ("A. Mitchell", "McConnell, Jr.", "Senator"), ("James Conley", "Justice, II", "Senator"),
          ("Sheldon", "Whitehouse", "Senator"), ("Shelley M", "Capito", "Senator"),
          ("Thomas H", "Tuberville", "Senator"), ("Ron", "Wyden", "Senator"),
          ("Patrick J", "Toomey", "Former Senator"), ("Kelly", "Loeffler", "Former Senator")]
TICKERS = [("AAPL", "Apple Inc.", "Stock"), ("MSFT", "Microsoft Corporation", "Stock"),
           ("--", "US Treasury Note <div class=\"text-muted\"><em>Rate/Coupon:</em> 4.25%<br><em>Matures:</em> 09/30/2030</div>", "Corporate Bond"),
           ("NVDA", "NVIDIA Corporation <div class=\"text-muted\"><em>Option Type:</em> Call<br><em>Strike price:</em> $120</div>", "Stock Option"),
           ("--", "Vanguard Total Stock Market Fund &amp; Co", "Mutual Fund")]
TYPES = ["Purchase", "Sale (Full)", "Sale (Partial)", "Exchange"]
AMOUNTS = ["$1,001 - $15,000", "$15,001 - $50,000", "$50,001 - $100,000",
           "$1,000,001 - $5,000,000", "Over $50,000,000"]
OWNERS = ["Self", "Spouse", "Joint", "Child"]

REPORTS = {}       # uuid -> report info
TRANSACTIONS = {}  # uuid -> list of rows (as written on the page)
BROKEN = set()     # uuids whose page has no transactions table
MISSING = set()    # uuids whose page is 404


def add_report(rng, received, kind="ptr", filer=None, broken=False, missing=False):
    uuid = f"{received:%Y%m%d}-{len(REPORTS):05d}-{rng.randint(1000, 9999)}"
    first, last, ftype = filer or rng.choice(FILERS)
    title = f"Periodic Transaction Report for {received:%m/%d/%Y}"
    if rng.random() < 0.05:
        title += " (Amendment 1)"
    REPORTS[uuid] = {"uuid": uuid, "kind": kind, "first": first, "last": last, "type": ftype,
                     "title": title if kind == "ptr" else "Periodic Transaction Report",
                     "received": received}
    if kind == "ptr":
        rows = []
        for _ in range(rng.randint(1, 6)):
            ticker, asset, atype = rng.choice(TICKERS)
            # most trades are reported within the 45-day limit, some much later
            day = received - timedelta(days=rng.randint(1, 44) if rng.random() < 0.88 else rng.randint(46, 160))
            rows.append([f"{day:%m/%d/%Y}", rng.choice(OWNERS), ticker, asset, atype,
                         rng.choice(TYPES), rng.choice(AMOUNTS), rng.choice(["--", "Sold for rebalancing"])])
        TRANSACTIONS[uuid] = rows
    if broken:
        BROKEN.add(uuid)
    if missing:
        MISSING.add(uuid)
    return uuid


def build_data(seed=7):
    REPORTS.clear(); TRANSACTIONS.clear(); BROKEN.clear(); MISSING.clear()
    rng = random.Random(seed)
    month = date(2012, 1, 1)
    while month <= TODAY:
        next_month = (month.replace(day=28) + timedelta(days=4)).replace(day=1)
        last_day = min(next_month - timedelta(days=1), TODAY)
        for _ in range(rng.randint(2, 9)):
            received = month + timedelta(days=rng.randint(0, (last_day - month).days))
            add_report(rng, received, kind="paper" if rng.random() < 0.1 else "ptr")
        month = next_month
    # A very busy month (more than 100 results) and a single day with more than 100
    for _ in range(150):
        add_report(rng, date(2020, 3, 1) + timedelta(days=rng.randint(0, 30)))
    for _ in range(130):
        add_report(rng, date(2019, 6, 14))
    # A few unreadable reports, far apart, and two missing pages
    for day, kwargs in ((date(2016, 5, 10), {"broken": True}), (date(2021, 8, 3), {"broken": True}),
                        (date(2024, 2, 20), {"broken": True}), (date(2014, 9, 9), {"missing": True}),
                        (date(2023, 11, 1), {"missing": True})):
        add_report(rng, day, **kwargs)


def truth():
    electronic = [u for u, r in REPORTS.items() if r["kind"] == "ptr"]
    paper = [u for u, r in REPORTS.items() if r["kind"] == "paper"]
    readable = [u for u in electronic if u not in BROKEN and u not in MISSING]
    return {"reports": len(REPORTS), "electronic": len(electronic), "paper": len(paper),
            "read": len(readable), "failed": len(electronic) - len(readable),
            "transactions": sum(len(TRANSACTIONS[u]) for u in readable)}


AKAMAI = """<HTML><HEAD><TITLE>Access Denied</TITLE></HEAD><BODY><H1>Access Denied</H1>
You don't have permission to access "http&#58;&#47;&#47;efdsearch&#46;senate&#46;gov&#47;search&#47;" on this server.<P>
Reference&#32;&#35;18&#46;77aa11bb&#46;1761050000&#46;3c3c3c3</BODY></HTML>"""
HOME = f"""<html><head><title>eFD: Home</title></head><body><form method="post" action="/search/home/">
<input type="hidden" name="csrfmiddlewaretoken" value="{MASKED}">
<input type="checkbox" name="prohibition_agreement" value="1"></form></body></html>"""
SEARCH = "<html><head><title>eFD: Search</title></head><body><form id='searchForm'></form></body></html>"


def report_page(uuid):
    if CONFIG["all_broken"] or uuid in BROKEN:
        return "<html><body><h1>Report</h1><p>This report is being processed.</p></body></html>"
    head = "".join(f"<th scope='col'>{h}</th>" for h in
                   ["#", "Transaction Date", "Owner", "Ticker", "Asset Name", "Asset Type", "Type", "Amount", "Comment"])
    body = []
    for n, row in enumerate(TRANSACTIONS[uuid], 1):
        ticker = row[2] if row[2] == "--" else f"<a href='https://finance.yahoo.com/quote/{row[2]}'>{row[2]}</a>"
        cells = [str(n), row[0], row[1], ticker] + row[3:]
        body.append("<tr>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>")
    return (f"<html><body><h2 class='filedReport'>The Honorable {REPORTS[uuid]['first']} {REPORTS[uuid]['last']}</h2>"
            f"<table class='table table-striped'><thead><tr class='header'>{head}</tr></thead>"
            f"<tbody>{''.join(body)}</tbody></table></body></html>")


def parse_day(text):
    m = re.fullmatch(r"(\d\d)/(\d\d)/(\d{4}) \d\d:\d\d:\d\d", text or "")
    return date(int(m.group(3)), int(m.group(1)), int(m.group(2))) if m else None


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def cookies(self):
        jar = SimpleCookie(self.headers.get("Cookie", ""))
        return {k: v.value for k, v in jar.items()}

    def send(self, code, body, ctype="text/html; charset=utf-8", headers=()):
        data = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        for k, v in headers:
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def base(self):
        return f"http://{self.headers['Host']}"

    def session_ok(self):
        sid = self.cookies().get("sessionid")
        if sid not in STATE["sessions"]:
            return False
        if CONFIG["expire_every"] and STATE["requests"] - STATE["sessions"][sid] >= CONFIG["expire_every"]:
            del STATE["sessions"][sid]
            return False
        return True

    def gate(self):
        STATE["requests"] += 1
        LOG.append((time.monotonic(), self.command, self.path))
        if CONFIG["down"]:
            self.send(503, "<html><body>Service Unavailable</body></html>"); return False
        if CONFIG["block_after"] and STATE["requests"] > CONFIG["block_after"]:
            self.send(403, AKAMAI); return False
        return True

    def do_GET(self):
        if not self.gate():
            return
        if self.path == "/search/home/":
            return self.send(200, HOME, headers=[("Set-Cookie", f"csrftoken={SECRET}; Path=/")])
        if self.path == "/search/":
            return self.send(200, SEARCH) if self.session_ok() else self.redirect("/search/home/")
        m = re.fullmatch(r"/search/view/(ptr|paper)/([^/]+)/", self.path)
        if m:
            if not self.session_ok():
                return self.redirect("/search/home/")
            uuid = m.group(2)
            if uuid not in REPORTS or uuid in MISSING:
                return self.send(404, "<html><body><h1>Not Found</h1></body></html>")
            return self.send(200, report_page(uuid))
        self.send(404, "<html><body>Not Found</body></html>")

    def redirect(self, location, cookie=None):
        headers = [("Location", location)] + ([("Set-Cookie", cookie)] if cookie else [])
        self.send(302, "", headers=headers)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        form = {k: v[0] for k, v in parse_qs(self.rfile.read(length).decode(), keep_blank_values=True).items()}
        if not self.gate():
            return
        token_ok = (self.cookies().get("csrftoken") == SECRET and form.get("csrfmiddlewaretoken") in (SECRET, MASKED))
        if self.path == "/search/home/":
            if not token_ok or self.headers.get("Referer") != self.base() + "/search/home/":
                return self.send(403, "<p>CSRF verification failed. Request aborted.</p>")
            STATE["session_counter"] += 1
            sid = f"s{STATE['session_counter']}"
            STATE["sessions"][sid] = STATE["requests"]
            return self.redirect("/search/", cookie=f"sessionid={sid}; Path=/")
        if self.path == "/search/report/data/":
            if not self.session_ok():
                return self.redirect("/search/home/")
            if not token_ok or self.headers.get("Referer") != self.base() + "/search/":
                return self.send(403, "<p>CSRF verification failed. Request aborted.</p>")
            start_day, end_day = parse_day(form.get("submitted_start_date")), parse_day(form.get("submitted_end_date"))
            if form.get("report_types") != "[11]" or start_day is None or end_day is None:
                return self.send(400, "<html><body>Bad Request</body></html>")
            matches = sorted((r for r in REPORTS.values() if start_day <= r["received"] <= end_day),
                             key=lambda r: (r["last"], r["first"]))
            start, length = int(form.get("start", 0)), int(form.get("length", 25))
            page = matches[start:start + length]
            data = [[r["first"], r["last"], f"{r['last']}, {r['first']} ({r['type']})",
                     f'<a href="/search/view/{r["kind"]}/{r["uuid"]}/" target="_blank">{r["title"]}</a>',
                     f"{r['received']:%m/%d/%Y}"] for r in page]
            body = {"draw": 1, "recordsTotal": len(REPORTS), "recordsFiltered": len(matches), "data": data, "result": "ok"}
            return self.send(200, json.dumps(body), ctype="application/json")
        self.send(404, "<html><body>Not Found</body></html>")


def start():
    """Start the site; its address."""
    return serve(Handler)
