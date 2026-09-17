# VAT Fee Reclaim Tool

Works out the input VAT you can reclaim on Amazon's seller fees for a VAT period,
straight from Amazon's own invoice PDFs, and packs them up for your accountant.

## How to run it

1. Double-click **`VAT Reclaim.command`**.
2. Type the VAT period (for example `Q3 2026` or `Jun-Aug 2026`) and press Enter.
3. Seller Central opens in your browser. Log in if asked, and check the account
   name at the top right is the right one.
4. On that page press `Cmd+Option+J`. A panel opens. Click in it, press `Cmd+V`,
   then `Enter`.
5. Wait about 3 minutes. A box at the top right of the page shows progress and
   turns green with `=== DONE ===` when finished.
6. Go back to the tool's window and press any key.

It shows the **VAT to reclaim** and opens Finder on the zip file. Send that zip
to the accountant.

**When to run it:** after the period has ended, from about the 5th of the next
month (Amazon issues each month's invoice a few days late).

**Periods you can type:** `Q3 2026` · `Jun-Aug 2026` · `June to August 2026` ·
`06/2026-08/2026` · `Nov 2025-Jan 2026` · `Aug 2026`. Quarters are calendar
quarters: Q1 Jan-Mar, Q2 Apr-Jun, Q3 Jul-Sep, Q4 Oct-Dec.

**Why step 4 exists:** Amazon has no "download all" button. The tool puts a
small script on your clipboard; pasting it into that panel makes the page
download the period's invoice PDFs into Downloads for you. Use Chrome.

## From the dashboard instead

On a Mac that runs the Operations dashboard, open the **VAT reclaim** tab:

1. Type the period and press **Open Seller Central**.
2. On the Seller Central page, click the **Download VAT invoices** bookmark and
   wait for `=== DONE ===`.
3. Back on the dashboard, press **Read the invoices**, then download the zip.

The bookmark is set up once: open "Formats, Beau and CICI, one-time bookmark" on
the tab and drag the button to your bookmarks bar. No bookmark? Use step 4 above
instead. The bookmark already knows the period you typed. It only asks if you
opened the Seller Central page yourself.

## More than one seller account (Beau, CICI)

Do one account at a time. The download runs for whichever Seller Central
account the browser is logged into, so check the account name at the top right.

The result shows only the account you just downloaded. Older invoice PDFs for
the other account can stay in Downloads: the tool sets them aside (it says so
in the summary) and never mixes two businesses in one figure or zip.

## Reading the result

- **GREEN**: every invoice parsed and self-checked, every month of the period present.
  Send the zip.
- **AMBER**: something needs a glance: a month with no invoice, an invoice
  dated outside the period, two copies of an invoice with different figures. (The
  same PDF downloaded twice is fine: counted once, stays GREEN.) The diagnostics at the
  bottom say exactly what.
- **RED**: an invoice couldn't be read; the total is incomplete. Don't rely on
  it until that invoice is sorted.

Old PDFs left in Downloads don't matter: every document's own invoice period
decides which period it belongs to, and anything outside the chosen period is
listed under "Left out" and excluded.

The figure is in **GBP** and correct across currencies: it reads the real VAT
off each invoice and, for euro/krona invoices, uses the GBP conversion Amazon
prints. Non-UK marketplaces (Amazon.sa, Amazon.ae) are skipped; that VAT is
not reclaimable on a UK return.

## Putting it on another Mac (updates itself)

1. Open **Terminal** (press `Cmd+Space`, type `Terminal`, press Enter).
2. Paste this line and press Enter:

   ```bash
   git clone https://github.com/thenexttiger/vat-tool.git ~/vat-tool && open ~/vat-tool
   ```

   If a window offers to install Apple's "command line developer tools", click
   **Install**, wait for it to finish (5 to 10 minutes), then paste the line again.
3. A folder opens. Double-click **`VAT Reclaim.command`**. The first run sets
   itself up (a minute or two).

From then on, every time the tool is opened it first fetches the latest version
by itself. No internet? It just runs the version it has.

Tip: drag `VAT Reclaim.command` to the right-hand side of the Dock to keep it handy.

The browser on that Mac must be logged into the right Seller Central account.
