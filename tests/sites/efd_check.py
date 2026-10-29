"""A small stand-in eFD for testing the eFD access check.

It imitates the real flow: CSRF cookie + hidden field on the home page, the
agreement POST answered with a 302 to /search/ that sets the session cookie,
the DataTables JSON endpoint, and an electronic PTR page with a table.
`MODE` switches on failure scenarios.
"""
import json
import re
import time
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs

from ..support.web import serve

MODE = "ok"
LOG = []            # (time, method, path, headers, form)
CALLS = {}          # per-path call counters (for the flaky mode)
SECRET = "Xq2b7Lr0aZcVt9WmPe3sYkN6jHuD4fG1"          # 32-char unmasked token (cookie)
MASKED = "m" * 32 + "M" * 32                          # 64-char masked token (form field)
SESSION = "sess-abc123"

AKAMAI = """<HTML><HEAD>
<TITLE>Access Denied</TITLE>
</HEAD><BODY>
<H1>Access Denied</H1>

You don't have permission to access "http&#58;&#47;&#47;efdsearch&#46;senate&#46;gov&#47;search&#47;home&#47;" on this server.<P>
Reference&#32;&#35;18&#46;5f3a2d17&#46;1761050000&#46;2b9c4e1
<P>https&#58;&#47;&#47;errors&#46;edgesuite&#46;net&#47;18&#46;5f3a2d17&#46;1761050000&#46;2b9c4e1</P>
</BODY>
</HTML>
"""

CSRF_FAIL = """<!DOCTYPE html><html><head><title>403 Forbidden</title></head><body>
<h1>Forbidden <span>(403)</span></h1><p>CSRF verification failed. Request aborted.</p></body></html>"""

HOME = f"""<!DOCTYPE html><html><head><title>eFD: Home</title></head><body>
<h1>United States Senate Financial Disclosures (eFD)</h1>
<form id="agreement_form" method="post" action="/search/home/">
<input type="hidden" name="csrfmiddlewaretoken" value="{MASKED}">
<input class="form-check-input" type="checkbox" value="1" id="agree_statement" name="prohibition_agreement">
<label for="agree_statement">I understand the prohibitions on obtaining and use of financial disclosure reports.</label>
</form></body></html>"""

SEARCH_PAGE = f"""<!DOCTYPE html><html><head><title>eFD: Search</title></head><body>
<form id="searchForm"><input type="hidden" name="csrfmiddlewaretoken" value="{MASKED}">
<input type="checkbox" name="report_type" value="11"> Periodic Transactions
<button type="submit">Search Reports</button></form></body></html>"""

MAINTENANCE = """<!DOCTYPE html><html><head><title>eFD</title></head><body>
<h1>Site Maintenance</h1><p>eFD is currently down for scheduled maintenance. Please check back later.</p>
</body></html>"""

PTR_PAGE = """<!DOCTYPE html><html><head><title>eFD: Print Report</title></head><body>
<h1>Periodic Transaction Report for 10/15/2026</h1>
<h2 class="filedReport">The Honorable Jane Q Example (Example, Jane)</h2>
<div class="table-responsive">
<table class="table table-striped">
  <thead>
    <tr class="header">
      <th scope="col">#</th><th scope="col">Transaction Date</th><th scope="col">Owner</th>
      <th scope="col">Ticker</th><th scope="col">Asset Name</th><th scope="col">Asset Type</th>
      <th scope="col">Type</th><th scope="col">Amount</th><th scope="col">Comment</th>
    </tr>
  </thead>
  <tbody>
    <tr><td>1</td><td>10/01/2026</td><td>Spouse</td>
        <td><a href="https://finance.yahoo.com/quote/AAPL" target="_blank">AAPL</a></td>
        <td>Apple Inc.</td><td>Stock</td><td>Purchase</td><td>$1,001 - $15,000</td><td>--</td></tr>
    <tr><th scope="row">2</th><td>10/02/2026</td><td>Self</td><td>--</td>
        <td>US Treasury Note <div class="text-muted"><em>Rate/Coupon:</em> 4.25%
        <br> <em>Matures:</em> 09/30/2030</div></td>
        <td>Corporate Bond</td><td>Sale (Full)</td><td>$15,001 - $50,000</td><td>Called by issuer</td></tr>
    <tr><td>3</td><td>10/03/2026</td><td>Joint</td><td>MSFT</td><td>Microsoft Corp &amp; Co</td>
        <td>Stock Option</td><td>Exchange</td><td>$50,001 - $100,000</td><td>--</td></tr>
  </tbody>
</table></div></body></html>"""

TABLE_CHANGED = """<!DOCTYPE html><html><body><table>
<tr><th>Date</th><th>Security</th><th>Value</th></tr>
<tr><td>10/01/2026</td><td>Apple</td><td>$1,001 - $15,000</td></tr></table></body></html>"""


def row(first, last, filer, kind, uuid, title, received):
    return [first, last, filer,
            f'<a href="/search/view/{kind}/{uuid}/" target="_blank">{title}</a>', received]


