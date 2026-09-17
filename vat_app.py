#!/usr/bin/env python3
"""VAT Reclaim as a page in the browser instead of a Terminal window.

Started by "VAT Reclaim.command" (and the VAT Reclaim app it builds). Serves one
page on this Mac only (127.0.0.1): type the period, open Seller Central, read the
invoices, get the zip. All the work is done by the same files the Terminal flow
uses: download_fee_invoices.js and vat_fee_summariser.py.
"""
import glob
import html
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
PORT = int(os.environ.get("VAT_PORT", "8791"))
INPUTS = os.path.join(HERE, "vat_inputs")
STATE = os.path.join(INPUTS, "app_state.json")
DOWNLOADS = os.environ.get("VAT_DOWNLOADS") or os.path.expanduser("~/Downloads")
SELLER_URL = "https://sellercentral.amazon.co.uk/tax/seller-fee-invoices"
IDLE_QUIT_SECONDS = 3 * 3600
last_seen = time.time()


def esc(x):
    return html.escape(str(x if x is not None else ""))


def load_state():
    try:
        return json.load(open(STATE, encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_state(**upd):
    st = load_state()
    st.update(upd)
    os.makedirs(INPUTS, exist_ok=True)
    json.dump(st, open(STATE, "w", encoding="utf-8"), indent=1)
    return st


def parse_period(text):
    """(label, MM/YYYY-MM/YYYY) via the summariser's own parser, or ("", "")."""
    try:
        out = subprocess.run([sys.executable, os.path.join(HERE, "vat_fee_summariser.py"),
                              "--parse-period", text or ""],
                             capture_output=True, text=True, timeout=60).stdout.strip()
        label, rng = out.split("|", 1)
        return label, rng
    except Exception:
        return "", ""


def default_period():
    t = date.today()
    q = (t.month - 1) // 3          # quarters completed so far this year
    return f"Q{q} {t.year}" if q else f"Q4 {t.year - 1}"


def do_start(text):
    label, rng = parse_period(text)
    if not label:
        save_state(error=f"Couldn't read the period “{text}”. Try  Q3 2026  or  Jun-Aug 2026.")
        return
    js = open(os.path.join(HERE, "download_fee_invoices.js"), encoding="utf-8").read().replace("__QUARTER__", rng)
    subprocess.run(["pbcopy"], input=js.encode("utf-8"))
    if not os.environ.get("VAT_NO_OPEN"):
        subprocess.Popen(["open", f"{SELLER_URL}#vat={rng}"])
    save_state(period=label, started=label, started_at=time.time(), result=None, error=None)


def do_read():
    st = load_state()
    label = st.get("started") or st.get("period") or ""
    pdfs = sorted(glob.glob(os.path.join(DOWNLOADS, "GB-AEU-*.pdf")) + glob.glob(os.path.join(DOWNLOADS, "GB-CN-AEU-*.pdf")))
    result = {"period": label}
    if not pdfs:
        result.update(output="", reports=[], problem=(
            "No Amazon fee invoice PDFs were found in your Downloads folder. Did the download reach "
            "=== DONE ===? If macOS asked whether VAT Reclaim may access Downloads, choose Allow and press the button again."))
    else:
        os.makedirs(INPUTS, exist_ok=True)
        for old in glob.glob(os.path.join(INPUTS, "*.pdf")) + glob.glob(os.path.join(INPUTS, "vat_manifest_*.txt")):
            os.remove(old)
        for f in pdfs + glob.glob(os.path.join(DOWNLOADS, "vat_manifest_*.txt")):
            shutil.copy2(f, INPUTS)
        cmd = [sys.executable, os.path.join(HERE, "vat_fee_summariser.py"), INPUTS, "--zip-dir", INPUTS]
        if label:
            cmd += ["--period", label]
        if st.get("started_at"):
            cmd += ["--since", str(st["started_at"])]
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=1800).stdout
        except Exception as e:
            out = f"ERROR: {e}"
        reports = []
        for ln in out.splitlines():
            if ln.startswith("RESULT|"):
                _t, biz, conf, net, zp = (ln.split("|") + ["", "", "", ""])[:5]
                try:
                    net = float(net)
                except ValueError:
                    net = None
                reports.append({"business": biz, "confidence": conf, "net": net, "zip": zp or None})
        clean = "\n".join(l for l in out.splitlines() if not l.startswith(("ZIP_PATH=", "RESULT|")))
        result.update(output=clean, reports=reports, count=len(pdfs),
                      problem=None if reports else "The invoices could not be read. Open “Full details” below.")
        first = next((r["zip"] for r in reports if r["zip"]), None)
        if first and not os.environ.get("VAT_NO_OPEN"):
            subprocess.Popen(["open", "-R", first])
    save_state(result=result, started=None)


CSS = """
:root{--bg:#f4f6f8;--card:#fff;--ink:#16202a;--ink2:#5b6875;--line:#e3e8ee;--green:#0a7d4f;--greenbg:#e3f5ec;
--amber:#9a5b00;--amberbg:#fdf0d8;--red:#b3261e;--redbg:#fde7e5;--blue:#1f4fd8}
@media (prefers-color-scheme:dark){:root{--bg:#10151b;--card:#19212b;--ink:#eef2f6;--ink2:#9aa7b4;--line:#2a3542;
--greenbg:#12372a;--amberbg:#3d2f12;--redbg:#431d1a;--green:#4cc792;--amber:#f0b557;--red:#ff8a80;--blue:#7ea2ff}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.55 -apple-system,BlinkMacSystemFont,"Helvetica Neue",Arial,sans-serif}
.wrap{max-width:720px;margin:0 auto;padding:28px 16px 60px}
header{display:flex;align-items:center;gap:14px;margin-bottom:22px}header img{width:56px;height:56px}
h1{font-size:24px;margin:0}header p{margin:2px 0 0;color:var(--ink2);font-size:14.5px}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:20px 22px;margin-bottom:16px}
.card.dim{opacity:.55}h2{font-size:17px;margin:0 0 12px}.n{display:inline-block;width:26px;height:26px;line-height:26px;text-align:center;
border-radius:50%;background:var(--green);color:#fff;font-size:14px;font-weight:700;margin-right:8px}
.row{display:flex;gap:10px;flex-wrap:wrap;align-items:center}
input[type=text]{font:inherit;padding:10px 12px;border:1px solid var(--line);border-radius:9px;background:var(--bg);color:var(--ink);width:210px}
button,.btn{font:600 15.5px/1 -apple-system,BlinkMacSystemFont,"Helvetica Neue",Arial,sans-serif;padding:12px 18px;border-radius:10px;border:0;background:var(--green);color:#fff;cursor:pointer;text-decoration:none;display:inline-block}
button.alt,.btn.alt{background:transparent;color:var(--ink);border:1px solid var(--line)}button:disabled{opacity:.6;cursor:wait}
.hint{color:var(--ink2);font-size:14px}ol{margin:4px 0 14px 20px;padding:0}li{margin:6px 0}kbd{font:600 13px ui-monospace,Menlo,monospace;
background:var(--bg);border:1px solid var(--line);border-bottom-width:2px;border-radius:6px;padding:2px 6px}
.err{background:var(--redbg);color:var(--red);padding:10px 12px;border-radius:9px;margin-top:12px;font-size:14.5px}
.biz{padding:14px 0;border-top:1px solid var(--line)}.biz:first-of-type{border-top:0;padding-top:0}
.pill{display:inline-block;font:700 12.5px/1 -apple-system,BlinkMacSystemFont,"Helvetica Neue",Arial,sans-serif;letter-spacing:.04em;padding:6px 10px;border-radius:99px;vertical-align:middle}
.GREEN{background:var(--greenbg);color:var(--green)}.AMBER{background:var(--amberbg);color:var(--amber)}.RED{background:var(--redbg);color:var(--red)}
.amt{font:700 26px/1.2 ui-monospace,Menlo,monospace;margin:8px 0 2px}
details{margin-top:12px}summary{cursor:pointer;color:var(--ink2);font-size:14.5px}
pre{white-space:pre-wrap;font:12px/1.5 ui-monospace,Menlo,monospace;background:var(--bg);padding:12px;border-radius:9px;overflow:auto;max-height:420px}
footer{color:var(--ink2);font-size:13.5px;margin-top:22px}
"""

MEANING = {
    "GREEN": "Everything was read and checked. Send the zip to the accountant.",
    "AMBER": "Read, but something needs a look first. Open “Full details” and read the DIAGNOSTICS at the bottom.",
    "RED": "Not complete. Open “Full details” and read the DIAGNOSTICS: it says what is missing and what to do.",
}


def page():
    st = load_state()
    started, result, err = st.get("started"), st.get("result"), st.get("error")
    period = st.get("period") or default_period()
    h = [f"<!doctype html><html lang=en><head><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
         f"<title>VAT Reclaim</title><link rel=icon href=/icon.png><style>{CSS}</style></head><body><div class=wrap>"
         "<header><img src=/icon.png alt=''><div><h1>VAT Reclaim</h1>"
         "<p>Works out the VAT to reclaim on Amazon's fees and packs the invoices for the accountant.</p></div></header>"]

    h.append(f"""<div class=card><h2><span class=n>1</span>Which VAT period?</h2>
<form method=post action=/start class=row>
<input type=text name=period value="{esc(period)}" required autofocus>
<button type=submit>Open Seller Central</button></form>
<p class=hint style="margin:10px 0 0">Examples: <b>Q3 2026</b> · <b>Jun-Aug 2026</b> · <b>Dec-Feb 2026</b> · <b>Aug 2026</b></p>
{f'<div class=err>{esc(err)}</div>' if err else ''}
<details><summary>More about periods and accounts</summary>
<p class=hint>Quarters are calendar quarters: Q1 Jan-Mar, Q2 Apr-Jun, Q3 Jul-Sep, Q4 Oct-Dec. Any run of months works
(<b>June to August 2026</b>, <b>06/2026-08/2026</b>), including across a year end (<b>Nov 2025-Jan 2026</b>).
Run it after the period has ended, from about the 5th of the next month.</p>
<p class=hint>It downloads for whichever Seller Central account your browser is logged into. Do one account at a time;
older invoices for another account left in Downloads are set aside, never mixed in.</p></details></div>""")

    if started:
        h.append(f"""<div class=card><h2><span class=n>2</span>Download the {esc(started)} invoices</h2>
<ol><li>Seller Central has opened in another tab. Log in if asked, and check the account name at the top right.</li>
<li>On that page press <kbd>⌘ Cmd</kbd> + <kbd>⌥ Option</kbd> + <kbd>J</kbd>. A panel opens.</li>
<li>Click in the panel, press <kbd>⌘ Cmd</kbd> + <kbd>V</kbd>, then <kbd>Enter</kbd>.
<span class=hint>(If Chrome asks you to type “allow pasting” first, type that, press Enter, then paste again.)</span></li>
<li>A box at the top right of that page counts the downloads. Wait until it turns green and says <b>=== DONE ===</b>.
<span class=hint>If Chrome asks whether to allow multiple downloads, choose Allow.</span></li>
<li>Come back to this tab and press the button.</li></ol>
<form method=post action=/read onsubmit="var b=this.querySelector('button');b.disabled=true;b.textContent='Reading the invoices… up to a minute or two'">
<button type=submit>Read the invoices</button>
<span class=hint>&nbsp; Nothing happened in Seller Central? <a href="#" onclick="document.getElementById('again').submit();return false">Open it again</a></span></form>
<form id=again method=post action=/start style="display:none"><input name=period value="{esc(started)}"></form></div>""")
    elif not result:
        h.append("<div class='card dim'><h2><span class=n>2</span>Download the invoices</h2><p class=hint style='margin:0'>The steps appear here after step 1.</p></div>")

    if result:
        blocks = ""
        for rp in result.get("reports") or []:
            conf = rp.get("confidence") or "RED"
            zipname = os.path.basename(rp.get("zip") or "")
            amt = f"£{rp['net']:,.2f}" if rp.get("net") is not None else "no figure"
            blocks += (f"<div class=biz><div><b>{esc(rp.get('business'))}</b></div>"
                       f"<div class=amt>{amt} <span class='pill {esc(conf)}'>{esc(conf)}</span></div>"
                       f"<p class=hint style='margin:2px 0 12px'>{esc(MEANING.get(conf, ''))}</p>"
                       + (f"<div class=row><form method=post action=/reveal><input type=hidden name=zip value=\"{esc(zipname)}\">"
                          f"<button type=submit>Show the zip in Finder</button></form>"
                          f"<a class='btn alt' href=\"/zip/{urllib.parse.quote(zipname)}\" download>Save a copy to Downloads</a></div>" if zipname else "")
                       + "</div>")
        prob = f"<div class=err>{esc(result['problem'])}</div>" if result.get("problem") else ""
        det = (f"<details><summary>Full details</summary><pre>{esc(result.get('output'))}</pre></details>" if result.get("output") else "")
        h.append(f"<div class=card><h2><span class=n>3</span>VAT to reclaim · {esc(result.get('period'))}</h2>{blocks}{prob}{det}</div>")

    h.append("<footer>This page runs only on this Mac; nothing is sent anywhere. You can close the tab when you're done. "
             "<form method=post action=/quit style='display:inline'><button class=alt style='padding:6px 10px;font-size:13px' type=submit>Quit VAT Reclaim</button></form></footer>"
             "</div></body></html>")
    return "".join(h)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body=b"", ctype="text/html; charset=utf-8", extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _home(self):
        self.send_response(303)
        self.send_header("Location", "/")
        self.end_headers()

    def _form(self):
        n = int(self.headers.get("Content-Length", 0))
        return urllib.parse.parse_qs(self.rfile.read(n).decode("utf-8"))

    def _local_only(self):
        # this page drives the clipboard and the browser: refuse anything that
        # did not come from a page on this same local address
        host = (self.headers.get("Host") or "").split(":")[0]
        origin = self.headers.get("Origin") or ""
        ok_origin = not origin or origin in (f"http://127.0.0.1:{PORT}", f"http://localhost:{PORT}")
        return host in ("127.0.0.1", "localhost") and ok_origin

    def do_GET(self):
        global last_seen
        last_seen = time.time()
        path = self.path.split("?")[0]
        if path == "/":
            self._send(200, page().encode("utf-8"))
        elif path == "/ping":
            self._send(200, b"vat-reclaim", "text/plain")
        elif path == "/icon.png":
            try:
                self._send(200, open(os.path.join(HERE, "icon.png"), "rb").read(), "image/png")
            except OSError:
                self._send(404)
        elif path.startswith("/zip/"):
            name = os.path.basename(urllib.parse.unquote(path[5:]))
            full = os.path.join(INPUTS, name)
            if name.endswith(".zip") and os.path.isfile(full):
                self._send(200, open(full, "rb").read(), "application/zip",
                           {"Content-Disposition": f'attachment; filename="{name}"'})
            else:
                self._send(404, b"not found", "text/plain")
        else:
            self._send(404, b"not found", "text/plain")

    def do_POST(self):
        global last_seen
        last_seen = time.time()
        if not self._local_only():
            self._send(403, b"forbidden", "text/plain")
            return
        form = self._form()
        if self.path == "/start":
            do_start((form.get("period") or [""])[0].strip())
        elif self.path == "/read":
            do_read()
        elif self.path == "/reveal":
            name = os.path.basename((form.get("zip") or [""])[0])
            full = os.path.join(INPUTS, name)
            if name.endswith(".zip") and os.path.isfile(full):
                subprocess.Popen(["open", "-R", full])
        elif self.path == "/quit":
            self._send(200, b"<body style='font:16px -apple-system;padding:40px'>VAT Reclaim has quit. You can close this tab.</body>")
            threading.Thread(target=lambda: (time.sleep(0.3), os._exit(0)), daemon=True).start()
            return
        self._home()


def idle_watch():
    while True:
        time.sleep(60)
        if time.time() - last_seen > IDLE_QUIT_SECONDS:
            os._exit(0)


def main():
    url = f"http://127.0.0.1:{PORT}/"
    ThreadingHTTPServer.allow_reuse_address = True
    server = None
    for attempt in range(6):
        try:
            server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
            break
        except OSError:
            # an earlier copy is still running (maybe an older version): ask it to stop
            try:
                if urllib.request.urlopen(url + "ping", timeout=2).read() == b"vat-reclaim":
                    urllib.request.urlopen(urllib.request.Request(url + "quit", data=b"", method="POST"), timeout=2)
            except Exception:
                pass
            time.sleep(1)
    if server is None:
        print(f"Could not start on port {PORT}; something else is using it.")
        sys.exit(1)
    save_state(error=None)
    if not os.environ.get("VAT_NO_OPEN"):
        subprocess.Popen(["open", url])
    print(f"VAT Reclaim is open in your browser: {url}")
    threading.Thread(target=idle_watch, daemon=True).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
