#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Dmitriy Volkov <dennshield@duck.com>
# SPDX-License-Identifier: GPL-2.0-only OR GPL-3.0-only OR LicenseRef-KDE-Accepted-GPL

"""Deterministic Debian/OBS source bundle builder for desktop and offline Android builds."""

import argparse
import email.parser
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import tarfile
import urllib.request
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAINTAINER = "Dmitriy Volkov <dennshield@duck.com>"

LIBEI_VERSION = "1.6.0"
LIBEI_URL = "https://gitlab.freedesktop.org/libinput/libei/-/archive/1.6.0/libei-1.6.0.tar.bz2"
LIBEI_SHA256 = "5ed6078fa63afd554cc04b1001675615da0ed8fe23b80492ab63403140b5a830"
LIBEI_MUNIT_URL = "https://archive.ubuntu.com/ubuntu/pool/main/libe/libei/libei_1.5.0.orig-munit.tar.bz2"
LIBEI_MUNIT_SHA256 = "2407d9d6b6bb99c4c5c7dc0ba25b760faff952c3374b68c78802e8de91b610e9"
LIBEI_DEBIAN_URL = "https://archive.ubuntu.com/ubuntu/pool/main/libe/libei/libei_1.5.0-3.debian.tar.xz"
LIBEI_DEBIAN_SHA256 = "be230ad4c73e43ef033e785580ed8d4fe8088cc4c61f72688febedee27b3aa4d"

SPDX_STRIP_FILES = {"kdeconnect.qmlfiles", "qmldeps.overrides", "kdeconnect.maintscript"}
SPDX_COMMENT_LINE_RE = re.compile(r"^\s*#\s*SPDX-(?:FileCopyrightText|License-Identifier):.*$")
XML_COMMENT_LINE_RE = re.compile(r"^\s*<!--.*?-->\s*$")


def strip_spdx_comments(text: str) -> str:
    """Remove SPDX comment lines from comment-sensitive debhelper control files."""
    kept = [line for line in text.splitlines() if not SPDX_COMMENT_LINE_RE.match(line)]
    return "\n".join(kept).lstrip("\n") + "\n" if kept else ""


def strip_xml_comments(text: str) -> str:
    """Strip single-line XML comment headers before sending metadata to OBS API."""
    kept = [line for line in text.splitlines() if not XML_COMMENT_LINE_RE.match(line)]
    return "\n".join(kept).strip() + "\n"


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def parse_control_metadata(control_text: str) -> dict:
    """Extract source and binary package fields from Debian control text."""
    clean_lines = [line for line in control_text.splitlines() if not line.startswith("#")]
    paragraphs = [p for p in re.split(r"\n\s*\n", "\n".join(clean_lines).strip()) if p.strip()]
    if not paragraphs:
        raise ValueError("debian/control contains no paragraphs")

    src = email.parser.Parser().parsestr(paragraphs[0])
    source_name = (src.get("Source") or "").strip()
    source_section = (src.get("Section") or "misc").strip()
    standards_version = (src.get("Standards-Version") or "4.7.2").strip()
    homepage = (src.get("Homepage") or "").strip()
    build_depends = " ".join((src.get("Build-Depends") or "").split())
    if not source_name or not build_depends:
        raise ValueError("debian/control source paragraph missing Source or Build-Depends")

    binaries: list[str] = []
    package_list: list[str] = []
    for raw_para in paragraphs[1:]:
        pkg = email.parser.Parser().parsestr(raw_para)
        name = (pkg.get("Package") or "").strip()
        if not name:
            continue
        sec = (pkg.get("Section") or source_section).strip()
        prio = (pkg.get("Priority") or "optional").strip()
        arch = (pkg.get("Architecture") or "any").strip()
        binaries.append(name)
        package_list.append(f" {name} deb {sec} {prio} arch={arch}")

    return {
        "source": source_name,
        "binaries": binaries,
        "package_list": package_list,
        "standards_version": standards_version,
        "homepage": homepage,
        "build_depends": build_depends,
    }


