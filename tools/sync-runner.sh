#!/bin/bash
# SPDX-FileCopyrightText: 2026 Dmitriy Volkov <dennshield@duck.com>
# SPDX-License-Identifier: GPL-2.0-only OR GPL-3.0-only OR LicenseRef-KDE-Accepted-GPL

set -euo pipefail
umask 077

project=$(cd "$(dirname "$0")/.." && pwd -P)
local_env="$project/.state/local.env"
if [[ ! -r $local_env ]]; then
    echo "Missing local config: $local_env" >&2
    exit 1
fi

set -a
# shellcheck source=/dev/null
source "$local_env"
set +a

: "${KDECONNECT_DESKTOP_SRC:?Missing KDECONNECT_DESKTOP_SRC in .state/local.env}"
: "${KDECONNECT_ANDROID_SRC:?Missing KDECONNECT_ANDROID_SRC in .state/local.env}"
: "${RUNNER_HOST:?Missing RUNNER_HOST in .state/local.env}"
: "${RUNNER_DIR:?Missing RUNNER_DIR in .state/local.env}"

export TMPDIR="${TMPDIR:-$project/.state/tmp}"
mkdir -p "$TMPDIR"
if [[ $(findmnt -n -o FSTYPE -T "$TMPDIR") == tmpfs ]]; then
    echo "Refusing tmpfs TMPDIR: $TMPDIR" >&2
    exit 1
fi

# Run Gate 1 checks locally before syncing anything to the runner.
python3 "$project/tools/check_public_tree.py" --worktree
python3 "$project/tools/check_kde_compliance.py"
python3 "$project/tools/check_kde_compliance.py" \
  --upstream-repo "$KDECONNECT_DESKTOP_SRC" \
  --base origin/master

desktop_git=(
  git -C "$KDECONNECT_DESKTOP_SRC"
  -c core.hooksPath=/dev/null
  -c core.fsmonitor=false
)
android_git=(
  git -C "$KDECONNECT_ANDROID_SRC"
  -c core.hooksPath=/dev/null
  -c core.fsmonitor=false
)

plugin_base=$("${desktop_git[@]}" merge-base HEAD origin/master)
plugin_head=$("${desktop_git[@]}" rev-parse HEAD)
android_base=$("${android_git[@]}" rev-parse origin/master)

tmp_bundle=$(mktemp -p "$TMPDIR" kdeconnect-plugin-XXXXXX.bundle)
trap 'rm -f -- "$tmp_bundle"' EXIT
"${desktop_git[@]}" bundle create "$tmp_bundle" "^$plugin_base" HEAD

ssh "$RUNNER_HOST" bash -s -- "$RUNNER_DIR" <<'REMOTE_PREP'
set -euo pipefail
umask 077
raw_dir=$1
if [[ $raw_dir == "~/"* ]]; then
    remote_dir="$HOME/${raw_dir#~/}"
elif [[ $raw_dir == "~" ]]; then
    remote_dir="$HOME"
else
    remote_dir="$raw_dir"
fi
mkdir -p "$remote_dir/LICENSES" "$remote_dir/obs" "$remote_dir/tools" "$remote_dir/tests"
mkdir -p "$remote_dir/.state/tmp" "$remote_dir/.state/sync" "$remote_dir/.state/repos"
chmod 700 "$remote_dir/.state"
REMOTE_PREP

# Sync only explicit public runner paths; never touch remote .state/.
rsync -az "$project/AGENTS.md" "$project/REUSE.toml" "$RUNNER_HOST:$RUNNER_DIR/"
rsync -az --delete --exclude='__pycache__/' "$project/LICENSES/" "$RUNNER_HOST:$RUNNER_DIR/LICENSES/"
rsync -az --delete --exclude='__pycache__/' "$project/obs/" "$RUNNER_HOST:$RUNNER_DIR/obs/"
rsync -az --delete --exclude='__pycache__/' "$project/tools/" "$RUNNER_HOST:$RUNNER_DIR/tools/"
rsync -az --delete --exclude='__pycache__/' "$project/tests/" "$RUNNER_HOST:$RUNNER_DIR/tests/"

bundle_name=$(basename "$tmp_bundle")
rsync -az "$tmp_bundle" "$RUNNER_HOST:$RUNNER_DIR/.state/tmp/$bundle_name"

ssh "$RUNNER_HOST" bash -s -- "$RUNNER_DIR" "$bundle_name" "$plugin_base" "$plugin_head" "$android_base" <<'REMOTE_IMPORT'
set -euo pipefail
umask 077
raw_dir=$1
bundle_name=$2
plugin_base=$3
plugin_head=$4
android_base=$5

if [[ $raw_dir == "~/"* ]]; then
    remote_dir="$HOME/${raw_dir#~/}"
elif [[ $raw_dir == "~" ]]; then
    remote_dir="$HOME"
else
    remote_dir="$raw_dir"
fi

bundle_file="$remote_dir/.state/tmp/$bundle_name"
trap 'rm -f -- "$bundle_file"' EXIT
[[ -s $bundle_file ]]

repo_dir="$remote_dir/.state/repos/kdeconnect-kde"
unset OSC_CONFIG SSH_AUTH_SOCK SSH_AGENT_PID GIT_SSH_COMMAND
export GIT_TERMINAL_PROMPT=0 GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_SYSTEM=/dev/null LC_ALL=C

git_common=(
  -c core.hooksPath=/dev/null
  -c core.fsmonitor=false
  -c core.symlinks=false
  -c submodule.recurse=false
  -c filter.lfs.smudge=
  -c filter.lfs.clean=
  -c filter.lfs.process=
  -c fetch.fsckObjects=true
  -c transfer.fsckObjects=true
  -c receive.fsckObjects=true
)

if [[ ! -d $repo_dir/.git ]]; then
    GIT_ALLOW_PROTOCOL=https git "${git_common[@]}" \
      -c protocol.allow=never -c protocol.ext.allow=never -c protocol.https.allow=always \
      clone --branch master --single-branch https://invent.kde.org/network/kdeconnect-kde.git "$repo_dir"
else
    GIT_ALLOW_PROTOCOL=https git -C "$repo_dir" "${git_common[@]}" \
      -c protocol.allow=never -c protocol.ext.allow=never -c protocol.https.allow=always \
      fetch origin master
fi

GIT_ALLOW_PROTOCOL=file git -C "$repo_dir" "${git_common[@]}" \
  -c protocol.allow=never -c protocol.ext.allow=never -c protocol.file.allow=always \
  fetch "$bundle_file" "+HEAD:refs/heads/plugin/screenstream"

imported_head=$(git -C "$repo_dir" "${git_common[@]}" rev-parse refs/heads/plugin/screenstream)
if [[ $imported_head != "$plugin_head" ]]; then
    echo "Imported bundle HEAD mismatch: got $imported_head expected $plugin_head" >&2
    exit 1
fi
git -C "$repo_dir" "${git_common[@]}" cat-file -e "$plugin_base^{commit}"

printf '%s\n' "$plugin_base" >"$remote_dir/.state/sync/plugin_base.txt"
printf '%s\n' "$android_base" >"$remote_dir/.state/sync/android_base.txt"

"$remote_dir/tools/run-controller.sh" verify-env
echo "SYNC_RUNNER_OK (plugin_head=$plugin_head plugin_base=$plugin_base)"
REMOTE_IMPORT
