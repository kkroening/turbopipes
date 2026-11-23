#!/usr/bin/env bash
##
##  `base.sh` script boilerplate.
##

set -euo pipefail

base::abspath() {
  # Cross-platform(-ish) abspath for systems that lack GNU readlink.
  arg1="$1" python -c "import os; print(os.path.realpath(os.environ['arg1']))"
}

BASE__ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
BASE__SCRIPTS_DIR="${BASE__ROOT_DIR}/scripts"

BASE__COLOR_LIGHT_GREEN='\033[1;32m'
BASE__COLOR_OFF='\033[0m'

base::_log() {
  if (($# < 1)); then
    base::abort "Usage: ${FUNCNAME[1]} <fmt> [printf args...]"
  fi
  local args fmt
  args=("${@:2}")
  fmt="$1"
  printf \
    "${prefix}${fmt}\n" \
    "${args[@]:-}" \
    >&2
}

base::log_debug() {
  [[ -z "${BASE__DEBUG:-}" ]] \
    || prefix='[DEBUG] ' base::_log "$@"
}

base::log_info() {
  prefix='' base::_log "$@"
}

base::log_warning() {
  prefix='[WARNING] ' base::_log "$@"
}

base::log_error() {
  prefix='[ERROR] ' base::_log "$@"
}

base::abort() {
  base::log_error "${@:-Aborting.}"
  exit 1
}

base::show_banner() {
  printf "\n===> ${BASE__COLOR_LIGHT_GREEN}${@}${BASE__COLOR_OFF}\n" >&2
}

base::_shorten_filename() {
  #
  # Shortens a potentially long filename into a more concise representation -
  # e.g. `/some/really/long/directory/path/file.txt` -> `.../file.txt`.
  #
  base::assert_eq $# 1
  local filename="$1"
  # TODO: improve; lazy implementation so far
  echo ".../$(basename "${filename}")"
}

base::show_stacktrace() {
  #
  # Source: https://stackoverflow.com/a/62757929
  #
  local i=1 code line file func
  echo "Stacktrace (most recent call first):" >&2
  while read -r line func file < <(caller $i); do
    code="$(sed -n ${line}p ${file})"
    file="$(base::_shorten_filename "${file}")"
    echo >&2 "  [$i] ${file}:${line} ${func}(): ${code}"
    ((i++))
  done
}

base::assert_eq() {
  if (($# != 2)); then
    base::abort "Usage: %s <actual> <expected>" "${FUNCNAME[0]}"
  fi
  local actual="$1"
  local expected="$2"
  if [[ "${actual}" != "${expected}" ]]; then
    base::abort \
      "Assertion failed: %s != %s\n%s" \
      "${actual}" \
      "${expected}" \
      "$(base::show_stacktrace 2>&1)"
  fi
}

base::detect_version() {
  #
  # Detects a version identifier according to git (e.g. `1.2.3`).
  #
  if ! git describe --tags; then
    base::log_error "Failed to detect version info."
    return 1
  fi
}

base::get_repo_https_url() {
  #
  # `base::get_repo_https_url`: Determine the GitHub https URL for this repo
  # based on the default `origin` git remote.
  #
  # If the remote URL is git/SSH, the URL is converted to https using basic
  # string manipulation.
  #
  local url
  url="$(git config --get remote.origin.url)" || false
  url="${url/#git@*github.com:/https://github.com/}" # also SSH host aliases
  url="${url/%.git/}"
  echo "${url}"
}

base::insulate_gha_exit() {
  #
  # Run a command, propagating GHA workflow cancellation gracefully.
  #
  # When GHA cancels a step, its runner sends SIGINT only to the step's
  # top-level process (the bash invoking `run:`), then SIGKILLs the rest
  # of the tree ~10s later. Bash blocked in `wait` doesn't forward signals
  # to children, so a grandchild like `terraform apply` gets no chance to
  # release its state lock. Locally this never happens because the TTY
  # broadcasts Ctrl+C to the entire foreground process group.
  #
  # This helper reproduces the TTY behavior: `set -m` makes the child a
  # process group leader, and the INT/TERM trap broadcasts SIGINT to that
  # group. The double `wait` is required because GHA's `bash -eo pipefail`
  # would otherwise exit on the trap-interrupted first `wait` (rc
  # 128+signum) before the child finishes draining; `kill -0` then
  # distinguishes "already exited" from "still draining, wait again."
  #
  # Refs:
  #   - https://github.com/ringerc/github-actions-signal-handling-demo
  #   - https://github.com/orgs/community/discussions/26311
  #   - https://veithen.io/2014/11/16/sigterm-propagation.html
  #
  local shell_opts="$-"
  set -m
  local pid

  # Install the trap *before* backgrounding so a signal landing in the
  # tiny window between `&` and `pid="$!"` can't slip past; the
  # `${pid:-}` guard silences the trap until pid has been assigned.
  trap '[[ -n "${pid:-}" ]] && kill -INT "-${pid}" 2>/dev/null || true' INT TERM
  # Clear our traps and restore monitor mode (if it wasn't already on)
  # on return, so sourcing this helper doesn't leak shell state.
  trap 'trap - INT TERM RETURN; [[ "${shell_opts}" == *m* ]] || set +m' RETURN

  "$@" &
  pid="$!"

  wait "${pid}" || {
    local rc=$?
    if kill -0 "${pid}" 2> /dev/null; then
      wait "${pid}"
    else
      return "$rc"
    fi
  }
}

if [[ -r "${BASE__SCRIPTS_DIR}/settings.sh" ]]; then
  source "${BASE__SCRIPTS_DIR}/settings.sh"
fi
