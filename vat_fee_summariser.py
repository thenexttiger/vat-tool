#!/usr/bin/env python3
"""
Amazon Seller Fee VAT Summariser (PDF-based)

Reads Amazon fee tax invoices + credit notes as PDFs (the actual legal VAT
documents) and produces the reclaimable UK input VAT for the period, with a
GREEN/AMBER/RED confidence rating and a zip package for your accountant.

WHY PDF, NOT CSV: the fee CSVs carry no currency and no exchange rate, so for
EU/SEK marketplaces they can't yield a correct GBP figure (a CSV of Swedish
fees looks like pounds). The PDF is the legal invoice and prints the GBP VAT
explicitly — directly for .co.uk, and as an "-GBP x.xx" conversion line for
EUR/SEK invoices — so it's both correct across currencies and what HMRC wants.

Each invoice is self-checked three ways:
  1. net + VAT = gross            (native currency arithmetic)
  2. VAT ≈ net × VAT rate         (rate sanity)
  3. GBP VAT ≈ native VAT × rate  (conversion sanity, non-GBP invoices)
Any invoice that fails a check, or can't be parsed, drops the rating.

Every document's period (or issue date) is read from the PDF, so the total
covers exactly one VAT quarter: pass it with --quarter, or let the tool detect
it from the invoices. Anything dated outside the quarter is listed and left
out; a month with no invoice, or an undated invoice, drops the rating.

Usage:
    ./venv/bin/python vat_fee_summariser.py <folder_of_pdfs> [--quarter "Q3 2026"] [--zip out.zip | --zip-dir folder]

Example:
    ./venv/bin/python vat_fee_summariser.py vat_inputs/ --quarter "Q2 2026" --zip-dir vat_inputs/
    (writes vat_inputs/fee_invoices_for_accountant_Q2_2026.zip + the summary beside it)
"""

import io
import os
import re
import sys
import zipfile
from collections import defaultdict
from datetime import datetime

import pdfplumber

# Amazon's UK VAT number — every reclaimable fee invoice should carry this as
# the supplier. A different supplier VAT is a flag (not necessarily UK-input).
AMAZON_UK_VAT = "GB727255821"
# the supplier-name label that follows the customer's name on the same line
# (de, es, it, nl, sv, plus en/fr/pl in case Amazon moves them onto one line too)
SUPPLIER_LABEL_RE = re.compile(
    r"\s*(?:Leistungserbringer|Nombre del proveedor|Nome(?: del)? fornitore|Dienstverlener|"
    r"Leverantörsnamn|Supplier Name|Nom du fournisseur|Nazwa dostawcy)\s*:\s*$", re.I)

# Invoice-number shapes we care about (UK input VAT). Non-GB marketplaces
# (SA-*, AE-*) are foreign VAT and not reclaimable here, so they're ignored.
INVOICE_NUM_RE = re.compile(r"\bGB-AEU-\d{4}-\d+\b")
CREDIT_NUM_RE = re.compile(r"\bGB-CN-AEU-\d{4}-\d+\b")

# One money token, e.g. "-EUR 5.94", "GBP 12.34", "SEK 65.23".
MONEY_RE = re.compile(r"(-?)\s*([A-Z]{3})\s*(-?[\d,]+\.\d{2})")
# Exchange rate is printed as "[0.866250 GBP / 1 EUR]" — match the bracket, not
# the label word, since Amazon localises it (Tauschrate/Växlingskurs/...).
EXRATE_RE = re.compile(r"\[\s*([\d.]+)\s*GBP\s*/\s*1\s*([A-Z]{3})\s*\]")

TOLERANCE = 0.02  # absolute £/€ tolerance for arithmetic checks

