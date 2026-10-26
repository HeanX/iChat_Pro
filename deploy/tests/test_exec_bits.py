"""Committed file modes for the scripts the runbook executes directly.

Review finding OPS-04: ``deploy/monitor.sh`` and
``deploy/staging/check_isolation.sh`` were committed as ``100644`` while
runbook §3b installs monitor.sh in root's crontab and §3c runs
check_isolation.sh directly. A cron line with no interpreter relies on the
file's executable bit, so the monitor never started on Linux and the failure
was invisible (cron only mails stderr).

The assertions read the *git index* (``git ls-files -s``) — the mode that the
next commit will publish — rather than the working tree. On Windows NTFS does
not carry a POSIX executable bit, so a local ``chmod`` proves nothing about
what a Linux checkout receives; ``git update-index --chmod=+x`` is what
actually changes the recorded mode.

These tests skip only when the tree is not a git checkout (a ``git archive``
export, for example), because then there is no index to read. That case says
so explicitly instead of passing.
"""

import shutil
import subprocess
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# Scripts the documentation tells an operator to run directly. Anything that
# appears as a bare path in a cron line or a `sudo deploy/...` command belongs
# here: without the executable bit it fails with EACCES on a POSIX host.
DIRECTLY_EXECUTED = (
    "deploy/monitor.sh",
    "deploy/staging/check_isolation.sh",
    "deploy/smoke.sh",
    "deploy/deploy.sh",
    "deploy/backup.sh",
    "deploy/restore.sh",
    "deploy/restore_drill.sh",
    "deploy/nginx/reload-nginx.sh",
)

GIT = shutil.which("git")


def indexed_modes(*paths):
    """Return {path: mode} from the git index, or None outside a checkout."""
    if not GIT:
        return None
    result = subprocess.run(
        [GIT, "ls-files", "-s", "--", *paths],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        errors="replace",
        timeout=60,
    )
    if result.returncode != 0:
        return None
    modes = {}
    for line in result.stdout.splitlines():
        # "<mode> SP <object> SP <stage> TAB <path>"
        meta, _, path = line.partition("\t")
        fields = meta.split()
        if len(fields) >= 3 and path:
            modes[path.strip()] = fields[0]
    return modes


class ExecutableBitTests(unittest.TestCase):
    """Every directly-invoked script must be staged as 100755."""

    @classmethod
    def setUpClass(cls):
        cls.modes = indexed_modes(*DIRECTLY_EXECUTED)
        if not cls.modes:
            raise unittest.SkipTest(
                "not a git checkout (or git is unavailable): committed file "
                "modes cannot be read here. The mode check MUST run in CI "
                "before merge."
            )

    def test_every_directly_executed_script_is_100755(self):
        missing = [p for p in DIRECTLY_EXECUTED if p not in self.modes]
        self.assertEqual(missing, [], msg="scripts absent from the index")
        wrong = {
            path: self.modes[path]
            for path in DIRECTLY_EXECUTED
            if self.modes[path] != "100755"
        }
        self.assertEqual(
            wrong,
            {},
            msg=(
                "these scripts are executed directly by the runbook but are "
                "recorded without the executable bit; fix with "
                "`git update-index --chmod=+x <path>`:\n"
                + "\n".join(f"  {p}: {m}" for p, m in wrong.items())
            ),
        )

    def test_monitor_sh_specifically_is_executable(self):
        """The cron entry runs it with no interpreter, so this one is critical."""
        self.assertEqual(
            self.modes.get("deploy/monitor.sh"),
            "100755",
            msg="root's crontab runs deploy/monitor.sh directly (runbook 3b)",
        )

    def test_check_isolation_sh_specifically_is_executable(self):
        self.assertEqual(
            self.modes.get("deploy/staging/check_isolation.sh"),
            "100755",
            msg="runbook 3c runs deploy/staging/check_isolation.sh directly",
        )


class RunbookInvocationTests(unittest.TestCase):
    """The documented invocation must match how the script is shipped.

    The mode only matters because the runbook omits the interpreter. Pinning
    both keeps them from drifting apart again: if someone later changes the
    cron line to `bash monitor.sh`, the executable bit stops being load-bearing
    and the reviewer should be told.
    """

    RUNBOOK = REPO_ROOT / "deploy" / "runbook.md"

    def test_runbook_cron_line_uses_the_script_directly(self):
        text = self.RUNBOOK.read_text(encoding="utf-8")
        self.assertIn(
            "*/5 * * * * /opt/ichat/repo/deploy/monitor.sh",
            text,
            msg="runbook §3b must show the exact cron line that is relied on",
        )

    def test_runbook_isolation_check_is_a_direct_path(self):
        text = self.RUNBOOK.read_text(encoding="utf-8")
        self.assertIn(
            "sudo deploy/staging/check_isolation.sh",
            text,
            msg="runbook §3c must show the direct invocation",
        )


if __name__ == "__main__":
    unittest.main()
