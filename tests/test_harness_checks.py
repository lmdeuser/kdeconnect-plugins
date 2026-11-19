# SPDX-FileCopyrightText: 2026 Dmitriy Volkov <dennshield@duck.com>
# SPDX-License-Identifier: GPL-2.0-only OR GPL-3.0-only OR LicenseRef-KDE-Accepted-GPL

import subprocess
import tempfile
import unittest
from pathlib import Path

from tools.check_kde_compliance import check_repo_reuse, check_upstream_branch
from tools.check_public_tree import scan_repository


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout


class PublicTreeCheckTests(unittest.TestCase):
    def _make_clean_repo(self, root: Path) -> None:
        (root / "README.md").write_text(
            "<!-- SPDX-FileCopyrightText: 2026 Dmitriy Volkov <dennshield@duck.com> -->\n"
            "<!-- SPDX-License-Identifier: GPL-2.0-only OR GPL-3.0-only OR LicenseRef-KDE-Accepted-GPL -->\n\n"
            "# KDE Connect Plugins\n\n"
            "Upstream target: https://invent.kde.org/network/kdeconnect-kde\n",
            encoding="utf-8",
        )

    def test_accepts_clean_english_tree(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._make_clean_repo(root)
            self.assertEqual(scan_repository(root, staged_only=False), [])

    def test_rejects_github_actions_workflows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._make_clean_repo(root)
            wf = root / ".github" / "workflows" / "ci.yml"
            wf.parent.mkdir(parents=True)
            wf.write_text("name: CI\n", encoding="utf-8")
            issues = scan_repository(root, staged_only=False)
            self.assertTrue(any(".github/workflows/ci.yml" in item for item in issues), issues)

    def test_rejects_binary_artifacts_and_state_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._make_clean_repo(root)
            for rel in (
                "pkg.deb",
                "probe.apk",
                "src.tar.xz",
                "release.keystore",
                ".state/local.env",
            ):
                target = root / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("placeholder\n", encoding="utf-8")
            issues = scan_repository(root, staged_only=False)
            joined = "\n".join(issues)
            for rel in ("pkg.deb", "probe.apk", "src.tar.xz", "release.keystore", ".state/local.env"):
                self.assertIn(rel, joined)

    def test_rejects_private_ips_local_paths_secrets_and_cyrillic(self) -> None:
        sensitive_cases = {
            "private_172.txt": "host=" + "172." + "31.10.5\n",
            "private_192.txt": "host=" + "192." + "168.1.10\n",
            "private_10.txt": "host=" + "10." + "0.0.5\n",
            "loopback.txt": "host=" + "127." + "0.0.1\n",
            "home_path.txt": "src=" + "/" + "home/" + "alice/kdeconnect\n",
            "srv_temp.txt": "tmp=" + "/" + "srv/" + "temp/work\n",
            "token.txt": "token=" + "ghp_" + "a1b2c3d4e5f6g7h8i9j0k1l2m3n4\n",
            "glpat.txt": "token=" + "glpat-" + "a1b2c3d4e5f6g7h8i9j0\n",
            "pem.txt": "-----BEGIN " + "OPENSSH PRIVATE KEY-----\n",
            "cyrillic.md": "# " + "\u0422\u0435\u0441\u0442" + "\n",
        }
        for filename, payload in sensitive_cases.items():
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                (root / filename).write_text(payload, encoding="utf-8")
                issues = scan_repository(root, staged_only=False)
                self.assertTrue(
                    any(filename in item for item in issues),
                    f"Expected {filename} to be flagged; got {issues}",
                )

    def test_worktree_scan_skips_upstreams_and_worktrees_only_when_skip_state_enabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._make_clean_repo(root)
            sample_files = {
                "upstreams/kdeconnect-android/local.properties": "sdk.dir=" + "/" + "home/" + "alice/Android/Sdk\n",
                "upstreams/kdeconnect-android/res/values-ru/strings.xml": "<resources><string name=\"t\">\u0422\u0435\u0441\u0442</string></resources>\n",
                "upstreams/kdeconnect-android/build/debug.apk": "placeholder\n",
                "worktrees/exp/probe.deb": "placeholder\n",
            }
            for rel, content in sample_files.items():
                target = root / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content, encoding="utf-8")

            self.assertEqual(scan_repository(root, staged_only=False, skip_state_dir=True), [])

            strict_issues = scan_repository(root, staged_only=False, skip_state_dir=False)
            self.assertTrue(any(item.startswith("upstreams/") for item in strict_issues), strict_issues)
            self.assertTrue(any(item.startswith("worktrees/") for item in strict_issues), strict_issues)

    def test_rejects_symlinks_in_worktree(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._make_clean_repo(root)
            (root / "escape.link").symlink_to("/etc/passwd")
            sym_dir = root / "dirlink"
            sym_dir.symlink_to("/etc")
            issues = scan_repository(root, staged_only=False)
            joined = "\n".join(issues)
            self.assertIn("escape.link", joined)
            self.assertIn("dirlink", joined)


class KdeComplianceTests(unittest.TestCase):
    def _populate_licenses(self, root: Path) -> None:
        lic = root / "LICENSES"
        lic.mkdir(parents=True, exist_ok=True)
        for name in (
            "GPL-2.0-only.txt",
            "GPL-3.0-only.txt",
            "LicenseRef-KDE-Accepted-GPL.txt",
            "BSD-2-Clause.txt",
            "CC0-1.0.txt",
        ):
            (lic / name).write_text("License text placeholder\n", encoding="utf-8")

    def test_repo_reuse_accepts_valid_headers_including_skill_frontmatter(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._populate_licenses(root)
            (root / "REUSE.toml").write_text(
                "# SPDX-FileCopyrightText: 2026 Dmitriy Volkov <dennshield@duck.com>\n"
                "# SPDX-License-Identifier: CC0-1.0\n\n"
                "version = 1\n",
                encoding="utf-8",
            )
            skill = root / ".agents" / "skills" / "demo" / "SKILL.md"
            skill.parent.mkdir(parents=True)
            skill.write_text(
                "---\n"
                "# SPDX-FileCopyrightText: 2026 Dmitriy Volkov <dennshield@duck.com>\n"
                "# SPDX-License-Identifier: GPL-2.0-only OR GPL-3.0-only OR LicenseRef-KDE-Accepted-GPL\n"
                "name: demo\n"
                "description: Demo skill\n"
                "---\n\n"
                "# Demo\n",
                encoding="utf-8",
            )
            self.assertEqual(check_repo_reuse(root), [])

    def test_repo_reuse_rejects_missing_headers_and_unknown_licenses(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._populate_licenses(root)
            (root / "missing.py").write_text("print('no header')\n", encoding="utf-8")
            (root / "bad_lic.py").write_text(
                "# SPDX-FileCopyrightText: 2026 Dmitriy Volkov <dennshield@duck.com>\n"
                "# SPDX-License-Identifier: MIT\n",
                encoding="utf-8",
            )
            issues = check_repo_reuse(root)
            joined = "\n".join(issues)
            self.assertIn("missing.py", joined)
            self.assertIn("bad_lic.py", joined)

    def test_repo_reuse_validates_annotations_and_rejects_unknown_annotation_licenses(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._populate_licenses(root)
            (root / "REUSE.toml").write_text(
                "# SPDX-FileCopyrightText: 2026 Dmitriy Volkov <dennshield@duck.com>\n"
                "# SPDX-License-Identifier: CC0-1.0\n\n"
                "version = 1\n\n"
                "[[annotations]]\n"
                'path = "patches/**"\n'
                'SPDX-FileCopyrightText = "2026 Dmitriy Volkov <dennshield@duck.com>"\n'
                'SPDX-License-Identifier = "GPL-2.0-only OR GPL-3.0-only OR LicenseRef-KDE-Accepted-GPL"\n',
                encoding="utf-8",
            )
            patch = root / "patches" / "desktop" / "demo" / "0001-demo.patch"
            patch.parent.mkdir(parents=True)
            patch.write_text("From: Test <t@example.invalid>\nSubject: demo\n", encoding="utf-8")
            self.assertEqual(check_repo_reuse(root), [])

            (root / "unannotated.py").write_text("print('hi')\n", encoding="utf-8")
            self.assertTrue(any("unannotated.py" in item for item in check_repo_reuse(root)))
            (root / "unannotated.py").unlink()

            (root / "REUSE.toml").write_text(
                "# SPDX-FileCopyrightText: 2026 Dmitriy Volkov <dennshield@duck.com>\n"
                "# SPDX-License-Identifier: CC0-1.0\n\n"
                "[[annotations]]\n"
                'path = "patches/**"\n'
                'SPDX-FileCopyrightText = "2026 Dmitriy Volkov <dennshield@duck.com>"\n'
                'SPDX-License-Identifier = "MIT"\n',
                encoding="utf-8",
            )
            self.assertTrue(any("unknown license" in item for item in check_repo_reuse(root)))

    def _init_upstream_repo(self, repo: Path) -> str:
        _git(repo, "init", "-b", "master")
        _git(repo, "config", "user.name", "Test Maintainer")
        _git(repo, "config", "user.email", "maintainer@example.org")
        (repo / "CMakeLists.txt").write_text("cmake_minimum_required(VERSION 3.20)\n", encoding="utf-8")
        _git(repo, "add", "CMakeLists.txt")
        _git(repo, "commit", "-m", "chore: initial upstream base")
        return _git(repo, "rev-parse", "HEAD").strip()

    def test_upstream_branch_accepts_clean_plugin_commit_with_assisted_by(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            base = self._init_upstream_repo(repo)
            _git(repo, "checkout", "-b", "work/demo")
            plugin = repo / "plugins" / "demo" / "demoplugin.cpp"
            plugin.parent.mkdir(parents=True)
            plugin.write_text(
                "/**\n"
                " * SPDX-FileCopyrightText: 2026 Dmitriy Volkov <dennshield@duck.com>\n"
                " *\n"
                " * SPDX-License-Identifier: GPL-2.0-only OR GPL-3.0-only OR LicenseRef-KDE-Accepted-GPL\n"
                " */\n"
                "int demo() { return 0; }\n",
                encoding="utf-8",
            )
            _git(repo, "add", ".")
            _git(repo, "commit", "-m", "Add demo plugin\n\nAssisted-by: Antigravity")
            self.assertEqual(check_upstream_branch(repo, base), [])

    def test_upstream_branch_rejects_non_ascii_leaked_paths_and_ai_coauthored(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            base = self._init_upstream_repo(repo)
            _git(repo, "checkout", "-b", "work/bad")

            cpp = repo / "plugins" / "bad" / "badplugin.cpp"
            cpp.parent.mkdir(parents=True)
            cpp.write_text(
                "// SPDX-FileCopyrightText: 2026 Dmitriy Volkov <dennshield@duck.com>\n"
                "// SPDX-License-Identifier: GPL-2.0-only OR GPL-3.0-only OR LicenseRef-KDE-Accepted-GPL\n"
                "// size: 10 " + "\u00d7" + " 20" + "\u2026" + "\n",
                encoding="utf-8",
            )
            for forbidden in ("debian/control", "obs/package.xml", ".agents/AGENTS.md", "prototype/note.txt"):
                target = repo / forbidden
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("x\n", encoding="utf-8")

            _git(repo, "add", ".")
            _git(
                repo,
                "commit",
                "-m",
                "Bad upstream commit\n\nCo-authored-by: AI Assistant <ai@example.invalid>",
            )

            issues = check_upstream_branch(repo, base)
            joined = "\n".join(issues)
            self.assertIn("badplugin.cpp", joined)
            self.assertIn("debian/control", joined)
            self.assertIn("obs/package.xml", joined)
            self.assertIn(".agents/AGENTS.md", joined)
            self.assertIn("prototype/note.txt", joined)
            self.assertIn("Co-authored-by", joined)


if __name__ == "__main__":
    unittest.main()
