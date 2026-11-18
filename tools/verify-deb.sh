#!/bin/bash
# SPDX-FileCopyrightText: 2026 Dmitriy Volkov <dennshield@duck.com>
# SPDX-License-Identifier: GPL-2.0-only OR GPL-3.0-only OR LicenseRef-KDE-Accepted-GPL

set -euo pipefail
umask 077

if [[ $# -lt 2 || $# -gt 3 ]]; then
    echo "Usage: $0 <kdeconnect-debs-dir> <libei-debs-dir> [expected-version]" >&2
    exit 2
fi

project=$(cd "$(dirname "$0")/.." && pwd -P)
export TMPDIR="${TMPDIR:-$project/.state/tmp}"
mkdir -p "$TMPDIR"
if [[ $(findmnt -n -o FSTYPE -T "$TMPDIR") == tmpfs ]]; then
    echo "Refusing tmpfs TMPDIR: $TMPDIR" >&2
    exit 1
fi

unset OSC_CONFIG SSH_AUTH_SOCK SSH_AGENT_PID GIT_SSH_COMMAND

if [[ -z ${DOCKER_HOST:-} ]]; then
    runtime_dir="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
    if [[ -S $runtime_dir/docker.sock ]]; then
        export DOCKER_HOST="unix://$runtime_dir/docker.sock"
    fi
fi

debs_dir=$(realpath "$1")
libei_dir=$(realpath "$2")
expected_version=${3:-}

[[ -d $debs_dir ]]
[[ -d $libei_dir ]]

# Static host-side verification before container execution.
if [[ -n $expected_version ]]; then
    python3 "$project/tools/upstream_sync.py" verify-debs "$debs_dir" "$expected_version"
else
    python3 "$project/tools/upstream_sync.py" verify-debs "$debs_dir"
fi
python3 "$project/tools/upstream_sync.py" verify-debs "$libei_dir"

image_tag="kdeconnect-verifier:26.04"
dockerfile_sha=$(sha256sum "$project/obs/verifier.Dockerfile" | awk '{print $1}')
rebuild_image=0
if ! docker image inspect "$image_tag" >/dev/null 2>&1; then
    rebuild_image=1
else
    created_iso=$(docker image inspect --format '{{.Created}}' "$image_tag")
    created_epoch=$(date -d "$created_iso" +%s 2>/dev/null || echo 0)
    now_epoch=$(date +%s)
    img_sha=$(docker image inspect --format '{{ index .Config.Labels "org.kdeconnect.verifier.dockerfile-sha256" }}' "$image_tag" 2>/dev/null || true)
    if (( now_epoch - created_epoch > 604800 )) || [[ $img_sha != "$dockerfile_sha" ]]; then
        rebuild_image=1
    fi
fi

if [[ $rebuild_image -eq 1 ]]; then
    docker build --pull --memory=1g \
      --label "org.kdeconnect.verifier.dockerfile-sha256=$dockerfile_sha" \
      --tag "$image_tag" \
      --file "$project/obs/verifier.Dockerfile" "$project/obs"
    docker image prune -f >/dev/null 2>&1 || true
    docker builder prune -f --filter "until=168h" >/dev/null 2>&1 || true
fi

docker run --rm \
  --network none \
  --security-opt=no-new-privileges:true \
  --cap-drop=ALL \
  --cap-add=CHOWN \
  --cap-add=DAC_OVERRIDE \
  --cap-add=FOWNER \
  --cap-add=SETUID \
  --cap-add=SETGID \
  --cpus=1.0 \
  --memory=1g \
  --memory-swap=1g \
  --pids-limit=256 \
  --shm-size=128m \
  --mount "type=bind,src=$debs_dir,dst=/input/debs,readonly" \
  --mount "type=bind,src=$libei_dir,dst=/input/libei,readonly" \
  "$image_tag" bash -lc '
set -euo pipefail
dpkg -i /input/libei/libei1_*.deb /input/debs/kdeconnect-libs_*.deb /input/debs/qml6-module-org-kde-kdeconnect_*.deb /input/debs/kdeconnect_*.deb
so_path=$(find /usr/lib -name "kdeconnect_screenstream.so" | head -n 1)
test -n "$so_path"
test -x /usr/bin/kdeconnect-screenstream-viewer
test -f /usr/share/kdeconnect/kdeconnect_screenstream_config.qml

needed=$(readelf -d /usr/bin/kdeconnect-screenstream-viewer | awk "/NEEDED/ {print $NF}")
echo "$needed"
if echo "$needed" | grep -qi "Qt6Multimedia"; then
    echo "ERROR: Qt6Multimedia must not be linked" >&2
    exit 1
fi
echo "$needed" | grep -q "libgstreamer-1.0.so.0"
echo "$needed" | grep -q "libQt6Widgets.so.6"

gst-inspect-1.0 appsrc h264parse avdec_h264 audioconvert audioresample autoaudiosink >/dev/null
dpkg -P kdeconnect qml6-module-org-kde-kdeconnect kdeconnect-libs libei1
echo "VERIFY_DEB_OK"
'