# SPDX-FileCopyrightText: 2026 Dmitriy Volkov <dennshield@duck.com>
# SPDX-License-Identifier: GPL-2.0-only OR GPL-3.0-only OR LicenseRef-KDE-Accepted-GPL

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from tools.export_stack_patches import export_all_targets
from tools.reproduce_stack import (
    EXPECTED_URLS,
    SCHEMA_VERSION,
    apply_locked_target,
    verify_lockfile,
)

ROOT = Path(__file__).resolve().parents[1]


def _git(repo: Path, *args: str, env_extra: dict[str, str] | None = None) -> str:
    env = dict(os.environ)
    env.update(
        {
            "GIT_AUTHOR_NAME": "Dmitriy Volkov",
            "GIT_AUTHOR_EMAIL": "dennshield@duck.com",
            "GIT_AUTHOR_DATE": "2026-11-19T12:00:00+00:00",
            "GIT_COMMITTER_NAME": "Dmitriy Volkov",
            "GIT_COMMITTER_EMAIL": "dennshield@duck.com",
            "GIT_COMMITTER_DATE": "2026-11-19T12:00:00+00:00",
        }
    )
    if env_extra:
        env.update(env_extra)
    return subprocess.check_output(["git", "-C", str(repo), *args], env=env, text=True).strip()


