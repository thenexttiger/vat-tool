// =============================================================
// Amazon Seller Central — Fee Invoice PDF Downloader
// =============================================================
//
// Downloads one PDF per UK fee invoice / credit note (GB-AEU-* and
// GB-CN-AEU-*) for a VAT period (a quarter, or any run of months). PDFs are the legal VAT invoices and carry
// the correct GBP VAT (directly for .co.uk, via a conversion line for EUR/SEK),
// so vat_fee_summariser.py reads them straight. Non-UK marketplaces
// (SA-*, AE-*) are skipped — that's foreign VAT, not UK-reclaimable.
//
// HOW TO USE:
// 1. Go to Seller Central > Tax Document Library > Seller Fee Tax Invoices
//    (https://sellercentral.amazon.co.uk/tax/seller-fee-invoices)
// 2. Open browser console (Cmd+Option+J on Mac, Ctrl+Shift+J on Windows)
// 3. Paste this entire script and press Enter
// 4. It will prompt you for the period (unless the launcher filled it in), set the date range,
//    generate documents, and download all UK invoice PDFs
// 5. At the end it prints a coverage report (PDFs by month + what it skipped)
//
// CONFIGURATION — edit these if your VAT quarters are non-standard:
// =============================================================

const VAT_QUARTERS = {
    'Q1': { startMonth: 1, startDay: 1, endMonth: 3, endDay: 31 },
    'Q2': { startMonth: 4, startDay: 1, endMonth: 6, endDay: 30 },
    'Q3': { startMonth: 7, startDay: 1, endMonth: 9, endDay: 30 },
    'Q4': { startMonth: 10, startDay: 1, endMonth: 12, endDay: 31 },
};

// Delay between downloads (ms) — increase if Amazon throttles you
const DOWNLOAD_DELAY = 4000;

// =============================================================
// SCRIPT — don't edit below unless debugging
// =============================================================

