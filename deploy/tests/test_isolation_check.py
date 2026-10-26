"""Isolation check tests (P4 T49 / Issue #176).

Drives the real deploy/staging/check_isolation.sh against fixture .env pairs:
a fully isolated pair must pass; each dimension the acceptance criterion
names (secrets, database, redis logical db, storage paths, runtime identity)
must be flagged as a violation when it is shared with production.

Three review findings shape the negative cases:

  OPS-09  changing only the database NAME is not secret isolation: the
          production role and password must be checked on their own.
  OPS-10  static roots nest too, not just media: /opt/ichat/staticfiles/staging
          is inside /opt/ichat/staticfiles, and releases run
          `collectstatic --clear`.
  OPS-08  `--check-data` used to print "live data / ISOLATED" without ever
          opening a database connection. It must either really connect (and
          report a failed/missing client as UNVERIFIED, non-zero) or drop the
          live claim.
"""

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from deploy.tests.support import bash, bash_unavailable_reason

REPO_ROOT = Path(__file__).resolve().parents[2]
CHECK = REPO_ROOT / "deploy" / "staging" / "check_isolation.sh"

PROD_ENV = {
    "DJANGO_DEBUG": "False",
    "DJANGO_SECRET_KEY": "prod-secret-key-aaaaaaaaaaaaaaaaaaaa",
    "DJANGO_ALLOWED_HOSTS": "chat.example.com,127.0.0.1",
    "DJANGO_CSRF_TRUSTED_ORIGINS": "https://chat.example.com:8443",
    "DATABASE_URL": "postgres://ichat:prod-pw@127.0.0.1:5432/ichat",
    "REDIS_URL": "redis://127.0.0.1:6379/0",
    "DJANGO_MEDIA_ROOT": "/var/lib/ichat/media",
    "DJANGO_STATIC_ROOT": "/opt/ichat/staticfiles",
}