class ReproduceStackTests(unittest.TestCase):
    def _write_minimal_plugin_harness(self, root: Path) -> None:
        (root / "docs" / "plugins" / "alpha").mkdir(parents=True, exist_ok=True)
        (root / "docs" / "plugins" / "beta").mkdir(parents=True, exist_ok=True)
        (root / "docs" / "plugins" / "alpha" / "spec.md").write_text("# Alpha\n", encoding="utf-8")
        (root / "docs" / "plugins" / "beta" / "spec.md").write_text("# Beta\n", encoding="utf-8")
        (root / "plugins.toml").write_text(
            "version = 1\n\n"
            "[[plugins]]\n"
            'id = "alpha"\n'
            'kind = "new_plugin"\n'
            'status = "active"\n'
            'desktop_branch = "work/alpha"\n'
            'android_branch = "work/alpha"\n'
            'spec = "docs/plugins/alpha/spec.md"\n\n'
            "[[plugins]]\n"
            'id = "beta"\n'
            'kind = "upstream_enhancement"\n'
            'status = "active"\n'
            'desktop_branch = "work/beta"\n'
            'android_branch = "work/beta"\n'
            'spec = "docs/plugins/beta/spec.md"\n',
            encoding="utf-8",
        )
        shutil.copytree(ROOT / "obs" / "kdeconnect" / "debian", root / "obs" / "kdeconnect" / "debian")

    def _init_sample_upstream(self, repo: Path, kind: str) -> str:
        repo.mkdir(parents=True, exist_ok=True)
        _git(repo, "init", "-b", "master")
        (repo / "CMakeLists.txt").write_text(
            'set(RELEASE_SERVICE_VERSION_MAJOR "27")\n'
            'set(RELEASE_SERVICE_VERSION_MINOR "03")\n'
            'set(RELEASE_SERVICE_VERSION_MICRO "70")\n',
            encoding="utf-8",
        )
        (repo / "plugins.txt").write_text("base\n", encoding="utf-8")
        _git(repo, "add", ".")
        _git(repo, "commit", "-m", f"{kind}: base commit")
        base_sha = _git(repo, "rev-parse", "HEAD")
        _git(repo, "update-ref", "refs/remotes/origin/master", base_sha)

        _git(repo, "checkout", "-b", "work/alpha", base_sha)
        (repo / "alpha.cpp").write_text(
            "// SPDX-FileCopyrightText: 2026 Dmitriy Volkov <dennshield@duck.com>\n"
            "// SPDX-License-Identifier: GPL-2.0-only OR GPL-3.0-only OR LicenseRef-KDE-Accepted-GPL\n"
            "int alpha() { return 1; }\n",
            encoding="utf-8",
        )
        _git(repo, "add", "alpha.cpp")
        _git(repo, "commit", "-m", f"{kind}: add alpha")

        _git(repo, "checkout", "-b", "work/beta", base_sha)
        (repo / "beta.cpp").write_text(
            "// SPDX-FileCopyrightText: 2026 Dmitriy Volkov <dennshield@duck.com>\n"
            "// SPDX-License-Identifier: GPL-2.0-only OR GPL-3.0-only OR LicenseRef-KDE-Accepted-GPL\n"
            "int beta() { return 2; }\n",
            encoding="utf-8",
        )
        _git(repo, "add", "beta.cpp")
        _git(repo, "commit", "-m", f"{kind}: add beta", env_extra={"GIT_AUTHOR_DATE": "2026-11-19T12:05:00+00:00", "GIT_COMMITTER_DATE": "2026-11-19T12:05:00+00:00"})

        _git(repo, "checkout", "master")
        return base_sha

    def test_real_repo_lockfile_validates(self) -> None:
        lock_data, issues = verify_lockfile(ROOT)
        self.assertEqual(issues, [])
        self.assertEqual(lock_data["schema_version"], SCHEMA_VERSION)
        self.assertEqual(len(lock_data["targets"]["desktop"]["plugins"]), 6)
        self.assertEqual(len(lock_data["targets"]["android"]["plugins"]), 6)

    def test_verify_lockfile_rejects_tampering_url_traversal_symlink_and_orphan(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            harness = tmp_path / "harness"
            desktop = tmp_path / "upstream-desktop"
            android = tmp_path / "upstream-android"
            scratch = tmp_path / "tmp"
            scratch.mkdir()

            self._write_minimal_plugin_harness(harness)
            self._init_sample_upstream(desktop, "desktop")
            self._init_sample_upstream(android, "android")

            export_all_targets(
                repo_root=harness,
                desktop_repo=desktop,
                android_repo=android,
                tmpdir=scratch,
                base_ref="origin/master",
                check_compliance=False,
            )
            lock_file = harness / "stack.lock.json"
            orig_lock_text = lock_file.read_text(encoding="utf-8")

            # 1. Tamper patch byte content -> SHA-256 & size mismatch rejected
            first_patch = harness / json.loads(orig_lock_text)["targets"]["desktop"]["plugins"][0]["patches"][0]["path"]
            orig_patch = first_patch.read_bytes()
            first_patch.write_bytes(orig_patch + b"\n")
            _, issues = verify_lockfile(harness)
            self.assertTrue(any("mismatch" in item for item in issues), issues)
            first_patch.write_bytes(orig_patch)

            # 2. Untrusted upstream URL rejected
            bad_url = json.loads(orig_lock_text)
            bad_url["targets"]["desktop"]["upstream_url"] = "https://evil.example.invalid/kdeconnect.git"
            lock_file.write_text(json.dumps(bad_url), encoding="utf-8")
            _, issues = verify_lockfile(harness)
            self.assertTrue(any("unexpected upstream_url" in item for item in issues), issues)

            # 3. Path traversal in patch path rejected
            bad_path = json.loads(orig_lock_text)
            bad_path["targets"]["desktop"]["plugins"][0]["patches"][0]["path"] = "patches/desktop/alpha/../../outside.patch"
            lock_file.write_text(json.dumps(bad_path), encoding="utf-8")
            _, issues = verify_lockfile(harness)
            self.assertTrue(any("unsafe" in item for item in issues), issues)

            # 4. Symlink inside patches/ rejected
            lock_file.write_text(orig_lock_text, encoding="utf-8")
            sym = harness / "patches" / "desktop" / "alpha" / "9999-sym.patch"
            sym.symlink_to(harness / "plugins.toml")
            _, issues = verify_lockfile(harness)
            self.assertTrue(any("symlink" in item for item in issues), issues)
            sym.unlink()

            # 5. Unreferenced orphan file inside patches/ rejected
            orphan = harness / "patches" / "desktop" / "alpha" / "9999-orphan.patch"
            orphan.write_text("diff\n", encoding="utf-8")
            _, issues = verify_lockfile(harness)
            self.assertTrue(any("unreferenced" in item for item in issues), issues)

    def test_reproduce_stack_end_to_end_deterministic_deb_source_and_guards(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            harness = tmp_path / "harness"
            desktop = tmp_path / "upstream-desktop"
            android = tmp_path / "upstream-android"
            scratch = tmp_path / "tmp"
            scratch.mkdir()

            self._write_minimal_plugin_harness(harness)
            self._init_sample_upstream(desktop, "desktop")
            self._init_sample_upstream(android, "android")

            lock = export_all_targets(
                repo_root=harness,
                desktop_repo=desktop,
                android_repo=android,
                tmpdir=scratch,
                base_ref="origin/master",
                check_compliance=True,
            )

            res1 = apply_locked_target(
                repo_root=harness,
                target="desktop",
                out_dir=tmp_path / "repro-1",
                mirror_repo=desktop,
                emit_deb_source=tmp_path / "deb-1",
                check_compliance=True,
            )
            res2 = apply_locked_target(
                repo_root=harness,
                target="desktop",
                out_dir=tmp_path / "repro-2",
                mirror_repo=desktop,
                emit_deb_source=tmp_path / "deb-2",
                check_compliance=True,
            )

            self.assertEqual(res1["tree_sha"], lock["targets"]["desktop"]["expected_tree_sha"])
            self.assertEqual(res1["head_sha"], lock["targets"]["desktop"]["expected_head_sha"])
            self.assertEqual(res1["tree_sha"], res2["tree_sha"])
            self.assertEqual(res1["head_sha"], res2["head_sha"])
            self.assertEqual(res1["deb_source"]["manifest_sha256"], res2["deb_source"]["manifest_sha256"])

            files1 = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted((tmp_path / "deb-1").iterdir())}
            files2 = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted((tmp_path / "deb-2").iterdir())}
            self.assertEqual(files1, files2)
            self.assertTrue(any(name.endswith(".orig.tar.xz") for name in files1))
            self.assertTrue(any(name.endswith(".debian.tar.xz") for name in files1))
            self.assertTrue(any(name.endswith(".dsc") for name in files1))

            # Non-empty output directory must be rejected
            with self.assertRaises(ValueError):
                apply_locked_target(
                    repo_root=harness,
                    target="desktop",
                    out_dir=tmp_path / "repro-1",
                    mirror_repo=desktop,
                )

            # Tree SHA tamper must fail reproduction
            tampered = json.loads((harness / "stack.lock.json").read_text(encoding="utf-8"))
            tampered["targets"]["desktop"]["expected_tree_sha"] = "0" * 40
            (harness / "stack.lock.json").write_text(json.dumps(tampered), encoding="utf-8")
            with self.assertRaises(RuntimeError):
                apply_locked_target(
                    repo_root=harness,
                    target="desktop",
                    out_dir=tmp_path / "repro-bad",
                    mirror_repo=desktop,
                )


if __name__ == "__main__":
    unittest.main()
