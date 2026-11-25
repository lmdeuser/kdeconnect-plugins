#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Dmitriy Volkov <dennshield@duck.com>
# SPDX-License-Identifier: GPL-2.0-only OR GPL-3.0-only OR LicenseRef-KDE-Accepted-GPL

"""Multi-plugin registry loader, schema validator, and runtime check generator."""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import json
from pathlib import Path, PurePosixPath
import re
import shlex
import sys
import tomllib

PLUGIN_ID_RE = re.compile(r"^[a-z0-9_]+$")
BRANCH_RE = re.compile(r"^work/[a-z0-9_]+$")
GST_ELEMENT_RE = re.compile(r"^[A-Za-z0-9_+-]+$")
LIB_TOKEN_RE = re.compile(r"^[A-Za-z0-9.+_-]+$")

ALLOWED_KINDS = frozenset({"new_plugin", "upstream_enhancement"})
ALLOWED_STATUSES = frozenset({"active", "planned"})


@dataclass(frozen=True)
class PluginSpec:
    id: str
    kind: str
    status: str
    desktop_branch: str = ""
    android_branch: str = ""
    spec: str = ""
    verify_so: tuple[str, ...] = ()
    verify_bins: tuple[str, ...] = ()
    verify_qml: tuple[str, ...] = ()
    verify_gst_elements: tuple[str, ...] = ()
    required_needed_libs: dict[str, tuple[str, ...]] = field(default_factory=dict)
    forbidden_needed_libs: dict[str, tuple[str, ...]] = field(default_factory=dict)


def _validate_str_list(raw: object, field_name: str, plugin_id: str) -> tuple[str, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list) or not all(isinstance(item, str) for item in raw):
        raise ValueError(f"{plugin_id}: '{field_name}' must be a list of strings")
    return tuple(raw)


def _validate_usr_path(path_str: str, field_name: str, plugin_id: str) -> None:
    posix = PurePosixPath(path_str)
    if not path_str.startswith("/usr/") or ".." in posix.parts:
        raise ValueError(f"{plugin_id}: '{field_name}' entry must start with /usr/ without '..': {path_str}")


def _validate_needed_libs(
    raw: object,
    field_name: str,
    plugin_id: str,
) -> dict[str, tuple[str, ...]]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError(f"{plugin_id}: '{field_name}' must be a table mapping binary paths to library lists")
    parsed: dict[str, tuple[str, ...]] = {}
    for bin_path, libs_raw in raw.items():
        if not isinstance(bin_path, str):
            raise ValueError(f"{plugin_id}: '{field_name}' keys must be strings")
        _validate_usr_path(bin_path, field_name, plugin_id)
        libs = _validate_str_list(libs_raw, f"{field_name}.{bin_path}", plugin_id)
        for lib in libs:
            if not LIB_TOKEN_RE.match(lib):
                raise ValueError(f"{plugin_id}: invalid library token '{lib}' in '{field_name}'")
        parsed[bin_path] = libs
    return parsed


