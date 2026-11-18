# SPDX-FileCopyrightText: 2026 Dmitriy Volkov <dennshield@duck.com>
# SPDX-License-Identifier: GPL-2.0-only OR GPL-3.0-only OR LicenseRef-KDE-Accepted-GPL

import getpass
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import tools.obs_controller as obs_controller
from tools.check_public_tree import _iter_worktree_entries, scan_repository
from tools.install_cron import BEGIN, END, job, merge, remove_block
from tools.obs_controller import (
    build_osc_command,
    evaluate_package_promotion_readiness,
    evaluate_promotion_readiness,
    parse_obs_build_log,
    parse_obs_result_states,
    prune_old_artifacts,
    verify_controller_environment,
)
from tools.obs_packaging import strip_spdx_comments

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

    def test_build_osc_command_isolates_cookiejar_and_disables_keyring(self) -> None:
        cmd = build_osc_command("results", state_dir=Path("/var/tmp/state"))
        self.assertEqual(
            cmd,
            ["osc", "--no-keyring", "--setopt", "cookiejar=/var/tmp/state/osc-cookiejar", "results"],
        )

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

    def test_package_promotion_gate_handles_kdeconnect_and_libei(self) -> None:
        self.assertFalse(
            evaluate_package_promotion_readiness(
                package="kdeconnect",
                staging_state="succeeded",
                ctest_ran=0,
                ctest_failed=0,
                lintian_errors=0,
                deb_verified=True,
            ).ready
        )
        self.assertTrue(
            evaluate_package_promotion_readiness(
                package="libei",
                staging_state="succeeded",
                ctest_ran=0,
                ctest_failed=0,
                lintian_errors=0,
                deb_verified=True,
            ).ready
        )
        self.assertFalse(
            evaluate_package_promotion_readiness(
                package="libei",
                staging_state="succeeded",
                ctest_ran=0,
                ctest_failed=0,
                lintian_errors=1,
                deb_verified=True,
            ).ready
        )
        self.assertFalse(
            evaluate_package_promotion_readiness(
                package="libei",
                staging_state="failed",
                ctest_ran=0,
                ctest_failed=0,
                lintian_errors=0,
                deb_verified=True,
            ).ready
        )
        self.assertFalse(
            evaluate_package_promotion_readiness(
                package="libei",
                staging_state="succeeded",
                ctest_ran=0,
                ctest_failed=0,
                lintian_errors=0,
                deb_verified=False,
            ).ready
        )

    def test_parse_obs_build_log_extracts_ctest_lintian_and_apk_verification(self) -> None:
        desktop_log = (
            "[  100s] Test project /build/obj-x86_64-linux-gnu\n"
            "[  102s] 1/6 Test #1: notificationstest .........   Passed    0.12 sec\n"
            "[  104s] 100% tests passed, 0 tests failed out of 6\n"
            "[  120s] W: kdeconnect: no-manual-page\n"
            "[  121s] Running lintian --fail-on error\n"
        )
        metrics = parse_obs_build_log(desktop_log)
        self.assertEqual(metrics.ctest_ran, 6)
        self.assertEqual(metrics.ctest_failed, 0)
        self.assertEqual(metrics.lintian_errors, 0)
        self.assertFalse(metrics.apk_verified)

        failing_log = (
            "83% tests passed, 1 tests failed out of 6\n"
            "E: kdeconnect: arch-dependent-file-in-usr-share [usr/share/foo]\n"
            "E: kdeconnect-libs: missing-dependency-on-libc\n"
        )
        fail_metrics = parse_obs_build_log(failing_log)
        self.assertEqual(fail_metrics.ctest_ran, 6)
        self.assertEqual(fail_metrics.ctest_failed, 1)
        self.assertEqual(fail_metrics.lintian_errors, 2)

        probe_log = (
            "Verifies\n"
            "Verified using v2 scheme (APK Signature Scheme v2): true\n"
            "APK_VERIFIED=1\n"
        )
        probe_metrics = parse_obs_build_log(probe_log)
        self.assertTrue(probe_metrics.apk_verified)
        self.assertEqual(probe_metrics.lintian_errors, 0)

    def test_parse_obs_result_states_tracks_dirty_and_status(self) -> None:
        xml = (
            '<resultlist state="00">\n'
            '  <result project="home:dwolfF:kdeconnect:staging" repository="xUbuntu_26.04" '
            'arch="x86_64" code="building" state="building" dirty="true">\n'
            '    <status package="kdeconnect" code="succeeded"/>\n'
            '  </result>\n'
            '  <result project="home:dwolfF:kdeconnect:staging" repository="xUbuntu_26.04" '
            'arch="x86_64" code="published" state="published">\n'
            '    <status package="libei" code="succeeded"/>\n'
            '  </result>\n'
            '</resultlist>\n'
        )
        entries = parse_obs_result_states(xml)
        self.assertEqual(entries[0]["package"], "kdeconnect")
        self.assertEqual(entries[0]["code"], "succeeded")
        self.assertTrue(entries[0]["dirty"])
        self.assertEqual(entries[1]["package"], "libei")
        self.assertFalse(entries[1]["dirty"])

    def test_prjconf_strips_spdx_headers_and_preserves_solver_preferences(self) -> None:
        staging_conf = strip_spdx_comments((ROOT / "obs" / "staging.prjconf").read_text(encoding="utf-8"))
        stable_conf = strip_spdx_comments((ROOT / "obs" / "stable.prjconf").read_text(encoding="utf-8"))
        self.assertNotIn("SPDX-", staging_conf)
        self.assertNotIn("SPDX-", stable_conf)
        self.assertIn("PublishFlags: nofailedpackages", staging_conf)
        self.assertIn("Prefer: gpg", staging_conf)
        self.assertIn("Prefer: libavcodec62", staging_conf)
        self.assertIn("Prefer: libavformat62", staging_conf)
        self.assertIn("Prefer: libavfilter11", staging_conf)
        self.assertIn("PublishFlags: nofailedpackages", stable_conf)

    def test_public_workspace_has_no_private_identifiers_or_secrets(self) -> None:
        issues = scan_repository(ROOT, staged_only=False, skip_state_dir=True)
        self.assertEqual(issues, [], f"Public tree check failed: {issues}")

        generic_tokens = {"root", "user", "ubuntu", "tester", "runner", "localhost"}
        runtime_tokens = {
            token
            for token in (getpass.getuser(), socket.gethostname().split(".", 1)[0])
            if len(token) >= 4 and token not in generic_tokens
        }
        for rel, data in _iter_worktree_entries(ROOT, skip_state_dir=True):
            text = data.decode("utf-8")
            for token in runtime_tokens:
                self.assertNotRegex(
                    text,
                    rf"\b{re.escape(token)}\b",
                    f"{rel} leaked local runtime identifier",
                )

    def test_promote_toctou_srcmd5_guard_and_prune_old_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            bin_root = state / "binaries" / "kdeconnect"
            logs_root = state / "logs"
            bin_root.mkdir(parents=True)
            logs_root.mkdir(parents=True)

            hashes = [f"{i:032x}" for i in range(1, 6)]
            for idx, h in enumerate(hashes):
                d = bin_root / h
                d.mkdir()
                log_f = logs_root / f"kdeconnect-{h}.log"
                log_f.write_text("log", encoding="utf-8")
                mtime = 1000 + idx * 10
                os.utime(d, (mtime, mtime))
                os.utime(log_f, (mtime, mtime))

            verified_hash = hashes[4]
            other_hash = hashes[0]
            latest_meta = {
                "srcmd5": verified_hash,
                "code": "succeeded",
                "ctest_ran": 8,
                "ctest_failed": 0,
                "lintian_errors": 0,
                "binaries": [],
            }
            (bin_root / "latest.json").write_text(json.dumps(latest_meta), encoding="utf-8")

            with mock.patch.object(obs_controller, "STATE", state):
                # Expect TOCTOU rejection when expected_srcmd5 differs from latest.json.
                rc = obs_controller._cmd_promote(
                    "staging",
                    "stable",
                    "kdeconnect",
                    "xUbuntu_26.04",
                    "x86_64",
                    deb_verified=True,
                    wait=False,
                    timeout_sec=10,
                    expected_srcmd5=other_hash,
                )
                self.assertEqual(rc, 1)

                # Expect TOCTOU rejection when live OBS source differs from verified srcmd5.
                with mock.patch.object(
                    obs_controller,
                    "_osc_api",
                    return_value=f'<directory srcmd5="{other_hash}"/>',
                ):
                    rc_live = obs_controller._cmd_promote(
                        "staging",
                        "stable",
                        "kdeconnect",
                        "xUbuntu_26.04",
                        "x86_64",
                        deb_verified=True,
                        wait=False,
                        timeout_sec=10,
                        expected_srcmd5=verified_hash,
                    )
                    self.assertEqual(rc_live, 1)

            # Protect promoted hash even if older, keep fresh temp file, sweep stale temp/bundles.
            prom_dir = state / "promotions"
            prom_dir.mkdir()
            (prom_dir / "kdeconnect.json").write_text(json.dumps({"srcmd5": other_hash}), encoding="utf-8")

            tmp_dir = state / "tmp"
            bundles_dir = state / "bundles"
            tmp_dir.mkdir()
            bundles_dir.mkdir()
            stale_tmp = tmp_dir / "old-scratch"
            fresh_tmp = tmp_dir / "active-scratch"
            stale_bundle = bundles_dir / "old.tar.xz"
            stale_tmp.mkdir()
            fresh_tmp.mkdir()
            stale_bundle.write_text("stale", encoding="utf-8")
            old_ts = time.time() - 10000
            os.utime(stale_tmp, (old_ts, old_ts))
            os.utime(stale_bundle, (old_ts, old_ts))

            res = prune_old_artifacts("kdeconnect", keep=2, state_dir=state, sweep_tmp_age_sec=3600)
            self.assertEqual(set(res["removed_dirs"]), {hashes[1], hashes[2]})
            self.assertEqual(set(res["removed_logs"]), {f"kdeconnect-{hashes[1]}.log", f"kdeconnect-{hashes[2]}.log"})
            self.assertEqual(set(res["removed_tmp"]), {"tmp/old-scratch", "bundles/old.tar.xz"})
            self.assertTrue((bin_root / other_hash).is_dir())
            self.assertTrue((bin_root / verified_hash).is_dir())
            self.assertTrue((bin_root / hashes[3]).is_dir())
            self.assertTrue(fresh_tmp.is_dir())

    def test_cycle_idempotent_noop_and_circuit_breaker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "hub"
            state = root / ".state"
            repo = state / "repos" / "kdeconnect-kde"
            overlay = root / "obs" / "kdeconnect" / "debian"
            (state / "sync").mkdir(parents=True)
            overlay.mkdir(parents=True)
            (overlay / "control").write_text("Source: kdeconnect\n", encoding="utf-8")
            (repo / ".git").mkdir(parents=True)
            upstream_sha = "a" * 40
            plugin_sha = "b" * 40
            base_sha = "c" * 40
            (state / "sync" / "plugin_base.txt").write_text(base_sha + "\n", encoding="utf-8")

            from tools.upstream_sync import compute_cycle_fingerprint, compute_overlay_sha256

            fp = compute_cycle_fingerprint(upstream_sha, plugin_sha, compute_overlay_sha256(overlay))

            def fake_git(_repo: Path | None, *args: str, **_kwargs: object) -> subprocess.CompletedProcess:
                if args == ("remote", "get-url", "origin"):
                    return subprocess.CompletedProcess([], 0, stdout="https://invent.kde.org/network/kdeconnect-kde.git\n")
                if args == ("fetch", "origin", "master"):
                    return subprocess.CompletedProcess([], 0, stdout="")
                if args == ("rev-parse", "refs/remotes/origin/master"):
                    return subprocess.CompletedProcess([], 0, stdout=upstream_sha + "\n")
                if args == ("rev-parse", "refs/heads/plugin/screenstream"):
                    return subprocess.CompletedProcess([], 0, stdout=plugin_sha + "\n")
                raise AssertionError(f"Unexpected git call: {args}")

            with (
                mock.patch.object(obs_controller, "ROOT", root),
                mock.patch.object(obs_controller, "STATE", state),
                mock.patch.object(obs_controller, "_cmd_verify_env", return_value=0),
                mock.patch.object(obs_controller, "check_android_upstream_drift", return_value={"status": "ok"}),
                mock.patch.object(obs_controller, "run_git_hardened", side_effect=fake_git),
                mock.patch.object(obs_controller, "synthesize_plugin_branch") as synth_mock,
            ):
                # 1. Idempotent no-op when promoted_fingerprint matches.
                (state / "sync" / "kdeconnect.json").write_text(
                    json.dumps({"promoted_fingerprint": fp}),
                    encoding="utf-8",
                )
                self.assertEqual(obs_controller._cmd_cycle(wait_sec=0), 0)
                synth_mock.assert_not_called()

                # 2. Circuit breaker skips previously failed_gate candidate with same fingerprint.
                (state / "sync" / "kdeconnect.json").write_text(
                    json.dumps({"active_candidate": {"fingerprint": fp, "stage": "failed_gate"}}),
                    encoding="utf-8",
                )
                self.assertEqual(obs_controller._cmd_cycle(wait_sec=0), 0)
                synth_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
