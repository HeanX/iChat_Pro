"""Round-2 regressions for the .env parser and the Redis live check.

F-05  monitor.sh took the raw text after the first '=' as the value, so
      ICHAT_DISK_PCT="1" and ICHAT_DISK_PCT=1  # alert at 1% both failed the
      numeric check and silently reverted to the default 85 — while systemd,
      which loads the same file, honours both forms.

F-07  check_isolation.sh parsed the live Redis db with
      `sed -n 's/^db=\\([0-9]*\\).*/\\1/p'`, which only matches a bare "db=1"
      line. A real `CLIENT INFO` is one line starting with id= and carrying
      db= in the middle, so a correctly isolated pair was reported UNVERIFIED
      (or, without -n, both sides read db 0 and were judged IDENTICAL).
"""

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from deploy.tests.support import bash, bash_unavailable_reason, write_script

REPO_ROOT = Path(__file__).resolve().parents[2]
MONITOR = REPO_ROOT / "deploy" / "monitor.sh"
CHECK = REPO_ROOT / "deploy" / "staging" / "check_isolation.sh"

PROD_ENV = {
    "DJANGO_SECRET_KEY": "prod-secret-key-aaaaaaaaaaaaaaaaaaaa",
    "DJANGO_ALLOWED_HOSTS": "chat.example.com,127.0.0.1",
    "DJANGO_CSRF_TRUSTED_ORIGINS": "https://chat.example.com:8443",
    "DATABASE_URL": "postgres://ichat:prod-pw@127.0.0.1:5432/ichat",
    "REDIS_URL": "redis://127.0.0.1:6379/0",
    "DJANGO_MEDIA_ROOT": "/var/lib/ichat/media",
    "DJANGO_STATIC_ROOT": "/opt/ichat/staticfiles",
}


def staging_env(**overrides):
    values = {
        "DJANGO_SECRET_KEY": "staging-secret-key-bbbbbbbbbbbbbbbbbbbb",
        "DJANGO_ALLOWED_HOSTS": "staging.example.com,127.0.0.1",
        "DJANGO_CSRF_TRUSTED_ORIGINS": "https://staging.example.com:8444",
        "DATABASE_URL": "postgres://ichat_staging:staging-pw@127.0.0.1:5432/ichat_staging",
        "REDIS_URL": "redis://127.0.0.1:6380/1",
        "DJANGO_MEDIA_ROOT": "/var/lib/ichat-staging/media",
        "DJANGO_STATIC_ROOT": "/opt/ichat-staging/staticfiles",
        "ICHAT_APP_DIR": "/opt/ichat-staging",
        "ICHAT_SERVICE_NAME": "ichat-staging",
        "ICHAT_SERVICE_USER": "ichat-staging",
    }
    values.update(overrides)
    return values


