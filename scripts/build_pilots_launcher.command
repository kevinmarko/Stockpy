#!/bin/bash
# =============================================================================
# build_pilots_launcher.command — builds "Stockpy Pilots.app" / "Stockpy
# Pilots (Stop).app", two real macOS app bundles for the Pilots PWA (webapp/)
# =============================================================================
#
# Double-click this file ONCE from Finder to build two tiny app bundles at
# the repo root:
#   • Stockpy Pilots.app         — double-click to start: opens a real,
#                                  VISIBLE Terminal window running live mode
#                                  (no Mock/Live prompt) and brings up the
#                                  backends + the PWA in it, then opens your
#                                  browser. Closing that window (or Ctrl+C
#                                  inside it) is the whole shutdown story —
#                                  launch_webapp.command's own EXIT/TERM/HUP
#                                  traps clean up every backend it started.
#                                  Double-clicking again while it's already
#                                  running just reopens the tab, no restart.
#   • Stockpy Pilots (Stop).app  — a SAFETY NET, not the required shutdown
#                                  step: use it if Terminal ever gets
#                                  force-quit or crashes before a Start
#                                  window's own close-triggered cleanup can
#                                  run. Sweeps for and stops any of this
#                                  project's processes still holding
#                                  ports 5173/8601-8604.
#
# Same spirit as build_macos_app.command's InvestYo.app (an AppleScript
# wrapper, custom icon) but for the webapp instead of the legacy desktop
# GUI. The Stop app still uses `do shell script` (invisible, driving
# launch_webapp.command's --stop mode) — see that script's own header
# comment. The Start app uses a different template, `tell application
# "Terminal" to do script` (see _build_terminal_app below), specifically so
# launch_webapp.command's --live mode has a real, closable window to run in
# — `do shell script` never opens one at all, which is exactly why the old
# --background/no-window design couldn't tie shutdown to closing anything.
#
# Re-run this script any time you move the repo to a new folder (it bakes in
# the absolute path at build time) or want to refresh the icon.
# =============================================================================

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_DIR"

echo ""
echo "══════════════════════════════════════════════════════════════"
echo "  Stockpy Pilots — building the launcher apps"
echo "  Repo: $REPO_DIR"
echo "══════════════════════════════════════════════════════════════"
echo ""

# ── Guard: macOS only (osacompile/sips/iconutil are macOS-only tools) ───────
if [ "$(uname)" != "Darwin" ]; then
    echo "  ERROR: This script only works on macOS (needs osacompile/sips/iconutil)."
    exit 1
fi

# ── Guard: launch_webapp.command must exist here ─────────────────────────────
if [ ! -f "$REPO_DIR/launch_webapp.command" ]; then
    echo "  ERROR: launch_webapp.command not found in $REPO_DIR"
    exit 1
fi
chmod +x "$REPO_DIR/launch_webapp.command"

ICON_PNG="$REPO_DIR/webapp/public/icon-512.png"
BUILD_DIR="$(mktemp -d)"

_on_exit() {
    local _exit_code=$?
    rm -rf "$BUILD_DIR" 2>/dev/null
    echo ""
    echo "──────────────────────────────────────────────────────────────"
    if [ "$_exit_code" -eq 0 ]; then
        echo "  Done."
    else
        echo "  build_pilots_launcher.command exited with code $_exit_code."
    fi
    read -r -s -n 1 -p "  Press any key to close this window…" _ 2>/dev/null || true
    echo ""
}
trap '_on_exit' EXIT

# ── Shared: build a .icns from webapp/public/icon-512.png once, reuse for both
ICNS_PATH=""
if [ -f "$ICON_PNG" ]; then
    echo "  ▶  Building app icon from webapp/public/icon-512.png …"
    ICONSET="$BUILD_DIR/AppIcon.iconset"
    mkdir -p "$ICONSET"
    for size in 16 32 64 128 256 512; do
        sips -z "$size" "$size" "$ICON_PNG" --out "$ICONSET/icon_${size}x${size}.png" >/dev/null
        double=$((size * 2))
        sips -z "$double" "$double" "$ICON_PNG" --out "$ICONSET/icon_${size}x${size}@2x.png" >/dev/null
    done
    if iconutil -c icns "$ICONSET" -o "$BUILD_DIR/AppIcon.icns"; then
        ICNS_PATH="$BUILD_DIR/AppIcon.icns"
        echo "  ✓  Icon built."
    else
        echo "  ⚠  iconutil failed — apps will use the default AppleScript icon."
    fi
else
    echo "  ⚠  $ICON_PNG not found — apps will use the default AppleScript icon."
fi

