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
python3 "$project/tools/plugin_registry.py" validate --registry "$project/plugins.toml"
python3 "$project/tools/check_public_tree.py" --worktree
python3 "$project/tools/check_kde_compliance.py"

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

active_desktop_json=$(python3 "$project/tools/plugin_registry.py" list --target desktop --registry "$project/plugins.toml")
mapfile -t stack_rows < <(python3 -c '
import json, sys
for row in json.loads(sys.argv[1]):
    print("\t".join((row["id"], row["branch"])))
' "$active_desktop_json")

if [[ ${#stack_rows[@]} -eq 0 ]]; then
    echo "No active desktop plugins in plugins.toml" >&2
    exit 1
fi

bundle_args=()
manifest_rows=()
first_base=""
for entry in "${stack_rows[@]}"; do
    plugin_id=${entry%%$'\t'*}
    branch=${entry#*$'\t'}
    python3 "$project/tools/check_kde_compliance.py" \
      --upstream-repo "$KDECONNECT_DESKTOP_SRC" \
      --base origin/master \
      --ref "refs/heads/$branch"
    base_sha=$("${desktop_git[@]}" merge-base "refs/heads/$branch" origin/master)
    head_sha=$("${desktop_git[@]}" rev-parse "refs/heads/$branch")
    if [[ -z $first_base ]]; then
        first_base=$base_sha
    fi
    bundle_args+=("^$base_sha" "refs/heads/$branch")
    manifest_rows+=("${plugin_id}|${branch}|refs/heads/plugin/${plugin_id}|${base_sha}|${head_sha}")
done

python3 "$project/tools/synthesize_android_stack.py" \
  --desktop \
  --repo "$KDECONNECT_DESKTOP_SRC" \
  --registry "$project/plugins.toml" \
  --dry-run

android_base=$("${android_git[@]}" rev-parse origin/master)

tmp_bundle=$(mktemp -p "$TMPDIR" kdeconnect-stack-XXXXXX.bundle)
tmp_manifest=$(mktemp -p "$TMPDIR" kdeconnect-stack-XXXXXX.json)
trap 'rm -f -- "$tmp_bundle" "$tmp_manifest"' EXIT
"${desktop_git[@]}" bundle create "$tmp_bundle" "${bundle_args[@]}"

python3 - "$tmp_manifest" "${manifest_rows[@]}" <<'PY_MANIFEST'
import json
import sys

out_path = sys.argv[1]
records = []
for raw in sys.argv[2:]:
    plugin_id, branch, remote_ref, base_sha, head_sha = raw.split("|", 4)
    records.append(
        {
            "id": plugin_id,
            "branch": branch,
            "ref": remote_ref,
            "base_sha": base_sha,
            "head_sha": head_sha,
        }
    )
with open(out_path, "w", encoding="utf-8") as fh:
    json.dump(records, fh, indent=2)
    fh.write("\n")
PY_MANIFEST

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
rsync -az "$project/AGENTS.md" "$project/REUSE.toml" "$project/plugins.toml" "$RUNNER_HOST:$RUNNER_DIR/"
rsync -az --delete --exclude='__pycache__/' "$project/LICENSES/" "$RUNNER_HOST:$RUNNER_DIR/LICENSES/"
rsync -az --delete --exclude='__pycache__/' "$project/obs/" "$RUNNER_HOST:$RUNNER_DIR/obs/"
rsync -az --delete --exclude='__pycache__/' "$project/tools/" "$RUNNER_HOST:$RUNNER_DIR/tools/"
rsync -az --delete --exclude='__pycache__/' "$project/tests/" "$RUNNER_HOST:$RUNNER_DIR/tests/"

bundle_name=$(basename "$tmp_bundle")
manifest_name=$(basename "$tmp_manifest")
rsync -az "$tmp_bundle" "$RUNNER_HOST:$RUNNER_DIR/.state/tmp/$bundle_name"
rsync -az "$tmp_manifest" "$RUNNER_HOST:$RUNNER_DIR/.state/tmp/$manifest_name"

ssh "$RUNNER_HOST" bash -s -- "$RUNNER_DIR" "$bundle_name" "$manifest_name" "$first_base" "$android_base" <<'REMOTE_IMPORT'
set -euo pipefail
umask 077
raw_dir=$1
bundle_name=$2
manifest_name=$3
first_base=$4
android_base=$5

if [[ $raw_dir == "~/"* ]]; then
    remote_dir="$HOME/${raw_dir#~/}"
elif [[ $raw_dir == "~" ]]; then
    remote_dir="$HOME"
else
    remote_dir="$raw_dir"
fi

bundle_file="$remote_dir/.state/tmp/$bundle_name"
manifest_file="$remote_dir/.state/tmp/$manifest_name"
trap 'rm -f -- "$bundle_file" "$manifest_file"' EXIT
[[ -s $bundle_file ]]
[[ -s $manifest_file ]]

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

mapfile -t import_rows < <(python3 -c '
import json, sys
for row in json.load(open(sys.argv[1], encoding="utf-8")):
    print("\t".join((row["id"], row["branch"], row["ref"], row["base_sha"], row["head_sha"])))
' "$manifest_file")

for item in "${import_rows[@]}"; do
    IFS=$'\t' read -r plugin_id branch remote_ref base_sha head_sha <<<"$item"
    GIT_ALLOW_PROTOCOL=file git -C "$repo_dir" "${git_common[@]}" \
      -c protocol.allow=never -c protocol.ext.allow=never -c protocol.file.allow=always \
      fetch "$bundle_file" "+refs/heads/$branch:$remote_ref"
    imported_head=$(git -C "$repo_dir" "${git_common[@]}" rev-parse "$remote_ref")
    if [[ $imported_head != "$head_sha" ]]; then
        echo "Imported bundle HEAD mismatch for $plugin_id: got $imported_head expected $head_sha" >&2
        exit 1
    fi
    git -C "$repo_dir" "${git_common[@]}" cat-file -e "$base_sha^{commit}"
done

cp -f -- "$manifest_file" "$remote_dir/.state/sync/stack.json"
chmod 600 "$remote_dir/.state/sync/stack.json"
printf '%s\n' "$first_base" >"$remote_dir/.state/sync/plugin_base.txt"
printf '%s\n' "$android_base" >"$remote_dir/.state/sync/android_base.txt"

"$remote_dir/tools/run-controller.sh" verify-env
echo "SYNC_RUNNER_OK (plugins=${#import_rows[@]} first_base=$first_base)"
REMOTE_IMPORT