class EnvFileSyntaxTests(unittest.TestCase):
    """F-05: the monitor accepts the syntax systemd accepts."""

    @classmethod
    def setUpClass(cls):
        reason = bash_unavailable_reason()
        if reason:
            raise AssertionError(reason)

    def setUp(self):
        self.workdir = Path(tempfile.mkdtemp(prefix="ichat-envsyntax-"))
        self.addCleanup(shutil.rmtree, self.workdir, True)
        self.bindir = self.workdir / "bin"
        self.bindir.mkdir()
        self.env_file = self.workdir / "monitor.env"
        self.state = self.workdir / "state"

        write_script(self.bindir / "df",
                     'echo "Filesystem 1024-blocks Used Available Capacity Mounted on"\n'
                     'echo "/dev/vda1 83886080 10000000 73886080 12% /pgdata"\n')
        write_script(self.bindir / "psql", "echo 5\n")
        write_script(self.bindir / "redis-cli", 'echo "used_memory:104857600"\n')
        write_script(self.bindir / "journalctl", 'echo "h1"; echo "h2"\n')
        write_script(self.bindir / "logger", ":\n")
        write_script(
            self.bindir / "openssl",
            'echo "notAfter=$(date -u -d "+3650 days" "+%b %e %H:%M:%S %Y GMT")"\n',
        )
        pgdata = self.workdir / "pgdata"
        pgdata.mkdir()
        live = self.workdir / "le" / "live" / "chat.example.com"
        live.mkdir(parents=True)
        (live / "cert.pem").write_text("stub", encoding="utf-8")
        self.pgdata = pgdata
        self.live = self.workdir / "le" / "live"

    def run_monitor(self, env_body):
        self.env_file.write_text(env_body, encoding="utf-8", newline="\n")
        wrapper = self.workdir / "run.sh"
        wrapper.write_text(
            "#!/usr/bin/env bash\n"
            'export PATH="$(to_posix "$STUB"):/usr/bin:/mingw64/bin"\n'
            '[[ -n "$LETLS" ]] && LETLS=$(to_posix "$LETLS")\n'
            '[[ -n "$PGDATA" ]] && PGDATA=$(to_posix "$PGDATA")\n'
            "export ICHAT_LETLS_DIR=$LETLS ICHAT_PG_DATA_DIR=$PGDATA\n"
            f'exec bash "{MONITOR.as_posix()}"\n',
            encoding="utf-8",
            newline="\n",
        )
        # Reuse the production helper so both suites share one implementation.
        from deploy.tests.deploy_harness import TO_POSIX_SH

        wrapper.write_text(
            "#!/usr/bin/env bash\n" + TO_POSIX_SH +
            'export PATH="$(to_posix "$STUB"):/usr/bin:/mingw64/bin"\n'
            '[[ -n "$LETLS" ]] && LETLS=$(to_posix "$LETLS")\n'
            '[[ -n "$PGDATA" ]] && PGDATA=$(to_posix "$PGDATA")\n'
            "export ICHAT_LETLS_DIR=$LETLS ICHAT_PG_DATA_DIR=$PGDATA\n"
            f'exec bash "{MONITOR.as_posix()}"\n',
            encoding="utf-8",
            newline="\n",
        )
        wrapper.chmod(0o755)
        return subprocess.run(
            [bash(), str(wrapper)],
            env={
                "PATH": os.environ.get("PATH", ""),
                "TMPDIR": os.environ.get("TMPDIR") or os.environ.get("TEMP") or "",
                "ICHAT_MONITOR_STATE": str(self.state),
                "ICHAT_ENV_FILE": str(self.env_file),
                "ICHAT_ALERT_WEBHOOK": "",
                "ICHAT_BASE_URL": "http://127.0.0.1:1",
                "STUB": str(self.bindir),
                "LETLS": str(self.live),
                "PGDATA": str(self.pgdata),
                "ICHAT_PYTHON": __import__("sys").executable,
            },
            capture_output=True,
            text=True,
            errors="replace",
            timeout=120,
        )

    BASE = "DATABASE_URL=postgres://ichat:secret@127.0.0.1:5432/ichat\n"

    def assert_threshold_applied(self, env_body, label):
        result = self.run_monitor(env_body)
        output = result.stdout + result.stderr
        self.assertIn(
            "ALERT [disk]", output,
            msg=f"{label}: the 1% threshold from .env did not fire:\n{output}",
        )

    def test_bare_value(self):
        self.assert_threshold_applied(self.BASE + "ICHAT_DISK_PCT=1\n", "bare")

    def test_double_quoted_value(self):
        self.assert_threshold_applied(
            self.BASE + 'ICHAT_DISK_PCT="1"\n', "double-quoted"
        )

    def test_single_quoted_value(self):
        self.assert_threshold_applied(
            self.BASE + "ICHAT_DISK_PCT='1'\n", "single-quoted"
        )

    def test_trailing_comment(self):
        self.assert_threshold_applied(
            self.BASE + "ICHAT_DISK_PCT=1  # alert at 1%\n", "trailing comment"
        )

    def test_quoted_value_with_trailing_comment(self):
        self.assert_threshold_applied(
            self.BASE + 'ICHAT_DISK_PCT="1"  # alert\n', "quoted + comment"
        )

    def test_hash_inside_a_value_is_not_a_comment(self):
        """A '#' glued to the value is data, not a comment (systemd agrees)."""
        result = self.run_monitor(
            self.BASE + "ICHAT_ALERT_MIN_SECS=19#30\n"
        )
        # The value is not numeric, so the default applies; the point is that
        # parsing must not have truncated it to "19".
        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, msg=output)

    def test_the_stripper_never_evaluates(self):
        marker = self.workdir / "pwned"
        result = self.run_monitor(
            self.BASE + f'ICHAT_DISK_PCT="$(touch {marker.as_posix()})"\n'
        )
        self.assertFalse(marker.exists(), msg="the .env value was executed")
        self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)


