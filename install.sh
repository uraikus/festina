#!/bin/sh
# Festina installer.
#
# This repository has no release pipeline yet -- nothing builds and
# publishes a prebuilt `festina` binary anywhere a script could download
# one from (see claude.md #145). "Install from source" is the honest,
# actually-functional version of a one-line install today: clone a
# fresh checkout, then hand off to `festina doctor --fix` (claude.md
# #144) -- already built, already tested -- for both checking/
# installing build dependencies and adding `festina` to PATH, rather
# than reimplementing either of those in shell a second time here.
#
# Supported shells: Linux and macOS's own default shells, and MSYS2
# UCRT64 bash on Windows -- windows.md's own one supported Windows
# toolchain/shell (MSVC is explicitly out of scope there; a native
# PowerShell installer would first need to bootstrap MSYS2 itself from
# nothing, a materially bigger undertaking than this script, and out
# of scope here for the identical reason).
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/uraikus/festina/main/install.sh | sh
#
#   # Skip every confirmation prompt (including festina doctor --fix's
#   # own) -- e.g. for a non-interactive/CI install:
#   curl -fsSL .../install.sh | sh -s -- --yes
#
# Env vars (all optional):
#   FESTINA_INSTALL_DIR  where to clone (default: $HOME/.festina)
#   FESTINA_REPO         git remote to clone (default: the real upstream)
#   FESTINA_BRANCH       branch/ref to check out (default: main)
set -eu

FESTINA_REPO="${FESTINA_REPO:-https://github.com/uraikus/festina.git}"
FESTINA_BRANCH="${FESTINA_BRANCH:-main}"
FESTINA_INSTALL_DIR="${FESTINA_INSTALL_DIR:-$HOME/.festina}"

say() { printf '%s\n' "$*"; }
die() { printf 'festina install: %s\n' "$*" >&2; exit 1; }

assume_yes=""
for arg in "$@"; do
    case "$arg" in
        --yes|-y) assume_yes="--yes" ;;
        *) die "unrecognized argument '$arg' (only --yes/-y is supported)" ;;
    esac
done

# claude.md #128/windows.md Phase 0: MSYS2's own three subsystems all
# report a Windows uname (MINGW64/MINGW32/CLANG64 -- UCRT64 specifically
# for the one this project supports) or MSYS/CYGWIN for the plain
# POSIX-emulation shells that aren't it -- $MSYSTEM (unset outside any
# MSYS2 shell) is the same signal festina/cli.py's own doctor logic
# already keys off, checked here for the identical reason: a program
# built from the wrong one of these shells links against the wrong
# runtime entirely (see festina/cli.py's "wrong shell" doctor line).
case "$(uname -s 2>/dev/null || echo unknown)" in
    Linux|Darwin) ;;
    MINGW*|MSYS*|CYGWIN*)
        case "${MSYSTEM:-}" in
            UCRT64) ;;
            "") die "this looks like Windows but not an MSYS2 shell -- run this from an MSYS2 UCRT64 shell instead, see setup.md" ;;
            *) die "run this from an MSYS2 UCRT64 shell specifically, not \$MSYSTEM=$MSYSTEM -- see setup.md" ;;
        esac
        ;;
    *) die "unsupported platform -- see setup.md for manual setup" ;;
esac

command -v python3 >/dev/null 2>&1 || die "python3 is required to run the compiler frontend -- install it first, see setup.md"

if [ -e "$FESTINA_INSTALL_DIR" ]; then
    if [ -d "$FESTINA_INSTALL_DIR/.git" ]; then
        say "Updating existing checkout at $FESTINA_INSTALL_DIR..."
        git -C "$FESTINA_INSTALL_DIR" fetch --depth 1 origin "$FESTINA_BRANCH"
        git -C "$FESTINA_INSTALL_DIR" checkout "$FESTINA_BRANCH"
        git -C "$FESTINA_INSTALL_DIR" reset --hard "origin/$FESTINA_BRANCH"
    else
        die "$FESTINA_INSTALL_DIR already exists and isn't a festina checkout -- set FESTINA_INSTALL_DIR to a different path"
    fi
elif command -v git >/dev/null 2>&1; then
    say "Cloning festina into $FESTINA_INSTALL_DIR..."
    git clone --depth 1 --branch "$FESTINA_BRANCH" "$FESTINA_REPO" "$FESTINA_INSTALL_DIR"
else
    # No git at all -- fall back to downloading GitHub's own source
    # archive for the same ref, no git required. Works for any public
    # GitHub repo with zero release infrastructure of its own (unlike a
    # prebuilt-binary download, which this project genuinely has none
    # of yet -- see this file's own top comment).
    command -v curl >/dev/null 2>&1 || die "need either git or curl to fetch the source"
    command -v tar >/dev/null 2>&1 || die "need tar to extract the source archive"
    archive_url=$(printf '%s' "$FESTINA_REPO" | sed -E 's#\.git$##')"/archive/refs/heads/${FESTINA_BRANCH}.tar.gz"
    say "Downloading festina source into $FESTINA_INSTALL_DIR..."
    mkdir -p "$FESTINA_INSTALL_DIR"
    curl -fsSL "$archive_url" | tar -xz -C "$FESTINA_INSTALL_DIR" --strip-components=1
fi

chmod +x "$FESTINA_INSTALL_DIR/bin/festina" 2>/dev/null || true
FESTINA_BIN="$FESTINA_INSTALL_DIR/bin/festina"

say ""
say "festina is now at $FESTINA_INSTALL_DIR"
say ""

# `curl ... | sh` hands this script's own stdin to the pipe, not a real
# terminal -- `festina doctor --fix`'s own confirmation prompt would
# otherwise always hit its non-interactive guard and refuse, even when
# a real person is watching. Reconnecting the child's stdin to
# /dev/tty (the standard curl-pipe-installer trick -- the same one
# rustup's own install script uses) restores real interactivity
# whenever one exists; `[ -t 1 ] && [ -r /dev/tty ]` is false in a
# genuinely headless context (CI, `sh install.sh < /dev/null`), where
# this falls back to just printing the one command to run instead of
# guessing at consent that was never actually given.
if [ -t 1 ] && [ -r /dev/tty ]; then
    say "Finishing setup (checks build dependencies and adds festina to PATH)..."
    say ""
    # shellcheck disable=SC2086
    "$FESTINA_BIN" doctor --fix $assume_yes < /dev/tty || true
else
    say "Run this to finish setup (checks build dependencies, offers to install"
    say "anything missing, and adds festina to PATH):"
    say ""
    say "  $FESTINA_BIN doctor --fix"
fi

say ""
say "See setup.md for the full dependency list and api.md for the language reference."