ROWS_OK = [
    row("Jane Q", "Example", "Example, Jane (Senator)", "ptr", "1111-aaaa", "Periodic Transaction Report for 10/09/2026", "10/10/2026"),
    row("John", "Sample", "Sample, John (Senator)", "ptr", "2222-bbbb", "Periodic Transaction Report for 10/15/2026", "10/17/2026"),
    row("Pat", "Paper", "Paper, Pat (Senator)", "paper", "3333-cccc", "Periodic Transaction Report", "10/18/2026"),
    row("Odd", "Date", "Date, Odd (Senator)", "ptr", "4444-dddd", "Periodic Transaction Report", "n/a"),
]
ROWS_PAPER = [ROWS_OK[2], row("Sam", "Scan", "Scan, Sam (Senator)", "paper", "5555-eeee", "PTR", "10/12/2026")]


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):     # keep test output quiet
        pass

    def cookies(self):
        jar = SimpleCookie(self.headers.get("Cookie", ""))
        return {key: morsel.value for key, morsel in jar.items()}

    def send(self, code, body, ctype="text/html; charset=utf-8", headers=None):
        data = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        for key, value in (headers or []):
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(data)

    def redirect(self, location, cookie=None):
        headers = [("Location", location)]
        if cookie:
            headers.append(("Set-Cookie", cookie))
        self.send(302, "", headers=headers)

    def base(self):
        return f"http://{self.headers['Host']}"

    def record(self, form=None):
        LOG.append((time.monotonic(), self.command, self.path, dict(self.headers), form))
        CALLS[(self.command, self.path)] = CALLS.get((self.command, self.path), 0) + 1
        return CALLS[(self.command, self.path)]

    def common_failures(self, nth):
        if MODE == "blocked":
            self.send(403, AKAMAI); return True
        if MODE == "down":
            self.send(503, "<html><body>Service Unavailable</body></html>"); return True
        if MODE == "ratelimit":
            self.send(429, "<html><body>Too Many Requests</body></html>"); return True
        if MODE == "flaky" and nth == 1 and (self.command, self.path) in {
                ("GET", "/search/home/"), ("POST", "/search/report/data/")}:
            self.send(502, "<html><body>Bad Gateway</body></html>"); return True
        return False

    def do_GET(self):
        nth = self.record()
        if self.common_failures(nth):
            return
        session_ok = self.cookies().get("sessionid") == SESSION
        if self.path == "/search/home/":
            if MODE == "maintenance":
                return self.send(200, MAINTENANCE)
            if MODE == "notoken":
                return self.send(200, "<html><body><h1>Welcome</h1><p>New design</p></body></html>")
            return self.send(200, HOME, headers=[("Set-Cookie", f"csrftoken={SECRET}; Path=/; SameSite=Lax")])
        if self.path == "/search/":
            if not session_ok:
                return self.redirect("/search/home/")
            return self.send(200, SEARCH_PAGE)
        if self.path.startswith("/search/view/ptr/"):
            if not session_ok or MODE == "reportlost":
                return self.redirect("/search/home/")
            if MODE == "tablechanged":
                return self.send(200, TABLE_CHANGED)
            return self.send(200, PTR_PAGE)
        self.send(404, "<html><body>Not Found</body></html>")

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        form = {k: v[0] for k, v in parse_qs(self.rfile.read(length).decode(), keep_blank_values=True).items()}
        nth = self.record(form)
        if self.common_failures(nth):
            return
        cookies = self.cookies()
        token_ok = (cookies.get("csrftoken") == SECRET and form.get("csrfmiddlewaretoken") in (SECRET, MASKED))
        if self.path == "/search/home/":
            if MODE == "csrf" or not token_ok or self.headers.get("Referer") != self.base() + "/search/home/":
                return self.send(403, CSRF_FAIL)
            if MODE == "noagree" or form.get("prohibition_agreement") != "1":
                return self.send(200, HOME)
            return self.redirect("/search/", cookie=f"sessionid={SESSION}; Path=/; HttpOnly")
        if self.path == "/search/report/data/":
            if cookies.get("sessionid") != SESSION or MODE == "sessionlost":
                return self.redirect("/search/home/")
            if not token_ok or self.headers.get("Referer") != self.base() + "/search/":
                return self.send(403, CSRF_FAIL)
            # Reject malformed searches like a strict server would
            date_ok = all(re.fullmatch(r"\d\d/\d\d/\d{4} 00:00:00", form.get(k, ""))
                          for k in ("submitted_start_date", "submitted_end_date"))
            if form.get("report_types") != "[11]" or form.get("start") != "0" or form.get("length") != "25" or not date_ok:
                return self.send(400, "<html><body>Bad Request</body></html>")
            if MODE == "notjson":
                return self.send(200, "<html><body><h1>Search</h1><p>Results moved</p></body></html>")
            if MODE == "badrows":
                body = {"draw": 1, "recordsTotal": 2, "recordsFiltered": 2, "data": [{"name": "x"}], "result": "ok"}
            elif MODE == "empty":
                body = {"draw": 1, "recordsTotal": 0, "recordsFiltered": 0, "data": [], "result": "ok"}
            elif MODE == "paperonly":
                body = {"draw": 1, "recordsTotal": 2, "recordsFiltered": 2, "data": ROWS_PAPER, "result": "ok"}
            else:
                body = {"draw": 1, "recordsTotal": 99999, "recordsFiltered": 63, "data": ROWS_OK, "result": "ok"}
            return self.send(200, json.dumps(body), ctype="application/json")
        self.send(404, "<html><body>Not Found</body></html>")


def start():
    """Start the site; its address."""
    return serve(Handler)