def write_dsc_and_manifest(
    output_dir: Path,
    source_name: str,
    binaries: list[str],
    package_list: list[str],
    architecture: str,
    version: str,
    maintainer: str,
    homepage: str,
    standards_version: str,
    build_depends: str,
    archive_paths: list[Path],
    dsc_filename: str,
) -> tuple[Path, Path]:
    """Emit a Format 3.0 (quilt) .dsc file and matching SHA-256 manifest.json."""
    output_dir.mkdir(parents=True, exist_ok=True)
    file_records = []
    lines = [
        "Format: 3.0 (quilt)",
        f"Source: {source_name}",
        f"Binary: {', '.join(binaries)}",
        f"Architecture: {architecture}",
        f"Version: {version}",
        f"Maintainer: {maintainer}",
    ]
    if homepage:
        lines.append(f"Homepage: {homepage}")
    lines.extend([
        f"Standards-Version: {standards_version}",
        f"Build-Depends: {build_depends}",
        "Package-List:",
        *package_list,
    ])

    checksum_entries = []
    for archive_path in archive_paths:
        data = archive_path.read_bytes()
        entry = {
            "filename": archive_path.name,
            "size": len(data),
            "md5": hashlib.md5(data).hexdigest(),
            "sha1": hashlib.sha1(data).hexdigest(),
            "sha256": _sha256_bytes(data),
        }
        checksum_entries.append(entry)
        file_records.append(
            {"filename": archive_path.name, "size": len(data), "sha256": entry["sha256"]}
        )

    lines.append("Checksums-Sha1:")
    lines.extend(f" {e['sha1']} {e['size']} {e['filename']}" for e in checksum_entries)
    lines.append("Checksums-Sha256:")
    lines.extend(f" {e['sha256']} {e['size']} {e['filename']}" for e in checksum_entries)
    lines.append("Files:")
    lines.extend(f" {e['md5']} {e['size']} {e['filename']}" for e in checksum_entries)

    dsc_path = output_dir / dsc_filename
    dsc_bytes = ("\n".join(lines) + "\n").encode("utf-8")
    dsc_path.write_bytes(dsc_bytes)
    file_records.append(
        {"filename": dsc_filename, "size": len(dsc_bytes), "sha256": _sha256_bytes(dsc_bytes)}
    )

    manifest_path = output_dir / "manifest.json"
    manifest = {
        "package": source_name,
        "version": version,
        "dsc": dsc_filename,
        "files": file_records,
    }
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return dsc_path, manifest_path


def verify_bundle_manifest(bundle_dir: Path) -> dict:
    """Verify every file recorded in manifest.json exists with expected size and SHA-256."""
    manifest_path = bundle_dir / "manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"Missing manifest.json in {bundle_dir}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for entry in manifest.get("files", []):
        target = bundle_dir / entry["filename"]
        if not target.is_file():
            raise FileNotFoundError(f"Missing bundle file: {target}")
        data = target.read_bytes()
        if len(data) != entry["size"] or _sha256_bytes(data) != entry["sha256"]:
            raise ValueError(f"Checksum or size mismatch for {target}")
    return manifest


def _add_file_bytes(tf: tarfile.TarFile, arcname: str, data: bytes, mode: int, mtime: int) -> None:
    info = tarfile.TarInfo(name=arcname)
    info.size = len(data)
    info.mode = mode
    info.mtime = mtime
    info.uid = 0
    info.gid = 0
    info.uname = "root"
    info.gname = "root"
    tf.addfile(info, io.BytesIO(data))


