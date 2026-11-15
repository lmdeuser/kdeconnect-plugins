# SPDX-FileCopyrightText: 2026 Dmitriy Volkov <dennshield@duck.com>
# SPDX-License-Identifier: GPL-2.0-only OR GPL-3.0-only OR LicenseRef-KDE-Accepted-GPL

import io
import json
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path

from tools.obs_packaging import (
    build_android_probe_bundle,
    build_kdeconnect_bundle,
    prepare_libei_debian_tar,
    strip_spdx_comments,
    strip_xml_comments,
    verify_bundle_manifest,
    write_dsc_and_manifest,
)

ROOT = Path(__file__).resolve().parents[1]


def _git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()


class ObsPackagingTests(unittest.TestCase):
    def test_strip_spdx_comments_for_sensitive_debhelper_files(self) -> None:
        raw = (
            "# SPDX-FileCopyrightText: 2026 Dmitriy Volkov <dennshield@duck.com>\n"
            "# SPDX-License-Identifier: GPL-2.0-only OR GPL-3.0-only OR LicenseRef-KDE-Accepted-GPL\n"
            "-qrcFiles plugins/presenter/assets.qrc\n"
            "-qmlFiles\n"
            "\tapp/**/*.qml\n"
        )
        cleaned = strip_spdx_comments(raw)
        self.assertEqual(
            cleaned,
            "-qrcFiles plugins/presenter/assets.qrc\n-qmlFiles\n\tapp/**/*.qml\n",
        )

    def test_strip_xml_comments_preserves_root_element(self) -> None:
        raw = (ROOT / "obs" / "staging.xml").read_text(encoding="utf-8")
        cleaned = strip_xml_comments(raw)
        self.assertTrue(cleaned.lstrip().startswith('<project name="home:dwolfF:kdeconnect:staging">'))
        self.assertNotIn("SPDX-", cleaned)

    def test_write_dsc_and_manifest_detects_tampering(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            orig = out / "demo_1.0.orig.tar.xz"
            deb = out / "demo_1.0-1.debian.tar.xz"
            orig.write_bytes(b"orig-bytes")
            deb.write_bytes(b"deb-bytes")

            dsc_path, manifest_path = write_dsc_and_manifest(
                output_dir=out,
                source_name="demo",
                binaries=["demo"],
                package_list=["demo deb misc optional arch=amd64"],
                architecture="amd64",
                version="1.0-1",
                maintainer="Dmitriy Volkov <dennshield@duck.com>",
                homepage="https://kdeconnect.kde.org",
                standards_version="4.7.2",
                build_depends="debhelper-compat (= 13)",
                archive_paths=[orig, deb],
                dsc_filename="demo_1.0-1.dsc",
            )
            dsc_text = dsc_path.read_text(encoding="utf-8")
            self.assertIn("Format: 3.0 (quilt)", dsc_text)
            self.assertIn("Checksums-Sha1:", dsc_text)
            self.assertIn("Checksums-Sha256:", dsc_text)
            self.assertIn("Files:", dsc_text)

            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["package"], "demo")
            verify_bundle_manifest(out)

            orig.write_bytes(b"tampered")
            with self.assertRaises(ValueError):
                verify_bundle_manifest(out)

    def test_build_kdeconnect_bundle_packs_clean_source_and_strips_debhelper_spdx(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            fake_desktop = tmp_path / "kdeconnect-kde"
            fake_desktop.mkdir()
            _git(fake_desktop, "init", "-b", "master")
            _git(fake_desktop, "config", "user.name", "Dmitriy Volkov")
            _git(fake_desktop, "config", "user.email", "dennshield@duck.com")
            (fake_desktop / "CMakeLists.txt").write_text(
                'set (RELEASE_SERVICE_VERSION_MAJOR "27")\n'
                'set (RELEASE_SERVICE_VERSION_MINOR "03")\n'
                'set (RELEASE_SERVICE_VERSION_MICRO "70")\n',
                encoding="utf-8",
            )
            _git(fake_desktop, "add", "CMakeLists.txt")
            _git(fake_desktop, "commit", "-m", "chore: test baseline")

            out_dir = tmp_path / "bundle"
            result = build_kdeconnect_bundle(
                desktop_src=fake_desktop,
                overlay_debian_dir=ROOT / "obs" / "kdeconnect" / "debian",
                output_dir=out_dir,
                obs_rev=1,
            )
            verify_bundle_manifest(out_dir)

            orig_path = next(out_dir.glob("kdeconnect_*.orig.tar.xz"))
            with tarfile.open(orig_path, "r:xz") as tf:
                names = tf.getnames()
                self.assertTrue(any(n.endswith("/CMakeLists.txt") for n in names))
                self.assertFalse(any("/.git" in n or n.endswith("/.git") for n in names))

            deb_path = next(out_dir.glob("kdeconnect_*.debian.tar.xz"))
            with tarfile.open(deb_path, "r:xz") as tf:
                names = tf.getnames()
                self.assertIn("debian/source/format", names)
                self.assertIn("debian/changelog", names)
                self.assertIn("debian/copyright", names)
                rules_info = tf.getmember("debian/rules")
                self.assertEqual(rules_info.mode & 0o777, 0o755)
                qmlfiles = tf.extractfile("debian/kdeconnect.qmlfiles").read().decode("utf-8")
                self.assertNotIn("SPDX-", qmlfiles)
                self.assertTrue(qmlfiles.startswith("-qrcFiles"))
                self.assertIn("debian/qmldeps.overrides", names)
                overrides = tf.extractfile("debian/qmldeps.overrides").read().decode("utf-8")
                self.assertNotIn("SPDX-", overrides)
                self.assertNotIn("SPDX-", tf.extractfile("debian/kdeconnect.maintscript").read().decode("utf-8"))
                changelog = tf.extractfile("debian/changelog").read().decode("utf-8")
                self.assertIn(") resolute; urgency=medium", changelog)
            self.assertEqual(result["package"], "kdeconnect")

    def test_build_android_probe_bundle_packs_sdk_slice_and_validates_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            sdk = tmp_path / "sdk"
            required = [
                sdk / "platforms/android-35/android.jar",
                sdk / "build-tools/36.0.0/aapt2",
                sdk / "build-tools/36.0.0/d8",
                sdk / "build-tools/36.0.0/zipalign",
                sdk / "build-tools/36.0.0/apksigner",
                sdk / "build-tools/36.0.0/lib/d8.jar",
                sdk / "build-tools/36.0.0/lib/apksigner.jar",
                sdk / "build-tools/36.0.0/lib64/libc++.so",
            ]
            for path in required:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"stub")

            out_dir = tmp_path / "out"
            build_android_probe_bundle(
                probe_src=ROOT / "prototype" / "android-probe",
                sdk_root=sdk,
                overlay_debian_dir=ROOT / "obs" / "android-probe" / "debian",
                output_dir=out_dir,
                obs_rev=1,
            )
            verify_bundle_manifest(out_dir)

            sdk_tar = next(out_dir.glob("*.orig-sdk.tar.xz"))
            with tarfile.open(sdk_tar, "r:xz") as tf:
                names = tf.getnames()
                self.assertIn("sdk/platforms/android-35/android.jar", names)
                self.assertEqual(tf.getmember("sdk/build-tools/36.0.0/aapt2").mode & 0o777, 0o755)

            required[-1].unlink()
            with self.assertRaises(FileNotFoundError):
                build_android_probe_bundle(
                    probe_src=ROOT / "prototype" / "android-probe",
                    sdk_root=sdk,
                    overlay_debian_dir=ROOT / "obs" / "android-probe" / "debian",
                    output_dir=tmp_path / "out2",
                    obs_rev=1,
                )

    def test_prepare_libei_debian_tar_updates_version_symbols_level_and_lintian(self) -> None:
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:xz") as tf:
            entries = {
                "debian/source/format": (b"3.0 (quilt)\n", 0o644),
                "debian/control": (
                    b"Source: libei\n"
                    b"Section: x11\n"
                    b"Maintainer: Test <test@example.invalid>\n"
                    b"Build-Depends:\n"
                    b" debhelper-compat (= 13),\n"
                    b" meson,\n"
                    b"Standards-Version: 4.7.3\n"
                    b"Homepage: https://gitlab.freedesktop.org/libinput/libei\n\n"
                    b"Package: libei1\n"
                    b"Architecture: any\n"
                    b"Description: client lib\n",
                    0o644,
                ),
                "debian/rules": (
                    b"#!/usr/bin/make -f\n"
                    b"export DPKG_GENSYMBOLS_CHECK_LEVEL = 4\n"
                    b"%:\n\tdh $@\n",
                    0o755,
                ),
                "debian/changelog": (
                    b"libei (1.5.0-3) unstable; urgency=medium\n\n  * Old.\n\n"
                    b" -- Test <test@example.invalid>  Mon, 01 Jan 2026 00:00:00 +0000\n",
                    0o644,
                ),
            }
            for name, (data, mode) in entries.items():
                info = tarfile.TarInfo(name=name)
                info.size = len(data)
                info.mode = mode
                tf.addfile(info, io.BytesIO(data))

        out_bytes, metadata = prepare_libei_debian_tar(buf.getvalue(), upstream_version="1.6.0", obs_rev=1)
        self.assertEqual(metadata["binaries"], ["libei1"])
        self.assertIn("lintian", metadata["build_depends"])
        with tarfile.open(fileobj=io.BytesIO(out_bytes), mode="r:xz") as tf:
            rules = tf.extractfile("debian/rules").read().decode("utf-8")
            self.assertIn("DPKG_GENSYMBOLS_CHECK_LEVEL = 1", rules)
            self.assertIn("override_dh_makeshlibs:", rules)
            self.assertIn("dh_makeshlibs -V -- -v1.6.0", rules)
            self.assertIn("lintian --fail-on error", rules)
            changelog = tf.extractfile("debian/changelog").read().decode("utf-8")
            self.assertTrue(changelog.startswith("libei (1.6.0-1~obs1) resolute; urgency=medium"))


if __name__ == "__main__":
    unittest.main()