# _build_app <AppName.app> <launch_webapp.command flag> <friendly action verb>
_build_app() {
    local app_name="$1" flag="$2" verb="$3"
    local app_path="$REPO_DIR/$app_name"
    local script_src="$BUILD_DIR/$(basename "$app_name" .app).applescript"

    cat > "$script_src" <<APPLESCRIPT
on run
    set repoPath to "$REPO_DIR"
    try
        do shell script "cd " & quoted form of repoPath & " && ./launch_webapp.command $flag"
    on error errMsg number errNum
        if errNum is not -128 then
            display dialog "Stockpy Pilots failed to $verb:" & return & return & errMsg & return & return & "Full log: /tmp/stockpy_webapp_logs/app_launcher.log" buttons {"OK"} default button "OK" with icon stop with title "Stockpy Pilots"
        end if
    end try
end run
APPLESCRIPT

    echo "  ▶  Compiling $app_name …"
    rm -rf "$app_path"
    if ! osacompile -o "$app_path" "$script_src"; then
        echo "  ERROR: osacompile failed for $app_name."
        exit 1
    fi
    if [ -n "$ICNS_PATH" ]; then
        cp "$ICNS_PATH" "$app_path/Contents/Resources/applet.icns"
        touch "$app_path"
    fi
    echo "  ✓  $app_name built."
}

# _build_terminal_app <AppName.app> <launch_webapp.command flag> <friendly action verb>
# Unlike _build_app (do shell script, invisible), this opens a REAL
# Terminal.app window so the process can be a foreground, closable session —
# required for launch_webapp.command's EXIT/TERM/HUP-trap close-to-stop
# lifecycle to have a window to be "foreground" in at all. Note: `tell
# application "Terminal" to do script` returns as soon as Terminal has been
# told to start the command — it does NOT block until the shell command
# finishes, and does NOT surface the shell command's own exit code as a
# catchable AppleScript error the way `do shell script` does. So the
# `on error` handler below will essentially never fire for a script-level
# failure (npm install failing, a backend not starting, etc.) anymore — but
# that's not a regression: those failures are now directly visible in the
# now-visible Terminal window itself, which is strictly better than a hidden
# dialog. The handler is still worth keeping: it still legitimately catches
# Terminal-automation-level failures (e.g. Automation/Apple-Events
# permission for Terminal not yet granted).
_build_terminal_app() {
    local app_name="$1" flag="$2" verb="$3"
    local app_path="$REPO_DIR/$app_name"
    local script_src="$BUILD_DIR/$(basename "$app_name" .app).applescript"

    cat > "$script_src" <<APPLESCRIPT
on run
    set repoPath to "$REPO_DIR"
    try
        tell application "Terminal"
            activate
            do script "cd " & quoted form of repoPath & " && ./launch_webapp.command $flag"
        end tell
    on error errMsg number errNum
        if errNum is not -128 then
            display dialog "Stockpy Pilots failed to $verb:" & return & return & errMsg & return & return & "Full log: /tmp/stockpy_webapp_logs/app_launcher.log" buttons {"OK"} default button "OK" with icon stop with title "Stockpy Pilots"
        end if
    end try
end run
APPLESCRIPT

    echo "  ▶  Compiling $app_name …"
    rm -rf "$app_path"
    if ! osacompile -o "$app_path" "$script_src"; then
        echo "  ERROR: osacompile failed for $app_name."
        exit 1
    fi
    if [ -n "$ICNS_PATH" ]; then
        cp "$ICNS_PATH" "$app_path/Contents/Resources/applet.icns"
        touch "$app_path"
    fi
    echo "  ✓  $app_name built."
}

_build_terminal_app "Stockpy Pilots.app"        "--live" "start"
_build_app           "Stockpy Pilots (Stop).app" "--stop" "stop"

echo ""
echo "══════════════════════════════════════════════════════════════"
echo "  ✓  Built:"
echo "       $REPO_DIR/Stockpy Pilots.app"
echo "       $REPO_DIR/Stockpy Pilots (Stop).app"
echo ""
echo "  Next steps:"
echo "    • Double-click \"Stockpy Pilots.app\" to start it — opens a real"
echo "      Terminal window running live mode (no prompts), brings up the"
echo "      backends + the PWA in it, opens your browser. Close that window"
echo "      (or Ctrl+C inside it) when you're done — that's the whole"
echo "      shutdown, everything it started stops with it."
echo "    • Double-click it again while already running and it just reopens"
echo "      the browser tab, no restart."
echo "    • Double-click \"Stockpy Pilots (Stop).app\" only if you need it as"
echo "      a fallback — e.g. Terminal got force-quit or crashed before its"
echo "      window could close normally. It sweeps for and stops anything"
echo "      of this project's still left running."
echo "    • Drag either (or both) into /Applications and/or the Dock to keep"
echo "      them handy."
echo "    • If you move this repo folder, re-run this script to rebuild both"
echo "      apps with the new path baked in."
echo "══════════════════════════════════════════════════════════════"
