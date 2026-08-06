#!/bin/bash
# =============================================================================
# build_pilots_launcher.command — builds "Stockpy Pilots.app" / "Stockpy
# Pilots (Stop).app", two real macOS app bundles for the Pilots PWA (webapp/)
# =============================================================================
#
# Double-click this file ONCE from Finder to build two tiny app bundles at
# the repo root:
#   • Stockpy Pilots.app         — double-click to start: brings up the live
#                                  backends + the PWA in the background (no
#                                  Terminal window, no prompts) and opens your
#                                  browser to it. Double-clicking again while
#                                  it's already running just reopens the tab.
#   • Stockpy Pilots (Stop).app  — double-click to stop everything the above
#                                  started.
#
# Same spirit as build_macos_app.command's InvestYo.app (an AppleScript
# wrapper around `do shell script`, no Terminal flash, custom icon) but for
# the webapp instead of the legacy desktop GUI, and driving
# launch_webapp.command's --background/--stop modes instead of a foreground
# interactive session — see that script's own header comment for what those
# modes do.
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

_build_app "Stockpy Pilots.app"        "--background" "start"
_build_app "Stockpy Pilots (Stop).app" "--stop"        "stop"

echo ""
echo "══════════════════════════════════════════════════════════════"
echo "  ✓  Built:"
echo "       $REPO_DIR/Stockpy Pilots.app"
echo "       $REPO_DIR/Stockpy Pilots (Stop).app"
echo ""
echo "  Next steps:"
echo "    • Double-click \"Stockpy Pilots.app\" to start it — no Terminal"
echo "      window, no prompts; it brings up the live backends + the PWA in"
echo "      the background and opens your browser to it. A notification"
echo "      confirms it's up (or reports an error)."
echo "    • Double-click \"Stockpy Pilots (Stop).app\" to stop everything it"
echo "      started."
echo "    • Drag either (or both) into /Applications and/or the Dock to keep"
echo "      them handy — a real double-click-to-open, double-click-to-close"
echo "      pair, like any other Mac app."
echo "    • If you move this repo folder, re-run this script to rebuild both"
echo "      apps with the new path baked in."
echo "══════════════════════════════════════════════════════════════"
