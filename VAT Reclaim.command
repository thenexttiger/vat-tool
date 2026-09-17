#!/bin/bash
# Amazon fee VAT reclaim, start to finish, in one double-click:
#   asks which quarter -> puts the download script on your clipboard and opens
#   Seller Central -> waits while the invoice PDFs download -> reads them ->
#   prints the VAT to reclaim and shows the accountant zip in Finder.
# The first run also sets up its own Python environment (nothing to install by
# hand on a Mac that has Xcode's command line tools or Homebrew Python).

cd "$(dirname "$0")" || exit 1
DL="${VAT_DOWNLOADS:-$HOME/Downloads}"      # override only for testing

pause() { echo ""; read -n1 -r -p "Press any key to close..."; echo ""; }

echo "======================================================"
echo "  Amazon fee VAT reclaim"
echo "======================================================"
echo ""

# --- 0a. Self-update: when this folder was installed with git (a copy of the
#      public vat-tool repo), fetch the latest version before doing anything.
#      Never blocks: offline, no git, or any error just carries on as-is.
if [ -d .git ] && [ -z "$VAT_UPDATED" ] && xcode-select -p >/dev/null 2>&1; then
    BEFORE=$(git rev-parse HEAD 2>/dev/null)
    export GIT_TERMINAL_PROMPT=0
    GITQ="git -c http.lowSpeedLimit=1000 -c http.lowSpeedTime=8"
    if $GITQ fetch --quiet origin 2>/dev/null; then
        # the tool's own files always match the published version; downloads,
        # results (vat_inputs) and the Python environment are not touched
        git reset --quiet --hard origin/main 2>/dev/null
    fi
    AFTER=$(git rev-parse HEAD 2>/dev/null)
    if [ -n "$AFTER" ] && [ "$BEFORE" != "$AFTER" ]; then
        echo "Updated to the latest version."
        echo ""
        VAT_UPDATED=1 exec "$0"
    fi
fi

# --- 0b. Icon: macOS keeps a file's custom icon outside its contents, so git
#      can't carry it. Put it on (again) whenever it is missing.
if [ -f icon.png ] && ! xattr -p com.apple.FinderInfo "$0" >/dev/null 2>&1; then
    osascript -l JavaScript - "$PWD/icon.png" "$PWD/$(basename "$0")" >/dev/null 2>&1 <<'JXA'
function run(argv) {
    ObjC.import('AppKit');
    var img = $.NSImage.alloc.initWithContentsOfFile(argv[0]);
    $.NSWorkspace.sharedWorkspace.setIconForFileOptions(img, argv[1], 0);
}
JXA
fi

# --- 0c. A real app for the Dock: macOS won't always take a script file in the
#      Dock, so build a tiny "VAT Reclaim" app that just opens this file. Goes in
#      Applications (or the user's own Applications folder), so it is also in
#      Launchpad and Spotlight. Rebuilt if missing, moved, or the icon changed.
make_app() {
    local here="$PWD/$(basename "$0")" dir app
    for dir in ${VAT_APP_DIR:+"$VAT_APP_DIR"} "/Applications" "$HOME/Applications"; do   # VAT_APP_DIR: testing only
        mkdir -p "$dir" 2>/dev/null
        [ -w "$dir" ] && break
    done
    [ -w "$dir" ] || return 0
    app="$dir/VAT Reclaim.app"
    if [ -x "$app/Contents/MacOS/VATReclaim" ] && grep -qF "\"$here\"" "$app/Contents/MacOS/VATReclaim" \
       && [ ! icon.png -nt "$app/Contents/Resources/icon.icns" ]; then
        return 0
    fi
    local fresh=1; [ -d "$app" ] && fresh=0
    rm -rf "$app"
    mkdir -p "$app/Contents/MacOS" "$app/Contents/Resources" || return 0
    printf '#!/bin/bash\nopen -a Terminal "%s"\n' "$here" > "$app/Contents/MacOS/VATReclaim"
    chmod +x "$app/Contents/MacOS/VATReclaim"
    cat > "$app/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleName</key><string>VAT Reclaim</string>
  <key>CFBundleDisplayName</key><string>VAT Reclaim</string>
  <key>CFBundleIdentifier</key><string>com.thenexttiger.vatreclaim</string>
  <key>CFBundleExecutable</key><string>VATReclaim</string>
  <key>CFBundleIconFile</key><string>icon</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>1.0</string>
  <key>LSUIElement</key><true/>
</dict></plist>
PLIST
    if [ -f icon.png ]; then
        local set; set="$(mktemp -d)/icon.iconset"; mkdir -p "$set"
        for sz in 16 32 128 256 512; do
            sips -z $sz $sz icon.png --out "$set/icon_${sz}x${sz}.png" >/dev/null 2>&1
            sips -z $((sz*2)) $((sz*2)) icon.png --out "$set/icon_${sz}x${sz}@2x.png" >/dev/null 2>&1
        done
        iconutil -c icns "$set" -o "$app/Contents/Resources/icon.icns" 2>/dev/null
    fi
    touch "$app"
    if [ "$fresh" = 1 ]; then
        NEW_APP="$app"
    fi
}
NEW_APP=""
[ -z "$VAT_NO_APP" ] && make_app

