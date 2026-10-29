"""
Reading the SEC's quarterly data files: which filings and trades are kept (Form 4 buys and
sells by officers and directors), amendments, funds, a typo in the price, 10b5-1 plans.
"""
import sys
import tempfile
from pathlib import Path

from insider_tracking.sources.sec import parsing

from .sites.sec_files import ndt, own, sub, zip_of
from .support.checks import Checks

check = Checks()
data = zip_of(
    [sub("A-1", "4", "320193", "Apple Inc.", "AAPL", "1"), sub("A-2", "4", "789019", "MICROSOFT CORP", "MSFT"),
     sub("A-3", "3", "789019", "MICROSOFT CORP", "MSFT"), sub("A-4", "4", "1", "FUND CO", "FND"),
     sub("A-5", "4/A", "320193", "Apple Inc.", "AAPL"), sub("A-6", "4", "2", "Typo Inc", "TYPO"),
     sub("A-7", "4", "3", "Nothing Inc", "NONE")],
    [own("A-1", "1214156", "COOK TIMOTHY D", "Director,Officer", "Chief Executive Officer"),
     own("A-2", "222", "GATES WILLIAM H", "Director", ""), own("A-3", "333", "NEW DIRECTOR", "Director", ""),
     own("A-4", "444", "BIG FUND LP", "TenPercentOwner", ""), own("A-5", "1214156", "COOK TIMOTHY D", "Officer", "CEO"),
     own("A-6", "555", "FAT FINGER", "Officer", "CFO"), own("A-7", "666", "QUIET PERSON", "Officer", "COO")],
    [ndt("A-1", "1", "S", "50000", "210.5", "D"), ndt("A-1", "2", "F", "1000", "210", "D"),
     ndt("A-2", "3", "P", "1000", "420", "A", di="I", nature="By trust"), ndt("A-2", "4", "A", "500", "0", "A"),
     ndt("A-4", "5", "P", "99999", "1", "A"), ndt("A-5", "6", "S", "50000", "210.5", "D"),
     ndt("A-6", "7", "S", "1000", "5000000", "D"), ndt("A-6", "8", "P", "100", "", "A"),
     ndt("A-7", "9", "M", "100", "1", "A")],
    metadata=True)
path = Path(tempfile.mkdtemp()) / "q.zip"
path.write_bytes(data)
filings, trades, counts = parsing.parse_quarter(path)
print(counts)
for f in filings:
    print(" filing", f["accession"], f["form"], f["ticker"], f["company_name"], "plan", f["plan_10b5_1"],
          "amend", f["is_amendment"], "url", f["url"])
for t in trades:
    print(" trade", t["accession"], t["insider_name"], t["insider_title"], t["roles"], t["kind"], t["shares"], t["price"],
          t["value"], "direct" if t["direct"] else t["nature"], "SUSPECT:" + str(t["suspect_reason"]) if t["suspect"] else "")

check("counts: 7 submissions, 6 Form 4s, 6 buys and sells, 5 kept",
      counts["submissions"] == 7 and counts["form4"] == 6 and counts["buys_sells"] == 6 and counts["kept_trades"] == 5, counts)
check("filings kept: officers' and directors' buys and sells (not the fund, not a Form 3, not an option exercise)",
      {f["accession"] for f in filings} == {"A-1", "A-2", "A-5", "A-6"}, sorted(f["accession"] for f in filings))
check("one trade is suspect (a price far too high)", sum(t["suspect"] for t in trades) == 1)
check("a trade without a price is not suspect",
      [t for t in trades if t["accession"] == "A-6" and t["price"] is None][0]["suspect"] is False)
check("a 10b5-1 plan is recognised", [f for f in filings if f["accession"] == "A-1"][0]["plan_10b5_1"] is True)
check("an amendment (4/A) is marked", [f for f in filings if f["accession"] == "A-5"][0]["is_amendment"] is True)
check("an indirect holding keeps its nature", [t for t in trades if t["accession"] == "A-2"][0]["nature"] == "By trust")
sys.exit(check.finish())
