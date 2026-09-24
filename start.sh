#!/usr/bin/env sh
# Linux / macOS launcher. ./start.sh [--lan]
# Serial access on Linux needs your user in the 'dialout' group (sudo usermod -aG dialout $USER, then log in again).
cd "$(dirname "$0")"
if [ "$1" = "--lan" ]; then export BDT_LAN=1; fi   # engine stays on 127.0.0.1
node scripts/engine.mjs &
ENGINE=$!
trap 'kill $ENGINE 2>/dev/null' EXIT
npm --prefix frontend run dev
