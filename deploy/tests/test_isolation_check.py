"""Isolation check tests (P4 T49 / Issue #176).

Drives the real deploy/staging/check_isolation.sh against fixture .env pairs:
a fully isolated pair must pass; each dimension the acceptance criterion
names (secrets, database, redis logical db, storage paths, runtime identity)
must be flagged as a violation when it is shared with production.
"""

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CHECK = REPO_ROOT / "deploy" / "staging" / "check_isolation.sh"

BASH = shutil.which("bash")
HAS_BASH = BASH is not None and CHECK.is_file()

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
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def staging_env(**overrides):
    values = {
        "DJANGO_DEBUG": "False",
        "DJANGO_SECRET_KEY": "staging-secret-key-bbbbbbbbbbbbbbbbbbbb",
        "DJANGO_ALLOWED_HOSTS": "staging.example.com,127.0.0.1",
        "DJANGO_CSRF_TRUSTED_ORIGINS": "https://staging.example.com:8444",
        "DATABASE_URL": "postgres://ichat_staging:staging-pw@127.0.0.1:5432/ichat_staging",
        "REDIS_URL": "redis://127.0.0.1:6379/1",
        "DJANGO_MEDIA_ROOT": "/var/lib/ichat-staging/media",
        "DJANGO_STATIC_ROOT": "/opt/ichat-staging/staticfiles",
        "ICHAT_APP_DIR": "/opt/ichat-staging",
        "ICHAT_SERVICE_NAME": "ichat-staging",
    }
    values.update(overrides)
    return values


