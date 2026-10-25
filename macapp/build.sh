#!/bin/bash
# Build SyncRoom.app (a py2app "alias" app that runs the code in this folder, so code changes
# don't need a rebuild) and install it in ~/Applications.
set -euo pipefail
cd "$(dirname "$0")"
ROOT=$(cd .. && pwd)
PY="$ROOT/.venv/bin/python"
if [ ! -x "$PY" ]; then
  /Library/Frameworks/Python.framework/Versions/3.12/bin/python3 -m venv "$ROOT/.venv" \
    || python3 -m venv "$ROOT/.venv"
fi
"$PY" -m pip install -q -e "$ROOT[app]"
rm -rf build dist
"$PY" setup.py -q py2app --alias >/dev/null
codesign --force --deep --sign - dist/SyncRoom.app  # ad-hoc: gives macOS a stable app identity
mkdir -p ~/Applications
rm -rf ~/Applications/SyncRoom.app
mv dist/SyncRoom.app ~/Applications/
rm -rf build dist
echo "Installed ~/Applications/SyncRoom.app — open it from Spotlight or Finder."
