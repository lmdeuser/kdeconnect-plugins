# SPDX-FileCopyrightText: 2026 Dmitriy Volkov <dennshield@duck.com>
# SPDX-License-Identifier: GPL-2.0-only OR GPL-3.0-only OR LicenseRef-KDE-Accepted-GPL

import getpass
import re
import socket
import sys
import tempfile
import unittest
from pathlib import Path

from tools.check_public_tree import scan_repository
from tools.install_cron import BEGIN, END, job, merge, remove_block
from tools.obs_controller import evaluate_promotion_readiness, verify_controller_environment

ROOT = Path(__file__).resolve().parents[1]


class RunnerTests(unittest.TestCase):
    def test_cron_merge_and_remove_preserve_existing_blocks(self) -> None:
        previous = (
            "# BEGIN AMNEZIA SPLIT\n"
            "15 22 * * * /var/opt/amnezia-split/update.sh\n"
            "# END AMNEZIA SPLIT\n"
            "# BEGIN ANTIGRAVITY OBS\n"
            "17 * * * * /usr/bin/timeout 80m /var/opt/runners/antigravity/tools/cron-cycle.sh\n"
            "# END ANTIGRAVITY OBS\n"
            "# BEGIN AMNEZIA OBS\n"
            "37 * * * * /usr/bin/timeout 100m /var/opt/runners/amnezia-vpn/tools/cron-cycle.sh\n"
            "# END AMNEZIA OBS\n"
        )
        updated = merge(previous)
        self.assertTrue(updated.startswith(previous))
        self.assertIn("47 * * * * /usr/bin/timeout 90m", job())
        self.assertIn(str(ROOT / "tools/cron-cycle.sh"), job())
        self.assertEqual(merge(updated), updated)
        self.assertEqual(remove_block(updated), previous)
        self.assertEqual(remove_block(previous), previous)

    def test_cron_rejects_malformed_markers(self) -> None:
        with self.assertRaises(ValueError):
            merge(f"{END}\n{BEGIN}\n")
        with self.assertRaises(ValueError):
            remove_block(f"{BEGIN}\n{BEGIN}\n{END}\n")
        with self.assertRaises(ValueError):
            merge(f"{BEGIN}\n")

    def test_run_controller_runs_host_python_directly_without_docker(self) -> None:
        controller = (ROOT / "tools/run-controller.sh").read_text(encoding="utf-8")
        self.assertNotIn("docker run", controller)
        self.assertIn("findmnt -n -o FSTYPE", controller)
        self.assertIn("XDG_CONFIG_HOME", controller)
        self.assertIn("OSC_CONFIG", controller)
        self.assertIn("exec python3 tools/obs_controller.py", controller)

    def test_verify_controller_environment_accepts_disk_tmpdir_and_oscrc(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tmpdir = root / ".state" / "tmp"
            tmpdir.mkdir(parents=True)
            oscrc = root / "oscrc"
            oscrc.write_text("[general]\napiurl = https://api.opensuse.org\n", encoding="utf-8")
            result = verify_controller_environment(
                tmpdir=tmpdir,
                osc_config=oscrc,
                osc_binary=sys.executable,
                check_non_tmpfs=False,
            )
            self.assertTrue(result.ok, result.errors)

    def test_verify_controller_environment_rejects_missing_osc_or_oscrc(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp) / "tmp"
            tmpdir.mkdir()
            result = verify_controller_environment(
                tmpdir=tmpdir,
                osc_config=Path(tmp) / "missing_oscrc",
                osc_binary="",
                check_non_tmpfs=False,
            )
            self.assertFalse(result.ok)
            self.assertGreaterEqual(len(result.errors), 2)

    def test_promotion_gate_requires_staging_build_ctest_lintian_and_deb_check(self) -> None:
        self.assertFalse(
            evaluate_promotion_readiness(
                staging_state="succeeded",
                ctest_ran=0,
                ctest_failed=0,
                lintian_errors=0,
                deb_verified=True,
            ).ready
        )
        self.assertFalse(
            evaluate_promotion_readiness(
                staging_state="succeeded",
                ctest_ran=5,
                ctest_failed=1,
                lintian_errors=0,
                deb_verified=True,
            ).ready
        )
        self.assertFalse(
            evaluate_promotion_readiness(
                staging_state="succeeded",
                ctest_ran=5,
                ctest_failed=0,
                lintian_errors=1,
                deb_verified=True,
            ).ready
        )
        self.assertFalse(
            evaluate_promotion_readiness(
                staging_state="succeeded",
                ctest_ran=5,
                ctest_failed=0,
                lintian_errors=0,
                deb_verified=False,
            ).ready
        )
        self.assertTrue(
            evaluate_promotion_readiness(
                staging_state="succeeded",
                ctest_ran=5,
                ctest_failed=0,
                lintian_errors=0,
                deb_verified=True,
            ).ready
        )

    def test_public_workspace_has_no_private_identifiers_or_secrets(self) -> None:
        issues = scan_repository(ROOT, staged_only=False, skip_state_dir=True)
        self.assertEqual(issues, [], f"Public tree check failed: {issues}")

        generic_tokens = {"root", "user", "ubuntu", "tester", "runner", "localhost"}
        runtime_tokens = {
            token
            for token in (getpass.getuser(), socket.gethostname().split(".", 1)[0])
            if len(token) >= 4 and token not in generic_tokens
        }
        for path in sorted(ROOT.rglob("*")):
            if not path.is_file():
                continue
            rel = path.relative_to(ROOT).as_posix()
            if rel.startswith((".git/", ".state/", "LICENSES/")) or "__pycache__" in path.parts or path.suffix == ".pyc":
                continue
            text = path.read_text(encoding="utf-8")
            for token in runtime_tokens:
                self.assertNotRegex(
                    text,
                    rf"\b{re.escape(token)}\b",
                    f"{rel} leaked local runtime identifier",
                )


if __name__ == "__main__":
    unittest.main()
