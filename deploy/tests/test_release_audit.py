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

# The helpers block runs from the first audit helper through the banner that
# starts --status, so sanitise() and every helper drawn from it are included.
HELPERS_START = "# --- release audit helpers ---"
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
            "#!/usr/bin/env bash\nset -euo pipefail\n"
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
        """Run the approval block with the same variables deploy.sh sets.

        ICHAT_APPROVAL/ICHAT_REQUIRE_APPROVAL arrive through the environment so
        whitespace is preserved verbatim; embedding them as shell literals
        would turn a tab into the two characters "\\t".
        """
        workdir = Path(tempfile.mkdtemp(prefix="ichat-approval-"))
        self.addCleanup(shutil.rmtree, workdir, True)
        log = workdir / "DEPLOYMENTS.log"
        log.touch()
        # The extracted range continues past the gate to the python-env
        # banner, and the .env guard sits in between — satisfy it so the test
        # observes the gate's own exit code.
        (workdir / ".env").write_text("", encoding="utf-8")
        harness = workdir / "h.sh"
        harness.write_text(
            "#!/usr/bin/env bash\nset -euo pipefail\n"
            + helpers_block()
            + "\n"
            + f"LOG_FILE={log.as_posix()!r}\n"
            + f"APP_DIR={workdir.as_posix()!r}\n"
            + "BIN_COPY=/opt/ichat/bin/deploy.sh\n"
            + "DEPLOYED_SHA=deadbeef\n"
            + "TARGET=main\n"
            + f"COMMIT={commit!r}\n"
            + extract(APPROVAL_START, "echo \"==> Deploying iChat Pro target=")
            + "\necho GATE_PASSED\n",
            encoding="utf-8",
        )
        harness.chmod(0o755)
        env = {
            "PATH": os.environ.get("PATH", ""),
            "TMPDIR": os.environ.get("TMPDIR") or os.environ.get("TEMP") or "",
            "USER": "tester",
            "ICHAT_APPROVAL": approval,
            "ICHAT_REQUIRE_APPROVAL": require,
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
        sha = "cafebabe" * 5  # 40 hex chars, a real commit id shape
        result, _ = self.run_gate(approval="none", commit=sha)
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("GATE_PASSED", result.stdout)

    def test_a_branch_name_is_not_a_rollback(self):
        """`--commit main` would forward-deploy unapproved code as "rollback"."""
        for ref in ("main", "origin/main", "HEAD", "v1.0", "cafebabe"):
            result, _ = self.run_gate(approval="none", commit=ref)
            self.assertNotEqual(
                result.returncode, 0,
                msg=f"--commit {ref!r} bypassed the approval gate",
            )
            self.assertIn("full 40-character commit SHA", result.stderr)

    def test_a_branch_name_with_approval_still_proceeds(self):
        """The ref is refused for its shape, not for lacking approval."""
        result, _ = self.run_gate(approval="#177", commit="main")
        self.assertIn("full 40-character commit SHA", result.stderr)

    def test_emergency_override_is_allowed_but_warned(self):
        result, _ = self.run_gate(approval="none", commit="", require="0")
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("no approval reference", result.stderr)

    def test_whitespace_only_approval_is_not_an_approval(self):
        """A blank/punctuation value must not slip past the gate.

        The value is passed through the environment (not embedded as a shell
        literal) so a real tab/newline reaches the script — repr() would hand
        bash the two characters "\\t" instead of a tab.
        """
        for blank in (" ", "   ", "\t", "\n", " \t\n ", "!!!", "///"):
            result, _ = self.run_gate(approval=blank, commit="")
            self.assertNotEqual(
                result.returncode, 0,
                msg=f"approval={blank!r} was accepted; it is not an approval",
            )
            self.assertIn("no approval reference", result.stderr)

    def test_unrecognised_require_approval_is_a_config_error(self):
        """A typo must fail loudly, never silently disable the gate."""
        result, _ = self.run_gate(approval="none", commit="", require="true-ish")
        self.assertEqual(result.returncode, 2, msg=result.stdout + result.stderr)
        self.assertIn("not a recognised value", result.stderr)

    def test_truthy_variants_keep_the_gate_on(self):
        for truthy in ("1", "true", "YES", "on"):
            result, _ = self.run_gate(approval="none", commit="", require=truthy)
            self.assertNotEqual(
                result.returncode, 0,
                msg=f"ICHAT_REQUIRE_APPROVAL={truthy} must still require approval",
            )

    def test_approval_gate_runs_before_touching_the_service(self):
        """Position matters: the refusal must precede migrate/restart."""
        text = DEPLOY_SCRIPT.read_text(encoding="utf-8")
        gate_at = text.index(APPROVAL_START)
        for later in ("migrate --noinput", 'systemctl restart "$SERVICE_NAME.service"'):
            self.assertLess(
                gate_at,
                text.index(later),
                msg=f"the approval gate must run before {later!r}",
            )

    def test_approval_gate_runs_before_any_git_operation(self):
        """A refused release must not move the production tree either.

        /opt/ichat/repo is what systemd runs, so fetching or checking out
        before the gate would leave the tree on an unapproved commit and make
        `git rev-parse HEAD` (the documented evidence of what runs) point at a
        commit that was never deployed.
        """
        text = DEPLOY_SCRIPT.read_text(encoding="utf-8")
        gate_at = text.index(APPROVAL_START)
        for earlier_sensitive in (
            "git clone",
            "fetch --quiet origin",
            "checkout --quiet --force",
            "reset --quiet --hard",
        ):
            position = text.index(earlier_sensitive)
            self.assertLess(
                gate_at,
                position,
                msg=(
                    f"the approval gate must run before {earlier_sensitive!r}; "
                    "a refused release must not modify the production tree"
                ),
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
            "#!/usr/bin/env bash\nset -euo pipefail\n"
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
        line = self.run_record(commit="cafebabe" * 5).strip()
        self.assertIn("kind=rollback", line)

    def test_skipped_smoke_is_visible_in_the_log(self):
        line = self.run_record(commit="", smoke="skipped").strip()
        self.assertIn("smoke=skipped", line)


@unittest.skipUnless(HAS_BASH, "bash is not available; deploy.sh not executed")
class DeployStateTests(unittest.TestCase):
    """current/previous must follow deployments, not git reflog.

    The rollback target an operator is told to use has to be the release that
    was superseded, so the state file is updated from its own previous
    contents rather than from HEAD@{1} (which tracks fetches and checkouts).
    """

    def run_record(self, state_dir, sha):
        workdir = Path(tempfile.mkdtemp(prefix="ichat-state-"))
        self.addCleanup(shutil.rmtree, workdir, True)
        log = workdir / "DEPLOYMENTS.log"
        log.touch()
        harness = workdir / "h.sh"
        harness.write_text(
            "#!/usr/bin/env bash\nset -euo pipefail\n"
            + helpers_block()
            + "\n"
            + f"LOG_FILE={log.as_posix()!r}\n"
            + f"REPO_DIR={(workdir / 'repo').as_posix()!r}\n"
            + f"STATE_DIR={state_dir.as_posix()!r}\n"
            + f"DEPLOYED_SHA={sha!r}\n"
            + "TARGET=main\nCOMMIT=\nSMOKE_RESULT=passed\nICHAT_DEPLOYER=tester\n"
            + (
                "RELEASE_KIND=deploy\n"
            )
            + extract(RECORD_START, "exit 0")
            + "\nexit 0\n",
            encoding="utf-8",
        )
        harness.chmod(0o755)
        result = subprocess.run(
            [BASH, str(harness)],
            env={
                "PATH": os.environ.get("PATH", ""),
                "TMPDIR": os.environ.get("TMPDIR") or os.environ.get("TEMP") or "",
                "USER": "tester",
            },
            capture_output=True,
            text=True,
            errors="replace",
            timeout=60,
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        return result

    def test_previous_follows_the_last_deployed_sha(self):
        workdir = Path(tempfile.mkdtemp(prefix="ichat-state-root-"))
        self.addCleanup(shutil.rmtree, workdir, True)
        state = workdir / "state"

        self.run_record(state, "aaaa")
        self.assertEqual((state / "current").read_text().strip(), "aaaa")
        self.assertFalse((state / "previous").exists())

        self.run_record(state, "bbbb")
        self.assertEqual((state / "current").read_text().strip(), "bbbb")
        self.assertEqual((state / "previous").read_text().strip(), "aaaa")

    def test_redeploying_the_same_sha_does_not_invent_a_previous(self):
        workdir = Path(tempfile.mkdtemp(prefix="ichat-state-same-"))
        self.addCleanup(shutil.rmtree, workdir, True)
        state = workdir / "state"
        self.run_record(state, "aaaa")
        self.run_record(state, "aaaa")
        self.assertEqual((state / "current").read_text().strip(), "aaaa")
        self.assertFalse(
            (state / "previous").exists(),
            msg="resetting to the running SHA is not a rollback target",
        )

    def test_rollback_target_is_printed_for_the_operator(self):
        workdir = Path(tempfile.mkdtemp(prefix="ichat-state-print-"))
        self.addCleanup(shutil.rmtree, workdir, True)
        state = workdir / "state"
        self.run_record(state, "aaaa")
        result = self.run_record(state, "bbbb")
        self.assertIn("rollback target if needed: aaaa", result.stdout)

    def test_unwritable_state_does_not_fail_a_recorded_release(self):
        """The log line is already written and the release is live.

        Bookkeeping must never turn that into exit 1 — under errexit an
        unguarded mkdir/redirect here would abort the script after the
        authoritative record exists.
        """
        workdir = Path(tempfile.mkdtemp(prefix="ichat-state-ro-"))
        self.addCleanup(shutil.rmtree, workdir, True)
        # A regular file where the state directory should be: mkdir -p fails.
        blocker = workdir / "state"
        blocker.write_text("not a directory", encoding="utf-8")

        result = self.run_record(blocker, "aaaa")

        self.assertEqual(
            result.returncode, 0,
            msg=f"an unwritable state dir failed a recorded release:\n{result.stderr}",
        )
        self.assertIn("could not update", result.stderr)
        self.assertIn(
            "IS recorded",
            result.stderr,
            msg="the operator must be told the release still counts",
        )


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
            "#!/usr/bin/env bash\nset -euo pipefail\n"
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
        self.assertIn("Running now (last recorded release): bbbb", output)
