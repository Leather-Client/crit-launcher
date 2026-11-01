#!/usr/bin/env bash
# Build the Linux AppImage. See linux/build-appimage.sh for details.
exec "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/linux/build-appimage.sh" "$@"