# Dates as Amazon prints them on UK and localised invoices:
#   30/04/2026   30.04.2026   30-04-2026   2026-04-30   30 April 2026   30 Apr 2026
#   30 avril 2026   30. April 2026   30 april 2026 (sv/nl)
# A period is two of them joined by to / - / bis / till / au / al / t/m.
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
_MONTH_WORDS = {
    1: "jan|janv|januar|january|januari|janvier|enero|gennaio",
    2: "feb|febr|februar|february|februari|févr|fevr|février|fevrier|febrero|febbraio",
    3: "mar|mär|märz|marz|march|mars|maart|marzo",
    4: "apr|april|avr|avril|abril|aprile",
    5: "may|mai|maj|mei|mayo|maggio",
    6: "jun|juni|june|juin|junio|giugno",
    7: "jul|juli|july|juil|juillet|julio|luglio",
    8: "aug|august|augusti|augustus|août|aout|agosto",
    9: "sep|sept|september|septembre|septiembre|settembre",
    10: "oct|okt|october|oktober|octobre|octubre|ottobre",
    11: "nov|november|novembre|noviembre",
    12: "dec|dez|december|dezember|décembre|decembre|diciembre|dicembre",
}
_MONTH_LOOKUP = {w: m for m, words in _MONTH_WORDS.items() for w in words.split("|")}
_NUM_DATE = r"\d{1,2}[/.\-]\d{1,2}[/.\-]\d{4}|\d{4}-\d{2}-\d{2}"
_WORD_DATE = r"\d{1,2}\.?\s+[A-Za-zÀ-ÿ]{3,10}\.?\s+\d{4}"
_ANY_DATE = rf"(?:{_NUM_DATE}|{_WORD_DATE})"
DATE_RE = re.compile(_ANY_DATE)
PERIOD_RE = re.compile(rf"({_ANY_DATE})\s*(?:to|-|–|bis|till|au|al|t/m|through)\s*({_ANY_DATE})", re.I)


def _to_date(s):
    """Parse one printed date; day-first for numeric dates (Amazon UK/EU), ISO
    when it starts with the year, month words in the languages Amazon prints.
    Returns None rather than guessing when it can't."""
    s = s.strip()
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        y, mo, d = map(int, m.groups())
    else:
        m = re.fullmatch(r"(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{4})", s)
        if m:
            d, mo, y = map(int, m.groups())
        else:
            m = re.fullmatch(r"(\d{1,2})\.?\s+([A-Za-zÀ-ÿ]{3,10})\.?\s+(\d{4})", s)
            if not m:
                return None
            d, y = int(m.group(1)), int(m.group(3))
            mo = _MONTH_LOOKUP.get(m.group(2).lower().rstrip("."))
            if mo is None:
                return None
    try:
        return datetime(y, mo, d).date()
    except ValueError:
        return None


def _ym(year, month):
    return year * 12 + (month - 1)


def parse_quarter(text):
    """A VAT period from what a person types. Returns (start_ym, end_ym) with
    ym = year*12 + month-1, or None.
      quarters:  Q3 2026 · q3 2026 · 2026 Q3 · Q3-2026 · q32026
      ranges:    Jun-Aug 2026 · June to August 2026 · Nov 2025 - Jan 2026
                 Nov-Jan 2026 (start falls in the year before) · 06/2026-08/2026
      one month: Aug 2026"""
    t = (text or "").strip()
    if not t:
        return None
    m = re.fullmatch(r"(\d{1,2})/(\d{4})\s*(?:-|–|to)\s*(\d{1,2})/(\d{4})", t, re.I)
    if m:
        sm, sy, em, ey = map(int, m.groups())
        if 1 <= sm <= 12 and 1 <= em <= 12 and _ym(sy, sm) <= _ym(ey, em) and _ym(ey, em) - _ym(sy, sm) < 12:
            return (_ym(sy, sm), _ym(ey, em))
        return None
    q = re.search(r"(?<![A-Za-z])[Qq]\s*([1-4])(?!\d)", t) or re.search(r"(?<![A-Za-z])[Qq]([1-4])(?=20\d{2})", t)
    years = [int(y) for y in re.findall(r"(?<!\d)(20\d{2})(?!\d)", t)]
    if q and years and not re.search(r"[A-Za-z]{3,}", re.sub(r"[Qq]", "", t)):
        qn = int(q.group(1))
        return (_ym(years[0], (qn - 1) * 3 + 1), _ym(years[0], qn * 3))
    words = [w for w in re.findall(r"[A-Za-zÀ-ÿ]{3,}", t) if w.lower() not in ("to", "till", "until", "bis", "through")]
    months = [_MONTH_LOOKUP.get(w.lower()) for w in words]
    if not years or not months or any(mo is None for mo in months) or len(months) > 2:
        return None
    if len(months) == 1:
        return (_ym(years[-1], months[0]), _ym(years[-1], months[0]))
    sm, em = months
    ey = years[-1]
    sy = years[0] if len(years) > 1 else (ey - 1 if sm > em else ey)
    if _ym(sy, sm) > _ym(ey, em) or _ym(ey, em) - _ym(sy, sm) >= 12:
        return None
    return (_ym(sy, sm), _ym(ey, em))


def in_period(day, period):
    return period[0] <= _ym(day.year, day.month) <= period[1]