# --- 0. Python: use whatever the Mac has; offer Apple's installer if none ---
PY=""
for c in python3.13 python3.12 python3.11 python3; do
    if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' 2>/dev/null; then
        PY="$c"; break
    fi
done
if [ -z "$PY" ]; then
    echo "This Mac has no Python yet. Apple will offer to install it now:"
    echo "click Install in the window that appears, wait for it to finish,"
    echo "then double-click this file again."
    xcode-select --install 2>/dev/null
    pause; exit 1
fi
# (re)build when missing, or when an update changed the package list
if [ ! -x "venv/bin/python" ] || [ requirements.txt -nt venv/.installed ]; then
    echo "First run: setting things up (one time, about a minute)..."
    [ -x venv/bin/python ] || "$PY" -m venv venv || { echo "Setup failed (venv)."; pause; exit 1; }
    ./venv/bin/pip install -q --disable-pip-version-check -r requirements.txt || { echo "Setup failed (packages)."; pause; exit 1; }
    touch venv/.installed
    echo "Setup complete."
    echo ""
fi

if [ -n "$NEW_APP" ]; then
    echo "New: a \"VAT Reclaim\" app is now in your Applications (it is showing in Finder)."
    echo "Drag it into your Dock. From now on, open the tool from there."
    echo ""
    open -R "$NEW_APP" 2>/dev/null
fi

# --- 1. Which VAT period? Default = the calendar quarter that ended most recently ---
m=$(date +%m); y=$(date +%Y)
case $m in 01|02|03) dq=4; dy=$((y-1));; 04|05|06) dq=1; dy=$y;; 07|08|09) dq=2; dy=$y;; *) dq=3; dy=$y;; esac
DEFQ="Q$dq $dy"
echo "Type the VAT period. Any of these work:"
echo "    Q3 2026            a calendar quarter (Q1 Jan-Mar, Q2 Apr-Jun, Q3 Jul-Sep, Q4 Oct-Dec)"
echo "    Jun-Aug 2026       any run of months   (also: June to August 2026, 06/2026-08/2026)"
echo "    Nov 2025-Jan 2026  across a year end   (Nov-Jan 2026 means the same)"
while :; do
    read -r -p "Which VAT period? [$DEFQ] " Q
    Q=${Q:-$DEFQ}
    PARSED=$(./venv/bin/python vat_fee_summariser.py --parse-period "$Q" 2>/dev/null)
    if [ -n "$PARSED" ]; then Q="${PARSED%%|*}"; RANGE="${PARSED##*|}"; break; fi
    echo "  Couldn't read that. Examples:  Q3 2026   or   Jun-Aug 2026"
done
echo "Using $Q."
echo ""

# --- 2. Download: script to clipboard (with the quarter filled in), open the page ---
STARTED=$(date +%s)
sed "s|__QUARTER__|$RANGE|" download_fee_invoices.js | pbcopy
[ -z "$VAT_NO_OPEN" ] && open "https://sellercentral.amazon.co.uk/tax/seller-fee-invoices#vat=$RANGE"
cat <<EOM
Step 1 of 2: download the $Q invoices
  Seller Central's Seller Fee Tax Invoices page is opening in your browser.
  It downloads for whichever seller account that browser is logged into,
  so check the account name at the top right first. Then, on that page:
    1. press  Cmd + Option + J   (a panel opens at the side or bottom)
    2. click in the panel's bottom line, press  Cmd + V , then  Enter
    3. wait about 3 minutes until a green box on the page says  === DONE ===
  The invoice PDFs save into your Downloads folder.

EOM
read -n1 -r -p "When it says DONE, press any key here to continue..."
echo ""; echo ""

# --- 3. Summarise: every fee PDF in Downloads; the summariser keeps only $Q ---
shopt -s nullglob
ALL_FEE=("$DL"/GB-AEU-*.pdf "$DL"/GB-CN-AEU-*.pdf)
if [ ${#ALL_FEE[@]} -eq 0 ]; then
    echo "No Amazon fee invoice PDFs found in Downloads. Did the download finish?"
    pause; exit 0
fi
mkdir -p vat_inputs
rm -f vat_inputs/*.pdf
cp -p "${ALL_FEE[@]}" vat_inputs/
echo "Step 2 of 2: reading ${#ALL_FEE[@]} invoice PDF(s)..."
echo ""
OUT=$(./venv/bin/python vat_fee_summariser.py vat_inputs/ --period "$Q" --zip-dir vat_inputs --since "$STARTED" 2>&1)
echo "$OUT" | grep -v -e '^ZIP_PATH=' -e '^RESULT|'
ZIP=$(echo "$OUT" | sed -n 's/^ZIP_PATH=//p' | head -1)

# --- 4. Reveal the zip ---
echo ""
if [ -n "$ZIP" ] && [ -f "$ZIP" ]; then
    open -R "$ZIP" 2>/dev/null
    echo "The accountant zip ($(basename "$ZIP")) is highlighted in Finder."
    echo "If the Confidence line above says GREEN, send it on."
    echo "AMBER or RED: read the diagnostics at the bottom first."
fi
pause
