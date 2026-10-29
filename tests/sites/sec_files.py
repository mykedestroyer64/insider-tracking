"""
sec_files.py - made-up SEC quarterly data files ("Insider Transactions Data Sets"): a ZIP of
tab-separated tables, with only the columns' names and the rows a test asks for.
"""

import io
import zipfile

SUB = ["ACCESSION_NUMBER", "FILING_DATE", "PERIOD_OF_REPORT", "DATE_OF_ORIG_SUB", "NO_SECURITIES_OWNED",
       "NOT_SUBJECT_SEC16", "FORM3_HOLDINGS_REPORTED", "FORM4_TRANS_REPORTED", "DOCUMENT_TYPE", "ISSUERCIK",
       "ISSUERNAME", "ISSUERTRADINGSYMBOL", "REMARKS", "AFF10B5ONE"]
OWN = ["ACCESSION_NUMBER", "RPTOWNERCIK", "RPTOWNERNAME", "RPTOWNER_RELATIONSHIP", "RPTOWNER_TITLE", "RPTOWNER_TXT",
       "RPTOWNER_STREET1", "RPTOWNER_STREET2", "RPTOWNER_CITY", "RPTOWNER_STATE", "RPTOWNER_ZIPCODE",
       "RPTOWNER_STATE_DESC", "FILE_NUMBER"]
NDT = ["ACCESSION_NUMBER", "NONDERIV_TRANS_SK", "SECURITY_TITLE", "SECURITY_TITLE_FN", "TRANS_DATE", "TRANS_DATE_FN",
       "DEEMED_EXECUTION_DATE", "DEEMED_EXECUTION_DATE_FN", "TRANS_FORM_TYPE", "TRANS_CODE", "EQUITY_SWAP_INVOLVED",
       "EQUITY_SWAP_TRANS_CD_FN", "TRANS_TIMELINESS", "TRANS_TIMELINESS_FN", "TRANS_SHARES", "TRANS_SHARES_FN",
       "TRANS_PRICEPERSHARE", "TRANS_PRICEPERSHARE_FN", "TRANS_ACQUIRED_DISP_CD", "TRANS_ACQUIRED_DISP_CD_FN",
       "SHRS_OWND_FOLWNG_TRANS", "SHRS_OWND_FOLWNG_TRANS_FN", "VALU_OWND_FOLWNG_TRANS", "VALU_OWND_FOLWNG_TRANS_FN",
       "DIRECT_INDIRECT_OWNERSHIP", "DIRECT_INDIRECT_OWNERSHIP_FN", "NATURE_OF_OWNERSHIP", "NATURE_OF_OWNERSHIP_FN"]


def tsv(header, rows):
    return ("\t".join(header) + "\n" + "".join("\t".join(str(c) for c in r) + "\n" for r in rows)).encode()


def sub(acc, form, cik, name, sym, plan="0", filed="15-JUL-2026"):
    """A row of SUBMISSION.tsv: one filing."""
    return [acc, filed, "13-JUL-2026", "", "", "0", "", "", form, cik, name, sym, "", plan]


def own(acc, cik, name, rel, title):
    """A row of REPORTINGOWNER.tsv: who filed."""
    return [acc, cik, name, rel, title] + [""] * 8


def ndt(acc, sk, code, shares, price, ad, after="1000", di="D", nature=""):
    """A row of NONDERIV_TRANS.tsv: one transaction in shares."""
    row = [""] * len(NDT)
    row[0], row[1], row[2], row[4], row[8], row[9] = acc, sk, "Common Stock", "13-JUL-2026", "4", code
    row[14], row[16], row[18], row[20], row[24], row[26] = shares, price, ad, after, di, nature
    return row


def zip_of(subs, owns, trans, metadata=False):
    """The quarterly file with these rows (bytes of a ZIP)."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as z:
        z.writestr("SUBMISSION.tsv", tsv(SUB, subs))
        z.writestr("REPORTINGOWNER.tsv", tsv(OWN, owns))
        z.writestr("NONDERIV_TRANS.tsv", tsv(NDT, trans))
        if metadata:
            z.writestr("FORM_345_metadata.json", "{}")
    return buffer.getvalue()


def quarter_zip(tag, n_filings, filed):
    """A quarter of `n_filings` made-up Form 4s: a buy or a sell each, an award every tenth."""
    subs, owns, trans = [], [], []
    for i in range(n_filings):
        acc = f"{tag}-{i:04d}"
        subs.append(sub(acc, "4", str(1000 + i % 7), f"Company {i % 7}", f"C{i % 7}", "1" if i % 3 == 0 else "0", filed))
        owns.append(own(acc, str(5000 + i % 11), f"PERSON{i % 11} JOHN X", "Officer" if i % 2 else "Director", "CFO" if i % 2 else ""))
        trans.append(ndt(acc, "1", "S" if i % 2 else "P", str(100 + i), "10.5", "D" if i % 2 else "A"))
        if i % 10 == 0:
            trans.append(ndt(acc, "2", "A", "50", "0", "A"))      # an award: skipped
    return zip_of(subs, owns, trans)
