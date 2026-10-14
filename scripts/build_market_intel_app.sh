#!/bin/zsh
set -euo pipefail

PROJECT="$(cd "$(dirname "$0")/.." && pwd)"
APP="$PROJECT/dist/Market Intel.app"
BUILD="$PROJECT/tmp/market-intel-app-build"

rm -rf "$APP" "$BUILD"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources" "$BUILD"
export CLANG_MODULE_CACHE_PATH="$BUILD/module-cache"

clang -O2 -fobjc-arc -fblocks -framework Cocoa -framework WebKit \
  "$PROJECT/desktop_app/MarketIntelApp.m" \
  -o "$APP/Contents/MacOS/MarketIntel"

clang -O2 -fobjc-arc -fblocks -framework AppKit \
  "$PROJECT/desktop_app/AppIconGenerator.m" \
  -o "$BUILD/AppIconGenerator"

cp "$PROJECT/desktop_app/Info.plist" "$APP/Contents/Info.plist"
"$BUILD/AppIconGenerator" "$BUILD/AppIcon.iconset"
if ! iconutil -c icns "$BUILD/AppIcon.iconset" -o "$APP/Contents/Resources/AppIcon.icns"; then
  sips -s format icns "$BUILD/AppIcon.iconset/icon_512x512@2x.png" \
    --out "$APP/Contents/Resources/AppIcon.icns" >/dev/null
fi

chmod 755 "$APP/Contents/MacOS/MarketIntel"
codesign --force --deep --sign - "$APP"
rm -rf "$BUILD"

echo "$APP"
