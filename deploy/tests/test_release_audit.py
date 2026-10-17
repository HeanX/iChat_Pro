"""Tests for the release audit trail and approval gate (P4 T50 / Issue #177).

Issue #177 asks for a release process where *who* released *what*, *when* is
traceable and a stable version can be restored. That lives in deploy.sh:

  * every successful release appends one structured line to DEPLOYMENTS.log
    carrying ts/sha/target/kind/actor/via/from/smoke/approval;
  * a release without an approval reference is refused before anything on the
    running service is touched;
  * a rollback (--commit) stays available without an approval and is recorded
    as kind=rollback;
  * `--status` reports recent releases and what is running.

As with test_deploy_gate.py, running deploy.sh end-to-end is impossible (it
clones into /opt/ichat and restarts systemd), so the relevant blocks are
extracted from the script by their markers instead of being copied.
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

HELPERS_START = "deploy_actor() {"
HELPERS_END = "# --- --status"
APPROVAL_START = "# --- approval gate (P4 T50 / #177) ---"
# The audit line begins with this literal printf (a raw backslash-n in the
# script, so the needle must keep it literal rather than becoming a newline).
RECORD_START = (
    r"printf 'ts=%s sha=%s target=%s kind=%s actor=%s via=%s from=%s"
    r" smoke=%s approval=%s\n'"
)


def script_lines():
    return DEPLOY_SCRIPT.read_text(encoding="utf-8").splitlines()


def find_line(needle, *, start=0, after=None):
    lines = script_lines()
    for i, line in enumerate(lines):
        if i < start:
            continue
        if after is not None:
            # Match a stripped-equal line, used for block terminators.
            if i > after and line.strip() == needle.strip():
                return i
            continue
        if needle in line:
            return i
    raise AssertionError(f"{needle!r} not found in deploy.sh")


def extract(start_marker, end_marker=None):
    """Return the script lines from start_marker up to (not incl.) end_marker."""
    lines = script_lines()
    try:
        start = next(i for i, line in enumerate(lines) if start_marker in line)
    except StopIteration:  # pragma: no cover - guards a rename
        raise AssertionError(f"{start_marker!r} not found in deploy.sh")
    stop = len(lines)
    if end_marker:
        try:
            stop = next(
                i for i, line in enumerate(lines) if i > start and end_marker in line
            )
        except StopIteration:  # pragma: no cover
            raise AssertionError(f"{end_marker!r} not found after {start_marker!r}")
    return "\n".join(lines[start:stop])


def extract_until_blank_fi(start_needle):
    """Extract a top-level `if ... fi` block starting at start_needle.

    Tracks nesting so the inner `fi` lines of the --status block do not close
    it early.
    """
    lines = script_lines()
    start = next(i for i, line in enumerate(lines) if start_needle in line)
    depth = 0
    for i in range(start, len(lines)):
        stripped = lines[i].strip()
        depth += stripped.count("if ")
        if stripped == "fi":
            depth -= 1
            if depth == 0:
                return "\n".join(lines[start:i + 1])
    raise AssertionError(f"unterminated block starting {start_needle!r}")


def helpers_block():
    return extract(HELPERS_START, HELPERS_END)


@unittest.skipUnless(HAS_BASH, "bash is not available; deploy.sh not executed")
class AuditFieldTests(unittest.TestCase):
    """actor/via/from/approval must be accurate and injection-safe."""

    def evaluate(self, expr, env):
        """Run an expression with the real helper functions sourced."""
        workdir = Path(tempfile.mkdtemp(prefix="ichat-audit-"))
        self.addCleanup(shutil.rmtree, workdir, True)
        harness = workdir / "h.sh"
        harness.write_text(
            "#!/usr/bin/env bash\nset -uo pipefail\n"
            + helpers_block()
            + "\nprintf '%s' \"$(" + expr + ")\"\n",
            encoding="utf-8",
        )
        harness.chmod(0o755)
        base_env = {"PATH": os.environ.get("PATH", ""), "TMPDIR": os.environ.get("TMPDIR") or os.environ.get("TEMP") or ""}
        base_env.update(env)
        result = subprocess.run(
            [BASH, str(harness)],
            env=base_env,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=60,
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        return result.stdout

    def test_sudo_operator_is_the_actor_not_root(self):
        self.assertEqual(
            self.evaluate("deploy_actor", {"SUDO_USER": "alice", "USER": "root"}),
            "alice",
        )

    def test_ci_deployer_overrides_everything(self):
        self.assertEqual(
            self.evaluate(
                "deploy_actor",
                {"ICHAT_DEPLOYER": "github-actions_runner-7", "SUDO_USER": "alice"},
            ),
            "github-actions_runner-7",
        )

    def test_actor_cannot_inject_log_fields(self):
        """A hostile actor string must not break the key=value line."""
        actor = self.evaluate(
            "deploy_actor", {"ICHAT_DEPLOYER": "evil sha=deadbeef smoke=passed"}
        )
        self.assertNotIn(" ", actor)
        self.assertNotIn("=", actor)

    def test_via_distinguishes_sudo_from_direct(self):
        self.assertEqual(self.evaluate("deploy_via", {"SUDO_USER": "alice"}), "sudo")
        self.assertEqual(self.evaluate("deploy_via", {}), "direct")

    def test_origin_records_the_ssh_client(self):
        self.assertEqual(
            self.evaluate(
                "deploy_origin",
                {"SSH_CONNECTION": "203.0.113.7 51234 10.0.0.1 22"},
            ),
            "203.0.113.7",
        )

    def test_origin_falls_back_to_local_console(self):
        self.assertEqual(self.evaluate("deploy_origin", {}), "local-console")

    def test_approval_defaults_to_none(self):
        self.assertEqual(self.evaluate("deploy_approval", {}), "none")

    def test_approval_keeps_ticket_shaped_references(self):
        self.assertEqual(
            self.evaluate("deploy_approval", {"ICHAT_APPROVAL": "#177"}),
            "#177",
        )


@unittest.skipUnless(HAS_BASH, "bash is not available; deploy.sh not executed")
class ApprovalGateTests(unittest.TestCase):
    """An unapproved release must be refused and must change nothing."""

    def run_gate(self, *, approval, commit, require="1"):
        """Run the approval block with the same variables deploy.sh sets."""
        workdir = Path(tempfile.mkdtemp(prefix="ichat-approval-"))
        self.addCleanup(shutil.rmtree, workdir, True)
        log = workdir / "DEPLOYMENTS.log"
        log.touch()
        harness = workdir / "h.sh"
        harness.write_text(
            "#!/usr/bin/env bash\nset -uo pipefail\n"
            + helpers_block()
            + "\n"
            + f"LOG_FILE={log.as_posix()!r}\n"
            + "BIN_COPY=/opt/ichat/bin/deploy.sh\n"
            + "DEPLOYED_SHA=deadbeef\n"
            + f"COMMIT={commit!r}\n"
            + f"ICHAT_REQUIRE_APPROVAL={require!r}\n"
            + f"ICHAT_APPROVAL={approval!r}\n"
            + extract(APPROVAL_START, "# --- python env ---")
            + "\necho GATE_PASSED\n",
            encoding="utf-8",
        )
        harness.chmod(0o755)
        env = {
            "PATH": os.environ.get("PATH", ""),
            "TMPDIR": os.environ.get("TMPDIR") or os.environ.get("TEMP") or "",
            "USER": "tester",
        }
        result = subprocess.run(
            [BASH, str(harness)],
            env=env,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=60,
        )
        return result, log.read_text(encoding="utf-8")

    def test_release_without_approval_is_refused(self):
        result, log = self.run_gate(approval="none", commit="")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("no approval reference", result.stderr)
        self.assertNotIn("GATE_PASSED", result.stdout)
        self.assertEqual(log, "")  # nothing recorded for a refused release

    def test_release_with_approval_proceeds(self):
        result, _ = self.run_gate(approval="#177", commit="")
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("GATE_PASSED", result.stdout)

    def test_rollback_needs_no_approval(self):
        """The escape hatch must never depend on paperwork."""
        result, _ = self.run_gate(approval="none", commit="cafebabe")
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("GATE_PASSED", result.stdout)

    def test_emergency_override_is_allowed_but_warned(self):
        result, _ = self.run_gate(approval="none", commit="", require="0")
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("ICHAT_APPROVAL is unset", result.stderr)

    def test_approval_gate_runs_before_touching_the_service(self):
        """Position matters: the refusal must precede migrate/restart."""
        text = DEPLOY_SCRIPT.read_text(encoding="utf-8")
        gate_at = text.index(APPROVAL_START)
        for later in ("migrate --noinput", "systemctl restart ichat.service"):
            self.assertLess(
                gate_at,
                text.index(later),
                msg=f"the approval gate must run before {later!r}",
            )


@unittest.skipUnless(HAS_BASH, "bash is not available; deploy.sh not executed")
class ReleaseRecordTests(unittest.TestCase):
    """The DEPLOYMENTS.log line must carry who/what/when/how-gated."""

    def run_record(self, *, commit, smoke="passed", approval="#177", actor="alice"):
        workdir = Path(tempfile.mkdtemp(prefix="ichat-record-"))
        self.addCleanup(shutil.rmtree, workdir, True)
        log = workdir / "DEPLOYMENTS.log"
        log.touch()
        repo = workdir / "repo"
        repo.mkdir()
        harness = workdir / "h.sh"
        harness.write_text(
            "#!/usr/bin/env bash\nset -uo pipefail\n"
            + helpers_block()
            + "\n"
            + f"LOG_FILE={log.as_posix()!r}\n"
            + f"REPO_DIR={repo.as_posix()!r}\n"
            + f"STATE_DIR={(workdir / '.deploy-state').as_posix()!r}\n"
            + "DEPLOYED_SHA=deadbeef\n"
            + "TARGET=main\n"
            + f"COMMIT={commit!r}\n"
            + f"SMOKE_RESULT={smoke!r}\n"
            + f"ICHAT_DEPLOYER={actor!r}\n"
            + (
                "RELEASE_KIND=rollback\n" if commit else "RELEASE_KIND=deploy\n"
            )
            + extract(RECORD_START, "exit 0")
            + "\nexit 0\n",
            encoding="utf-8",
        )
        harness.chmod(0o755)
        env = {
            "PATH": os.environ.get("PATH", ""),
            "TMPDIR": os.environ.get("TMPDIR") or os.environ.get("TEMP") or "",
            "USER": "tester",
            "ICHAT_APPROVAL": approval,
        }
        result = subprocess.run(
            [BASH, str(harness)],
            env=env,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=60,
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        return log.read_text(encoding="utf-8")

    def test_line_carries_every_audit_field(self):
        line = self.run_record(commit="").strip()
        for field in (
            "ts=", "sha=deadbeef", "target=main", "kind=deploy",
            "actor=alice", "via=", "from=", "smoke=passed", "approval=#177",
        ):
            self.assertIn(field, line, msg=f"{field!r} missing from: {line}")

    def test_rollback_is_recorded_as_rollback(self):
        line = self.run_record(commit="cafebabe").strip()
        self.assertIn("kind=rollback", line)

    def test_skipped_smoke_is_visible_in_the_log(self):
        line = self.run_record(commit="", smoke="skipped").strip()
        self.assertIn("smoke=skipped", line)


@unittest.skipUnless(HAS_BASH, "bash is not available; deploy.sh not executed")
class StatusOutputTests(unittest.TestCase):
    """--status must answer "what is running" and "what came before"."""

    def test_status_lists_releases_and_running_sha(self):
        workdir = Path(tempfile.mkdtemp(prefix="ichat-status-"))
        self.addCleanup(shutil.rmtree, workdir, True)
        log = workdir / "DEPLOYMENTS.log"
        log.write_text(
            "ts=2026-10-18T00:00:00Z sha=aaaa target=main kind=deploy "
            "actor=alice via=sudo from=203.0.113.7 smoke=passed approval=#177\n"
            "ts=2026-10-18T01:00:00Z sha=bbbb target=main kind=deploy "
            "actor=bob via=sudo from=203.0.113.8 smoke=passed approval=#178\n",
            encoding="utf-8",
        )
        harness = workdir / "h.sh"
        harness.write_text(
            "#!/usr/bin/env bash\nset -uo pipefail\n"
            + f"LOG_FILE={log.as_posix()!r}\n"
            + "REPO_DIR=/nonexistent-repo\n"
            + "SHOW_STATUS=1\nVERBOSE=0\nICHAT_STATUS_LINES=5\n"
            + extract_until_blank_fi('if [[ "$SHOW_STATUS" == "1" ]]; then')
            + "\nexit 0\n",
            encoding="utf-8",
        )
        harness.chmod(0o755)
        result = subprocess.run(
            [BASH, str(harness)],
            env={
                "PATH": os.environ.get("PATH", ""),
                "TMPDIR": os.environ.get("TMPDIR") or os.environ.get("TEMP") or "",
            },
            capture_output=True,
            text=True,
            errors="replace",
            timeout=60,
        )
        output = result.stdout + result.stderr
        self.assertIn("sha=aaaa", output)
        self.assertIn("sha=bbbb", output)
        self.assertIn("Running now:", output)
