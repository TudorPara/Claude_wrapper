#!/bin/zsh
set -euo pipefail
cd "$(dirname "$0")"
developer=/Library/Developer/CommandLineTools/Library/Developer
if [[ -d /Applications/Xcode.app || ! -d $developer/Frameworks/Testing.framework ]]; then
  exec swift test
fi
exec swift test -Xswiftc -F -Xswiftc $developer/Frameworks -Xlinker -F -Xlinker $developer/Frameworks \
  -Xlinker -rpath -Xlinker $developer/Frameworks -Xlinker -rpath -Xlinker $developer/usr/lib
