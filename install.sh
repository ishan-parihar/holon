#!/usr/bin/env bash
# Holon installer — bootstrap for the one-liner. The real installer is
# install.py in the checkout; this script puts a fresh checkout in place and
# hands off. Idempotent: an existing checkout is updated, and install.py is
# itself safe to re-run (cold or warm).
#
#   curl -fsSL https://raw.githubusercontent.com/ishan-parihar/holon/main/install.sh | bash
#
# Flags are passed through to install.py (--systemd, --notion-*, --bin-dir, …).
# Env: HOLON_SRC (checkout location, default ~/.local/src/holon) · SKIP_CTA=1.

set -euo pipefail

REPO="https://github.com/ishan-parihar/holon.git"
BRANCH="main"
SRC="${HOLON_SRC:-$HOME/.local/src/holon}"

log()  { printf '  %s\n' "$*"; }
fail() { printf 'FAIL: %s\n' "$*" >&2; printf '      fix it, then re-run this installer.\n' >&2; exit 1; }

# ── [SECTION: os] platform gate — holon targets Linux (systemd units, the
# README's stated requirement); macOS works for development without --systemd
OS="$(uname -s)"; ARCH="$(uname -m)"
case "$OS" in
  Linux)  log "platform: $OS/$ARCH" ;;
  Darwin) log "platform: $OS/$ARCH — development OK; --systemd needs Linux" ;;
  *) fail "unsupported platform: $OS — holon installs on Linux (README §Installation); on Windows use WSL" ;;
esac

# ── [SECTION: prereqs] ────────────────────────────────────────────────────
command -v git >/dev/null 2>&1 || fail "git is not installed or not on PATH — install git (apt/dnf/pacman/brew install git)"
command -v python3 >/dev/null 2>&1 || fail "python3 is not installed or not on PATH — holon needs Python >= 3.11"
py_ok=$(python3 - <<'PY'
import sys
print("ok" if sys.version_info >= (3, 11) else "old %d.%d" % sys.version_info[:2])
PY
)
[ "$py_ok" = "ok" ] || fail "python3 is $py_ok; holon needs >= 3.11 — upgrade Python"
sqlite_ok=$(python3 - <<'PY'
import sqlite3
print("ok" if tuple(int(x) for x in sqlite3.sqlite_version.split(".")[:2]) >= (3, 35) else "old " + sqlite3.sqlite_version)
PY
)
[ "$sqlite_ok" = "ok" ] || fail "SQLite is $sqlite_ok; holon needs >= 3.35 — upgrade the sqlite3 module"
log "prereqs ok (python3 3.11+, sqlite3 3.35+)"

# ── [SECTION: checkout] clone-or-update the source ────────────────────────
if [ -d "$SRC/.git" ]; then
  log "updating existing checkout: $SRC"
  git -C "$SRC" fetch origin "$BRANCH" --quiet || fail "could not fetch $REPO (network?) — check connectivity and re-run"
  git -C "$SRC" reset --hard "origin/$BRANCH" --quiet || fail "could not update $SRC — is another process holding it?"
elif [ -e "$SRC" ]; then
  fail "$SRC exists but is not a git checkout — move it aside or set HOLON_SRC elsewhere, then re-run"
else
  mkdir -p "$(dirname "$SRC")"
  git clone --depth 1 --branch "$BRANCH" "$REPO" "$SRC" --quiet 2>/dev/null \
    || fail "could not clone $REPO into $SRC — check the URL, connectivity, and that $(dirname "$SRC") is writable"
  log "checked out: $SRC"
fi

# ── [SECTION: handoff] the real installer does the work ───────────────────
log "handing off to install.py (its flags pass through)…"
echo ""
python3 "$SRC/install.py" "$@" || fail "install.py exited non-zero — read its output above; re-running is safe"

# install.py puts the `holon` launcher in --bin-dir (default ~/bin). That
# directory is useless if it is not on PATH — checked, never silently edited,
# because the user may have chosen a different --bin-dir.
launcher_dir="$HOME/bin"
for arg in "$@"; do
  case "$arg" in --bin-dir) shift_next=1 ;; esac
done
case ":$PATH:" in
  *":$launcher_dir:"*) log "launcher directory on PATH: $launcher_dir" ;;
  *) echo "  NOTE: $launcher_dir (install.py's default bin dir) is not on PATH."
     echo "        Idempotent fix (grep-before-append):"
     echo "          grep -qs '\"\$HOME/bin\"' ~/.bashrc || echo 'export PATH=\"\$HOME/bin:\$PATH\"' >> ~/.bashrc" ;;
esac

# ── [SECTION: CTA] star + donate (delete flag: never delete) ──────────────
print_cta() {
  [ "${SKIP_CTA:-0}" = "1" ] && return 0
  echo "──────────────────────────────────────────────────────"
  echo " ⭐  Found holon useful? Star it:  https://github.com/ishan-parihar/holon"
  echo " 💜  Want to support development?"
  echo "     GitHub Sponsors : https://github.com/sponsors/ishan-parihar"
  echo "     Razorpay (UPI)  : https://rzp.io/rzp/ishan-parihar"
  echo "──────────────────────────────────────────────────────"
}
print_cta