def load_registry(registry_path: Path) -> list[PluginSpec]:
    """Load and validate plugins.toml into an ordered list of PluginSpec entries."""
    with registry_path.open("rb") as fh:
        data = tomllib.load(fh)

    if data.get("version") != 1:
        raise ValueError(f"Unsupported plugin registry version: {data.get('version')!r}")

    raw_plugins = data.get("plugins")
    if not isinstance(raw_plugins, list) or not raw_plugins:
        raise ValueError("Registry must define a non-empty [[plugins]] list")

    entries: list[PluginSpec] = []
    seen_ids: set[str] = set()
    for item in raw_plugins:
        if not isinstance(item, dict):
            raise ValueError("Each [[plugins]] entry must be a TOML table")

        plugin_id = item.get("id")
        if not isinstance(plugin_id, str) or not PLUGIN_ID_RE.match(plugin_id):
            raise ValueError(f"Invalid plugin id: {plugin_id!r}")
        if plugin_id in seen_ids:
            raise ValueError(f"Duplicate plugin id: {plugin_id}")
        seen_ids.add(plugin_id)

        kind = item.get("kind")
        if kind not in ALLOWED_KINDS:
            raise ValueError(f"{plugin_id}: invalid kind {kind!r}")

        status = item.get("status")
        if status not in ALLOWED_STATUSES:
            raise ValueError(f"{plugin_id}: invalid status {status!r}")

        desktop_branch = item.get("desktop_branch", "")
        android_branch = item.get("android_branch", "")
        for label, branch in (("desktop_branch", desktop_branch), ("android_branch", android_branch)):
            if not isinstance(branch, str) or (branch != "" and not BRANCH_RE.match(branch)):
                raise ValueError(f"{plugin_id}: invalid {label} {branch!r}")

        spec = item.get("spec", "")
        if not isinstance(spec, str) or not spec:
            raise ValueError(f"{plugin_id}: missing 'spec' path")
        spec_posix = PurePosixPath(spec)
        if spec_posix.is_absolute() or ".." in spec_posix.parts:
            raise ValueError(f"{plugin_id}: 'spec' must be a safe relative path: {spec}")

        verify_so = _validate_str_list(item.get("verify_so"), "verify_so", plugin_id)
        for so_name in verify_so:
            if not so_name or "/" in so_name or "\\" in so_name or ".." in so_name or not so_name.endswith(".so"):
                raise ValueError(f"{plugin_id}: verify_so entry must be a *.so basename: {so_name}")

        verify_bins = _validate_str_list(item.get("verify_bins"), "verify_bins", plugin_id)
        for bin_path in verify_bins:
            _validate_usr_path(bin_path, "verify_bins", plugin_id)

        verify_qml = _validate_str_list(item.get("verify_qml"), "verify_qml", plugin_id)
        for qml_path in verify_qml:
            _validate_usr_path(qml_path, "verify_qml", plugin_id)

        verify_gst = _validate_str_list(item.get("verify_gst_elements"), "verify_gst_elements", plugin_id)
        for elem in verify_gst:
            if not GST_ELEMENT_RE.match(elem):
                raise ValueError(f"{plugin_id}: invalid GStreamer element name: {elem}")

        req_libs = _validate_needed_libs(item.get("required_needed_libs"), "required_needed_libs", plugin_id)
        forb_libs = _validate_needed_libs(item.get("forbidden_needed_libs"), "forbidden_needed_libs", plugin_id)

        entries.append(
            PluginSpec(
                id=plugin_id,
                kind=kind,
                status=status,
                desktop_branch=desktop_branch,
                android_branch=android_branch,
                spec=spec,
                verify_so=verify_so,
                verify_bins=verify_bins,
                verify_qml=verify_qml,
                verify_gst_elements=verify_gst,
                required_needed_libs=req_libs,
                forbidden_needed_libs=forb_libs,
            )
        )

    return entries


def active_desktop_plugins(entries: list[PluginSpec]) -> list[PluginSpec]:
    """Return active plugin entries with a configured desktop branch."""
    return [entry for entry in entries if entry.status == "active" and entry.desktop_branch]


def active_android_plugins(entries: list[PluginSpec]) -> list[PluginSpec]:
    """Return active plugin entries with a configured Android branch."""
    return [entry for entry in entries if entry.status == "active" and entry.android_branch]


