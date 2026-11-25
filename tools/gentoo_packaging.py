#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Dmitriy Volkov <dennshield@duck.com>
# SPDX-License-Identifier: GPL-2.0-only OR GPL-3.0-only OR LicenseRef-KDE-Accepted-GPL

"""Deterministic Gentoo Portage EAPI 8 overlay emitter for the Continuity stack."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import sys

try:
    from tools.reproduce_stack import verify_lockfile
except ImportError:
    from reproduce_stack import verify_lockfile

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EBUILD_VERSION = "27.03.70_p1"
DEFAULT_REPO_NAME = "kdeconnect-continuity"

EBUILD_VERSION_RE = re.compile(
    r"^[0-9]+(?:\.[0-9]+)*(?:[a-z])?(?:_(?:alpha|beta|pre|rc|p)[0-9]*)*(?:-r[0-9]+)?$"
)
REPO_NAME_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]*$")
MANIFEST_LINE_RE = re.compile(
    r"^DIST\s+(\S+)\s+(\d+)\s+BLAKE2B\s+([0-9a-f]{128})\s+SHA512\s+([0-9a-f]{128})$"
)

LAYOUT_CONF_TEXT = (
    "# SPDX-FileCopyrightText: 2026 Dmitriy Volkov <dennshield@duck.com>\n"
    "# SPDX-License-Identifier: GPL-2.0-only OR GPL-3.0-only OR LicenseRef-KDE-Accepted-GPL\n\n"
    "masters = gentoo\n"
    "thin-manifests = true\n"
    "sign-manifests = false\n"
    "profile-formats = portage-2\n"
    "cache-formats = md5-dict\n"
)

PACKAGE_MASK_TEXT = (
    "# SPDX-FileCopyrightText: 2026 Dmitriy Volkov <dennshield@duck.com>\n"
    "# SPDX-License-Identifier: GPL-2.0-only OR GPL-3.0-only OR LicenseRef-KDE-Accepted-GPL\n\n"
    "# Mask the standard ::gentoo kde-misc/kdeconnect ebuild in /etc/portage/package.mask/\n"
    "# if you want Portage to resolve only the kdeconnect-continuity overlay package:\n"
    "#\n"
    "#   echo \"kde-misc/kdeconnect::gentoo\" | sudo tee /etc/portage/package.mask/kdeconnect-gentoo\n"
    "#\n"
    "# Kept commented inside the repository profile so Portage does not reject ::repo\n"
    "# syntax or mask the overlay's own kde-misc/kdeconnect ebuild across configurations.\n"
    "# kde-misc/kdeconnect::gentoo\n"
)


def _validate_ebuild_version(ebuild_version: str) -> str:
    if not isinstance(ebuild_version, str) or not EBUILD_VERSION_RE.match(ebuild_version):
        raise ValueError(f"Invalid Gentoo ebuild version: {ebuild_version!r}")
    return ebuild_version


def _validate_repo_name(repo_name: str) -> str:
    if not isinstance(repo_name, str) or not REPO_NAME_RE.match(repo_name):
        raise ValueError(f"Invalid Portage repo_name: {repo_name!r}")
    return repo_name


def _ensure_not_symlink(path: Path, label: str) -> None:
    if path.is_symlink():
        raise ValueError(f"Refusing symlink {label}: {path}")


def extract_manifest_dist_line(manifest_path: Path, distfile_name: str) -> str | None:
    """Return a matching GLEP-74 DIST line for distfile_name from an existing Manifest."""
    _ensure_not_symlink(manifest_path, "Manifest")
    if not manifest_path.is_file():
        return None
    for lineno, raw_line in enumerate(manifest_path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        match = MANIFEST_LINE_RE.match(line)
        if not match:
            raise ValueError(f"Invalid GLEP-74 Manifest line {manifest_path}:{lineno}: {line!r}")
        fname, size_str, b2b, s512 = match.groups()
        if fname == distfile_name:
            return f"DIST {fname} {size_str} BLAKE2B {b2b} SHA512 {s512}"
    return None


def build_manifest_dist_line(distfile_path: Path, expected_name: str) -> str:
    """Compute a GLEP-74 DIST line (BLAKE2B + SHA512) for a local distfile."""
    _ensure_not_symlink(distfile_path, "distfile")
    if not distfile_path.is_file():
        raise FileNotFoundError(f"Distfile not found: {distfile_path}")
    if distfile_path.name != expected_name:
        raise ValueError(
            f"Distfile name mismatch: expected {expected_name!r}, got {distfile_path.name!r}"
        )
    b2b = hashlib.blake2b(digest_size=64)
    s512 = hashlib.sha512()
    size = 0
    with distfile_path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1048576), b""):
            b2b.update(chunk)
            s512.update(chunk)
            size += len(chunk)
    return f"DIST {expected_name} {size} BLAKE2B {b2b.hexdigest()} SHA512 {s512.hexdigest()}"


def render_gentoo_ebuild(upstream_url: str, base_sha: str, patch_relpaths: list[str]) -> str:
    """Render an EAPI 8 kde-misc/kdeconnect ebuild matching ::gentoo Qt6/KF6 atoms."""
    base_url = upstream_url.removesuffix(".git")
    src_uri = f"{base_url}/-/archive/{base_sha}/kdeconnect-kde-{base_sha}.tar.bz2"
    patch_lines = "\n".join(f'\t"${{FILESDIR}}/patches/{rel}"' for rel in patch_relpaths)
    return (
        "# Copyright 2026 Gentoo Authors\n"
        "# SPDX-FileCopyrightText: 2026 Dmitriy Volkov <dennshield@duck.com>\n"
        "# SPDX-License-Identifier: GPL-2.0-only OR GPL-3.0-only OR LicenseRef-KDE-Accepted-GPL\n"
        f"# Auto-generated from verified stack.lock.json (base_sha={base_sha})\n\n"
        "EAPI=8\n\n"
        'ECM_TEST="forceoptional"\n'
        "KFMIN=6.3.0\n"
        "QTMIN=6.9.0\n"
        "inherit ecm flag-o-matic kde.org xdg\n\n"
        'DESCRIPTION="KDE Connect with 6-plugin Continuity Stack"\n'
        'HOMEPAGE="https://kdeconnect.kde.org/"\n'
        f'SRC_URI="{src_uri}"\n'
        f'S="${{WORKDIR}}/kdeconnect-kde-{base_sha}"\n\n'
        'LICENSE="GPL-2+"\n'
        'SLOT="6"\n'
        'KEYWORDS="amd64 ~arm64"\n'
        'IUSE="bluetooth +pulseaudio telephony X"\n'
        'REQUIRED_USE="pulseaudio"\n\n'
        'COMMON_DEPEND="\n'
        "\t>=dev-libs/libei-1.6.0\n"
        "\tdev-libs/openssl:=\n"
        "\t>=dev-libs/wayland-1.15.0\n"
        "\t>=dev-qt/qtbase-${QTMIN}:6=[dbus,gui,network,wayland,widgets,X?]\n"
        "\t>=dev-qt/qtdeclarative-${QTMIN}:6[widgets]\n"
        "\t>=dev-qt/qtmultimedia-${QTMIN}:6[qml]\n"
        "\t>=kde-frameworks/kcmutils-${KFMIN}:6\n"
        "\t>=kde-frameworks/kcolorscheme-${KFMIN}:6\n"
        "\t>=kde-frameworks/kconfig-${KFMIN}:6[qml]\n"
        "\t>=kde-frameworks/kconfigwidgets-${KFMIN}:6\n"
        "\t>=kde-frameworks/kcoreaddons-${KFMIN}:6\n"
        "\t>=kde-frameworks/kcrash-${KFMIN}:6\n"
        "\t>=kde-frameworks/kdbusaddons-${KFMIN}:6\n"
        "\t>=kde-frameworks/kguiaddons-${KFMIN}:6\n"
        "\t>=kde-frameworks/ki18n-${KFMIN}:6\n"
        "\t>=kde-frameworks/kiconthemes-${KFMIN}:6\n"
        "\t>=kde-frameworks/kio-${KFMIN}:6\n"
        "\t>=kde-frameworks/kirigami-${KFMIN}:6\n"
        "\t>=kde-frameworks/kitemmodels-${KFMIN}:6\n"
        "\t>=kde-frameworks/knotifications-${KFMIN}:6\n"
        "\t>=kde-frameworks/kpackage-${KFMIN}:6\n"
        "\t>=kde-frameworks/kpeople-${KFMIN}:6\n"
        "\t>=kde-frameworks/kservice-${KFMIN}:6\n"
        "\t>=kde-frameworks/kstatusnotifieritem-${KFMIN}:6\n"
        "\t>=kde-frameworks/kwidgetsaddons-${KFMIN}:6\n"
        "\t>=kde-frameworks/kwindowsystem-${KFMIN}:6\n"
        "\t>=kde-frameworks/qqc2-desktop-style-${KFMIN}:6\n"
        "\t>=kde-frameworks/solid-${KFMIN}:6\n"
        "\t>=media-libs/pulseaudio-qt-1.4:=\n"
        "\tmedia-libs/libpulse\n"
        "\tmedia-libs/gstreamer:1.0\n"
        "\tmedia-libs/gst-plugins-base:1.0\n"
        "\tsys-apps/dbus\n"
        "\tbluetooth? (\n"
        "\t\t>=dev-qt/qtconnectivity-${QTMIN}:6[bluetooth]\n"
        "\t\tnet-wireless/bluez\n"
        "\t)\n"
        "\ttelephony? ( >=kde-frameworks/modemmanager-qt-${KFMIN}:6 )\n"
        "\tX? (\n"
        "\t\tx11-libs/libfakekey\n"
        "\t\tx11-libs/libX11\n"
        "\t\tx11-libs/libXtst\n"
        "\t\tx11-libs/libxkbcommon\n"
        "\t)\n"
        '"\n'
        'DEPEND="${COMMON_DEPEND}\n'
        "\tdev-libs/wayland-protocols\n"
        '"\n'
        'RDEPEND="${COMMON_DEPEND}\n'
        "\tdev-libs/kirigami-addons:6\n"
        "\t>=dev-qt/qt5compat-${QTMIN}:6[qml]\n"
        "\t>=dev-qt/qttools-${QTMIN}:6[qdbus]\n"
        "\t>=kde-frameworks/kdeclarative-${KFMIN}:6\n"
        "\t>=kde-frameworks/kdnssd-${KFMIN}:6\n"
        "\tkde-plasma/libplasma:6\n"
        "\tmedia-libs/gst-plugins-bad:1.0\n"
        "\tmedia-libs/gst-plugins-good:1.0\n"
        "\tmedia-plugins/gst-plugins-libav:1.0\n"
        "\t>=media-video/pipewire-1.0[gstreamer,sound-server]\n"
        "\tnet-fs/sshfs\n"
        '"\n'
        'BDEPEND="\n'
        "\t>=dev-qt/qtbase-${QTMIN}:6[wayland]\n"
        "\tdev-util/wayland-scanner\n"
        "\t>=kde-frameworks/extra-cmake-modules-${KFMIN}:0\n"
        "\tvirtual/pkgconfig\n"
        '"\n\n'
        "PATCHES=(\n"
        f"{patch_lines}\n"
        ")\n\n"
        "src_prepare() {\n"
        "\tecm_src_prepare\n"
        "\tuse X || cmake_comment_add_subdirectory -f plugins shareinputdevices\n"
        "}\n\n"
        "src_configure() {\n"
        "\tfilter-lto\n"
        "\tlocal mycmakeargs=(\n"
        "\t\t-DINSTALL_UFW_APPLICATION_RULE=OFF\n"
        "\t\t-DBLUETOOTH_ENABLED=$(usex bluetooth)\n"
        "\t\t-DWITH_PULSEAUDIO=ON\n"
        "\t\t$(cmake_use_find_package telephony KF6ModemManagerQt)\n"
        "\t\t-DWITH_X11=$(usex X)\n"
        "\t)\n"
        "\tecm_src_configure\n"
        "}\n"
    )


def emit_gentoo_overlay(
    repo_root: Path,
    output_dir: Path,
    ebuild_version: str = DEFAULT_EBUILD_VERSION,
    repo_name: str = DEFAULT_REPO_NAME,
    preserve_manifest_from: Path | None = None,
    distfile: Path | None = None,
    lock_path: Path | None = None,
    registry_path: Path | None = None,
    include_repo_metadata: bool = True,
    dry_run: bool = False,
) -> dict:
    """Verify stack.lock.json and emit a Gentoo Portage EAPI 8 overlay directory."""
    repo_root = Path(repo_root).resolve()
    output_dir = Path(output_dir)
    _ensure_not_symlink(output_dir, "output_dir")
    _validate_ebuild_version(ebuild_version)
    _validate_repo_name(repo_name)

    lock_data, issues = verify_lockfile(repo_root, lock_path=lock_path, registry_path=registry_path)
    if issues:
        raise ValueError("Lockfile verification failed:\n  - " + "\n  - ".join(issues))

    desktop_lock = lock_data["targets"]["desktop"]
    base_sha = desktop_lock["base_sha"]
    upstream_url = desktop_lock["upstream_url"]
    distfile_name = f"kdeconnect-kde-{base_sha}.tar.bz2"

    staged_patches: list[tuple[str, Path, str]] = []
    patch_relpaths: list[str] = []
    idx = 1
    for p_entry in desktop_lock["plugins"]:
        pid = p_entry["id"]
        for rec in p_entry["patches"]:
            posix = PurePosixPath(rec["path"])
            src_patch = (repo_root / posix).resolve()
            dest_name = f"{idx:03d}-{posix.name}"
            staged_patches.append((pid, src_patch, dest_name))
            patch_relpaths.append(f"{pid}/{dest_name}")
            idx += 1

    manifest_line: str | None = None
    if distfile is not None:
        manifest_line = build_manifest_dist_line(Path(distfile), distfile_name)
    elif preserve_manifest_from is not None:
        manifest_line = extract_manifest_dist_line(Path(preserve_manifest_from), distfile_name)

    resolved_out = output_dir.resolve()
    pkg_dir = resolved_out / "kde-misc" / "kdeconnect"
    ebuild_path = pkg_dir / f"kdeconnect-{ebuild_version}.ebuild"
    manifest_path = pkg_dir / "Manifest"
    src_uri = f"{upstream_url.removesuffix('.git')}/-/archive/{base_sha}/{distfile_name}"

    if not dry_run:
        output_dir.mkdir(parents=True, exist_ok=True)
        for rel_check in (
            resolved_out / "metadata",
            resolved_out / "profiles",
            resolved_out / "kde-misc",
            pkg_dir,
            pkg_dir / "files",
            pkg_dir / "files" / "patches",
        ):
            _ensure_not_symlink(rel_check, "overlay directory")

        if include_repo_metadata:
            layout_path = resolved_out / "metadata" / "layout.conf"
            repo_name_path = resolved_out / "profiles" / "repo_name"
            mask_path = resolved_out / "profiles" / "package.mask" / "gentoo-kdeconnect"
            layout_path.parent.mkdir(parents=True, exist_ok=True)
            repo_name_path.parent.mkdir(parents=True, exist_ok=True)
            mask_path.parent.mkdir(parents=True, exist_ok=True)
            layout_path.write_text(LAYOUT_CONF_TEXT, encoding="utf-8")
            repo_name_path.write_text(f"{repo_name}\n", encoding="utf-8")
            mask_path.write_text(PACKAGE_MASK_TEXT, encoding="utf-8")

        pkg_dir.mkdir(parents=True, exist_ok=True)
        patches_dst = pkg_dir / "files" / "patches"
        if patches_dst.exists():
            shutil.rmtree(patches_dst)
        patches_dst.mkdir(parents=True, exist_ok=True)

        for pid, src_patch, dest_name in staged_patches:
            sub = patches_dst / pid
            sub.mkdir(parents=True, exist_ok=True)
            dst_file = sub / dest_name
            shutil.copyfile(src_patch, dst_file)
            dst_file.chmod(0o644)

        for old_ebuild in pkg_dir.glob("kdeconnect-*.ebuild"):
            _ensure_not_symlink(old_ebuild, "existing ebuild")
            if old_ebuild.name != ebuild_path.name:
                old_ebuild.unlink()

        ebuild_path.write_text(
            render_gentoo_ebuild(upstream_url, base_sha, patch_relpaths),
            encoding="utf-8",
        )
        ebuild_path.chmod(0o644)

        _ensure_not_symlink(manifest_path, "Manifest")
        if manifest_line is not None:
            manifest_path.write_text(manifest_line + "\n", encoding="utf-8")
            manifest_path.chmod(0o644)
        elif preserve_manifest_from is not None and manifest_path.is_file():
            manifest_path.unlink()

    return {
        "repo_name": repo_name,
        "ebuild_version": ebuild_version,
        "base_sha": base_sha,
        "src_uri": src_uri,
        "output_dir": str(resolved_out),
        "ebuild_path": str(ebuild_path),
        "patch_count": len(staged_patches),
        "patches": patch_relpaths,
        "manifest_path": str(manifest_path) if manifest_line is not None else None,
        "manifest_preserved": manifest_line is not None,
        "include_repo_metadata": include_repo_metadata,
        "dry_run": dry_run,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Emit a verified Gentoo Portage EAPI 8 overlay for KDE Connect Continuity"
    )
    parser.add_argument("--repo-root", type=Path, default=ROOT, help="Harness repository root")
    parser.add_argument("--output-dir", type=Path, required=True, help="Target Portage overlay root")
    parser.add_argument("--ebuild-version", default=DEFAULT_EBUILD_VERSION)
    parser.add_argument("--repo-name", default=DEFAULT_REPO_NAME)
    parser.add_argument("--preserve-manifest-from", type=Path, help="Optional Manifest to copy matching DIST line")
    parser.add_argument("--distfile", type=Path, help="Optional local tarball to hash into GLEP-74 Manifest")
    parser.add_argument("--lock", type=Path, help="Optional path to stack.lock.json")
    parser.add_argument("--registry", type=Path, help="Optional path to plugins.toml")
    parser.add_argument(
        "--package-only",
        action="store_true",
        help="Write only kde-misc/kdeconnect without modifying overlay metadata/profiles",
    )
    parser.add_argument("--dry-run", action="store_true", help="Verify lockfile without writing overlay files")
    args = parser.parse_args()

    try:
        res = emit_gentoo_overlay(
            repo_root=args.repo_root,
            output_dir=args.output_dir,
            ebuild_version=args.ebuild_version,
            repo_name=args.repo_name,
            preserve_manifest_from=args.preserve_manifest_from,
            distfile=args.distfile,
            lock_path=args.lock,
            registry_path=args.registry,
            include_repo_metadata=not args.package_only,
            dry_run=args.dry_run,
        )
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(res, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