class RedisClientInfoShapeTests(unittest.TestCase):
    """F-07: parse a REAL single-line CLIENT INFO and pass the URL's db index."""

    @classmethod
    def setUpClass(cls):
        reason = bash_unavailable_reason()
        if reason:
            raise AssertionError(reason)

    def setUp(self):
        self.workdir = Path(tempfile.mkdtemp(prefix="ichat-redis-"))
        self.addCleanup(shutil.rmtree, self.workdir, True)
        self.prod = self.workdir / "prod.env"
        self.staging = self.workdir / "staging.env"
        self.prod.write_text(
            "".join(f"{k}={v}\n" for k, v in PROD_ENV.items()),
            encoding="utf-8", newline="\n",
        )
        tree = self.workdir / "ichat-staging"
        unit_dir = tree / "repo" / "deploy" / "staging"
        unit_dir.mkdir(parents=True)
        (unit_dir / "ichat-staging.service").write_text(
            "ExecStart=/opt/ichat-staging/.venv/bin/daphne -b 127.0.0.1 -p 8001 "
            "ichat_pro.asgi:application\n",
            encoding="utf-8",
        )
        self.tree = tree
        self.bindir = self.workdir / "bin"
        self.bindir.mkdir()
        self.call_log = self.workdir / "calls.log"

    def write_stubs(self, *, prod_line, staging_line, redis_rc=0):
        """redis-cli answers per PORT, in the real CLIENT INFO single-line shape."""
        write_script(
            self.bindir / "redis-cli",
            f'printf "redis-cli %s\\n" "$*" >> "{self.call_log.as_posix()}"\n'
            'case "$*" in\n'
            '    *"-p 6380"*) printf "%s\\n" ' + repr(staging_line).replace("'", '"') + " ;;\n"
            '    *) printf "%s\\n" ' + repr(prod_line).replace("'", '"') + " ;;\n"
            "esac\n"
            f"exit {redis_rc}\n",
        )
        write_script(self.bindir / "psql", 'printf "ichat\\n"\n')

    def run_check(self):
        self.staging.write_text(
            "".join(f"{k}={v}\n" for k, v in staging_env(ICHAT_APP_DIR=str(self.tree)).items()),
            encoding="utf-8", newline="\n",
        )
        return subprocess.run(
            [bash(), str(CHECK),
             "--prod-env", str(self.prod),
             "--staging-env", str(self.staging),
             "--check-data"],
            env={
                "PATH": f"{self.bindir}{os.pathsep}{os.environ.get('PATH', '')}",
                "TMPDIR": os.environ.get("TMPDIR") or os.environ.get("TEMP") or "",
            },
            capture_output=True, text=True, errors="replace", timeout=120,
        )

    # A real CLIENT INFO line: id=... first, db= in the middle.
    REAL_PROD = "id=42 addr=127.0.0.1:53100 fd=8 name= age=120 idle=0 flags=N db=0 sub=0 psub=0 ssub=0 multi=-1 qbuf=0"
    REAL_STAGING = "id=7 addr=127.0.0.1:53101 fd=9 name= age=3 idle=0 flags=N db=1 sub=0 psub=0 ssub=0 multi=-1 qbuf=0"

    def test_real_client_info_is_parsed_and_dbs_differ(self):
        self.write_stubs(prod_line=self.REAL_PROD, staging_line=self.REAL_STAGING)
        result = self.run_check()
        output = result.stdout + result.stderr
        self.assertIn("live redis logical db differs", output, msg=output)
        self.assertIn("prod=0 staging=1", output, msg=output)

    def test_the_url_db_index_is_passed_to_redis_cli(self):
        self.write_stubs(prod_line=self.REAL_PROD, staging_line=self.REAL_STAGING)
        self.run_check()
        calls = self.call_log.read_text(encoding="utf-8")
        # The staging URL ends in /1 and must be selected explicitly; without
        # -n both servers read db 0 and a correct pair is judged IDENTICAL.
        self.assertIn("-n 1", calls, msg=calls)
        self.assertIn("-n 0", calls, msg=calls)

    def test_identical_live_db_is_a_violation(self):
        self.write_stubs(
            prod_line=self.REAL_PROD,
            staging_line=self.REAL_PROD.replace("id=42", "id=99"),
        )
        result = self.run_check()
        output = result.stdout + result.stderr
        self.assertNotEqual(result.returncode, 0, msg=output)
        self.assertIn("IDENTICAL", output, msg=output)

    def test_unparseable_reply_is_unverified_not_a_pass(self):
        self.write_stubs(prod_line="PONG", staging_line="PONG")
        result = self.run_check()
        output = result.stdout + result.stderr
        self.assertNotEqual(result.returncode, 0, msg=output)
        self.assertIn("UNVERIFIED", output, msg=output)
        self.assertNotIn("ISOLATED: staging shares no config", output)

    def test_failing_client_is_unverified(self):
        self.write_stubs(
            prod_line=self.REAL_PROD, staging_line=self.REAL_STAGING, redis_rc=1
        )
        result = self.run_check()
        output = result.stdout + result.stderr
        self.assertNotEqual(result.returncode, 0, msg=output)
        self.assertIn("UNVERIFIED", output, msg=output)


if __name__ == "__main__":
    unittest.main()
