#!/usr/bin/env bash
# Build Crit Launcher as a Linux AppImage.
#
#   ./packaging/linux/build-appimage.sh            # builds ./dist-linux/Crit_Launcher-<version>-x86_64.AppImage
#
# Needs: python3 (3.11+), python3-venv, curl. appimagetool is downloaded if missing.
# For the widest compatibility, build on an older distro (CI uses Ubuntu 22.04);
# an AppImage built on a new distro will not run on older ones.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
OUT="${OUT_DIR:-$ROOT/dist-linux}"
WORK="${WORK_DIR:-$ROOT/build-linux}"
ARCH="${ARCH:-x86_64}"
# Strip any carriage return from the version (harmless on LF checkouts, needed on CRLF ones).
VERSION="$(sed -n 's/^APP_VERSION = "\(.*\)"/\1/p' "$ROOT/crit_launcher/version.py" | head -1 | tr -d '\r')"
VERSION="${VERSION:-dev}"

mkdir -p "$OUT" "$WORK"

echo "==> Python environment"
if [ ! -x "$WORK/venv/bin/python" ]; then
    python3 -m venv "$WORK/venv"
fi
PY="$WORK/venv/bin/python"
"$PY" -m pip install --quiet --upgrade pip
# Keep every Qt piece on the same 6.11 release; mismatched versions break QtWebEngine.
"$PY" -m pip install --quiet pyinstaller pywebview qtpy psutil pillow \
    "PyQt6==6.11.*" "PyQt6-Qt6==6.11.*" "PyQt6-WebEngine==6.11.*" "PyQt6-WebEngine-Qt6==6.11.*"

echo "==> Icon"
"$PY" - "$ROOT/icon.ico" "$WORK/crit-launcher.png" <<'EOF'
import sys
from PIL import Image
src, dest = sys.argv[1:3]
with Image.open(src) as im:
    sizes = im.info.get("sizes") or {im.size}
    best = max(sizes, key=lambda s: s[0])
    im.size = best
    im.load()
    im = im.convert("RGBA")
    if im.width < 256:
        im = im.resize((256, 256), Image.LANCZOS)
    im.save(dest, format="PNG")
EOF

echo "==> PyInstaller (onedir)"
"$PY" -m PyInstaller --noconfirm --clean --windowed --name crit \
    --distpath "$WORK/pyi-dist" --workpath "$WORK/pyi-work" --specpath "$WORK" --paths "$ROOT" \
    --add-data "$ROOT/index.linux.html:." \
    --add-data "$ROOT/assets:assets" \
    --hidden-import webview.platforms.qt \
    --hidden-import qtpy \
    --collect-submodules webview \
    "$ROOT/crit.py"

echo "==> AppDir"
APPDIR="$WORK/AppDir"
rm -rf "$APPDIR"
mkdir -p "$APPDIR/usr/lib" "$APPDIR/usr/share/applications" \
         "$APPDIR/usr/share/icons/hicolor/256x256/apps"
cp -a "$WORK/pyi-dist/crit" "$APPDIR/usr/lib/crit"
cp "$ROOT/packaging/linux/crit-launcher.desktop" "$APPDIR/crit-launcher.desktop"
cp "$ROOT/packaging/linux/crit-launcher.desktop" "$APPDIR/usr/share/applications/"
cp "$WORK/crit-launcher.png" "$APPDIR/crit-launcher.png"
cp "$WORK/crit-launcher.png" "$APPDIR/usr/share/icons/hicolor/256x256/apps/crit-launcher.png"
install -m 0755 "$ROOT/packaging/linux/AppRun" "$APPDIR/AppRun"

echo "==> appimagetool"
TOOL="${APPIMAGETOOL:-$WORK/appimagetool-$ARCH.AppImage}"
if [ ! -x "$TOOL" ]; then
    curl -fsSL -o "$TOOL" \
        "https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-$ARCH.AppImage"
    chmod +x "$TOOL"
fi

FINAL="$OUT/Crit_Launcher-$VERSION-$ARCH.AppImage"
# --appimage-extract-and-run lets this work where FUSE is missing (containers, CI).
ARCH="$ARCH" "$TOOL" --appimage-extract-and-run --no-appstream "$APPDIR" "$FINAL"
chmod +x "$FINAL"
echo "==> Done: $FINAL"