def write_env(path: Path, values):
    lines = [f"{k}={v}" for k, v in values.items()]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def staging_env(**overrides):
    values = {
        "DJANGO_DEBUG": "False",
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


class IsolationCase(unittest.TestCase):
    """Fresh .env pair, a findable staging unit, and the runner."""

    @classmethod
    def setUpClass(cls):
        # Fail, never skip: an unexecuted isolation check must not look green.
        reason = bash_unavailable_reason()
        if reason:
            raise AssertionError(reason)

    def setUp(self):
        self.workdir = Path(tempfile.mkdtemp(prefix="ichat-isolation-"))
        self.addCleanup(shutil.rmtree, self.workdir, True)
        self.prod = self.workdir / "prod.env"
        self.staging = self.workdir / "staging.env"
        write_env(self.prod, PROD_ENV)
        # A findable staging unit on the default port so the runtime/port
        # dimension is exercised in the passing case too.
        staging_tree = self.workdir / "ichat-staging"
        unit_dir = staging_tree / "repo" / "deploy" / "staging"
        unit_dir.mkdir(parents=True, exist_ok=True)
        (unit_dir / "ichat-staging.service").write_text(
            "ExecStart=/opt/ichat-staging/.venv/bin/daphne -b 127.0.0.1 -p 8001 ichat_pro.asgi:application\n",
            encoding="utf-8",
        )
        self.staging_tree = staging_tree

    def run_check(self, prod=None, staging=None, extra_args=(), path_prefix=None,
                  extra_env=None):
        """Run the checker against fixture env files.

        ``path_prefix`` prepends a stub directory. That alone is NOT enough to
        simulate a missing client: on Linux a real ``/usr/bin/psql`` stays
        reachable behind the stub, so deleting the stub found the real binary
        and the "not installed" case silently exercised the happy path (CI run
        40343782825). Cases that need an absent client point ``ICHAT_PSQL`` /
        ``ICHAT_REDIS_CLI`` at a path that does not exist instead.
        """
        write_env(self.staging, staging if staging is not None else staging_env())
        if prod is not None:
            write_env(self.prod, prod)
        env = {
            "PATH": os.environ.get("PATH", ""),
            "TMPDIR": os.environ.get("TMPDIR") or os.environ.get("TEMP") or "",
        }
        if path_prefix:
            env["PATH"] = f"{path_prefix}:{env['PATH']}"
        if extra_env:
            env.update(extra_env)
        return subprocess.run(
            [bash(), str(CHECK),
             "--prod-env", str(self.prod),
             "--staging-env", str(self.staging),
             *extra_args],
            env=env,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=120,
        )

    def output(self, result):
        return result.stdout + result.stderr


class IsolationCheckTests(IsolationCase):
    # --- the good case ----------------------------------------------------
    def test_fully_isolated_staging_passes(self):
        result = self.run_check(staging=staging_env(ICHAT_APP_DIR=str(self.staging_tree)))
        output = self.output(result)
        self.assertEqual(
            result.returncode, 0,
            msg=f"an isolated staging pair was rejected:\n{output}",
        )
        self.assertIn("ISOLATED: staging shares no config", output)
        for marker in ("secrets", "config", "storage", "runtime"):
            self.assertIn(marker, output)

    # --- each dimension must be enforced ----------------------------------
    def test_shared_secret_key_is_a_violation(self):
        result = self.run_check(
            staging=staging_env(DJANGO_SECRET_KEY=PROD_ENV["DJANGO_SECRET_KEY"])
        )
        output = self.output(result)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("DJANGO_SECRET_KEY: IDENTICAL", output)

    def test_shared_database_url_is_a_violation(self):
        result = self.run_check(
            staging=staging_env(DATABASE_URL=PROD_ENV["DATABASE_URL"])
        )
        self.assertIn("DATABASE_URL: IDENTICAL", self.output(result))
        self.assertNotEqual(result.returncode, 0)

    def test_wrong_database_name_is_a_violation(self):
        result = self.run_check(
            staging=staging_env(
                DATABASE_URL="postgres://ichat_staging:pw@127.0.0.1:5432/othername"
            )
        )
        self.assertIn("not named ichat_staging", self.output(result))
        self.assertNotEqual(result.returncode, 0)

    def test_same_redis_logical_db_is_a_violation(self):
        result = self.run_check(staging=staging_env(REDIS_URL=PROD_ENV["REDIS_URL"]))
        self.assertIn("both use logical db 0", self.output(result))
        self.assertNotEqual(result.returncode, 0)

    def test_shared_csrf_origin_is_a_violation(self):
        result = self.run_check(
            staging=staging_env(
                DJANGO_CSRF_TRUSTED_ORIGINS=PROD_ENV["DJANGO_CSRF_TRUSTED_ORIGINS"]
            )
        )
        self.assertIn("DJANGO_CSRF_TRUSTED_ORIGINS: IDENTICAL", self.output(result))
        self.assertNotEqual(result.returncode, 0)

    def test_media_root_inside_production_media_is_a_violation(self):
        result = self.run_check(
            staging=staging_env(DJANGO_MEDIA_ROOT="/var/lib/ichat/media/staging")
        )
        self.assertIn("overlaps", self.output(result))
        self.assertNotEqual(result.returncode, 0)

    def test_production_app_dir_is_a_violation(self):
        result = self.run_check(staging=staging_env(ICHAT_APP_DIR="/opt/ichat"))
        self.assertIn("uses the production tree", self.output(result))
        self.assertNotEqual(result.returncode, 0)

    def test_production_service_unit_is_a_violation(self):
        result = self.run_check(staging=staging_env(ICHAT_SERVICE_NAME="ichat"))
        self.assertIn("reuses the production unit", self.output(result))
        self.assertNotEqual(result.returncode, 0)

    def test_production_service_user_is_a_violation(self):
        result = self.run_check(staging=staging_env(ICHAT_SERVICE_USER="ichat"))
        self.assertIn("runs as the production user", self.output(result))
        self.assertNotEqual(result.returncode, 0)

    # --- OPS-09: secrets are compared field by field, not as one blob -----
    def test_shared_database_role_with_a_new_database_name_is_a_violation(self):
        """Keeping production's role/password must not pass on a rename.

        This is the exact finding: `ichat:prod-pw@.../ichat_staging` differs
        from the production URL as a string, so the old check reported
        "differs" and declared the secrets isolated.
        """
        result = self.run_check(
            staging=staging_env(
                DATABASE_URL="postgres://ichat:prod-pw@127.0.0.1:5432/ichat_staging"
            )
        )
        output = self.output(result)
        self.assertNotEqual(result.returncode, 0, msg=output)
        self.assertIn("shares the production database ROLE", output)

    def test_shared_database_password_only_is_a_violation(self):
        """A distinct role is not enough if the password is reused."""
        result = self.run_check(
            staging=staging_env(
                DATABASE_URL="postgres://ichat_staging:prod-pw@127.0.0.1:5432/ichat_staging"
            )
        )
        output = self.output(result)
        self.assertNotEqual(result.returncode, 0, msg=output)
        self.assertIn("shares the production database PASSWORD", output)

    def test_shared_password_is_reported_without_printing_it(self):
        result = self.run_check(
            staging=staging_env(
                DATABASE_URL="postgres://ichat_staging:prod-pw@127.0.0.1:5432/ichat_staging"
            )
        )
        output = self.output(result)
        self.assertNotIn("prod-pw", output, msg=output)

    def test_a_fully_independent_role_and_password_pass(self):
        """The positive case: distinct role, distinct password, distinct db."""
        result = self.run_check(staging=staging_env(ICHAT_APP_DIR=str(self.staging_tree)))
        output = self.output(result)
        self.assertEqual(result.returncode, 0, msg=output)
        self.assertIn("database role differs", output)
        self.assertIn("database password differs", output)

    # --- OPS-10: static roots nest too ------------------------------------
    def test_static_root_inside_production_static_is_a_violation(self):
        """`collectstatic --clear` in one tree would delete the other's files."""
        result = self.run_check(
            staging=staging_env(
                ICHAT_APP_DIR=str(self.staging_tree),
                DJANGO_STATIC_ROOT="/opt/ichat/staticfiles/staging",
            )
        )
        output = self.output(result)
        self.assertNotEqual(result.returncode, 0, msg=output)
        self.assertIn("STATIC_ROOT nesting", output)

    def test_production_static_root_inside_staging_static_is_a_violation(self):
        result = self.run_check(
            staging=staging_env(
                ICHAT_APP_DIR=str(self.staging_tree),
                DJANGO_STATIC_ROOT="/opt/ichat",
            )
        )
        output = self.output(result)
        # "/opt/ichat" is an ancestor of "/opt/ichat/staticfiles".
        self.assertNotEqual(result.returncode, 0, msg=output)
        self.assertIn("nesting", output)

    def test_static_root_trailing_slash_cannot_hide_nesting(self):
        result = self.run_check(
            staging=staging_env(
                ICHAT_APP_DIR=str(self.staging_tree),
                DJANGO_STATIC_ROOT="/opt/ichat/staticfiles/staging/",
            )
        )
        output = self.output(result)
        self.assertNotEqual(result.returncode, 0, msg=output)
        self.assertIn("STATIC_ROOT nesting", output)

    def test_static_root_dotdot_cannot_hide_nesting(self):
        # Lexically this normalises into production's static root; the
        # fixture staging tree stays in place so only the nesting check fires.
        result = self.run_check(
            staging=staging_env(
                ICHAT_APP_DIR=str(self.staging_tree),
                DJANGO_STATIC_ROOT="/opt/ichat-staging/../ichat/staticfiles/staging",
            )
        )
        output = self.output(result)
        self.assertNotEqual(result.returncode, 0, msg=output)
        self.assertIn("STATIC_ROOT nesting", output)

    def test_disjoint_static_roots_pass(self):
        result = self.run_check(staging=staging_env(ICHAT_APP_DIR=str(self.staging_tree)))
        output = self.output(result)
        self.assertEqual(result.returncode, 0, msg=output)
        self.assertIn("static roots are disjoint", output)

    # --- normalisation and bypass resistance -------------------------------
    def test_trailing_slash_cannot_hide_production_app_dir(self):
        result = self.run_check(staging=staging_env(ICHAT_APP_DIR="/opt/ichat/"))
        self.assertIn("uses the production tree", self.output(result))
        self.assertNotEqual(result.returncode, 0)

    def test_dotdot_media_path_cannot_dodge_nesting_check(self):
        result = self.run_check(
            staging=staging_env(DJANGO_MEDIA_ROOT="/var/lib/ichat-staging/../ichat/media")
        )
        self.assertIn("overlaps", self.output(result))
        self.assertNotEqual(result.returncode, 0)

    def test_missing_database_url_is_reported_not_silent(self):
        partial = staging_env()
        del partial["DATABASE_URL"]
        result = self.run_check(staging=partial)
        output = self.output(result)
        self.assertIn("DATABASE_URL: missing on one side", output)
        self.assertIn("media roots are disjoint", output.lower())  # later checks still ran
        self.assertNotEqual(result.returncode, 0)

    def test_missing_redis_url_is_reported_not_silent(self):
        partial = staging_env()
        del partial["REDIS_URL"]
        result = self.run_check(staging=partial)
        self.assertIn("REDIS_URL missing on one side", self.output(result))
        self.assertNotEqual(result.returncode, 0)

    def test_port_collision_is_reported_when_unit_is_findable(self):
        staging_dir = self.workdir / "ichat-staging"
        unit_dir = staging_dir / "repo" / "deploy" / "staging"
        unit_dir.mkdir(parents=True, exist_ok=True)
        (unit_dir / "ichat-staging.service").write_text(
            "ExecStart=/opt/ichat-staging/.venv/bin/daphne -b 127.0.0.1 -p 8000 ichat_pro.asgi:application\n",
            encoding="utf-8",
        )
        result = self.run_check(
            staging=staging_env(ICHAT_APP_DIR=str(staging_dir))
        )
        output = self.output(result)
        self.assertIn("port 8000 collides", output)
        self.assertNotEqual(result.returncode, 0)

    def test_unit_missing_is_reported_as_unverified_port(self):
        # No unit anywhere: the port dimension must not silently pass.
        shutil.rmtree(self.staging_tree)
        result = self.run_check(staging=staging_env(ICHAT_APP_DIR=str(self.staging_tree)))
        output = self.output(result)
        self.assertIn("port UNVERIFIED", output)
        self.assertNotEqual(result.returncode, 0)

    # --- evidence hygiene ---------------------------------------------------
    def test_violations_never_print_secret_values(self):
        result = self.run_check(
            staging=staging_env(DJANGO_SECRET_KEY=PROD_ENV["DJANGO_SECRET_KEY"])
        )
        output = self.output(result)
        self.assertNotIn(PROD_ENV["DJANGO_SECRET_KEY"], output)
        self.assertNotIn("prod-secret-key", output)

    def test_missing_key_on_either_side_is_flagged(self):
        partial = staging_env()
        del partial["DJANGO_SECRET_KEY"]
        result = self.run_check(staging=partial)
        self.assertIn("missing on one side", self.output(result))
        self.assertNotEqual(result.returncode, 0)


class LiveDataCheckTests(IsolationCase):
    """OPS-08: --check-data must really query, and say so when it cannot.

    Every stub below records its argv so the test can prove a client was
    actually invoked -- and, in the failure cases, that the absence of a
    result is reported as UNVERIFIED with a non-zero exit rather than folded
    into an "ISOLATED" verdict.
    """

    def make_stubs(self, *, psql_rc=0, psql_out=None, redis_rc=0, redis_out=None):
        """Stubs that answer per TARGET, the way two separate servers would.

        A single canned reply would make every environment look identical,
        which is exactly what the live check is supposed to detect — so the
        default answers are keyed on the database/URL each stub is asked
        about.
        """
        bindir = self.workdir / "stub-bin"
        bindir.mkdir(exist_ok=True)
        call_log = self.workdir / "data-calls.log"

        def write(name, body):
            path = bindir / name
            path.write_text("#!/usr/bin/env bash\n" + body, encoding="utf-8", newline="\n")
            path.chmod(0o755)

        # Default: report the database name the client was pointed at, i.e.
        # staging -> ichat_staging, production -> ichat.
        psql_body = (
            f'printf "psql %s\\n" "$*" >> "{call_log.as_posix()}"\n'
        )
        if psql_out is None:
            psql_body += (
                'case "${PGDATABASE:-}" in\n'
                '    *staging*) printf "ichat_staging\\n" ;;\n'
                '    *) printf "ichat\\n" ;;\n'
                "esac\n"
            )
        else:
            psql_body += f'printf "%s\\n" "{psql_out}"\n'
        psql_body += f"exit {psql_rc}\n"
        write("psql", psql_body)

        # Default: staging (port 6379 on the staging host is a separate server
        # in production; here the db index is what the URL says).
        redis_body = (
            f'printf "redis-cli %s\\n" "$*" >> "{call_log.as_posix()}"\n'
        )
        if redis_out is None:
            # Two separate servers stand in for prod/staging, told apart by
            # the port the URL names (6379 vs 6380 in the fixtures below).
            redis_body += (
                'case "$*" in\n'
                '    *"-p 6380"*) printf "db=1\\n" ;;\n'
                '    *) printf "db=0\\n" ;;\n'
                "esac\n"
            )
        else:
            redis_body += f'printf "%s\\n" "{redis_out}"\n'
        redis_body += f"exit {redis_rc}\n"
        write("redis-cli", redis_body)
        return bindir, call_log

    def test_check_data_actually_invokes_psql_and_redis_cli(self):
        bindir, call_log = self.make_stubs()
        result = self.run_check(
            staging=staging_env(ICHAT_APP_DIR=str(self.staging_tree)),
            extra_args=("--check-data",),
            path_prefix=str(bindir),
        )
        output = self.output(result)
        calls = call_log.read_text(encoding="utf-8") if call_log.exists() else ""
        self.assertIn("psql ", calls, msg=f"psql was never called:\n{output}")
        self.assertIn("redis-cli ", calls, msg=f"redis-cli was never called:\n{output}")
        self.assertEqual(result.returncode, 0, msg=output)
        self.assertIn("live database role", output)

    def test_missing_psql_makes_the_data_check_unverified(self):
        bindir, _ = self.make_stubs()
        (bindir / "psql").unlink()
        result = self.run_check(
            staging=staging_env(ICHAT_APP_DIR=str(self.staging_tree)),
            extra_args=("--check-data",),
            path_prefix=str(bindir),
            # Point the checker at a client that does not exist. Deleting the
            # stub was not enough: on Linux a real /usr/bin/psql stayed
            # reachable behind the stub directory, so this case exercised the
            # happy path and only passed on Windows (CI run 40343782825).
            extra_env={"ICHAT_PSQL": str(bindir / "psql-absent")},
        )
        output = self.output(result)
        self.assertNotEqual(result.returncode, 0, msg=output)
        self.assertIn("UNVERIFIED", output)
        self.assertIn("psql", output)
        self.assertNotIn("ISOLATED: staging shares no config", output)

    def test_failing_psql_makes_the_data_check_unverified(self):
        bindir, _ = self.make_stubs(psql_rc=77, psql_out="")
        result = self.run_check(
            staging=staging_env(ICHAT_APP_DIR=str(self.staging_tree)),
            extra_args=("--check-data",),
            path_prefix=str(bindir),
        )
        output = self.output(result)
        self.assertNotEqual(result.returncode, 0, msg=output)
        self.assertIn("UNVERIFIED", output)
        self.assertIn("live database", output)

    def test_same_live_role_is_a_violation(self):
        """The live check compares the ROLE, not just the database name."""
        bindir, _ = self.make_stubs(psql_out="ichat")
        result = self.run_check(
            staging=staging_env(ICHAT_APP_DIR=str(self.staging_tree)),
            extra_args=("--check-data",),
            path_prefix=str(bindir),
        )
        output = self.output(result)
        self.assertNotEqual(result.returncode, 0, msg=output)
        self.assertIn("live database role", output)
        self.assertIn("IDENTICAL", output)

    def test_same_live_redis_db_is_a_violation(self):
        bindir, _ = self.make_stubs(redis_out="0")
        result = self.run_check(
            staging=staging_env(ICHAT_APP_DIR=str(self.staging_tree)),
            extra_args=("--check-data",),
            path_prefix=str(bindir),
        )
        output = self.output(result)
        self.assertNotEqual(result.returncode, 0, msg=output)
        self.assertIn("live redis", output)

    def test_without_check_data_no_client_is_invoked(self):
        """The static-only mode must not claim to have queried anything."""
        bindir, call_log = self.make_stubs()
        result = self.run_check(
            staging=staging_env(ICHAT_APP_DIR=str(self.staging_tree)),
            path_prefix=str(bindir),
        )
        output = self.output(result)
        calls = call_log.read_text(encoding="utf-8") if call_log.exists() else ""
        self.assertNotIn("psql ", calls)
        self.assertNotIn("redis-cli ", calls)
        self.assertNotIn("live data", output)
        self.assertEqual(result.returncode, 0, msg=output)

    def test_the_verdict_never_says_live_data_when_unverified(self):
        bindir, _ = self.make_stubs(psql_rc=77, psql_out="")
        result = self.run_check(
            staging=staging_env(ICHAT_APP_DIR=str(self.staging_tree)),
            extra_args=("--check-data",),
            path_prefix=str(bindir),
        )
        output = self.output(result)
        self.assertIn("live data", output)
        self.assertIn("UNVERIFIED", output)
        # And it must not end with the success verdict.
        self.assertNotIn("==> ISOLATED", output)
