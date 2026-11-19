# SPDX-FileCopyrightText: 2026 Dmitriy Volkov <dennshield@duck.com>
# SPDX-License-Identifier: GPL-2.0-only OR GPL-3.0-only OR LicenseRef-KDE-Accepted-GPL

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from tools.plugin_registry import (
    active_android_plugins,
    active_desktop_plugins,
    build_deb_check_script,
    load_registry,
)
from tools.upstream_sync import PluginStackEntry, compute_stack_fingerprint, synthesize_plugin_stack

ROOT = Path(__file__).resolve().parents[1]


def _git_test(repo: Path, *args: str) -> str:
    return subprocess.check_output(
        [
            "git",
            "-C",
            str(repo),
            "-c",
            "user.name=Dmitriy Volkov",
            "-c",
            "user.email=dennshield@duck.com",
            "-c",
            "core.hooksPath=/dev/null",
            *args,
        ],
        text=True,
    ).strip()


class PluginRegistryTests(unittest.TestCase):
    def test_real_registry_loads_and_filters_active_targets(self) -> None:
        entries = load_registry(ROOT / "plugins.toml")
        ids = [entry.id for entry in entries]
        self.assertEqual(
            ids,
            [
                "screenstream",
                "clipboard_images",
                "otp_autofill",
                "continuity_camera",
                "remote_auth",
                "bt_audio_switch",
            ],
        )

        desktop_ids = [entry.id for entry in active_desktop_plugins(entries)]
        android_ids = [entry.id for entry in active_android_plugins(entries)]
        self.assertEqual(desktop_ids, ["screenstream", "clipboard_images", "otp_autofill"])
        self.assertEqual(android_ids, ["screenstream", "clipboard_images"])

        script = build_deb_check_script(entries)
        self.assertIn("kdeconnect_screenstream.so", script)
        self.assertIn("kdeconnect_clipboard.so", script)
        self.assertIn("kdeconnect_notifications.so", script)
        self.assertNotIn("kdeconnect_continuitycamera.so", script)
        self.assertIn("readelf -d /usr/bin/kdeconnect-screenstream-viewer", script)
        self.assertIn("libgstreamer-1.0.so.0", script)
        self.assertIn("libQt6Widgets.so.6", script)
        self.assertIn("Qt6Multimedia", script)
        self.assertIn("gst-inspect-1.0 appsrc h264parse avdec_h264 audioconvert audioresample autoaudiosink", script)
        self.assertNotIn("ldd ", script)

    def test_load_registry_rejects_invalid_entries(self) -> None:
        cases = [
            (
                "bad id",
                'version = 1\n[[plugins]]\nid = "Bad-ID"\nkind = "new_plugin"\nstatus = "active"\nspec = "docs/plugins/a/spec.md"\n',
            ),
            (
                "duplicate id",
                'version = 1\n[[plugins]]\nid = "dup"\nkind = "new_plugin"\nstatus = "active"\nspec = "a.md"\n'
                '[[plugins]]\nid = "dup"\nkind = "new_plugin"\nstatus = "planned"\nspec = "b.md"\n',
            ),
            (
                "traversal spec",
                'version = 1\n[[plugins]]\nid = "demo"\nkind = "new_plugin"\nstatus = "active"\nspec = "../secret.md"\n',
            ),
            (
                "bad branch",
                'version = 1\n[[plugins]]\nid = "demo"\nkind = "new_plugin"\nstatus = "active"\n'
                'desktop_branch = "feature/demo"\nspec = "docs/plugins/demo/spec.md"\n',
            ),
        ]
        for label, content in cases:
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "plugins.toml"
                path.write_text(content, encoding="utf-8")
                with self.assertRaises(ValueError, msg=label):
                    load_registry(path)

    def test_synthesize_plugin_stack_merges_three_branches_and_reports_conflict_plugin(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            remote_repo = root / "upstream.git"
            seed = root / "seed"
            work = root / "work"
            subprocess.check_call(["git", "init", "-q", "--bare", "-b", "master", str(remote_repo)])
            subprocess.check_call(["git", "clone", "-q", str(remote_repo), str(seed)])
            (seed / "shared.txt").write_text("base\n", encoding="utf-8")
            _git_test(seed, "add", "shared.txt")
            _git_test(seed, "commit", "-m", "base")
            _git_test(seed, "push", "-q", "origin", "master")
            base_sha = _git_test(seed, "rev-parse", "HEAD")

            plugin_shas: dict[str, str] = {}
            for name in ("alpha", "beta", "gamma"):
                _git_test(seed, "checkout", "-q", "-B", f"work/{name}", base_sha)
                (seed / f"{name}.cpp").write_text(f"// {name}\n", encoding="utf-8")
                _git_test(seed, "add", f"{name}.cpp")
                _git_test(seed, "commit", "-m", f"feat: {name}")
                plugin_shas[name] = _git_test(seed, "rev-parse", "HEAD")

            _git_test(seed, "checkout", "-q", "master")
            (seed / "upstream_only.txt").write_text("upstream\n", encoding="utf-8")
            _git_test(seed, "add", "upstream_only.txt")
            _git_test(seed, "commit", "-m", "upstream advance")
            _git_test(seed, "push", "-q", "origin", "master")
            upstream_sha = _git_test(seed, "rev-parse", "HEAD")

            subprocess.check_call(["git", "clone", "-q", "--branch", "master", str(remote_repo), str(work)])
            for name in ("alpha", "beta", "gamma"):
                _git_test(work, "fetch", "-q", str(seed), f"refs/heads/work/{name}:refs/heads/plugin/{name}")

            entries = [
                PluginStackEntry(plugin_id=name, plugin_ref=f"refs/heads/plugin/{name}", plugin_base_sha=base_sha)
                for name in ("alpha", "beta", "gamma")
            ]
            res = synthesize_plugin_stack(
                repo_dir=work,
                upstream_ref="refs/remotes/origin/master",
                entries=entries,
                target_branch="obs/kdeconnect-staging",
                allow_file_protocol=True,
            )
            self.assertTrue(res.ok, res)
            self.assertEqual(res.status, "ok")
            for name in ("alpha", "beta", "gamma"):
                self.assertTrue((work / f"{name}.cpp").is_file())
            self.assertTrue((work / "upstream_only.txt").is_file())

            fp1 = compute_stack_fingerprint(upstream_sha, plugin_shas, "0" * 64)
            fp2 = compute_stack_fingerprint(upstream_sha, dict(reversed(list(plugin_shas.items()))), "0" * 64)
            self.assertEqual(fp1, fp2)

            # Inject conflict on gamma relative to upstream master change.
            _git_test(seed, "checkout", "-q", "master")
            (seed / "shared.txt").write_text("upstream line\n", encoding="utf-8")
            _git_test(seed, "add", "shared.txt")
            _git_test(seed, "commit", "-m", "upstream shared change")
            _git_test(seed, "push", "-q", "origin", "master")

            _git_test(seed, "checkout", "-q", "work/gamma")
            (seed / "shared.txt").write_text("gamma line\n", encoding="utf-8")
            _git_test(seed, "add", "shared.txt")
            _git_test(seed, "commit", "-m", "gamma shared conflict")

            _git_test(work, "fetch", "-q", "origin", "master")
            _git_test(work, "fetch", "-q", str(seed), "refs/heads/work/gamma:refs/heads/plugin/gamma")

            bad_res = synthesize_plugin_stack(
                repo_dir=work,
                upstream_ref="refs/remotes/origin/master",
                entries=entries,
                target_branch="obs/kdeconnect-staging",
                allow_file_protocol=True,
            )
            self.assertFalse(bad_res.ok)
            self.assertEqual(bad_res.status, "rebase_conflict")
            self.assertIn("gamma:", bad_res.error)
            self.assertIn("shared.txt", bad_res.conflict_files)
            self.assertEqual(_git_test(work, "status", "--porcelain"), "")

    def test_synthesize_dry_run_supports_shallow_clones(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            remote_repo = root / "upstream.git"
            seed = root / "seed"
            shallow = root / "shallow"
            subprocess.check_call(["git", "init", "-q", "--bare", "-b", "master", str(remote_repo)])
            subprocess.check_call(["git", "clone", "-q", str(remote_repo), str(seed)])
            for idx in (1, 2):
                (seed / "base.txt").write_text(f"v{idx}\n", encoding="utf-8")
                _git_test(seed, "add", "base.txt")
                _git_test(seed, "commit", "-m", f"base {idx}")
            _git_test(seed, "push", "-q", "origin", "master")

            subprocess.check_call(["git", "clone", "-q", "--depth=1", f"file://{remote_repo}", str(shallow)])
            _git_test(shallow, "checkout", "-q", "-B", "work/demo", "origin/master")
            (shallow / "demo.cpp").write_text(
                "// SPDX-FileCopyrightText: 2026 Dmitriy Volkov <dennshield@duck.com>\n"
                "// SPDX-License-Identifier: GPL-2.0-only OR GPL-3.0-only OR LicenseRef-KDE-Accepted-GPL\n"
                "int demo() { return 0; }\n",
                encoding="utf-8",
            )
            _git_test(shallow, "add", "demo.cpp")
            _git_test(shallow, "commit", "-m", "demo: add clean plugin")

            reg = root / "plugins.toml"
            reg.write_text(
                'version = 1\n\n[[plugins]]\nid = "demo"\nkind = "new_plugin"\nstatus = "active"\n'
                'desktop_branch = "work/demo"\nspec = "docs/plugins/demo/spec.md"\n',
                encoding="utf-8",
            )
            proc = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "tools" / "synthesize_android_stack.py"),
                    "--desktop",
                    "--repo",
                    str(shallow),
                    "--registry",
                    str(reg),
                    "--dry-run",
                ],
                env=os.environ.copy(),
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertIn("DRY_RUN_STACK_OK plugins=demo", proc.stdout)


if __name__ == "__main__":
    unittest.main()