def _write_directory_tar_xz(
    src_dir: Path,
    dest_path: Path,
    top_dir: str,
    mtime: int,
    include_paths: list[Path] | None = None,
    executable_relpaths: set[str] | None = None,
    exclude_top_dirs: set[str] | None = None,
) -> None:
    exec_set = executable_relpaths or set()
    excluded_top = exclude_top_dirs or set()
    if include_paths is None:
        candidates = sorted(src_dir.rglob("*"), key=lambda p: p.relative_to(src_dir).as_posix())
    else:
        candidates = sorted(include_paths, key=lambda p: p.relative_to(src_dir).as_posix())

    with tarfile.open(dest_path, "w:xz") as tf:
        for path in candidates:
            rel = path.relative_to(src_dir).as_posix()
            parts = path.relative_to(src_dir).parts
            if ".git" in parts or "__pycache__" in parts or path.suffix in (".pyc", ".pyo"):
                continue
            if parts and parts[0] in excluded_top:
                continue
            if path.is_dir() and not path.is_symlink():
                continue
            arcname = f"{top_dir}/{rel}" if top_dir else rel
            if path.is_symlink():
                info = tarfile.TarInfo(name=arcname)
                info.type = tarfile.SYMTYPE
                info.linkname = os.readlink(path)
                info.mtime = mtime
                info.mode = 0o777
                info.uid = 0
                info.gid = 0
                info.uname = "root"
                info.gname = "root"
                tf.addfile(info)
                continue
            data = path.read_bytes()
            is_exec = rel in exec_set or bool(path.stat().st_mode & 0o111)
            _add_file_bytes(tf, arcname, data, 0o755 if is_exec else 0o644, mtime)


