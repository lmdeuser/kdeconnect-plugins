#!/bin/bash
# SPDX-FileCopyrightText: 2026 Dmitriy Volkov <dennshield@duck.com>
# SPDX-License-Identifier: GPL-2.0-only OR GPL-3.0-only OR LicenseRef-KDE-Accepted-GPL

set -euo pipefail
umask 077

project=$(cd "$(dirname "$0")/.." && pwd -P)
export TMPDIR="$project/.state/tmp"
mkdir -p "$TMPDIR" "$project/.state/home"

if [[ $(findmnt -n -o FSTYPE -T "$TMPDIR") == tmpfs ]]; then
    echo "Refusing tmpfs TMPDIR: $TMPDIR" >&2
    exit 1
fi

if [[ -f "$project/.state/controller.env" ]]; then
    set -a
    # shellcheck source=/dev/null
    source "$project/.state/controller.env"
    set +a
fi

xdg_default=${XDG_CONFIG_HOME:-$HOME/.config}
if [[ -n ${OSC_CONFIG:-} ]]; then
    config=$OSC_CONFIG
elif [[ -r $xdg_default/osc/oscrc ]]; then
    config=$xdg_default/osc/oscrc
else
    config=$HOME/.oscrc
fi

config=$(realpath "$config")
if [[ ! -r $config ]]; then
    echo "Readable osc configuration not found: $config" >&2
    exit 1
fi

xdg_config=$(realpath -m "${XDG_CONFIG_HOME:-$(dirname "$(dirname "$config")")}")
export HOME="$project/.state/home"
export XDG_CONFIG_HOME="$xdg_config"
export OSC_CONFIG="$config"
export PYTHONUNBUFFERED=1

cd "$project"
if [[ $# -eq 0 ]]; then
    set -- verify-env
fi

exec python3 tools/obs_controller.py "$@"
