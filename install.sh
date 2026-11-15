#!/usr/bin/env bash
# Install the `radiocli` command into ~/.local/bin via a symbolic link.
# From a GitHub clone:  git clone ... && cd radioCLI && ./install.sh
set -euo pipefail

HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
BIN_DIR="${XDG_BIN_HOME:-$HOME/.local/bin}"
TARGET="$BIN_DIR/radiocli"
PYTHON_BIN="${PYTHON:-python3}"

usage() {
  cat <<EOF
Usage: ./install.sh [--uninstall]

Installs (or removes) the "radiocli" command in $BIN_DIR.
Checks Python >= 3.10, ffmpeg and an audio player (mpv recommended).

System dependencies (Arch / Omarchy):
  sudo pacman -S python ffmpeg mpv

Without installing:
  ./radiocli          # run directly from this clone
EOF
}

check_python() {
  if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
    echo "Error: Python not found (python3 >= 3.10 required)." >&2
    return 1
  fi
  if ! "$PYTHON_BIN" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)'; then
    local py_version
    py_version="$("$PYTHON_BIN" -V 2>&1 || true)"
    echo "Error: $py_version — Python >= 3.10 required." >&2
    return 1
  fi
  printf '  ✓ %-8s %s\n' "python" "$("$PYTHON_BIN" -V 2>&1)"
}

check_deps() {
  local missing=0
  for cmd in ffmpeg; do
    if command -v "$cmd" >/dev/null 2>&1; then
      printf '  ✓ %-8s %s\n' "$cmd" "$(command -v "$cmd")"
    else
      printf '  ✗ %-8s not found (stream analysis will not work)\n' "$cmd"
      missing=1
    fi
  done
  if command -v mpv >/dev/null 2>&1; then
    printf '  ✓ %-8s %s\n' "mpv" "$(command -v mpv)"
  elif command -v ffplay >/dev/null 2>&1; then
    printf '  ! %-8s not found, falling back to ffplay\n' "mpv"
  else
    printf '  ✗ %-8s not found (audio will not play)\n' "mpv/ffplay"
    missing=1
  fi
  return "$missing"
}

case "${1:-}" in
  --uninstall)
    rm -f "$TARGET" "$BIN_DIR/grosse-radio"
    echo "Removed: $TARGET (and the legacy grosse-radio link)"
    exit 0
    ;;
  -h|--help)
    usage
    exit 0
    ;;
  "")
    ;;
  *)
    echo "Unknown option: $1" >&2
    usage >&2
    exit 2
    ;;
esac

echo "Checking environment:"
if ! check_python; then
  exit 1
fi
if ! check_deps; then
  echo
  echo "Install the missing tools, for example:"
  echo "  sudo pacman -S ffmpeg mpv"
  echo "(radioCLI can still run in --demo mode)."
  echo
fi

mkdir -p "$BIN_DIR"
ln -sfn "$HERE/radiocli" "$TARGET"
rm -f "$BIN_DIR/grosse-radio"
chmod +x "$HERE/radiocli" "$HERE/install.sh" 2>/dev/null || true

echo "Installed: $TARGET -> $HERE/radiocli"
case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *) echo "Add $BIN_DIR to your PATH to run \"radiocli\"." ;;
esac
echo "Run: radiocli   (or $TARGET)"