def quarter_of(day):
    """The calendar quarter containing a day, as a period."""
    qn = (day.month - 1) // 3
    return (_ym(day.year, qn * 3 + 1), _ym(day.year, qn * 3 + 3))


def quarter_months(period):
    """[(year, month), ...] across the period."""
    return [(ym // 12, ym % 12 + 1) for ym in range(period[0], period[1] + 1)]


def quarter_label(period):
    """'Q3 2026' for a calendar quarter, else 'Jun-Aug 2026' / 'Nov 2025-Jan 2026' / 'Aug 2026'."""
    (sy, sm), (ey, em) = quarter_months(period)[0], quarter_months(period)[-1]
    if sy == ey and (sm - 1) % 3 == 0 and em == sm + 2:
        return f"Q{(sm - 1) // 3 + 1} {sy}"
    if period[0] == period[1]:
        return f"{MONTHS[sm - 1]} {sy}"
    return f"{MONTHS[sm - 1]}-{MONTHS[em - 1]} {ey}" if sy == ey else f"{MONTHS[sm - 1]} {sy}-{MONTHS[em - 1]} {ey}"


def period_range(period):
    """'MM/YYYY-MM/YYYY', the form the downloader script takes."""
    (sy, sm), (ey, em) = quarter_months(period)[0], quarter_months(period)[-1]
    return f"{sm:02d}/{sy}-{em:02d}/{ey}"


def parse_money_tokens(line):
    """Return [(value, currency), ...] for every money token on a line."""
    out = []
    for sign, cur, num in MONEY_RE.findall(line):
        val = float(num.replace(",", ""))
        if sign == "-" or val < 0:
            val = -abs(val)
        out.append((val, cur))
    return out


def parse_pdf(filepath):
    """Parse one fee PDF. Returns a dict with amounts + self-check results.

    Keys: file, doc_type, number, native_ccy, net, vat_native, gross,
          gbp_vat, exchange_rate, vat_pct, supplier_vat, issues[], skip
    'skip' is True for non-UK invoices we deliberately ignore.
    'issues' holds CRITICAL/WARNING/INFO strings that feed the confidence rating.
    """
    name = os.path.basename(filepath)
    try:
        with pdfplumber.open(filepath) as pdf:
            text = "\n".join(p.extract_text() or "" for p in pdf.pages)
    except Exception as e:
        # the file name carries the invoice number, so a good second copy can stand in
        m = re.search(r"GB-(?:CN-)?AEU-\d{4}-\d+", name)
        return {"file": name, "skip": False, "doc_type": None, "number": m.group(0) if m else None,
                "net": None, "vat_native": None, "gross": None, "gbp_vat": None,
                "native_ccy": None, "exchange_rate": None, "vat_pct": None,
                "supplier_vat": None, "period_end": None,
                "business_vat": None, "business_name": None,
                "issues": [f"CRITICAL: {name}: could not open PDF ({e})"]}
    return parse_invoice_text(text, name)


def parse_invoice_text(text, name):
    """Parse the extracted text of one fee document. Split out from parse_pdf
    so the money/currency logic can be unit-tested without real PDF files."""
    r = {"file": name, "doc_type": None, "number": None, "native_ccy": None,
         "net": None, "vat_native": None, "gross": None, "gbp_vat": None,
         "exchange_rate": None, "vat_pct": None, "supplier_vat": None,
         "issues": [], "skip": False, "period_end": None,
         "business_vat": None, "business_name": None}

    # --- Doc type + invoice number: the FILENAME is Amazon's own invoice
    #     number and is authoritative. Reading it from the text is unreliable —
    #     a credit note also prints its *original* invoice's number, and
    #     foreign-language invoices don't contain the English "CREDIT NOTE". ---
    fn = os.path.splitext(name)[0]
    cn_fn, inv_fn = CREDIT_NUM_RE.search(fn), INVOICE_NUM_RE.search(fn)
    if cn_fn:
        r["doc_type"], r["number"] = "credit_note", cn_fn.group(0)
    elif inv_fn:
        r["doc_type"], r["number"] = "invoice", inv_fn.group(0)
    else:
        # Filename isn't a GB-AEU number — fall back to text, else skip as non-UK.
        cn_tx, inv_tx = CREDIT_NUM_RE.search(text), INVOICE_NUM_RE.search(text)
        if cn_tx:
            r["doc_type"], r["number"] = "credit_note", cn_tx.group(0)
        elif inv_tx:
            r["doc_type"], r["number"] = "invoice", inv_tx.group(0)
        else:
            r["skip"] = True
            r["issues"].append(f"INFO: {name}: not a GB-AEU UK fee invoice — skipped")
            return r

    # --- Which month this document belongs to: the period end (invoices and
    #     credit notes both print "dd/mm/yyyy to dd/mm/yyyy"), else the first
    #     printed date. Language-independent: the numbers, not the label. ---
    pm = PERIOD_RE.search(text)
    if pm:
        r["period_end"] = _to_date(pm.group(2))
    if r["period_end"] is None:
        for dm in DATE_RE.finditer(text):
            r["period_end"] = _to_date(dm.group(0))
            if r["period_end"]:
                break
    if r["period_end"] is None:
        r["issues"].append(f"WARNING: {name}: no invoice date found; can't tell which quarter it belongs to")

    # --- Who it is billed to (a Mac can hold invoices for more than one seller
    #     account): the customer's GB VAT number is the only GB number on the
    #     page that isn't Amazon's; the name sits beside or above Amazon's. ---
    gb = [x for x in re.findall(r"\bGB\d{9}\b", text) if x != AMAZON_UK_VAT]
    if gb:
        r["business_vat"] = gb[0]
    # Two layouts. English/French/Polish: the name has its own line just above
    # "Amazon EU S...". German/Spanish/Italian/Dutch/Swedish: the name shares a
    # line with the supplier label ("CSY LTD Leistungserbringer:") above it.
    # Anything with a colon left in it is a label, never a name.
    tl = [ln.strip() for ln in text.split("\n")]
    for i, ln in enumerate(tl):
        if "Amazon EU S" in ln:
            cand = ln.split("Amazon EU S")[0].strip()
            if not cand or cand.endswith(":"):
                cand = tl[i - 1] if i else ""
                cand = SUPPLIER_LABEL_RE.sub("", cand).strip()
            if cand and ":" not in cand and not re.search(r"GB-(?:CN-)?AEU-", cand):
                r["business_name"] = cand
            break

    # --- Is this UK VAT? Works in any language: Amazon's UK VAT number must be
    #     on the page, and every VAT rate printed must be the UK's 20% (or 0%).
    #     A 19/21/22/23/25% line would be another country's VAT, which a UK
    #     return cannot reclaim. ---
    squashed = text.replace(" ", "")
    if AMAZON_UK_VAT in squashed:
        r["supplier_vat"] = AMAZON_UK_VAT
    else:
        m = re.search(r"Supplier VAT Number:\s*([A-Z]{2}\d+)", text)
        r["supplier_vat"] = m.group(1) if m else None
        r["issues"].append(
            f"WARNING: {name}: Amazon's UK VAT number ({AMAZON_UK_VAT}) is not on this document"
            + (f" (supplier shown: {m.group(1)})" if m else "")
            + "; confirm it is UK VAT before reclaiming")
    rates = {float(x.replace(",", ".")) for x in re.findall(r"(\d{1,2}(?:[.,]\d+)?)\s*%", text)}
    foreign = sorted(x for x in rates if x not in (0.0, 20.0))
    if foreign:
        r["issues"].append(
            f"WARNING: {name}: VAT rate {', '.join(f'{x:g}%' for x in foreign)} is not the UK's 20%; "
            f"this may be another country's VAT and not reclaimable on a UK return")

    # --- Exchange rate (present on non-GBP invoices) ---
    em = EXRATE_RE.search(text)
    if em:
        r["exchange_rate"] = float(em.group(1))

    # --- Totals (language-independent): the summary total line is the LAST
    #     line carrying 3 money tokens of the same currency and NO percent.
    #     Fee rows always show a VAT% (e.g. "20.00%") so they're excluded;
    #     the total row never does. This handles "Total"/"Gesamtsumme"/etc. ---
    lines = text.split("\n")
    total_idx = None
    for i, line in enumerate(lines):
        if "%" in line:
            continue
        toks = parse_money_tokens(line)
        if len(toks) >= 3 and toks[0][1] == toks[1][1] == toks[2][1]:
            r["net"], r["native_ccy"] = toks[0][0], toks[0][1]
            r["vat_native"] = toks[1][0]
            r["gross"] = toks[2][0]
            total_idx = i   # keep the last match — the total follows the fees

    if total_idx is None:
        r["issues"].append(f"CRITICAL: {name}: could not find the totals line")
        return r

    # --- GBP VAT: GBP invoices already are; zero-rated fees have no VAT (and no
    #     conversion line); otherwise it's the standalone "GBP x.xx" line. ---
    if r["native_ccy"] == "GBP":
        r["gbp_vat"] = r["vat_native"]
    elif r["vat_native"] == 0:
        r["gbp_vat"] = 0.0   # zero-rated / outside UK VAT scope — nothing to reclaim
    else:
        for line in lines[total_idx + 1: total_idx + 6]:
            gbp = [v for v, c in parse_money_tokens(line) if c == "GBP"]
            if gbp:
                r["gbp_vat"] = gbp[0]
                break
        if r["gbp_vat"] is None:
            r["issues"].append(
                f"CRITICAL: {name}: {r['native_ccy']} invoice VAT "
                f"{r['vat_native']} has no GBP conversion line")

    _self_check(r)
    return r


def _self_check(r):
    """Three arithmetic checks per invoice. Failures append WARNING/CRITICAL."""
    name = r["file"]
    net, vat, gross = r["net"], r["vat_native"], r["gross"]

    # 1. net + VAT = gross
    if None not in (net, vat, gross) and abs((net + vat) - gross) > TOLERANCE:
        r["issues"].append(
            f"WARNING: {name}: net {net:.2f} + VAT {vat:.2f} != gross {gross:.2f}")

    # 2. Implied VAT rate is plausible (0% for zero-rated, or a real VAT band).
    #    Catches gross mis-parses (e.g. reading gross into the VAT slot) without
    #    false-flagging zero-rated fees or mixed-rate invoices.
    if net not in (None, 0) and vat is not None:
        implied = vat / net
        # tiny documents round to odd-looking rates (0.01 on 0.03 is "33%"): fine
        # whenever the VAT is within a penny of 20% or of nothing
        rounding = min(abs(abs(vat) - abs(net) * 0.20), abs(vat)) <= 0.011
        if (implied < -0.01 or implied > 0.30) and not rounding:
            r["issues"].append(
                f"WARNING: {name}: implied VAT rate {implied*100:.0f}% not plausible "
                f"(net {net:.2f}, VAT {vat:.2f})")

    # 3. GBP VAT ≈ native VAT × exchange rate (non-GBP only)
    if r["native_ccy"] != "GBP" and r["exchange_rate"] and None not in (vat, r["gbp_vat"]):
        expected = vat * r["exchange_rate"]
        if abs(expected - r["gbp_vat"]) > max(0.02, abs(expected) * 0.02):
            r["issues"].append(
                f"WARNING: {name}: GBP VAT {r['gbp_vat']:.2f} != {vat:.2f}×"
                f"{r['exchange_rate']} ({expected:.2f})")


def summarise(folder, zip_path=None, quarter=None, zip_dir=None, since=None):
    """Parse every PDF, then report once per business the invoices are billed
    to, so two seller accounts' downloads in one folder never mix."""
    if not os.path.isdir(folder):
        print(f"ERROR: {folder} is not a directory")
        sys.exit(1)

    pdfs = sorted(f for f in os.listdir(folder) if f.lower().endswith(".pdf"))
    if not pdfs:
        print(f"ERROR: No PDF invoices found in {folder}")
        sys.exit(1)

    parsed = [parse_pdf(os.path.join(folder, f)) for f in pdfs]
    skipped = [p for p in parsed if p["skip"]]
    live = [p for p in parsed if not p["skip"]]

    # one quarter for everyone: given, or the one most documents fall in
    if quarter is None:
        counts = defaultdict(int)
        for p in live:
            if p["period_end"]:
                counts[quarter_of(p["period_end"])] += 1
        if counts:
            quarter = max(counts, key=counts.get)

    groups = defaultdict(list)
    for p in live:
        groups[p["business_vat"] or p["business_name"] or "unknown business"].append(p)
    # documents with no readable business join the only business there is
    if len(groups) == 2 and "unknown business" in groups:
        other = next(k for k in groups if k != "unknown business")
        groups[other] += groups.pop("unknown business")

    # One account at a time: when the launcher says when this run started, report
    # only the businesses that had a PDF downloaded since then. Old downloads for
    # another account left in Downloads are set aside whole (never part-counted);
    # if nothing is newer than the start, everything is reported as before.
    if since and len(groups) > 1:
        def fresh(p):
            try:
                return os.path.getmtime(os.path.join(folder, p["file"])) >= since
            except OSError:
                return False
        current = {k for k, docs in groups.items() if any(fresh(p) for p in docs)}
        if current and current != set(groups):
            for k in sorted(set(groups) - current):
                docs = groups.pop(k)
                names = [d["business_name"] for d in docs if d["business_name"]]
                who = max(set(names), key=names.count) if names else k
                print(f"Set aside: {len(docs)} older PDF(s) in Downloads billed to {who}, "
                      f"not downloaded in this run. Run the tool on that account to report it.")
            print("")

    # The browser script leaves a checklist of the invoice numbers it set out to
    # download (vat_manifest_*.txt). Newest one for this period wins; any number
    # on it with no PDF here is reported, so a dropped download is never silent.
    missing_by_group = {}
    if quarter is not None:
        want = "VAT-MANIFEST %04d-%02d %04d-%02d" % (quarter[0] // 12, quarter[0] % 12 + 1,
                                                       quarter[1] // 12, quarter[1] % 12 + 1)
        best = None
        for f in os.listdir(folder):
            if f.startswith("vat_manifest_") and f.endswith(".txt"):
                fp = os.path.join(folder, f)
                try:
                    lines = open(fp, encoding="utf-8").read().split()
                except OSError:
                    continue
                if " ".join(lines[:3]) == want and (since is None or os.path.getmtime(fp) >= since):
                    if best is None or os.path.getmtime(fp) > best[0]:
                        best = (os.path.getmtime(fp), [n for n in lines[3:] if re.fullmatch(r"GB-(?:CN-)?AEU-\d{4}-\d+", n)])
        if best:
            have = {p["number"]: k for k, docs in groups.items() for p in docs if p["number"]}
            have.update({p["number"]: None for p in skipped if p.get("number")})
            missing = [n for n in best[1] if n not in have]
            if missing and groups:
                owners = [have[n] for n in best[1] if have.get(n)]
                owner = max(set(owners), key=owners.count) if owners else sorted(groups)[0]
                missing_by_group[owner] = missing

    results = []
    for key in sorted(groups):
        docs = groups[key]
        names = [d["business_name"] for d in docs if d["business_name"]]
        name = max(set(names), key=names.count) if names else None
        vat = next((d["business_vat"] for d in docs if d["business_vat"]), None)
        label = " ".join(x for x in (name, f"({vat})" if vat else None) if x) or key
        if len(groups) > 1:
            print("\n" + "#" * 70 + f"\n# {label}\n" + "#" * 70 + "\n")
        results.append(_report(folder, docs, skipped, zip_path if len(groups) == 1 else None,
                               quarter, zip_dir or (os.path.dirname(zip_path) if zip_path else None),
                               label, name or vat or key, missing_by_group.get(key)))
    return results


def _report(folder, parsed, skipped, zip_path, quarter, zip_dir, business, business_slug, not_downloaded=None):

    # Deduplicate by invoice number. The same invoice appearing twice — e.g. a
    # "GB-AEU-...(1).pdf" re-download, or Amazon reissuing under the same number —
    # would double-count its VAT. Keep the first, and warn loudly on the rest so
    # a duplicate can never silently inflate the reclaim.
    extra_issues = []
    if not_downloaded:
        shown = ", ".join(not_downloaded[:8]) + (f" and {len(not_downloaded) - 8} more" if len(not_downloaded) > 8 else "")
        extra_issues.append(
            f"CRITICAL: {len(not_downloaded)} invoice(s) on Amazon's list did not reach Downloads: {shown}. "
            f"Run the tool again for the same period; it will fetch them and this clears.")
    used, seen_numbers = [], {}
    for p in parsed:
        if p["skip"]:
            continue
        first = seen_numbers.get(p["number"]) if p["number"] else None
        if first is None:
            # a PDF whose number couldn't be read is never "the same as" another
            if p["number"]:
                seen_numbers[p["number"]] = p
            used.append(p)
        elif (first["net"], first["vat_native"], first["gbp_vat"]) == (p["net"], p["vat_native"], p["gbp_vat"]):
            # the same PDF downloaded twice (a second run the same day): harmless
            extra_issues.append(
                f"NOTE: {p['number']} was downloaded twice ({p['file']}); counted once")
        elif first["gbp_vat"] is None and p["gbp_vat"] is not None:
            # the first copy was unreadable (a broken download), this one is fine: use this one
            used[used.index(first)] = p
            seen_numbers[p["number"]] = p
            first["issues"] = []
            extra_issues.append(
                f"NOTE: {first['file']} could not be read but another copy of {p['number']} could ({p['file']}); used that one")
        elif p["gbp_vat"] is None:
            p["issues"] = []
            extra_issues.append(
                f"NOTE: {p['file']} could not be read but another copy of {p['number']} could; used that one")
        else:
            extra_issues.append(
                f"WARNING: duplicate invoice {p['number']} ({p['file']}) with different figures: "
                f"ignored to avoid double-counting, check which one is right")

    # --- Quarter: given, or the one most documents fall in. Keep only that
    #     quarter's documents; list the rest so nothing vanishes silently. ---
    dated = [p for p in used if p["period_end"]]
    undated = [p for p in used if not p["period_end"]]
    if quarter is None:
        counts = defaultdict(int)
        for p in dated:
            counts[quarter_of(p["period_end"])] += 1
        if counts:
            quarter = max(counts, key=counts.get)
            if len(counts) > 1:
                others = ", ".join(f"{quarter_label(q)} ({c})" for q, c in sorted(counts.items()) if q != quarter)
                extra_issues.append(
                    f"WARNING: invoices from more than one quarter were found; using {quarter_label(quarter)} "
                    f"and leaving out {others}. Pass --quarter to choose.")
    outside = [p for p in dated if quarter and not in_period(p["period_end"], quarter)]
    used = [p for p in used if p in undated or (quarter and in_period(p["period_end"], quarter))]
    if quarter:
        by_month = defaultdict(int)
        for p in used:
            if p["period_end"]:
                by_month[(p["period_end"].year, p["period_end"].month)] += 1
        for yr, mth in quarter_months(quarter):
            if by_month.get((yr, mth), 0) == 0:
                extra_issues.append(
                    f"WARNING: no invoice dated {MONTHS[mth - 1]} {yr}; the period is not fully covered "
                    f"(Amazon issues that month's invoice early the following month)")

    invoices = [p for p in used if p["doc_type"] == "invoice"]
    credits = [p for p in used if p["doc_type"] == "credit_note"]

    # Reclaim total = sum of GBP VAT (credit notes already carry negative signs)
    def gbp_sum(items):
        return sum(p["gbp_vat"] for p in items if p["gbp_vat"] is not None)

    inv_vat = gbp_sum(invoices)
    cred_vat = gbp_sum(credits)
    net_reclaim = inv_vat + cred_vat

    # Any invoice missing its GBP VAT is a hole in the total
    missing = [p for p in used if p["gbp_vat"] is None]

    all_issues = [i for p in parsed for i in p["issues"]] + extra_issues
    critical = [i for i in all_issues if i.startswith("CRITICAL")]
    warns = [i for i in all_issues if i.startswith("WARNING")]

    if critical or missing:
        confidence, msg = "RED", "Some invoices couldn't be parsed — total is incomplete. See diagnostics."
    elif warns:
        confidence, msg = "AMBER", "Parsed, but something needs a look. Read the diagnostics below."
    else:
        confidence, msg = "GREEN", "All invoices parsed and self-checked."

    # Native-currency breakdown (informational — shows the currency mix)
    by_ccy = defaultdict(lambda: {"inv": 0.0, "cred": 0.0, "n": 0})
    for p in used:
        if p["vat_native"] is None:
            continue
        b = by_ccy[p["native_ccy"]]
        b["n"] += 1
        if p["doc_type"] == "credit_note":
            b["cred"] += p["gbp_vat"] or 0.0
        else:
            b["inv"] += p["gbp_vat"] or 0.0

    # --- Report ---
    L = []
    L.append("=" * 70)
    L.append("AMAZON FEE VAT SUMMARY  (input VAT to reclaim, GBP)")
    L.append("=" * 70)
    L.append("")
    L.append(f"Generated:   {datetime.now().strftime('%d %b %Y %H:%M')}")
    L.append(f"Business:    {business}")
    if quarter:
        L.append(f"Period:      {quarter_label(quarter)}")
    L.append(f"Confidence:  {confidence} — {msg}")
    L.append("")
    if quarter:
        cov = " · ".join(f"{MONTHS[mth - 1]} {by_month.get((yr, mth), 0)}" for yr, mth in quarter_months(quarter))
        L.append(f"Coverage:    {cov} documents" + (f"   ({len(undated)} undated included)" if undated else ""))
        if outside:
            shown = ", ".join(f"{p['number']} ({p['period_end'].strftime('%b %Y')})" for p in outside[:6])
            more = f" and {len(outside) - 6} more" if len(outside) > 6 else ""
            L.append(f"Left out:    {len(outside)} document(s) dated outside {quarter_label(quarter)}: {shown}{more}")
        L.append("")
    L.append(f"Invoices:     {len(invoices):>3}   GBP VAT  {inv_vat:>10.2f}")
    L.append(f"Credit notes: {len(credits):>3}   GBP VAT  {cred_vat:>10.2f}")
    L.append("-" * 40)
    L.append(f">>> NET INPUT VAT TO RECLAIM:  GBP {net_reclaim:>10.2f} <<<")
    L.append("")
    L.append("By invoice currency (VAT already converted to GBP):")
    for ccy in sorted(by_ccy):
        b = by_ccy[ccy]
        L.append(f"  {ccy}: {b['n']:>3} docs   invoices {b['inv']:>9.2f}   "
                 f"credits {b['cred']:>9.2f}   net {b['inv']+b['cred']:>9.2f}")
    L.append("")

    if skipped:
        L.append(f"Skipped {len(skipped)} non-UK / non-fee PDF(s) (foreign VAT, not reclaimable here).")
        L.append("")

    # Per-invoice detail
    L.append("-" * 70)
    L.append(f"{'Invoice':<26}{'Ccy':<5}{'Net':>10}{'VAT':>9}{'GBP VAT':>10}")
    L.append("-" * 70)
    for p in sorted(used, key=lambda x: (x["doc_type"] or "", x["number"] or "")):
        if p["net"] is None:
            L.append(f"{(p['number'] or p['file']):<26}{'?':<5}{'PARSE FAIL':>29}")
            continue
        L.append(f"{p['number']:<26}{p['native_ccy']:<5}{p['net']:>10.2f}"
                 f"{p['vat_native']:>9.2f}{(p['gbp_vat'] if p['gbp_vat'] is not None else 0):>10.2f}")
    L.append("")

    # Diagnostics
    diag = [i for i in all_issues if not i.startswith("INFO")] or []
    if diag:
        L.append("-" * 70)
        L.append("DIAGNOSTICS")
        L.append("-" * 70)
        for i in diag:
            L.append(f"  {i}")
    else:
        L.append("No issues detected.")
    L.append("")
    L.append("=" * 70)

    output = "\n".join(L)
    print(output)

    if zip_dir and not zip_path:
        tag = re.sub(r"[^A-Za-z0-9\-]+", "_", quarter_label(quarter)) if quarter else datetime.now().strftime("%Y-%m-%d")
        slug = re.sub(r"[^A-Za-z0-9]+", "_", business_slug).strip("_")[:40]
        zip_path = os.path.join(zip_dir, f"fee_invoices_{tag}_{slug}.zip")
    if zip_path:
        keep = [p["file"] for p in used]           # only this quarter's documents
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for f in keep:
                zf.write(os.path.join(folder, f), f"invoices/{f}")
            zf.writestr("vat_summary.txt", output)
        with open(os.path.splitext(zip_path)[0] + "_summary.txt", "w") as fh:
            fh.write(output)
        print(f"\nZip saved to: {zip_path}")
        print(f"  Contains: {len(keep)} invoice PDF(s) + vat_summary.txt")
        print(f"ZIP_PATH={zip_path}")
    print(f"RESULT|{business}|{confidence}|{net_reclaim:.2f}|{zip_path or ''}")

    return confidence


def main():
    # --parse-period "<text>": print "label|MM/YYYY-MM/YYYY" (exit 1 if unreadable); used by the launchers
    if "--parse-period" in sys.argv:
        i = sys.argv.index("--parse-period")
        per = parse_quarter(sys.argv[i + 1] if i + 1 < len(sys.argv) else "")
        if not per:
            sys.exit(1)
        print(f"{quarter_label(per)}|{period_range(per)}")
        return
    args = [a for i, a in enumerate(sys.argv[1:], 1) if not a.startswith("--") and not sys.argv[i - 1].startswith("--")]
    if not args:
        print(__doc__)
        sys.exit(1)
    folder = args[0]

    def opt(flag):
        if flag in sys.argv:
            idx = sys.argv.index(flag)
            if idx + 1 < len(sys.argv):
                return sys.argv[idx + 1]
        return None
    quarter = None
    given = opt("--period") or opt("--quarter")
    if given:
        quarter = parse_quarter(given)
        if not quarter:
            print(f"ERROR: couldn't read the period {given!r}. Examples: Q3 2026 · Jun-Aug 2026 · Nov 2025-Jan 2026")
            sys.exit(1)
    since = None
    try:
        since = float(opt("--since")) if opt("--since") else None
    except ValueError:
        pass
    summarise(folder, opt("--zip"), quarter, opt("--zip-dir"), since)


if __name__ == "__main__":
    main()
