#!/bin/zsh
set -euo pipefail
cd "$(dirname "$0")"
swift build -c release
app="FRIDAY.app"
rm -rf "$app"
mkdir -p "$app/Contents/MacOS"
cp .build/release/Friday "$app/Contents/MacOS/Friday"
cat > "$app/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>FRIDAY</string>
  <key>CFBundleIdentifier</key><string>tech.nexttech.friday</string>
  <key>CFBundleExecutable</key><string>Friday</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>0.1.0</string>
  <key>LSMinimumSystemVersion</key><string>14.0</string>
  <key>NSHighResolutionCapable</key><true/>
  <key>NSMicrophoneUsageDescription</key><string>FRIDAY listens while you dictate a request into the chat.</string>
  <key>NSSpeechRecognitionUsageDescription</key><string>FRIDAY turns what you say into the text of your request.</string>
</dict>
</plist>
PLIST
# Sign the bundle so macOS ties the microphone and speech permissions to it.
codesign --force --sign - "$app" || echo "Could not sign $app: dictation permissions may not stick."
echo "Built $(pwd)/$app — drag it to /Applications or run: open $app"