def build_runtime_check_script(entries: list[PluginSpec]) -> str:
    """Build distro-neutral ELF, QML, and GStreamer verification commands for active desktop plugins."""
    lines: list[str] = []
    gst_elements: list[str] = []
    seen_gst: set[str] = set()

    for entry in active_desktop_plugins(entries):
        for so_name in entry.verify_so:
            q_so = shlex.quote(so_name)
            lines.append(
                f"so_path=$(find /usr/lib /usr/lib64 -name {q_so} 2>/dev/null | head -n 1 || true)"
            )
            lines.append('test -n "$so_path"')
        for bin_path in entry.verify_bins:
            lines.append(f"test -x {shlex.quote(bin_path)}")
        for qml_path in entry.verify_qml:
            lines.append(f"test -f {shlex.quote(qml_path)}")

        checked_bins = sorted(set(entry.required_needed_libs) | set(entry.forbidden_needed_libs))
        for bin_path in checked_bins:
            lines.append(f"needed=$(LC_ALL=C readelf -d {shlex.quote(bin_path)} | grep \"(NEEDED)\")")
            lines.append('echo "$needed"')
            for lib in entry.forbidden_needed_libs.get(bin_path, ()):
                q_lib = shlex.quote(lib)
                lines.append(
                    f'if echo "$needed" | grep -qi {q_lib}; then '
                    f'echo "ERROR: {lib} must not be linked" >&2; exit 1; fi'
                )
            for lib in entry.required_needed_libs.get(bin_path, ()):
                lines.append(f'echo "$needed" | grep -q {shlex.quote(lib)}')

        for elem in entry.verify_gst_elements:
            if elem not in seen_gst:
                seen_gst.add(elem)
                gst_elements.append(elem)

    if gst_elements:
        quoted_elems = " ".join(shlex.quote(elem) for elem in gst_elements)
        lines.append(f"gst-inspect-1.0 {quoted_elems} >/dev/null")

    return "\n".join(lines)


def build_deb_check_script(entries: list[PluginSpec]) -> str:
    """Build shell verification commands for Debian/Ubuntu .deb packages."""
    return build_runtime_check_script(entries)


def build_gentoo_check_script(entries: list[PluginSpec]) -> str:
    """Build distro-neutral shell verification commands for Gentoo Portage installs."""
    return build_runtime_check_script(entries)


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="Validate and inspect the KDE Connect plugin registry")
    sub = parser.add_subparsers(dest="command", required=True)

    val_p = sub.add_parser("validate", help="Validate plugins.toml schema and spec file paths")
    val_p.add_argument("--registry", type=Path, default=root / "plugins.toml")

    list_p = sub.add_parser("list", help="Print registered plugins as JSON")
    list_p.add_argument("--registry", type=Path, default=root / "plugins.toml")
    list_p.add_argument("--target", choices=("desktop", "android"), required=True)
    list_p.add_argument("--all", action="store_true", help="Include planned plugins")

    deb_p = sub.add_parser("deb-checks", help="Emit shell verification commands for active desktop plugins")
    deb_p.add_argument("--registry", type=Path, default=root / "plugins.toml")

    gentoo_p = sub.add_parser(
        "gentoo-checks",
        help="Emit distro-neutral ELF/QML/GStreamer verification commands for active desktop plugins",
    )
    gentoo_p.add_argument("--registry", type=Path, default=root / "plugins.toml")

    args = parser.parse_args()
    registry_path = args.registry.resolve()
    try:
        entries = load_registry(registry_path)
    except (OSError, ValueError, tomllib.TOMLDecodeError) as exc:
        print(f"ERROR: Invalid registry {registry_path}: {exc}", file=sys.stderr)
        return 1

    if args.command == "validate":
        missing = [
            entry.spec
            for entry in entries
            if not (registry_path.parent / entry.spec).is_file()
        ]
        if missing:
            for item in missing:
                print(f"ERROR: Missing spec file listed in registry: {item}", file=sys.stderr)
            return 1
        print(f"Plugin registry OK ({len(entries)} entries)")
        return 0

    if args.command == "list":
        filtered: list[dict[str, str]] = []
        for entry in entries:
            if not args.all and entry.status != "active":
                continue
            branch = entry.desktop_branch if args.target == "desktop" else entry.android_branch
            if not branch:
                continue
            filtered.append(
                {
                    "id": entry.id,
                    "status": entry.status,
                    "kind": entry.kind,
                    "branch": branch,
                    "spec": entry.spec,
                }
            )
        print(json.dumps(filtered, indent=2))
        return 0

    if args.command == "deb-checks":
        print(build_deb_check_script(entries))
        return 0

    if args.command == "gentoo-checks":
        print(build_gentoo_check_script(entries))
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