(async function downloadFeeInvoices() {
    'use strict';

    const sleep = ms => new Promise(r => setTimeout(r, ms));

    // A status box on the page itself, so someone using the bookmark (no console
    // open) can see progress and the DONE message. Text only, styled from script.
    const status = (msg, kind) => {
        try {
            let box = document.getElementById('vat-tool-status');
            if (!box) {
                box = document.createElement('div');
                box.id = 'vat-tool-status';
                Object.assign(box.style, {
                    position: 'fixed', top: '16px', right: '16px', zIndex: '2147483647',
                    maxWidth: '380px', padding: '14px 18px', borderRadius: '10px',
                    font: '600 15px/1.45 -apple-system, Helvetica, Arial, sans-serif',
                    color: '#fff', boxShadow: '0 6px 24px rgba(0,0,0,.35)', whiteSpace: 'pre-line',
                });
                document.body.appendChild(box);
            }
            box.style.background = kind === 'done' ? '#0a7d4f' : kind === 'error' ? '#b3261e' : '#1f4fd8';
            box.textContent = msg;
        } catch (e) { /* the console still has everything */ }
    };

    // --- Step 1: Ask for quarter and year ---
    const PRESET = '__QUARTER__';   // filled in by the .command; leave as-is to be asked
    // The dashboard and the launcher open this page with the period on the end of the
    // address (#vat=06/2026-08/2026), so the bookmark version knows it without asking.
    const fromAddress = (() => {
        const m = /[#&]vat=([^&]+)/.exec(location.hash || '');
        try { return m ? decodeURIComponent(m[1]) : ''; } catch (e) { return ''; }
    })();
    const input = !PRESET.startsWith('__') ? PRESET : fromAddress ? fromAddress :
        prompt(
        'Enter VAT quarter and year, e.g.:\n' +
        '  Q2 2026        (Apr-Jun 2026)\n' +
        '  Q4 2025        (Oct-Dec 2025)\n' +
        '  Jun-Aug 2026   (any run of months; Nov 2025-Jan 2026 across a year end)\n' +
        '  01/2026-03/2026  (same thing in numbers)\n\n' +
        'Quarter:'
        );

    if (!input) {
        console.log('Cancelled.');
        return;
    }

    let startDate, endDate;

    // Parse "Q2 2026" format
    const quarterMatch = input.trim().match(/^Q([1-4])\s+(\d{4})$/i);
    // Parse "MM/YYYY-MM/YYYY" custom range format
    const customMatch = input.trim().match(/^(\d{1,2})\/(\d{4})\s*-\s*(\d{1,2})\/(\d{4})$/);

    if (quarterMatch) {
        const q = VAT_QUARTERS['Q' + quarterMatch[1]];
        const year = parseInt(quarterMatch[2]);
        startDate = new Date(year, q.startMonth - 1, q.startDay);
        // For cross-year quarters (if customised), endMonth could be in next year
        const endYear = q.endMonth < q.startMonth ? year + 1 : year;
        endDate = new Date(endYear, q.endMonth - 1, q.endDay);
    } else if (customMatch) {
        const sm = parseInt(customMatch[1]), sy = parseInt(customMatch[2]);
        const em = parseInt(customMatch[3]), ey = parseInt(customMatch[4]);
        startDate = new Date(sy, sm - 1, 1);
        // Last day of end month
        endDate = new Date(ey, em, 0);
    } else {
        // "Jun-Aug 2026", "June to August 2026", "Nov 2025 - Jan 2026", "Nov-Jan 2026", "Aug 2026"
        const MON = ['jan','feb','mar','apr','may','jun','jul','aug','sep','oct','nov','dec'];
        const years = (input.match(/20\d{2}/g) || []).map(Number);
        const mons = (input.toLowerCase().match(/[a-z]{3,}/g) || [])
            .filter(w => !['to', 'till', 'until', 'through'].includes(w))
            .map(w => MON.indexOf(w.slice(0, 3)));
        if (!years.length || !mons.length || mons.length > 2 || mons.includes(-1)) {
            console.error('Could not read the period. Examples: Q2 2026 · Jun-Aug 2026 · Nov 2025-Jan 2026 · 01/2026-03/2026');
            status('Could not read that period.\nTry again with e.g. Q2 2026 or Jun-Aug 2026', 'error');
            return;
        }
        const sm = mons[0], em = mons[mons.length - 1];
        const ey = years[years.length - 1];
        const sy = years.length > 1 ? years[0] : (sm > em ? ey - 1 : ey);
        startDate = new Date(sy, sm, 1);
        endDate = new Date(ey, em + 1, 0);
    }

    const fmt = d => d.toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric' });
    console.log(`Date range: ${fmt(startDate)} - ${fmt(endDate)}`);
    status(`VAT invoices ${fmt(startDate)} - ${fmt(endDate)}\nFinding them. Leave this tab open...`);

    // --- Step 2: Try to set date range on the page ---
    let dateSet = false;

    const pad = n => String(n).padStart(2, '0');
    const mmddyyyy = d => `${pad(d.getMonth()+1)}/${pad(d.getDate())}/${d.getFullYear()}`;
    const isoDate = d => `${d.getFullYear()}-${pad(d.getMonth()+1)}-${pad(d.getDate())}`;

    // Try to find date inputs
    const allInputs = document.querySelectorAll('input');
    const dateInputs = [...allInputs].filter(i =>
        i.type === 'date' || (i.type === 'text' &&
        (i.name?.toLowerCase().includes('date') ||
         i.id?.toLowerCase().includes('date') ||
         i.placeholder?.toLowerCase().includes('date') ||
         i.getAttribute('aria-label')?.toLowerCase().includes('date')))
    );

    if (dateInputs.length >= 2) {
        const setInputValue = (input, value) => {
            const nativeInputValueSetter = Object.getOwnPropertyDescriptor(
                window.HTMLInputElement.prototype, 'value'
            ).set;
            nativeInputValueSetter.call(input, value);
            input.dispatchEvent(new Event('input', { bubbles: true }));
            input.dispatchEvent(new Event('change', { bubbles: true }));
        };

        const startInput = dateInputs[0];
        const endInput = dateInputs[1];
        const startVal = startInput.type === 'date' ? isoDate(startDate) : mmddyyyy(startDate);
        const endVal = endInput.type === 'date' ? isoDate(endDate) : mmddyyyy(endDate);

        setInputValue(startInput, startVal);
        setInputValue(endInput, endVal);
        dateSet = true;
        console.log(`Set date inputs: ${startVal} - ${endVal}`);
    }

    // Try to find and click a "Generate" / "Search" / "Apply" button
    if (dateSet) {
        await sleep(500);
        let generateBtn = null;
        document.querySelectorAll('button, input[type="submit"], a').forEach(el => {
            const text = el.textContent?.trim().toLowerCase() || el.value?.toLowerCase() || '';
            if (text.includes('generate') || text.includes('search') ||
                text.includes('apply') || text.includes('get documents')) {
                generateBtn = el;
            }
        });

        if (generateBtn) {
            console.log(`Clicking "${generateBtn.textContent?.trim() || generateBtn.value}"...`);
            generateBtn.click();
            console.log('Waiting for documents to load...');
            await sleep(5000);
        } else {
            console.log('Could not find a Generate/Search button. You may need to click it manually.');
            console.log('After documents load, re-run this script (it will skip date-setting).');
        }
    } else {
        console.log('Could not find date input fields automatically.');
        console.log('Please set the date range manually, generate documents, then re-run this script.');
        console.log(`Target range: ${fmt(startDate)} - ${fmt(endDate)}`);
        console.log('');
        console.log('Proceeding to download whatever is currently showing...');
    }

    // --- Step 2b: the page shows the 1,000 most recent documents; "Load More"
    //     brings older ones. Click it until the table reaches back past the
    //     start of the period (or there is nothing more to load). ---
    const ROW_DATE_RE = /(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{1,2}\s+\d{2}:\d{2}:\d{2}\s+UTC\s+(\d{4})/;
    const MON3 = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
    // every place a control can carry its name; checked one by one, because a
    // View button also has a value attribute (an id) that must not blur the match
    const labels = el => [el.textContent, el.getAttribute('label'), el.getAttribute('aria-label'), el.value]
        .map(t => (t || '').trim().toLowerCase()).filter(Boolean);
    const isNamed = (el, names) => labels(el).some(t => names.includes(t));
    const pressable = 'a, button, kat-button, kat-link, [role="button"], input[type="button"], input[type="submit"]';
    const press = el => { const inner = el.shadowRoot && el.shadowRoot.querySelector('button, a'); (inner || el).click(); };
    const oldestYM = () => {
        let min = Infinity;
        document.querySelectorAll('table tbody tr').forEach(r => {
            const m = r.textContent.match(ROW_DATE_RE);
            if (m) min = Math.min(min, parseInt(m[2]) * 12 + MON3.indexOf(m[1]));
        });
        return min;
    };
    const wantYM = startDate.getFullYear() * 12 + startDate.getMonth();
    for (let i = 0; i < 25 && oldestYM() >= wantYM; i++) {
        const more = [...document.querySelectorAll(pressable)].find(el => labels(el).some(t => /^load more/.test(t)));
        if (!more) break;
        const before = document.querySelectorAll('table tbody tr').length;
        console.log(`Loading older documents (${before} rows so far)...`);
        press(more);
        await sleep(3500);
        if (document.querySelectorAll('table tbody tr').length === before) break;
    }

    // --- Step 3: Find and download one PDF per UK fee invoice ---
    // The table lists every invoice twice — a CSV row and a PDF row. We take
    // the PDF of each UK invoice (GB-AEU-* / GB-CN-AEU-*) and ignore CSV rows
    // and non-UK invoices. This gives exactly one legal VAT document per UK
    // invoice across every month, including months that are PDF-only.

    const UK_INVOICE_RE = /\bGB-(?:CN-)?AEU-\d{4}-\d+\b/;
    const MONTHS = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];

    // Quarter bounds as year*12+monthIndex, so we filter by each row's OWN date.
    // This is the safety net: even if Amazon's date picker didn't apply and the
    // table is showing your whole history, only this quarter's rows download.
    const startYM = startDate.getFullYear() * 12 + startDate.getMonth();
    const endYM = endDate.getFullYear() * 12 + endDate.getMonth();

    const targets = [];               // { link, number, month }
    const seenNumbers = new Set();    // guard against duplicate PDF rows
    let skipCSV = 0, skipForeign = 0, skipOutside = 0, skipNoDate = 0, skipNoButton = 0;

    document.querySelectorAll('table tbody tr').forEach(row => {
        const rowText = row.textContent;
        const cells = [...row.querySelectorAll('td')].map(c => c.textContent.trim());

        // Only PDF rows (each invoice is listed as both a CSV row and a PDF row)
        const isPDF = cells.some(t => t.toUpperCase() === 'PDF');
        const isCSV = cells.some(t => t.toUpperCase() === 'CSV');
        if (!isPDF) { if (isCSV) skipCSV++; return; }

        // UK fee invoice number (GB-AEU / GB-CN-AEU). Skips SA-*/AE-* + AGL etc.
        const numMatch = rowText.match(/\b[A-Z]{2,3}-(?:CN-)?[A-Z]{2,3}-\d{4}-\d+\b/);
        const number = numMatch ? numMatch[0] : null;
        if (!number || !UK_INVOICE_RE.test(number)) { skipForeign++; return; }

        // Row's Start Date, format "Mon Jun 01 08:47:57 UTC 2026" -> filter to quarter
        const dm = rowText.match(/(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{1,2}\s+\d{2}:\d{2}:\d{2}\s+UTC\s+(\d{4})/);
        if (!dm) { skipNoDate++; return; }
        const rowMonth = dm[1];
        const rowYM = parseInt(dm[2]) * 12 + MONTHS.indexOf(rowMonth);
        if (rowYM < startYM || rowYM > endYM) { skipOutside++; return; }

        if (seenNumbers.has(number)) return;
        seenNumbers.add(number);

        // The View link/button in this row (plain elements today; Amazon's
        // own button components and labelled inputs are accepted too)
        let link = null;
        row.querySelectorAll(pressable).forEach(el => {
            if (isNamed(el, ['view', 'download', 'download pdf'])) link = el;
        });
        if (!link) {
            const all = row.querySelectorAll('a, button, kat-button, kat-link, [role="button"]');
            if (all.length) link = all[all.length - 1];
        }
        if (!link) { skipNoButton++; return; }

        targets.push({ link, number, month: rowMonth });
    });

    if (targets.length === 0) {
        const allRows = document.querySelectorAll('table tbody tr').length;
        console.error('No UK invoice PDF rows found for this period.');
        status(!allRows ? 'No invoices found on this page.\nAre you logged in and on Seller Fee Tax Invoices?'
             : (skipOutside && !skipNoDate) ? 'No invoices found for that period.\nCheck the period you typed.'
             : 'Could not read the invoice table.\nAmazon may have changed the page; the tool needs updating.', 'error');
        console.log(`What the page gave me: ${allRows} table rows, ${skipCSV} CSV rows, ${skipOutside} PDF rows outside the period, ` +
                    `${skipForeign} non-UK rows, ${skipNoDate} without a readable date, ${skipNoButton} without a View button.`);
        if (!allRows) console.log('No table rows at all: are you on Seller Fee Invoices (tax/seller-fee-invoices) and logged in? If the page looks different from before, Amazon has changed its layout and the script needs updating.');
        else if (skipOutside && !skipNoDate) console.log('Rows exist but none fall in the period: check the period you typed, or scroll to the bottom and click "Load More".');
        else if (skipNoDate || skipNoButton) console.log('Rows exist but their date or View button was not recognised: Amazon has changed the table and the script needs updating.');
        console.log('DEBUG — tables:', document.querySelectorAll('table').length,
                    'rows:', document.querySelectorAll('table tbody tr').length);
        document.querySelectorAll('table tbody tr').forEach((row, i) => {
            if (i < 5) console.log(`  Row ${i}: ${row.textContent.trim().substring(0, 140)}`);
        });
        return;
    }

    console.log(`\nFound ${targets.length} UK invoice PDF(s). Downloading...`);
    const byMonth = {};
    let downloaded = 0;
    for (const t of targets) {
        downloaded++;
        byMonth[t.month] = (byMonth[t.month] || 0) + 1;
        console.log(`  [${downloaded}/${targets.length}] ${t.number} (${t.month})`);
        status(`Downloading invoice ${downloaded} of ${targets.length}...\nLeave this tab open.`);
        if (window.__VAT_DRY_RUN) continue;       // test mode: list what would download, click nothing
        press(t.link);
        await sleep(DOWNLOAD_DELAY);
    }

    // --- Coverage report ---
    console.log(`\n=== DONE === ${downloaded} UK invoice PDF(s) downloaded for the quarter.`);
    status(window.__VAT_DRY_RUN ? `Test only: ${targets.length} invoice(s) would download.`
         : `=== DONE ===\n${downloaded} invoice(s) saved to Downloads.\nNow go back to the VAT tool and continue.`, 'done');
    console.log('Coverage by month:');
    MONTHS.forEach(m => { if (byMonth[m]) console.log(`   ${m}: ${byMonth[m]}`); });
    console.log(`Skipped: ${skipCSV} CSV rows (took the PDF instead), ` +
                `${skipOutside} rows outside this quarter, ` +
                `${skipForeign} non-UK / non-fee rows (SA-*/AE-*/AGL — not GB-AEU input VAT).`);
    if (skipNoDate) console.log(`         ${skipNoDate} PDF row(s) had no readable date and were skipped.`);
    if (skipNoButton) console.log(`         ${skipNoButton} PDF row(s) had no View button and were skipped: check those by hand.`);
    if (oldestYM() > wantYM) console.log('NOTE: the table does not reach back to the start of the period; scroll down, click "Load More", then run this again.');
    console.log('');
    console.log('The quarter is filtered per-row by date, so a full-history table is fine');
    console.log('— only this quarter downloads. If a month looks short, scroll the table');
    console.log('fully to the bottom so every row loads, then re-run.');
    console.log('');
    console.log('Next: go back to the VAT tool (its window, or the dashboard tab) and continue.');
})();
