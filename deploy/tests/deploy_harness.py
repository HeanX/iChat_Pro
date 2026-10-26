"""A stubbed host for running deploy.sh end to end in tests.

``deploy/deploy.sh`` is a server-only script: it clones into ``/opt/ichat``,
restarts systemd units and probes Nginx. Running it for real is impossible in
CI, but its *decisions* are exactly what the P4 review findings are about —
which unit gets restarted, which origin gets probed, whether a release is
recorded. So this harness provides the smallest host that lets the real script
run to completion:

  git, systemctl, install, curl   stubs on PATH that record their argv
  <app>/.venv/bin/{python,pip}    stubs at the paths deploy.sh hard-codes
  <app>/repo/.git, <app>/repo/deploy/   the deployed tree it operates on

Every recorded file is a plain text log, so a test asserts on what the script
*would have done* and can print that log as its failure message.

Windows note: Git Bash only resolves ``/c/...``-style paths, while a Linux CI runner
has no ``cygpath`` at all (review finding F-06: the substitution became
empty and 39 tests ERRORed). Each test therefore writes a wrapper that
converts paths through the generated ``to_posix`` shell function: it uses
``cygpath`` when available and falls back to a pure-bash drive-letter
rewrite otherwise, so the same harness runs on Windows and on
ubuntu-latest.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import textwrap
from pathlib import Path

from deploy.tests.support import bash, shell_env, write_script

REPO_ROOT = Path(__file__).resolve().parents[2]
DEPLOY_SCRIPT = REPO_ROOT / "deploy" / "deploy.sh"

FAKE_SHA = "0123456789abcdef0123456789abcdef01234567"

# Shell helper emitted into every generated wrapper (see module docstring).
# Kept as a raw string so the shell's backslashes survive verbatim.
TO_POSIX_SH = r'''
# Portable Windows -> POSIX path conversion (review finding F-06).
# cygpath exists only in Git Bash; a Linux CI runner has none, and an empty
# substitution there turned PATH into "[:/mingw64/bin:/usr/bin]" and
# ICHAT_APP_DIR into "" -> deploy.sh fell back to /opt/ichat and exited 1.
# The fallback rewrites C:\x\y to /c/x/y and passes POSIX/empty values
# through unchanged, so the same wrapper runs on both platforms.
# ICHAT_FORCE_NO_CYGPATH=1 exercises the fallback branch on a Git Bash host.
to_posix() {
    local p=$1
    if [[ "${ICHAT_FORCE_NO_CYGPATH:-0}" != "1" ]] && command -v cygpath >/dev/null 2>&1; then
        cygpath -u "$p"
        return 0
    fi
    case "$p" in
        [A-Za-z]:[\\/]*)
            local drive=${p%%:*} rest=${p#*:}
            rest=${rest//\\//}
            printf '/%s%s' "$(printf '%s' "$drive" | tr 'A-Z' 'a-z')" "$rest"
            ;;
        *) printf '%s' "$p" ;;
    esac
}
'''


class StubbedHost:
    """A throwaway install tree plus the stubs that make deploy.sh runnable."""

    LOG_KEYS = ("GITLOG", "SYSTEMCTL_LOG", "CURL_LOG", "INSTALL_LOG",
                "SMOKE_ENV_LOG", "PYTHON_LOG")

    def __init__(self, workdir: Path):
        self.workdir = workdir
        self.app_dir = workdir / "app"
        self.app_dir.mkdir()
        self.repo = self.app_dir / "repo"
        (self.repo / ".git").mkdir(parents=True)
        (self.repo / "deploy").mkdir()
        self.venv = self.app_dir / ".venv"
        (self.venv / "bin").mkdir(parents=True)
        (self.app_dir / "bin").mkdir(parents=True)
        self.bin = workdir / "host-bin"
        self.bin.mkdir()
        self.logs = {key: workdir / f"{key.lower()}.log" for key in self.LOG_KEYS}
        self._install_stubs()

    # --- stubs ------------------------------------------------------------
    def _stub(self, name: str, body: str) -> Path:
        prelude = TO_POSIX_SH + "".join(
            f'{key}="$(to_posix "$STUB_WIN_{key}")"\n' for key in self.logs
        )
        return write_script(self.bin / name, prelude + body)

    def _install_stubs(self) -> None:
        self._stub(
            "git",
            textwrap.dedent(
                """
                printf 'git %s\\n' "$*" >> "$GITLOG"
                case "$*" in
                    *"rev-parse HEAD"*) printf '%s\\n' "$FAKE_SHA_FOR_TESTS" ;;
                esac
                exit 0
                """
            ).lstrip(),
        )
        self._stub(
            "systemctl",
            'printf \'systemctl %s\\n\' "$*" >> "$SYSTEMCTL_LOG"\nexit 0\n',
        )
        # `install` is used twice: the re-exec self-copy (install -m 755 SRC
        # DST) and `install -d -o USER -g USER DIR...`. Reproduce both without
        # needing a real target user on the host, and record every call.
        self._stub(
            "install",
            textwrap.dedent(
                """
                printf 'install %s\\n' "$*" >> "$INSTALL_LOG"
                args=("$@")
                last=$((${#args[@]} - 1))
                if [[ "${args[0]}" == "-d" ]]; then
                    for ((i = 1; i <= last; i++)); do
                        [[ "${args[i]}" == -* ]] && continue
                        mkdir -p "${args[i]}"
                    done
                    exit 0
                fi
                if [[ "${args[0]}" == "-m" ]]; then
                    src=${args[2]}
                else
                    src=${args[last-1]}
                fi
                dest=${args[last]}
                mkdir -p "$(dirname "$dest")"
                cp "$src" "$dest"
                exit 0
                """
            ).lstrip(),
        )
        # Every curl call records its argv, then answers like a healthy
        # readiness endpoint so the retry loop exits on the first iteration.
        self._stub(
            "curl",
            textwrap.dedent(
                """
                printf 'curl %s\\n' "$*" >> "$CURL_LOG"
                for arg in "$@"; do
                    case "$arg" in
                        */health/ready/*)
                            printf '{"status": "ok"}'
                            exit 0
                            ;;
                    esac
                done
                exit 0
                """
            ).lstrip(),
        )
        # The venv's interpreter and pip live on hard-coded paths, not on PATH.
        write_script(
            self.venv / "bin" / "python",
            TO_POSIX_SH
            + 'printf "python %s\\n" "$*" >> "$(to_posix "$STUB_WIN_PYTHON_LOG")"\n'
            + "exit 0\n",
        )
        write_script(self.venv / "bin" / "pip", "exit 0\n")

    # --- the deployed tree ------------------------------------------------
    def write_smoke_stub(self, exit_code: int = 0) -> None:
        """Stand-in for the target tree's deploy/smoke.sh.

        It records the origin variables deploy.sh hands it, which is how the
        "readiness and smoke share one resolved config" property is asserted.
        """
        write_script(
            self.repo / "deploy" / "smoke.sh",
            TO_POSIX_SH
            + textwrap.dedent(
                f"""
                OUT="$(to_posix "$STUB_WIN_SMOKE_ENV_LOG")"
                {{
                  printf 'SMOKE_TLS_ORIGIN=%s\\n' "${{SMOKE_TLS_ORIGIN:-<unset>}}"
                  printf 'SMOKE_LOOPBACK_ORIGIN=%s\\n' "${{SMOKE_LOOPBACK_ORIGIN:-<unset>}}"
                  printf 'AUTO_BASE_URL=%s\\n' "${{AUTO_BASE_URL:-<unset>}}"
                }} > "$OUT"
                exit {exit_code}
                """
            ).lstrip(),
        )

    def remove_smoke_stub(self) -> None:
        smoke = self.repo / "deploy" / "smoke.sh"
        if smoke.exists():
            smoke.unlink()

    def write_env(self, **values) -> None:
        defaults = {
            "DJANGO_ALLOWED_HOSTS": "chat.example.com,127.0.0.1",
            "DJANGO_SECRET_KEY": "deploy-config-test-key",
            "SMOKE_USERNAME": "deploy_smoke",
            "SMOKE_PASSWORD": "not-a-real-password",
        }
        defaults.update(values)
        body = "".join(f"{k}={v}\n" for k, v in defaults.items() if v is not None)
        (self.app_dir / ".env").write_text(body, encoding="utf-8", newline="\n")

    # --- running the real script -----------------------------------------
    def run(self, *args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
        wrapper = self.workdir / "run-deploy.sh"
        exports = "\n".join(
            f'export {key}="$(to_posix "$STUB_WIN_{key}")"' for key in self.logs
        )
        args_literal = " ".join(f"'{a}'" for a in args)
        wrapper.write_text(
            "#!/usr/bin/env bash\n"
            + TO_POSIX_SH
            + 'export PATH="$(to_posix "$ICHAT_STUB_BIN"):/usr/bin:/mingw64/bin"\n'
            'export ICHAT_APP_DIR="$(to_posix "$ICHAT_APP_WIN")"\n'
            f"{exports}\n"
            # Optional: declare that THIS tree is production, for the tests
            # that pin the unchanged production defaults.
            '[[ -n "${STUB_WIN_PROD_APP:-}" ]] && '
            'export ICHAT_PROD_APP_DIR="$(to_posix "$STUB_WIN_PROD_APP")"\n'
            f'exec bash "{DEPLOY_SCRIPT.as_posix()}" {args_literal}\n',
            encoding="utf-8",
            newline="\n",
        )
        env_vars = shell_env(
            {
                # The stub directory must win over the host's real tools.
                "PATH": f"{self.bin}{os.pathsep}{os.environ.get('PATH', '')}",
                "ICHAT_STUB_BIN": str(self.bin),
                "ICHAT_APP_WIN": str(self.app_dir),
                "FAKE_SHA_FOR_TESTS": FAKE_SHA,
                "HOME": str(self.workdir),
                "ICHAT_REQUIRE_APPROVAL": "0",
            }
        )
        for key, path in self.logs.items():
            env_vars[f"STUB_WIN_{key}"] = str(path)
        env_vars.update(env or {})
        return subprocess.run(
            [bash(), str(wrapper)],
            env=env_vars,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=180,
            cwd=str(REPO_ROOT),
        )

    # --- assertions helpers ----------------------------------------------
    def read(self, key: str) -> str:
        try:
            return self.logs[key].read_text(encoding="utf-8")
        except FileNotFoundError:
            return ""

    def read_file(self, path: Path) -> str:
        try:
            return path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return ""

    @property
    def deployments_log(self) -> Path:
        return self.app_dir / "DEPLOYMENTS.log"


class StubbedHostTestCase:
    """Mixin: fresh stubbed host per test, cleaned up afterwards."""

    def make_host(self) -> StubbedHost:
        workdir = Path(tempfile.mkdtemp(prefix="ichat-deploy-"))
        self.addCleanup(shutil.rmtree, workdir, True)
        return StubbedHost(workdir)