def _pack_debian_overlay_xz(
    overlay_dir: Path,
    dest_path: Path,
    source_name: str,
    version: str,
    changelog_note: str,
    copyright_text: str,
    mtime: int,
) -> dict:
    files_map: dict[str, tuple[bytes, int]] = {}
    for path in sorted(overlay_dir.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(overlay_dir).as_posix()
        text = path.read_text(encoding="utf-8")
        if rel in SPDX_STRIP_FILES:
            text = strip_spdx_comments(text)
        mode = 0o755 if rel == "rules" else 0o644
        files_map[f"debian/{rel}"] = (text.encode("utf-8"), mode)

    stamp = format_datetime(datetime.fromtimestamp(mtime, tz=timezone.utc))
    changelog = (
        f"{source_name} ({version}) resolute; urgency=medium\n\n"
        f"  * {changelog_note}\n\n"
        f" -- {MAINTAINER}  {stamp}\n"
    )
    files_map["debian/changelog"] = (changelog.encode("utf-8"), 0o644)
    files_map["debian/source/format"] = (b"3.0 (quilt)\n", 0o644)
    files_map["debian/copyright"] = (copyright_text.encode("utf-8"), 0o644)

    with tarfile.open(dest_path, "w:xz") as tf:
        for arcname in sorted(files_map):
            data, mode = files_map[arcname]
            _add_file_bytes(tf, arcname, data, mode, mtime)

    control_text = files_map["debian/control"][0].decode("utf-8")
    return parse_control_metadata(control_text)


def _kdeconnect_copyright() -> str:
    return (
        "Format: https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/\n"
        "Upstream-Name: kdeconnect-kde\n"
        "Source: https://invent.kde.org/network/kdeconnect-kde\n\n"
        "Files: *\n"
        "Copyright: 2013-2026 KDE Connect Contributors\n"
        "License: GPL-2.0-only OR GPL-3.0-only\n"
        " On Debian systems, the complete text of the GNU General Public License\n"
        " versions 2 and 3 can be found in /usr/share/common-licenses/GPL-2 and\n"
        " /usr/share/common-licenses/GPL-3.\n\n"
        "Files: debian/*\n"
        "Copyright: 2014 David Suarez <david.sephirot@gmail.com>\n"
        "           2022-2024 Debian Qt/KDE Maintainers <debian-qt-kde@lists.debian.org>\n"
        f"           2026 {MAINTAINER}\n"
        "License: GPL-2.0-only OR GPL-3.0-only\n"
        " On Debian systems, the complete text of the GNU General Public License\n"
        " versions 2 and 3 can be found in /usr/share/common-licenses/GPL-2 and\n"
        " /usr/share/common-licenses/GPL-3.\n"
    )


def _android_probe_copyright() -> str:
    return (
        "Format: https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/\n"
        "Upstream-Name: kdeconnect-android-probe\n"
        "Source: https://github.com/lmdeuser/kdeconnect-plugins\n\n"
        "Files: *\n"
        f"Copyright: 2026 {MAINTAINER}\n"
        "License: GPL-2.0-only OR GPL-3.0-only\n"
        " On Debian systems, the complete text of the GNU General Public License\n"
        " versions 2 and 3 can be found in /usr/share/common-licenses/GPL-2 and\n"
        " /usr/share/common-licenses/GPL-3.\n"
    )


def _inject_lintian_build_dep(control_text: str) -> str:
    if re.search(r"(?m)^\s*lintian\b", control_text):
        return control_text
    lines = control_text.splitlines()
    out: list[str] = []
    inserted = False
    for line in lines:
        out.append(line)
        if not inserted and line.startswith("Build-Depends:"):
            out.append(" lintian,")
            inserted = True
    return "\n".join(out) + "\n"


def _rewrite_libei_control(control_text: str) -> str:
    lines = _inject_lintian_build_dep(control_text).splitlines()
    out: list[str] = []
    for line in lines:
        if line.startswith("Maintainer:"):
            out.append(f"Maintainer: {MAINTAINER}")
        elif line.startswith("Uploaders:"):
            out.append("XSBC-Original-Maintainer: Ubuntu Developers <ubuntu-devel-discuss@lists.ubuntu.com>")
        else:
            out.append(line)
    return "\n".join(out) + "\n"


def _rewrite_libei_rules(rules_text: str, upstream_version: str = LIBEI_VERSION) -> str:
    updated = rules_text.replace("DPKG_GENSYMBOLS_CHECK_LEVEL = 4", "DPKG_GENSYMBOLS_CHECK_LEVEL = 1")
    extra_blocks = []
    if "override_dh_makeshlibs:" not in updated:
        extra_blocks.append(f"override_dh_makeshlibs:\n\tdh_makeshlibs -V -- -v{upstream_version}\n")
    if "override_dh_builddeb:" not in updated:
        extra_blocks.append(
            "DEB_VERSION := $(shell dpkg-parsechangelog -S Version)\n\n"
            "override_dh_builddeb:\n"
            "\tdh_builddeb\n"
            '\tlintian --fail-on error $$(find "$(CURDIR)/.." -maxdepth 1 -name "*_$(DEB_VERSION)_*.deb" | sort)\n'
        )
    if extra_blocks:
        updated = updated.rstrip() + "\n\n" + "\n".join(extra_blocks)
    return updated


def prepare_libei_debian_tar(
    raw_deb_xz: bytes,
    upstream_version: str = LIBEI_VERSION,
    obs_rev: int = 1,
    timestamp: int | None = None,
) -> tuple[bytes, dict]:
    """Update Ubuntu libei debian.tar.xz for an offline 1.6.0 OBS staging backport."""
    pkg_version = f"{upstream_version}-1~obs{obs_rev}"
    entries: dict[str, tuple[bytes, int]] = {}
    max_member_mtime = 0

    with tarfile.open(fileobj=io.BytesIO(raw_deb_xz), mode="r:xz") as tf:
        for member in tf.getmembers():
            if not member.isfile():
                continue
            max_member_mtime = max(max_member_mtime, int(member.mtime))
            data = tf.extractfile(member).read()
            entries[member.name] = (data, member.mode & 0o777)

    mtime = timestamp if timestamp is not None else max_member_mtime

    if "debian/control" not in entries or "debian/rules" not in entries or "debian/changelog" not in entries:
        raise ValueError("libei debian tarball is missing control, rules, or changelog")

    updated_control = _rewrite_libei_control(entries["debian/control"][0].decode("utf-8"))
    entries["debian/control"] = (updated_control.encode("utf-8"), 0o644)

    updated_rules = _rewrite_libei_rules(
        entries["debian/rules"][0].decode("utf-8"),
        upstream_version=upstream_version,
    )
    entries["debian/rules"] = (updated_rules.encode("utf-8"), 0o755)

    old_changelog = entries["debian/changelog"][0].decode("utf-8")
    stamp = format_datetime(datetime.fromtimestamp(mtime, tz=timezone.utc))
    new_entry = (
        f"libei ({pkg_version}) resolute; urgency=medium\n\n"
        f"  * Backport upstream libei {upstream_version} for KDE Connect staging.\n\n"
        f" -- {MAINTAINER}  {stamp}\n\n"
    )
    entries["debian/changelog"] = ((new_entry + old_changelog).encode("utf-8"), 0o644)

    out_buf = io.BytesIO()
    with tarfile.open(fileobj=out_buf, mode="w:xz") as tf:
        for name in sorted(entries):
            data, mode = entries[name]
            _add_file_bytes(tf, name, data, mode, mtime)

    meta = parse_control_metadata(updated_control)
    meta["package_version"] = pkg_version
    return out_buf.getvalue(), meta


def _download_verified(url: str, expected_sha256: str | None = None) -> bytes:
    with urllib.request.urlopen(url, timeout=60) as resp:
        data = resp.read()
    if expected_sha256 is not None and _sha256_bytes(data) != expected_sha256:
        raise RuntimeError(f"SHA-256 mismatch downloading {url}")
    return data


def build_libei_bundle(output_dir: Path, obs_rev: int = 1) -> dict:
    """Create the OBS 3.0 (quilt) source bundle for libei 1.6.0."""
    output_dir.mkdir(parents=True, exist_ok=True)
    orig_data = _download_verified(LIBEI_URL, LIBEI_SHA256)
    munit_data = _download_verified(LIBEI_MUNIT_URL, LIBEI_MUNIT_SHA256)
    deb_raw = _download_verified(LIBEI_DEBIAN_URL, LIBEI_DEBIAN_SHA256)

    orig_path = output_dir / f"libei_{LIBEI_VERSION}.orig.tar.bz2"
    munit_path = output_dir / f"libei_{LIBEI_VERSION}.orig-munit.tar.bz2"
    orig_path.write_bytes(orig_data)
    munit_path.write_bytes(munit_data)

    deb_bytes, meta = prepare_libei_debian_tar(deb_raw, upstream_version=LIBEI_VERSION, obs_rev=obs_rev)
    pkg_version = meta["package_version"]
    deb_path = output_dir / f"libei_{pkg_version}.debian.tar.xz"
    deb_path.write_bytes(deb_bytes)

    dsc_path, manifest_path = write_dsc_and_manifest(
        output_dir=output_dir,
        source_name="libei",
        binaries=meta["binaries"],
        package_list=meta["package_list"],
        architecture="any",
        version=pkg_version,
        maintainer=MAINTAINER,
        homepage=meta["homepage"],
        standards_version=meta["standards_version"],
        build_depends=meta["build_depends"],
        archive_paths=[orig_path, munit_path, deb_path],
        dsc_filename=f"libei_{pkg_version}.dsc",
    )
    return {
        "package": "libei",
        "version": pkg_version,
        "dsc": str(dsc_path),
        "manifest": str(manifest_path),
    }


def _read_kdeconnect_version(desktop_src: Path) -> str:
    cmake = (desktop_src / "CMakeLists.txt").read_text(encoding="utf-8")
    major = re.search(r'RELEASE_SERVICE_VERSION_MAJOR\s+"([^"]+)"', cmake)
    minor = re.search(r'RELEASE_SERVICE_VERSION_MINOR\s+"([^"]+)"', cmake)
    micro = re.search(r'RELEASE_SERVICE_VERSION_MICRO\s+"([^"]+)"', cmake)
    if not (major and minor and micro):
        raise ValueError(f"Could not parse RELEASE_SERVICE_VERSION_* in {desktop_src / 'CMakeLists.txt'}")
    return f"{major.group(1)}.{minor.group(1)}.{micro.group(1)}"


def build_kdeconnect_bundle(
    desktop_src: Path,
    overlay_debian_dir: Path,
    output_dir: Path,
    obs_rev: int = 1,
) -> dict:
    """Create the OBS 3.0 (quilt) source bundle for kdeconnect-kde."""
    desktop_src = desktop_src.resolve()
    release_ver = _read_kdeconnect_version(desktop_src)
    git_cmd = ["git", "-C", str(desktop_src), "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false"]
    commit = subprocess.check_output([*git_cmd, "rev-parse", "HEAD"], text=True).strip()
    mtime = int(subprocess.check_output([*git_cmd, "show", "-s", "--format=%ct", "HEAD"], text=True))
    date_stamp = datetime.fromtimestamp(mtime, tz=timezone.utc).strftime("%Y%m%d%H%M%S")

    upstream_version = f"{release_ver}+git{date_stamp}.{commit[:12]}"
    pkg_version = f"{upstream_version}-0ubuntu1~obs{obs_rev}"
    output_dir.mkdir(parents=True, exist_ok=True)

    orig_path = output_dir / f"kdeconnect_{upstream_version}.orig.tar.xz"
    _write_directory_tar_xz(
        src_dir=desktop_src,
        dest_path=orig_path,
        top_dir=f"kdeconnect-{upstream_version}",
        mtime=mtime,
        exclude_top_dirs={"debian"},
    )

    deb_path = output_dir / f"kdeconnect_{pkg_version}.debian.tar.xz"
    meta = _pack_debian_overlay_xz(
        overlay_dir=overlay_debian_dir,
        dest_path=deb_path,
        source_name="kdeconnect",
        version=pkg_version,
        changelog_note=f"Staging build from kdeconnect-kde commit {commit}.",
        copyright_text=_kdeconnect_copyright(),
        mtime=mtime,
    )

    dsc_path, manifest_path = write_dsc_and_manifest(
        output_dir=output_dir,
        source_name="kdeconnect",
        binaries=meta["binaries"],
        package_list=meta["package_list"],
        architecture="any all",
        version=pkg_version,
        maintainer=MAINTAINER,
        homepage=meta["homepage"],
        standards_version=meta["standards_version"],
        build_depends=meta["build_depends"],
        archive_paths=[orig_path, deb_path],
        dsc_filename=f"kdeconnect_{pkg_version}.dsc",
    )
    return {
        "package": "kdeconnect",
        "version": pkg_version,
        "commit": commit,
        "dsc": str(dsc_path),
        "manifest": str(manifest_path),
    }


def _resolve_dir_git_timestamp(src_dir: Path) -> int:
    proc = subprocess.run(
        [
            "git",
            "-C",
            str(src_dir),
            "-c",
            "core.hooksPath=/dev/null",
            "-c",
            "core.fsmonitor=false",
            "log",
            "-1",
            "--format=%ct",
            "--",
            ".",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    raw = proc.stdout.strip()
    if proc.returncode == 0 and raw.isdigit():
        return int(raw)
    return 0


def build_android_probe_bundle(
    probe_src: Path,
    sdk_root: Path,
    overlay_debian_dir: Path,
    output_dir: Path,
    obs_rev: int = 1,
    timestamp: int | None = None,
) -> dict:
    """Create the OBS 3.0 (quilt) source bundle for prototype/android-probe with offline SDK slice."""
    required_relpaths = [
        "platforms/android-35/android.jar",
        "build-tools/36.0.0/aapt2",
        "build-tools/36.0.0/d8",
        "build-tools/36.0.0/zipalign",
        "build-tools/36.0.0/apksigner",
        "build-tools/36.0.0/lib/d8.jar",
        "build-tools/36.0.0/lib/apksigner.jar",
        "build-tools/36.0.0/lib64/libc++.so",
    ]
    sdk_files: list[Path] = []
    for rel in required_relpaths:
        candidate = sdk_root / rel
        if not candidate.is_file():
            raise FileNotFoundError(f"Required Android SDK tool/jar missing: {candidate}")
        sdk_files.append(candidate)

    mtime = timestamp if timestamp is not None else _resolve_dir_git_timestamp(probe_src)
    upstream_version = "0.1.0"
    pkg_version = f"{upstream_version}-0ubuntu1~obs{obs_rev}"
    output_dir.mkdir(parents=True, exist_ok=True)

    orig_path = output_dir / f"kdeconnect-android-probe_{upstream_version}.orig.tar.xz"
    _write_directory_tar_xz(
        src_dir=probe_src,
        dest_path=orig_path,
        top_dir=f"kdeconnect-android-probe-{upstream_version}",
        mtime=mtime,
    )

    sdk_path = output_dir / f"kdeconnect-android-probe_{upstream_version}.orig-sdk.tar.xz"
    _write_directory_tar_xz(
        src_dir=sdk_root,
        dest_path=sdk_path,
        top_dir="sdk",
        mtime=mtime,
        include_paths=sdk_files,
        executable_relpaths={
            "build-tools/36.0.0/aapt2",
            "build-tools/36.0.0/d8",
            "build-tools/36.0.0/zipalign",
            "build-tools/36.0.0/apksigner",
            "build-tools/36.0.0/lib64/libc++.so",
        },
    )

    deb_path = output_dir / f"kdeconnect-android-probe_{pkg_version}.debian.tar.xz"
    meta = _pack_debian_overlay_xz(
        overlay_dir=overlay_debian_dir,
        dest_path=deb_path,
        source_name="kdeconnect-android-probe",
        version=pkg_version,
        changelog_note="Remote OBS staging build of Android capture probe APK.",
        copyright_text=_android_probe_copyright(),
        mtime=mtime,
    )

    dsc_path, manifest_path = write_dsc_and_manifest(
        output_dir=output_dir,
        source_name="kdeconnect-android-probe",
        binaries=meta["binaries"],
        package_list=meta["package_list"],
        architecture="amd64",
        version=pkg_version,
        maintainer=MAINTAINER,
        homepage=meta["homepage"],
        standards_version=meta["standards_version"],
        build_depends=meta["build_depends"],
        archive_paths=[orig_path, sdk_path, deb_path],
        dsc_filename=f"kdeconnect-android-probe_{pkg_version}.dsc",
    )
    return {
        "package": "kdeconnect-android-probe",
        "version": pkg_version,
        "dsc": str(dsc_path),
        "manifest": str(manifest_path),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Build deterministic OBS source bundles")
    sub = parser.add_subparsers(dest="command", required=True)

    libei_p = sub.add_parser("build-libei", help="Build libei 1.6.0 OBS bundle")
    libei_p.add_argument("--output-dir", type=Path, required=True)
    libei_p.add_argument("--obs-rev", type=int, default=1)

    kde_p = sub.add_parser("build-kdeconnect", help="Build kdeconnect-kde OBS bundle")
    kde_p.add_argument("--desktop-src", type=Path, required=True)
    kde_p.add_argument("--overlay-debian", type=Path, default=ROOT / "obs" / "kdeconnect" / "debian")
    kde_p.add_argument("--output-dir", type=Path, required=True)
    kde_p.add_argument("--obs-rev", type=int, default=1)

    probe_p = sub.add_parser("build-android-probe", help="Build android-probe OBS bundle")
    probe_p.add_argument("--probe-src", type=Path, default=ROOT / "prototype" / "android-probe")
    probe_p.add_argument("--sdk-root", type=Path, default=Path(os.environ.get("ANDROID_SDK_ROOT", "")))
    probe_p.add_argument("--overlay-debian", type=Path, default=ROOT / "obs" / "android-probe" / "debian")
    probe_p.add_argument("--output-dir", type=Path, required=True)
    probe_p.add_argument("--obs-rev", type=int, default=1)

    args = parser.parse_args()
    if args.command == "build-libei":
        res = build_libei_bundle(args.output_dir.resolve(), obs_rev=args.obs_rev)
    elif args.command == "build-kdeconnect":
        res = build_kdeconnect_bundle(
            args.desktop_src.resolve(),
            args.overlay_debian.resolve(),
            args.output_dir.resolve(),
            obs_rev=args.obs_rev,
        )
    elif args.command == "build-android-probe":
        if not str(args.sdk_root):
            print("ERROR: --sdk-root or ANDROID_SDK_ROOT is required", file=sys.stderr)
            return 2
        res = build_android_probe_bundle(
            args.probe_src.resolve(),
            args.sdk_root.resolve(),
            args.overlay_debian.resolve(),
            args.output_dir.resolve(),
            obs_rev=args.obs_rev,
        )
    else:
        return 2
    print(json.dumps(res, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
