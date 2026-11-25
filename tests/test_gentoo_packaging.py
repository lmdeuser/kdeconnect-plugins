# SPDX-FileCopyrightText: 2026 Dmitriy Volkov <dennshield@duck.com>
# SPDX-License-Identifier: GPL-2.0-only OR GPL-3.0-only OR LicenseRef-KDE-Accepted-GPL

import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from tools.export_stack_patches import export_all_targets
from tools.gentoo_packaging import (
    DEFAULT_EBUILD_VERSION,
    MANIFEST_LINE_RE,
    emit_gentoo_overlay,
)
from tools.reproduce_stack import apply_locked_target
from tests.test_reproduce_stack import ReproduceStackTests

ROOT = Path(__file__).resolve().parents[1]


class GentooPackagingTests(unittest.TestCase):
    def test_emit_gentoo_overlay_from_real_lockfile_produces_valid_eapi8_overlay(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "overlay"
            res = emit_gentoo_overlay(ROOT, out)
            self.assertEqual(res["ebuild_version"], DEFAULT_EBUILD_VERSION)
            self.assertEqual(res["patch_count"], 30)
            self.assertFalse(res["manifest_preserved"])

            layout = (out / "metadata" / "layout.conf").read_text(encoding="utf-8")
            self.assertIn("masters = gentoo", layout)
            self.assertIn("thin-manifests = true", layout)
            self.assertIn("profile-formats = portage-2", layout)

            repo_name_lines = (out / "profiles" / "repo_name").read_text(encoding="utf-8").splitlines()
            self.assertEqual(repo_name_lines, ["kdeconnect-continuity"])
            self.assertTrue((out / "profiles" / "package.mask" / "gentoo-kdeconnect").is_file())

            ebuild_path = out / "kde-misc" / "kdeconnect" / "kdeconnect-27.03.70_p1.ebuild"
            self.assertTrue(ebuild_path.is_file())
            ebuild_text = ebuild_path.read_text(encoding="utf-8")

            bash_check = subprocess.run(["bash", "-n", str(ebuild_path)], capture_output=True, text=True, check=False)
            self.assertEqual(bash_check.returncode, 0, bash_check.stderr)

            for required in (
                "EAPI=8",
                'ECM_TEST="forceoptional"',
                "KFMIN=6.3.0",
                "QTMIN=6.9.0",
                "inherit ecm flag-o-matic kde.org xdg",
                'LICENSE="GPL-2+"',
                'SLOT="6"',
                'KEYWORDS="amd64 ~arm64"',
                'IUSE="bluetooth +pulseaudio telephony X"',
                'REQUIRED_USE="pulseaudio"',
                ">=dev-libs/libei-1.6.0",
                "dev-libs/openssl:=",
                ">=dev-qt/qtbase-${QTMIN}:6=[dbus,gui,network,wayland,widgets,X?]",
                ">=kde-frameworks/kconfig-${KFMIN}:6[qml]",
                ">=media-libs/pulseaudio-qt-1.4:=",
                "media-libs/libpulse",
                "dev-libs/wayland-protocols",
                "dev-libs/kirigami-addons:6",
                ">=media-video/pipewire-1.0[gstreamer,sound-server]",
                "virtual/pkgconfig",
                "use X || cmake_comment_add_subdirectory -f plugins shareinputdevices",
                "filter-lto",
                "-DINSTALL_UFW_APPLICATION_RULE=OFF",
                "-DWITH_PULSEAUDIO=ON",
                "$(cmake_use_find_package telephony KF6ModemManagerQt)",
            ):
                self.assertIn(required, ebuild_text)

            for forbidden in (
                "dev-libs/qca",
                "dev-qt/qtwayland",
                "kde-frameworks/pulseaudio-qt",
                "gst-plugins-meta",
                "gst-plugins-vaapi",
            ):
                self.assertNotIn(forbidden, ebuild_text)

            lock_data = json.loads((ROOT / "stack.lock.json").read_text(encoding="utf-8"))
            staged_files = sorted((out / "kde-misc" / "kdeconnect" / "files" / "patches").rglob("*.patch"))
            self.assertEqual(len(staged_files), 30)
            idx = 1
            for p_entry in lock_data["targets"]["desktop"]["plugins"]:
                for rec in p_entry["patches"]:
                    src = ROOT / rec["path"]
                    dst = (
                        out
                        / "kde-misc"
                        / "kdeconnect"
                        / "files"
                        / "patches"
                        / p_entry["id"]
                        / f"{idx:03d}-{src.name}"
                    )
                    self.assertTrue(dst.is_file(), f"Missing staged patch {dst}")
                    self.assertEqual(
                        hashlib.sha256(dst.read_bytes()).hexdigest(),
                        rec["sha256"],
                    )
                    idx += 1

    def test_manifest_preservation_distfile_hashing_and_package_only_cleanup(self) -> None:
        lock_data = json.loads((ROOT / "stack.lock.json").read_text(encoding="utf-8"))
        base_sha = lock_data["targets"]["desktop"]["base_sha"]
        tar_name = f"kdeconnect-kde-{base_sha}.tar.bz2"

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            dist = tmp_path / tar_name
            dist.write_bytes(b"synthetic-kdeconnect-archive")

            out = tmp_path / "overlay"
            res = emit_gentoo_overlay(ROOT, out, distfile=dist)
            self.assertTrue(res["manifest_preserved"])
            manifest_path = out / "kde-misc" / "kdeconnect" / "Manifest"
            manifest_line = manifest_path.read_text(encoding="utf-8").strip()
            match = MANIFEST_LINE_RE.match(manifest_line)
            self.assertIsNotNone(match, manifest_line)
            self.assertEqual(match.group(1), tar_name)
            self.assertEqual(int(match.group(2)), len(b"synthetic-kdeconnect-archive"))

            # Preserve matching DIST line into another overlay with --package-only and clean old ebuild.
            existing = tmp_path / "existing-overlay"
            (existing / "profiles").mkdir(parents=True)
            (existing / "profiles" / "repo_name").write_text("custom-local\n", encoding="utf-8")
            old_ebuild = existing / "kde-misc" / "kdeconnect" / "kdeconnect-26.04.0.ebuild"
            old_ebuild.parent.mkdir(parents=True)
            old_ebuild.write_text("# stale\n", encoding="utf-8")

            res2 = emit_gentoo_overlay(
                ROOT,
                existing,
                preserve_manifest_from=manifest_path,
                include_repo_metadata=False,
            )
            self.assertTrue(res2["manifest_preserved"])
            self.assertEqual((existing / "profiles" / "repo_name").read_text(encoding="utf-8"), "custom-local\n")
            self.assertFalse(old_ebuild.exists())
            self.assertTrue((existing / "kde-misc" / "kdeconnect" / "kdeconnect-27.03.70_p1.ebuild").is_file())
            self.assertEqual(
                (existing / "kde-misc" / "kdeconnect" / "Manifest").read_text(encoding="utf-8").strip(),
                manifest_line,
            )

            # Unmatched old Manifest entry is dropped when base_sha does not match.
            stale_manifest = tmp_path / "stale-Manifest"
            stale_manifest.write_text(
                f"DIST kdeconnect-kde-{'0' * 40}.tar.bz2 10 BLAKE2B {'a' * 128} SHA512 {'b' * 128}\n",
                encoding="utf-8",
            )
            res3 = emit_gentoo_overlay(
                ROOT,
                existing,
                preserve_manifest_from=stale_manifest,
                include_repo_metadata=False,
            )
            self.assertFalse(res3["manifest_preserved"])
            self.assertFalse((existing / "kde-misc" / "kdeconnect" / "Manifest").exists())

    def test_guards_reject_tampering_bad_versions_symlinks_and_dry_run_is_readonly(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            dry_out = tmp_path / "dry-overlay"
            res = emit_gentoo_overlay(ROOT, dry_out, dry_run=True)
            self.assertTrue(res["dry_run"])
            self.assertFalse(dry_out.exists())

            with self.assertRaises(ValueError):
                emit_gentoo_overlay(ROOT, tmp_path / "bad-ver", ebuild_version="../evil")
            with self.assertRaises(ValueError):
                emit_gentoo_overlay(ROOT, tmp_path / "bad-repo", repo_name="bad repo#name")

            sym_out = tmp_path / "sym-overlay"
            sym_out.symlink_to(tmp_path)
            with self.assertRaises(ValueError):
                emit_gentoo_overlay(ROOT, sym_out)

            # Tampered patch SHA-256 in a copied harness must abort overlay emission.
            harness = tmp_path / "harness"
            harness.mkdir(exist_ok=True)
            shutil.copy2(ROOT / "plugins.toml", harness / "plugins.toml")
            shutil.copy2(ROOT / "stack.lock.json", harness / "stack.lock.json")
            shutil.copytree(ROOT / "patches", harness / "patches")
            first_patch = next((harness / "patches" / "desktop").rglob("*.patch"))
            first_patch.write_bytes(first_patch.read_bytes() + b"\n")
            with self.assertRaises(ValueError):
                emit_gentoo_overlay(harness, tmp_path / "tampered-out")

    def test_reproduce_stack_cli_and_api_emit_gentoo_overlay(self) -> None:
        helper = ReproduceStackTests()
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            harness = tmp_path / "harness"
            desktop = tmp_path / "upstream-desktop"
            android = tmp_path / "upstream-android"
            scratch = tmp_path / "tmp"
            scratch.mkdir()

            helper._write_minimal_plugin_harness(harness)
            helper._init_sample_upstream(desktop, "desktop")
            helper._init_sample_upstream(android, "android")
            export_all_targets(
                repo_root=harness,
                desktop_repo=desktop,
                android_repo=android,
                tmpdir=scratch,
                base_ref="origin/master",
                check_compliance=True,
            )

            overlay_out = tmp_path / "gentoo-overlay"
            res = apply_locked_target(
                repo_root=harness,
                target="desktop",
                out_dir=tmp_path / "repro-desktop",
                mirror_repo=desktop,
                emit_gentoo_overlay=overlay_out,
                check_compliance=True,
            )
            self.assertEqual(res["gentoo_overlay"]["patch_count"], 2)
            self.assertTrue((overlay_out / "kde-misc" / "kdeconnect" / "kdeconnect-27.03.70_p1.ebuild").is_file())
            self.assertEqual((overlay_out / "profiles" / "repo_name").read_text(encoding="utf-8"), "kdeconnect-continuity\n")

            with self.assertRaises(ValueError):
                apply_locked_target(
                    repo_root=harness,
                    target="android",
                    out_dir=tmp_path / "repro-android",
                    mirror_repo=android,
                    emit_gentoo_overlay=tmp_path / "android-overlay",
                )

            help_proc = subprocess.run(
                [sys.executable, str(ROOT / "tools" / "reproduce_stack.py"), "checkout", "--help"],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(help_proc.returncode, 0, help_proc.stderr)
            self.assertIn("--emit-gentoo-overlay", help_proc.stdout)

            cli_overlay = tmp_path / "cli-overlay"
            gp_proc = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "tools" / "gentoo_packaging.py"),
                    "--repo-root",
                    str(harness),
                    "--output-dir",
                    str(cli_overlay),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(gp_proc.returncode, 0, gp_proc.stdout + gp_proc.stderr)
            self.assertTrue((cli_overlay / "kde-misc" / "kdeconnect" / "kdeconnect-27.03.70_p1.ebuild").is_file())


if __name__ == "__main__":
    unittest.main()
