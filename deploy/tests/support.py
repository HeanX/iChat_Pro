"""Shared helpers for the deploy tooling tests.

Every regression in this package executes the *real* shell script, so bash is
a hard requirement rather than an optional convenience. When it is missing the
honest outcome is a FAILED test that says so: ``unittest.skip`` would report
"ok" for checks that never ran, which is exactly the false evidence the ops
review called out (2026-10-24, "Bash 缺失导致 skip 不算通过").

Set ``ICHAT_TEST_BASH`` to point the suite at a specific interpreter (CI uses
this when bash is installed outside ``PATH``).
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

# Populated by bash(); kept module-level so the (expensive) probe runs once.
_BASH: str | None = None


class BashUnavailable(RuntimeError):
    """Raised when the shell the ops scripts need cannot be executed.

    Deliberately *not* a ``SkipTest``: a missing shell means the regression
    checks are unverified, and an unverified check must never look green.
    """


def find_bash() -> str:
    """Return a bash that actually runs, or raise BashUnavailable."""
    candidate = os.environ.get("ICHAT_TEST_BASH") or shutil.which("bash")
    if not candidate:
        raise BashUnavailable(
            "bash was not found on PATH, so the deploy/monitor/isolation "
            "shell regressions cannot be executed. Install bash (Git Bash on "
            "Windows) or set ICHAT_TEST_BASH to its full path. This is a "
            "FAILURE, not a skip: the ops gates are unverified without it."
        )
    if not Path(candidate).is_file():
        raise BashUnavailable(f"ICHAT_TEST_BASH={candidate!r} is not a file")
    try:
        probe = subprocess.run(
            [candidate, "-c", "exit 0"],
            capture_output=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as error:  # pragma: no cover
        raise BashUnavailable(f"{candidate!r} cannot be executed: {error}") from error
    if probe.returncode != 0:  # pragma: no cover - broken interpreter
        raise BashUnavailable(
            f"{candidate!r} exited {probe.returncode} on a trivial command"
        )
    return candidate


def bash() -> str:
    """Return a cached, verified bash path (raises BashUnavailable)."""
    global _BASH
    if _BASH is None:
        _BASH = find_bash()
    return _BASH


def bash_unavailable_reason() -> str | None:
    """The reason bash cannot be used, or None when it is available.

    ``setUpClass`` raises ``AssertionError(reason)`` with this so a missing
    shell reports an ERROR for the whole class instead of skipping it.
    """
    try:
        bash()
    except BashUnavailable as error:
        return str(error)
    return None


def require_bash() -> None:
    """Fail (never skip) when the shell the ops scripts need is missing."""
    reason = bash_unavailable_reason()
    if reason:
        raise AssertionError(reason)


def shell_env(base: dict[str, str] | None = None) -> dict[str, str]:
    """A minimal environment for the scripts under test.

    Windows' inherited environment leaks variables the scripts must not see
    (a stray ``DATABASE_URL`` would change which branch a test exercises), so
    the child gets PATH/TMPDIR only and each test opts in to the rest.
    """
    env = {
        "PATH": os.environ.get("PATH", ""),
        "TMPDIR": os.environ.get("TMPDIR") or os.environ.get("TEMP") or "",
    }
    env.update(base or {})
    return env


def write_script(path: Path, body: str) -> Path:
    """Write a bash script with LF endings and an executable bit.

    ``Path.write_text`` on Windows may emit CRLF only if asked, but the
    executable bit is not carried by NTFS, so it is set explicitly for the
    POSIX runners that do honour it.
    """
    path.write_text("#!/usr/bin/env bash\n" + body, encoding="utf-8", newline="\n")
    try:
        path.chmod(0o755)
    except OSError:  # pragma: no cover - exotic filesystem
        pass
    return path
