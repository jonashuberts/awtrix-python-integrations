#!/usr/bin/env bash
set -euo pipefail

if [[ "$(uname)" != "Darwin" ]]; then
  echo "This build script is for macOS only."
  exit 1
fi

APP_NAME="AWTRIX"
DIST_DIR="dist"
BUILD_DIR="build"

echo "Generating modern app icon..."
uv run --with Pillow scripts/generate_icon.py

echo "Building ${APP_NAME}.app with PyInstaller..."
uv run --with pyinstaller --with Pillow pyinstaller \
  --noconfirm \
  --clean \
  --windowed \
  --onedir \
  --name "${APP_NAME}" \
  --icon "app_icon.png" \
  --hidden-import pystray._darwin \
  --hidden-import PIL.Image \
  --hidden-import PIL.ImageDraw \
  --hidden-import plugins.youtube_plugin \
  --hidden-import plugins.weather_plugin \
  main.py

mkdir -p "${DIST_DIR}/${APP_NAME}" "${DIST_DIR}/${APP_NAME}.app/Contents/MacOS"
cp -f config.json "${DIST_DIR}/${APP_NAME}/config.json"
cp -f config.json "${DIST_DIR}/${APP_NAME}.app/Contents/MacOS/config.json"

if [[ -f ".env.example" ]]; then
  cp -f .env.example "${DIST_DIR}/${APP_NAME}/.env.example"
  cp -f .env.example "${DIST_DIR}/${APP_NAME}.app/Contents/MacOS/.env.example"
fi

# Make app run as menu-bar agent only (no Dock icon)
PLIST_FILE="${DIST_DIR}/${APP_NAME}.app/Contents/Info.plist"
/usr/libexec/PlistBuddy -c "Delete :LSUIElement" "${PLIST_FILE}" >/dev/null 2>&1 || true
/usr/libexec/PlistBuddy -c "Add :LSUIElement bool true" "${PLIST_FILE}"

echo
echo "Build complete."
echo "App: ${DIST_DIR}/${APP_NAME}.app"
