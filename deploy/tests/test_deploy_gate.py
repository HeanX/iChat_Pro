"""Tests for deploy.sh's gate wiring (P4 Test T23 / Issue #204).

``deploy.sh`` is a server-only script: it clones into /opt/ichat, restarts
systemd units and probes Nginx. Running it end-to-end here is impossible, so
these tests source its *gate block* in isolation and pin the property the
Issue actually depends on:

    a failed smoke check must leave DEPLOYMENTS.log untouched

The block under test is extracted from the script by its markers rather than
copied, so the test breaks if the real logic changes shape.
"""

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEPLOY_SCRIPT = REPO_ROOT / "deploy" / "deploy.sh"

BASH = shutil.which("bash")
HAS_BASH = BASH is not None and DEPLOY_SCRIPT.is_file()

# The gate block runs from its comment banner to the final `exit 0`. Extract it
# verbatim so the test cannot drift from the script.
START_MARKER = "# --- smoke gate (P4 Test T23 / #204) ---"


def extract_gate_block():
    lines = DEPLOY_SCRIPT.read_text(encoding="utf-8").splitlines()
    try:
        start = next(
            i for i, line in enumerate(lines) if line.strip() == START_MARKER
        )
    except StopIteration:  # pragma: no cover - guards a rename
        raise AssertionError(f"{START_MARKER!r} not found in deploy.sh")
    return "\n".join(lines[start:])


@unittest.skipUnless(HAS_BASH, "bash is not available; deploy.sh not executed")
class DeployGateLoggingTests(unittest.TestCase):
    """A failing gate must not record the release as successful."""

    def run_gate(self, *, skip, has_script, have_cred, smoke_rc):
        """Run the extracted gate block with stubbed dependencies.

        A fake smoke.sh returns ``smoke_rc``. The log file is inspected after
        the block finishes.
        """
        block = extract_gate_block()
        workdir = Path(tempfile.mkdtemp(prefix="ichat-gate-"))
        self.addCleanup(shutil.rmtree, workdir, True)

        # The block looks for "$REPO_DIR/deploy/smoke.sh", so mirror that
        # layout inside the scratch tree.
        repo_dir = workdir / "repo"
        (repo_dir / "deploy").mkdir(parents=True)
        script = repo_dir / "deploy" / "smoke.sh"
        if has_script:
            script.write_text(
                "#!/usr/bin/env bash\n"
                f'exit {smoke_rc}\n',
                encoding="utf-8",
            )
            script.chmod(0o755)

        log_file = workdir / "DEPLOYMENTS.log"
        log_file.touch()

        harness = workdir / "harness.sh"
        harness.write_text(
            "#!/usr/bin/env bash\n"
            "set -uo pipefail\n"
            f'REPO_DIR={repo_dir.as_posix()!r}\n'
            f'LOG_FILE={log_file.as_posix()!r}\n'
            f'APP_DIR={workdir.as_posix()!r}\n'
            f'VENV_DIR={(workdir / ".venv").as_posix()!r}\n'
            f'STATE_DIR={(workdir / ".deploy-state").as_posix()!r}\n'
            f'BIN_COPY=/opt/ichat/bin/deploy.sh\n'
            "DEPLOYED_SHA=deadbeef\n"
            "TARGET=main\n"
            # A plain deploy: no --commit, so the record says kind=deploy.
            "COMMIT=\n"
            "IS_ROLLBACK=0\n"
            f'ICHAT_SKIP_SMOKE={"1" if skip else "0"}\n'
            + (
                "SMOKE_USERNAME=probe\nSMOKE_PASSWORD=secret\n"
                if have_cred
                else "unset SMOKE_USERNAME SMOKE_PASSWORD\n"
            )
            + block
            + "\n",
            encoding="utf-8",
        )
        harness.chmod(0o755)

        # smoke.sh is invoked as `bash <path>`, so a non-executable stub is
        # fine; the block's own messaging is what we assert on.
        env = {
            "PATH": os.environ.get("PATH", ""),
            "TMPDIR": os.environ.get("TMPDIR") or os.environ.get("TEMP") or "",
        }
        result = subprocess.run(
            [BASH, str(harness)],
            env=env,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=60,
            cwd=str(REPO_ROOT),
        )
        return result, log_file.read_text(encoding="utf-8")

    def test_passing_smoke_records_the_release(self):
        result, log = self.run_gate(
            skip=False, has_script=True, have_cred=True, smoke_rc=0
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("smoke=passed", log)
        self.assertIn("deadbeef", log)

    def test_failing_smoke_leaves_no_log_entry(self):
        result, log = self.run_gate(
            skip=False, has_script=True, have_cred=True, smoke_rc=1
        )
        self.assertNotEqual(result.returncode, 0, msg="the deploy must fail")
        self.assertEqual(
            log, "",
            msg="a failed smoke gate must not append to DEPLOYMENTS.log",
        )
        self.assertIn("NOT recorded", result.stderr)

    def test_missing_credentials_fail_closed_and_log_nothing(self):
        result, log = self.run_gate(
            skip=False, has_script=True, have_cred=False, smoke_rc=0
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(log, "")
        self.assertIn("SMOKE_USERNAME/SMOKE_PASSWORD", result.stderr)

    def test_explicit_skip_is_recorded_as_skipped(self):
        result, log = self.run_gate(
            skip=True, has_script=True, have_cred=False, smoke_rc=1
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("smoke=skipped", log)
        self.assertNotIn("smoke=passed", log)

    def test_rollback_to_a_tree_without_smoke_sh_skips_loudly(self):
        """Rolling back past #204 must not brick the documented escape hatch."""
        result, log = self.run_gate(
            skip=False, has_script=False, have_cred=True, smoke_rc=0
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("predates deploy/smoke.sh", result.stderr)
        self.assertIn("smoke=skipped-no-script", log)

    def test_success_output_matches_the_logged_result(self):
        """The terminal must not say "passed" while the log says "skipped"."""
        result, log = self.run_gate(
            skip=True, has_script=True, have_cred=False, smoke_rc=0
        )
        self.assertIn("smoke: skipped", result.stdout)
        self.assertNotIn("smoke: passed", result.stdout)
