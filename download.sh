#!/bin/sh
# Entry point for downloading dataset.
#
# Works on Linux, macOS, WSL and Git-Bash.
# It only picks an interpreter and hands over to scripts/bootstrap.py,
# which does the real work (preflight, auth, dependencies, resumable download, verification).
#
#   ./download.sh --dest /data/rsna
#   ./download.sh --help
#
# Everything this script does not recognise is forwarded to the downloader verbatim, 
# e.g. ./download.sh --dest /data/rsna --limit 300
set -u

# Repo root = the directory holding this script.
# Resolved in a subshell: the caller's working directory is deliberately left alone,
# so that relative --dest paths keep their meaning.
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd) || exit 1

if [ -z "$ROOT" ]; then
    echo "download.sh: could not work out its own directory (is 'dirname' on PATH?)" >&2
    exit 1
fi

BOOTSTRAP="$ROOT/scripts/bootstrap.py"

if [ ! -f "$BOOTSTRAP" ]; then
    echo "download.sh: cannot find $BOOTSTRAP" >&2
    echo "Make sure you cloned the whole repository." >&2
    exit 1
fi

# 1) uv, if present:
#    install Python pinned in .python-version with dependencies into <repo>/.venv.
#    --frozen means never re-resolve or rewrite uv.lock.
if command -v uv >/dev/null 2>&1; then
    exec uv run --project "$ROOT" --frozen python "$BOOTSTRAP" "$@"
fi

# 2) otherwise any Python >= 3.8 will do:
#    bootstrap.py is stdlib-only and builds the venv when has to.
best=""
best_version=0
for candidate in python3 python3.13 python3.12 python3.11 python3.10 python3.9 python3.8 python; do
    command -v "$candidate" >/dev/null 2>&1 || continue
    version=$("$candidate" -c 'import sys; print(sys.version_info[0] * 100 + sys.version_info[1])' 2>/dev/null) || continue
    case "$version" in
        '' | *[!0-9]*) continue ;;
    esac
    if [ "$version" -gt "$best_version" ]; then
        best=$(command -v "$candidate")
        best_version=$version
    fi
done

if [ -n "$best" ] && [ "$best_version" -ge 308 ]; then
    exec "$best" "$BOOTSTRAP" "$@"
fi

if [ -n "$best" ]; then
    echo "download.sh: found $best but it is Python $(($best_version / 100)).$(($best_version % 100)); 3.8 or newer is required." >&2
else
    echo "download.sh: neither 'uv' nor a Python interpreter was found on PATH." >&2
fi

# printf, not a cat heredoc: this branch runs precisely when PATH is bare.
printf '%s\n' \
    "" \
    "Install one of the two, then run this script again." \
    "" \
    "  uv (recommended - it also installs the right Python for you):" \
    "    Linux / macOS / WSL:  curl -LsSf https://astral.sh/uv/install.sh | sh" \
    "    Windows / Git-Bash:   powershell -c \"irm https://astral.sh/uv/install.ps1 | iex\"" \
    "" \
    "  Python 3.9 or newer:" \
    "    Debian / Ubuntu:      sudo apt install python3 python3-venv python3-pip" \
    "    macOS (Homebrew):     brew install python" \
    "    Windows:              https://www.python.org/downloads/  (tick \"Add python.exe to PATH\")" \
    "" \
    "After installing uv, open a new terminal (or run . \"\$HOME/.local/bin/env\") so" \
    "that it lands on PATH." >&2
exit 1
