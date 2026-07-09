#!/bin/bash
# =============================================================================
# build_macos_app.command — builds InvestYo.app, a real macOS app bundle
# =============================================================================
#
# Double-click this file ONCE from Finder to build "InvestYo.app" — a real
# macOS application bundle (custom icon, no Terminal flash, draggable into
# /Applications or the Dock) that launches this exact copy of the repo's
# launch_app.command when opened.
#
# This does NOT repackage Python/the dependency stack (that would need
# something like py2app/pyinstaller, a much heavier and more fragile approach
# for an app this size). It is a thin AppleScript wrapper, in the same spirit
# as an Automator "Application" — it just runs `launch_app.command` from
# wherever this repo lives, with its own icon and no visible Terminal window.
#
# Re-run this script any time you move the repo to a new folder (it bakes in
# the absolute path at build time) or want to refresh the icon.
#
# =============================================================================

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_DIR"

echo ""
echo "══════════════════════════════════════════════════════════════"
echo "  InvestYo — building InvestYo.app"
echo "  Repo: $REPO_DIR"
echo "══════════════════════════════════════════════════════════════"
echo ""

# ── Guard: macOS only (osacompile/sips/iconutil are macOS-only tools) ───────
if [ "$(uname)" != "Darwin" ]; then
    echo "  ERROR: This script only works on macOS (needs osacompile/sips/iconutil)."
    exit 1
fi

# ── Guard: launch_app.command must exist here ────────────────────────────────
if [ ! -f "$REPO_DIR/launch_app.command" ]; then
    echo "  ERROR: launch_app.command not found in $REPO_DIR"
    exit 1
fi
chmod +x "$REPO_DIR/launch_app.command"

APP_NAME="InvestYo.app"
APP_PATH="$REPO_DIR/$APP_NAME"
ICON_PNG="$REPO_DIR/desktop/assets/app_icon.png"
BUILD_DIR="$(mktemp -d)"

_on_exit() {
    local _exit_code=$?
    rm -rf "$BUILD_DIR" 2>/dev/null
    echo ""
    echo "──────────────────────────────────────────────────────────────"
    if [ "$_exit_code" -eq 0 ]; then
        echo "  Done."
    else
        echo "  build_macos_app.command exited with code $_exit_code."
    fi
    read -r -s -n 1 -p "  Press any key to close this window…" _ 2>/dev/null || true
    echo ""
}
trap '_on_exit' EXIT

# ── Step 1: generate the AppleScript source with the repo path baked in ─────
SCRIPT_SRC="$BUILD_DIR/InvestYo.applescript"
cat > "$SCRIPT_SRC" <<APPLESCRIPT
on run
    set repoPath to "$REPO_DIR"
    try
        do shell script "cd " & quoted form of repoPath & " && ./launch_app.command > /tmp/investyo_app_launch.log 2>&1"
    on error errMsg number errNum
        if errNum is not -128 then
            display dialog "InvestYo failed to start:" & return & return & errMsg & return & return & "Full log: /tmp/investyo_app_launch.log" buttons {"OK"} default button "OK" with icon stop with title "InvestYo"
        end if
    end try
end run
APPLESCRIPT

echo "  ▶  Compiling $APP_NAME …"
rm -rf "$APP_PATH"
if ! osacompile -o "$APP_PATH" "$SCRIPT_SRC"; then
    echo "  ERROR: osacompile failed."
    exit 1
fi
echo "  ✓  $APP_NAME compiled."

# ── Step 2: build a .icns from desktop/assets/app_icon.png and install it ───
if [ -f "$ICON_PNG" ]; then
    echo "  ▶  Building custom icon …"
    ICONSET="$BUILD_DIR/AppIcon.iconset"
    mkdir -p "$ICONSET"
    for size in 16 32 64 128 256 512; do
        sips -z "$size" "$size" "$ICON_PNG" --out "$ICONSET/icon_${size}x${size}.png" >/dev/null
        double=$((size * 2))
        sips -z "$double" "$double" "$ICON_PNG" --out "$ICONSET/icon_${size}x${size}@2x.png" >/dev/null
    done
    if iconutil -c icns "$ICONSET" -o "$BUILD_DIR/AppIcon.icns"; then
        cp "$BUILD_DIR/AppIcon.icns" "$APP_PATH/Contents/Resources/applet.icns"
        touch "$APP_PATH"
        echo "  ✓  Custom icon installed."
    else
        echo "  ⚠  iconutil failed — app will use the default AppleScript icon."
    fi
else
    echo "  ⚠  $ICON_PNG not found — app will use the default AppleScript icon."
fi

echo ""
echo "══════════════════════════════════════════════════════════════"
echo "  ✓  Built: $APP_PATH"
echo ""
echo "  Next steps:"
echo "    • Double-click InvestYo.app to launch the full desktop app"
echo "      (no Terminal window — errors show in a dialog instead)."
echo "    • Drag InvestYo.app to /Applications and/or the Dock to keep it handy."
echo "    • If you move this repo folder, re-run this script to rebuild"
echo "      the app with the new path baked in."
echo "══════════════════════════════════════════════════════════════"