@unittest.skipUnless(HAS_BASH, "bash is not available; check_isolation.sh not executed")
class IsolationCheckTests(unittest.TestCase):
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

    def run_check(self, prod=None, staging=None):
        write_env(self.staging, staging if staging is not None else staging_env())
        if prod is not None:
            write_env(self.prod, prod)
        return subprocess.run(
            [BASH, str(CHECK),
             "--prod-env", str(self.prod),
             "--staging-env", str(self.staging)],
            env={
                "PATH": os.environ.get("PATH", ""),
                "TMPDIR": os.environ.get("TMPDIR") or os.environ.get("TEMP") or "",
            },
            capture_output=True,
            text=True,
            errors="replace",
            timeout=60,
        )

    # --- the good case ----------------------------------------------------
    def test_fully_isolated_staging_passes(self):
        result = self.run_check(staging=staging_env(ICHAT_APP_DIR=str(self.staging_tree)))
        output = result.stdout + result.stderr
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
        output = result.stdout + result.stderr
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("DJANGO_SECRET_KEY: IDENTICAL", output)

    def test_shared_database_url_is_a_violation(self):
        result = self.run_check(
            staging=staging_env(DATABASE_URL=PROD_ENV["DATABASE_URL"])
        )
        self.assertIn("DATABASE_URL: IDENTICAL", result.stdout + result.stderr)
        self.assertNotEqual(result.returncode, 0)

    def test_wrong_database_name_is_a_violation(self):
        result = self.run_check(
            staging=staging_env(
                DATABASE_URL="postgres://ichat:pw@127.0.0.1:5432/othername"
            )
        )
        self.assertIn("not named ichat_staging", result.stdout + result.stderr)
        self.assertNotEqual(result.returncode, 0)

    def test_same_redis_logical_db_is_a_violation(self):
        result = self.run_check(staging=staging_env(REDIS_URL=PROD_ENV["REDIS_URL"]))
        self.assertIn("both use logical db 0", result.stdout + result.stderr)
        self.assertNotEqual(result.returncode, 0)

    def test_shared_csrf_origin_is_a_violation(self):
        result = self.run_check(
            staging=staging_env(
                DJANGO_CSRF_TRUSTED_ORIGINS=PROD_ENV["DJANGO_CSRF_TRUSTED_ORIGINS"]
            )
        )
        self.assertIn("DJANGO_CSRF_TRUSTED_ORIGINS: IDENTICAL", result.stdout + result.stderr)
        self.assertNotEqual(result.returncode, 0)

    def test_media_root_inside_production_media_is_a_violation(self):
        result = self.run_check(
            staging=staging_env(DJANGO_MEDIA_ROOT="/var/lib/ichat/media/staging")
        )
        self.assertIn("overlaps", result.stdout + result.stderr)
        self.assertNotEqual(result.returncode, 0)

    def test_production_app_dir_is_a_violation(self):
        result = self.run_check(staging=staging_env(ICHAT_APP_DIR="/opt/ichat"))
        self.assertIn("uses the production tree", result.stdout + result.stderr)
        self.assertNotEqual(result.returncode, 0)

    def test_production_service_unit_is_a_violation(self):
        result = self.run_check(staging=staging_env(ICHAT_SERVICE_NAME="ichat"))
        self.assertIn("reuses the production unit", result.stdout + result.stderr)
        self.assertNotEqual(result.returncode, 0)
    # --- normalisation and bypass resistance -------------------------------
    def test_trailing_slash_cannot_hide_production_app_dir(self):
        result = self.run_check(staging=staging_env(ICHAT_APP_DIR="/opt/ichat/"))
        self.assertIn("uses the production tree", result.stdout + result.stderr)
        self.assertNotEqual(result.returncode, 0)

    def test_dotdot_media_path_cannot_dodge_nesting_check(self):
        result = self.run_check(
            staging=staging_env(DJANGO_MEDIA_ROOT="/var/lib/ichat-staging/../ichat/media")
        )
        self.assertIn("overlaps", result.stdout + result.stderr)
        self.assertNotEqual(result.returncode, 0)

    def test_missing_database_url_is_reported_not_silent(self):
        partial = staging_env()
        del partial["DATABASE_URL"]
        result = self.run_check(staging=partial)
        output = result.stdout + result.stderr
        self.assertIn("DATABASE_URL: missing on one side", output)
        self.assertIn("media roots are disjoint", output)  # later checks still ran
        self.assertNotEqual(result.returncode, 0)

    def test_missing_redis_url_is_reported_not_silent(self):
        partial = staging_env()
        del partial["REDIS_URL"]
        result = self.run_check(staging=partial)
        self.assertIn("REDIS_URL missing on one side", result.stdout + result.stderr)
        self.assertNotEqual(result.returncode, 0)

    def test_production_service_user_is_a_violation(self):
        result = self.run_check(staging=staging_env(ICHAT_SERVICE_USER="ichat"))
        self.assertIn("runs as the production user", result.stdout + result.stderr)
        self.assertNotEqual(result.returncode, 0)

    def test_port_collision_is_reported_when_unit_is_findable(self):
        # Lay the unit out where the checker looks for it: <staging app
        # dir>/repo/deploy/staging/. The checker script itself stands in as
        # the unit file? No - write a fake unit with port 8000.
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
        output = result.stdout + result.stderr
        self.assertIn("port 8000 collides", output)
        self.assertNotEqual(result.returncode, 0)

    def test_unit_missing_is_reported_as_unverified_port(self):
        # No unit anywhere: the port dimension must not silently pass.
        import shutil as _shutil
        _shutil.rmtree(self.staging_tree)
        result = self.run_check(staging=staging_env(ICHAT_APP_DIR=str(self.staging_tree)))
        output = result.stdout + result.stderr
        self.assertIn("port UNVERIFIED", output)
        self.assertNotEqual(result.returncode, 0)

    # --- evidence hygiene ---------------------------------------------------
    def test_violations_never_print_secret_values(self):
        result = self.run_check(
            staging=staging_env(DJANGO_SECRET_KEY=PROD_ENV["DJANGO_SECRET_KEY"])
        )
        output = result.stdout + result.stderr
        self.assertNotIn(PROD_ENV["DJANGO_SECRET_KEY"], output)
        self.assertNotIn("prod-secret-key", output)

    def test_missing_key_on_either_side_is_flagged(self):
        partial = staging_env()
        del partial["DJANGO_SECRET_KEY"]
        result = self.run_check(staging=partial)
        self.assertIn("missing on one side", result.stdout + result.stderr)
        self.assertNotEqual(result.returncode, 0)
