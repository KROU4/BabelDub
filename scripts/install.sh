#!/usr/bin/env bash
# Install the BabelDub CLI (babeldub) with uv and check the environment.
#
# 1. Checks that uv is available. Without uv the script prints the official install command and
#    stops; pass --install-uv to run that installer for you.
# 2. Installs the CLI with `uv tool install`: from the local runtime/ folder when the script runs
#    from a BabelDub checkout or kit, otherwise from GitHub (git+https://...#subdirectory=runtime).
# 3. Runs `babeldub doctor`. Exit code 4 from doctor only means that engines and models are not
#    set up yet (`babeldub setup` does that); the installation itself succeeded.
#
# Usage: bash scripts/install.sh [--from-git] [--ref v0.1.0] [--install-uv] [--python 3.12]
#                                [--skip-doctor] [--dry-run]
set -euo pipefail

REF="${BABELDUB_REF:-main}"
REPO="https://github.com/KROU4/BabelDub"
PYTHON_VERSION="3.12"
FROM_GIT=0
INSTALL_UV=0
SKIP_DOCTOR=0
DRY_RUN=0
UV_INSTALL_COMMAND='curl -LsSf https://astral.sh/uv/install.sh | sh'

usage() {
  sed -n '2,13p' "$0" | sed 's/^# \{0,1\}//'
}

while [ $# -gt 0 ]; do
  case "$1" in
    --ref) REF="$2"; shift 2 ;;
    --repo) REPO="$2"; shift 2 ;;
    --python) PYTHON_VERSION="$2"; shift 2 ;;
    --from-git) FROM_GIT=1; shift ;;
    --install-uv) INSTALL_UV=1; shift ;;
    --skip-doctor) SKIP_DOCTOR=1; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done

step() { printf '==> %s\n' "$*"; }

run() {
  printf '    %s\n' "$*"
  if [ "$DRY_RUN" -eq 1 ]; then
    return 0
  fi
  "$@"
}

find_uv() {
  if command -v uv >/dev/null 2>&1; then
    command -v uv
    return 0
  fi
  for candidate in "$HOME/.local/bin/uv" "$HOME/.cargo/bin/uv"; do
    if [ -x "$candidate" ]; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done
  return 1
}

find_local_runtime() {
  local script_dir candidate
  script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  for candidate in "$script_dir/runtime" "$script_dir/../runtime"; do
    if [ -f "$candidate/pyproject.toml" ] && grep -q '^name = "babeldub"' "$candidate/pyproject.toml"; then
      (cd "$candidate" && pwd)
      return 0
    fi
  done
  return 1
}

# 1. uv
step "Checking uv"
UV="$(find_uv || true)"
if [ -z "$UV" ]; then
  if [ "$INSTALL_UV" -ne 1 ]; then
    echo "uv is not installed. Install it with:" >&2
    echo "    $UV_INSTALL_COMMAND" >&2
    echo "or run this script again with --install-uv. Docs: https://docs.astral.sh/uv/" >&2
    exit 1
  fi
  step "Installing uv with the official installer"
  if [ "$DRY_RUN" -eq 1 ]; then
    printf '    %s\n' "$UV_INSTALL_COMMAND"
    UV="uv"
  else
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
    UV="$(find_uv || true)"
    if [ -z "$UV" ]; then
      echo "uv was installed but is not on PATH yet. Open a new shell and run this script again." >&2
      exit 1
    fi
  fi
fi
if [ "$DRY_RUN" -ne 1 ]; then
  printf '    %s\n' "$("$UV" --version)"
fi

# 2. babeldub CLI
RUNTIME=""
if [ "$FROM_GIT" -ne 1 ]; then
  RUNTIME="$(find_local_runtime || true)"
fi
if [ -n "$RUNTIME" ]; then
  SOURCE="$RUNTIME"
  step "Installing babeldub from $SOURCE"
else
  if ! command -v git >/dev/null 2>&1; then
    echo "git is required to install from GitHub." >&2
    [ "$DRY_RUN" -eq 1 ] || exit 1
  fi
  SOURCE="git+$REPO@$REF#subdirectory=runtime"
  step "Installing babeldub from $SOURCE"
fi
if ! run "$UV" tool install --force --reinstall-package babeldub --python "$PYTHON_VERSION" "$SOURCE"; then
  echo "uv tool install failed" >&2
  exit 1
fi

# 3. PATH and doctor
if [ "$DRY_RUN" -eq 1 ]; then
  BIN_DIR="$HOME/.local/bin"
else
  BIN_DIR="$("$UV" tool dir --bin)"
fi
case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *) echo "Note: $BIN_DIR is not on PATH. Run 'uv tool update-shell' and open a new shell." >&2 ;;
esac

if [ "$SKIP_DOCTOR" -eq 1 ]; then
  step "Done. Next: babeldub doctor"
  exit 0
fi
step "Running babeldub doctor"
set +e
run "$BIN_DIR/babeldub" doctor --pretty
code=$?
set -e
case "$code" in
  0) step "Done: environment is ready" ;;
  3) step "Done: doctor reports items that need attention (see above)" ;;
  4) step "Done: the CLI is installed; engines/models are not set up yet. Next: babeldub setup (or ask your agent)" ;;
  *) echo "babeldub doctor failed with exit code $code" >&2; exit 1 ;;
esac
exit 0
