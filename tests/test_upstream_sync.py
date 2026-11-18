# SPDX-FileCopyrightText: 2026 Dmitriy Volkov <dennshield@duck.com>
# SPDX-License-Identifier: GPL-2.0-only OR GPL-3.0-only OR LicenseRef-KDE-Accepted-GPL

from pathlib import Path
import subprocess
import tempfile
import unittest

from tools.upstream_sync import (
    compute_cycle_fingerprint,
    compute_overlay_sha256,
    build_hardened_git_command,
    build_scrubbed_env,
    query_remote_head,
    synthesize_plugin_branch,
    validate_deb_listing,
    validate_remote_url,
    verify_tree_cleanliness,
)

ROOT = Path(__file__).resolve().parents[1]


def _git_test(repo: Path, *args: str) -> str:
    cmd = [
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
    ]
    return subprocess.check_output(cmd, text=True).strip()


class UpstreamSyncSecurityTests(unittest.TestCase):
    def test_hardened_git_command_and_scrubbed_env_strip_vectors(self) -> None:
        env = build_scrubbed_env(
            {
                "PATH": "/usr/bin:/bin",
                "OSC_CONFIG": "/var/tmp/oscrc",
                "SSH_AUTH_SOCK": "/run/user/1000/agent.sock",
                "SSH_AGENT_PID": "1234",
                "GIT_SSH_COMMAND": "sh -c id",
                "GIT_EXEC_PATH": "/tmp/evil",
                "AWS_SECRET_ACCESS_KEY": "dummy",
                "GITHUB_TOKEN": "dummy",
                "GITLAB_TOKEN": "dummy",
            }
        )
        for forbidden in (
            "OSC_CONFIG",
            "SSH_AUTH_SOCK",
            "SSH_AGENT_PID",
            "GIT_SSH_COMMAND",
            "GIT_EXEC_PATH",
            "AWS_SECRET_ACCESS_KEY",
            "GITHUB_TOKEN",
            "GITLAB_TOKEN",
        ):
            self.assertNotIn(forbidden, env)
        self.assertEqual(env["GIT_TERMINAL_PROMPT"], "0")
        self.assertEqual(env["GIT_ALLOW_PROTOCOL"], "https")
        self.assertEqual(env["GIT_CONFIG_GLOBAL"], "/dev/null")
        self.assertEqual(env["GIT_CONFIG_SYSTEM"], "/dev/null")

        cmd = build_hardened_git_command(Path("/var/tmp/repo"), "fetch", "origin", "master")
        joined = " ".join(cmd)
        self.assertIn("core.hooksPath=/dev/null", joined)
        self.assertIn("core.symlinks=false", joined)
        self.assertIn("submodule.recurse=false", joined)
        self.assertIn("protocol.ext.allow=never", joined)
        self.assertIn("protocol.https.allow=always", joined)
        self.assertNotIn("protocol.file.allow=always", joined)
        self.assertIn("fetch.fsckObjects=true", joined)

    def test_validate_remote_url_enforces_strict_allowlist(self) -> None:
        self.assertEqual(
            validate_remote_url("https://invent.kde.org/network/kdeconnect-kde.git"),
            "https://invent.kde.org/network/kdeconnect-kde.git",
        )
        self.assertEqual(
            validate_remote_url("https://invent.kde.org/network/kdeconnect-android.git"),
            "https://invent.kde.org/network/kdeconnect-android.git",
        )
        for bad in (
            "ext::sh -c id% ",
            "ssh://git@invent.kde.org/network/kdeconnect-kde.git",
            "http://invent.kde.org/network/kdeconnect-kde.git",
            "https://github.com/kde/kdeconnect-kde.git",
            "file:///tmp/repo.git",
        ):
            with self.assertRaises(ValueError):
                validate_remote_url(bad)

    def test_synthesize_plugin_branch_rebases_cleanly_without_mutating_plugin_ref(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            remote_repo = root / "upstream.git"
            work_repo = root / "work"
            subprocess.check_call(["git", "init", "-q", "--bare", "-b", "master", str(remote_repo)])

            seed = root / "seed"
            subprocess.check_call(["git", "clone", "-q", str(remote_repo), str(seed)])
            (seed / "README.md").write_text("base\n", encoding="utf-8")
            (seed / "conflict.txt").write_text("line1\n", encoding="utf-8")
            _git_test(seed, "add", ".")
            _git_test(seed, "commit", "-m", "base")
            _git_test(seed, "push", "-q", "origin", "master")
            base_sha = _git_test(seed, "rev-parse", "HEAD")

            # Add plugin commit on plugin/screenstream branch.
            _git_test(seed, "checkout", "-q", "-b", "plugin/screenstream")
            (seed / "plugin.cpp").write_text("// plugin\n", encoding="utf-8")
            _git_test(seed, "add", "plugin.cpp")
            _git_test(seed, "commit", "-m", "feat: add screenstream")
            plugin_sha = _git_test(seed, "rev-parse", "HEAD")

            # Advance upstream master with an unrelated commit.
            _git_test(seed, "checkout", "-q", "master")
            (seed / "upstream.txt").write_text("v2\n", encoding="utf-8")
            _git_test(seed, "add", "upstream.txt")
            _git_test(seed, "commit", "-m", "upstream: advance")
            _git_test(seed, "push", "-q", "origin", "master")
            upstream_v2 = _git_test(seed, "rev-parse", "HEAD")

            subprocess.check_call(["git", "clone", "-q", "--branch", "master", str(remote_repo), str(work_repo)])
            _git_test(work_repo, "fetch", "-q", str(seed), f"{plugin_sha}:refs/heads/plugin/screenstream")
            self.assertEqual(query_remote_head(str(remote_repo), "refs/heads/master", allow_local_test=True), upstream_v2)

            res = synthesize_plugin_branch(
                work_repo,
                upstream_ref="refs/remotes/origin/master",
                plugin_ref="refs/heads/plugin/screenstream",
                plugin_base_sha=base_sha,
                target_branch="obs/kdeconnect-staging",
                allow_file_protocol=True,
            )
            self.assertTrue(res.ok, res)
            self.assertEqual(res.status, "ok")
            self.assertIsNotNone(res.synthesized_sha)
            self.assertNotEqual(res.synthesized_sha, plugin_sha)
            self.assertEqual(_git_test(work_repo, "rev-parse", "refs/heads/plugin/screenstream"), plugin_sha)
            self.assertTrue((work_repo / "upstream.txt").is_file())
            self.assertTrue((work_repo / "plugin.cpp").is_file())

            # Test conflict abort leaves tree clean and returns rebase_conflict.
            _git_test(seed, "checkout", "master")
            (seed / "conflict.txt").write_text("upstream change\n", encoding="utf-8")
            _git_test(seed, "add", "conflict.txt")
            _git_test(seed, "commit", "-m", "upstream conflict")
            _git_test(seed, "push", "origin", "master")

            _git_test(seed, "checkout", "plugin/screenstream")
            (seed / "conflict.txt").write_text("plugin change\n", encoding="utf-8")
            _git_test(seed, "add", "conflict.txt")
            _git_test(seed, "commit", "-m", "plugin conflict")
            plugin_conflict_sha = _git_test(seed, "rev-parse", "HEAD")

            _git_test(work_repo, "fetch", "origin", "master")
            _git_test(work_repo, "fetch", str(seed), f"{plugin_conflict_sha}:refs/heads/plugin/screenstream")
            conflict_res = synthesize_plugin_branch(
                work_repo,
                upstream_ref="refs/remotes/origin/master",
                plugin_ref="refs/heads/plugin/screenstream",
                plugin_base_sha=base_sha,
                target_branch="obs/kdeconnect-staging",
                allow_file_protocol=True,
            )
            self.assertFalse(conflict_res.ok)
            self.assertEqual(conflict_res.status, "rebase_conflict")
            self.assertIn("conflict.txt", conflict_res.conflict_files)
            self.assertEqual(_git_test(work_repo, "status", "--porcelain"), "")

    def test_verify_tree_cleanliness_rejects_symlinks_submodules_and_debian_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            subprocess.check_call(["git", "init", str(repo)])
            (repo / "src.cpp").write_text("int main() { return 0; }\n", encoding="utf-8")
            _git_test(repo, "add", "src.cpp")
            _git_test(repo, "commit", "-m", "clean")
            self.assertEqual(verify_tree_cleanliness(repo, "HEAD"), [])

            # Inject symlink entry into index via update-index.
            blob = subprocess.check_output(["git", "-C", str(repo), "hash-object", "-w", "--stdin"], input="/etc/passwd", text=True).strip()
            subprocess.check_call(["git", "-C", str(repo), "update-index", "--add", "--cacheinfo", f"120000,{blob},evil_link"])
            _git_test(repo, "commit", "-m", "add symlink")
            issues = verify_tree_cleanliness(repo, "HEAD")
            self.assertTrue(any("symlink" in item for item in issues), issues)

            # Inject gitlink (160000) submodule entry and debian/ path.
            _git_test(repo, "rm", "--cached", "evil_link")
            commit_sha = _git_test(repo, "rev-parse", "HEAD")
            subprocess.check_call(["git", "-C", str(repo), "update-index", "--add", "--cacheinfo", f"160000,{commit_sha},submod"])
            (repo / "debian").mkdir()
            (repo / "debian" / "control").write_text("Source: evil\n", encoding="utf-8")
            _git_test(repo, "add", "debian/control")
            _git_test(repo, "commit", "-m", "add submodule and debian")
            issues = verify_tree_cleanliness(repo, "HEAD")
            self.assertTrue(any("160000" in item for item in issues), issues)
            self.assertTrue(any("debian/control" in item for item in issues), issues)

    def test_validate_deb_listing_enforces_paths_modes_version_and_ctrl_members(self) -> None:
        good_lines = [
            "drwxr-xr-x root/root 0 2026-11-18 20:51 ./",
            "drwxr-xr-x root/root 0 2026-11-18 20:51 ./etc/",
            "drwxr-xr-x root/root 0 2026-11-18 20:51 ./etc/ufw/",
            "-rw-r--r-- root/root 100 2026-11-18 20:51 ./etc/ufw/applications.d/kdeconnect",
            "drwxr-xr-x root/root 0 2026-11-18 20:51 ./usr/",
            "-rwxr-xr-x root/root 1234 2026-11-18 20:51 ./usr/bin/kdeconnect-screenstream-viewer",
            "-rwxr-xr-x root/root 1234 2026-11-18 20:51 ./usr/libexec/kdeconnectd",
        ]
        good_ctrl = ["./", "./conffiles", "./control", "./md5sums", "./postinst", "./postrm"]
        self.assertEqual(
            validate_deb_listing(
                "kdeconnect",
                good_lines,
                expected_version="27.03.70-1",
                deb_version="27.03.70-1",
                ctrl_listing_lines=good_ctrl,
            ),
            [],
        )

        # Reject SUID, outside-allowlist path, traversal symlink, version mismatch, bad control file.
        for bad_line in (
            "-rwsr-xr-x root/root 10 2026-11-18 20:51 ./usr/bin/suid-bin",
            "-rw-r--r-- root/root 10 2026-11-18 20:51 ./etc/cron.d/evil",
            "lrwxrwxrwx root/root 0 2026-11-18 20:51 ./usr/bin/x -> ../../etc/shadow",
            "-rwxr-xr-x root/root 10 2026-11-18 20:51 ./usr/local/bin/evil",
        ):
            self.assertNotEqual(validate_deb_listing("kdeconnect", [bad_line]), [], bad_line)

        self.assertNotEqual(
            validate_deb_listing("kdeconnect", good_lines, expected_version="27.03.70-2", deb_version="27.03.70-1"),
            [],
        )
        self.assertNotEqual(
            validate_deb_listing("kdeconnect", good_lines, ctrl_listing_lines=["./control", "./evilhook"]),
            [],
        )

    def test_verify_deb_script_uses_hardened_airgapped_docker_and_readelf(self) -> None:
        script = (ROOT / "tools" / "verify-deb.sh").read_text(encoding="utf-8")
        self.assertIn("--network none", script)
        self.assertIn("--security-opt=no-new-privileges:true", script)
        self.assertIn("--cap-drop=ALL", script)
        self.assertIn("--pids-limit=256", script)
        self.assertIn("docker image prune -f", script)
        self.assertNotIn("docker image prune -a", script)
        self.assertIn('docker builder prune -f --filter "until=168h"', script)
        self.assertIn("readelf -d /usr/bin/kdeconnect-screenstream-viewer", script)
        self.assertNotIn("ldd ", script)

        ov_hash = compute_overlay_sha256(ROOT / "obs" / "kdeconnect" / "debian")
        self.assertEqual(len(ov_hash), 64)
        fp = compute_cycle_fingerprint("a" * 40, "b" * 40, ov_hash)
        self.assertEqual(len(fp), 64)


if __name__ == "__main__":
    unittest.main()
