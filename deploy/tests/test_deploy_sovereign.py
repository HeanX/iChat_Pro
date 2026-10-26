"""Round-2 regressions: .env must not be able to authorise its own deployment.

Findings F-01..F-04 all share one root cause — ``deploy.sh`` read some
security-critical values *after* ``source $APP_DIR/.env``, so the tree being
deployed could influence decisions about itself:

  F-01  ICHAT_PROD_APP_DIR=<itself> made the incomplete-staging guard pass,
        after which the script restarted the PRODUCTION unit and logged a
        successful production release.
  F-02  ICHAT_DEPLOYER / ICHAT_APPROVAL set in .env rewrote the audit line,
        so the recorded actor/approval differed from the ones the gate used.
  F-03  ICHAT_READY_ORIGIN=https://127.0.0.1:8443 bypassed the port guard.
  F-04  normalise_dir() only stripped trailing slashes, so //, /. and ../
        spellings of the production media root were accepted.

Every test drives the real script through the stubbed host and asserts on what
it *did*: the recorded systemctl/install calls, the log line, and the URLs
curl was asked for.
"""

import os
import unittest
from pathlib import Path

from deploy.tests.deploy_harness import StubbedHostTestCase
from deploy.tests.support import bash_unavailable_reason


class SovereignEnvKeyTests(StubbedHostTestCase, unittest.TestCase):
    """F-01 / F-02: a deployed tree may not choose its own deployment."""

    @classmethod
    def setUpClass(cls):
        reason = bash_unavailable_reason()
        if reason:
            raise AssertionError(reason)

    def setUp(self):
        self.host = self.make_host()

    def staging_env_file(self, **extra):
        """A staging .env that is otherwise complete and correct."""
        values = {
            "ICHAT_SERVICE_NAME": "ichat-staging",
            "ICHAT_SERVICE_USER": "ichat-staging",
            "ICHAT_MEDIA_DIR": str(self.host.workdir / "staging-media"),
            "ICHAT_TLS_PORT": "8444",
            "ICHAT_HTTP_PORT": "8001",
        }
        values.update(extra)
        return values

    def run_deploy(self, env=None, args=("--branch", "main")):
        return self.host.run(*args, env=env or {})

    # --- F-01: the self-declared-production bypass ------------------------
    def test_env_cannot_declare_itself_production(self):
        """ICHAT_PROD_APP_DIR=<own tree> in .env must be refused, not obeyed.

        Before the fix the guard compared APP_DIR with a PROD_APP_DIR that the
        file itself supplied, so this .env made 'staging == production' true,
        skipped every identity check, and restarted ichat.service.
        """
        self.host.write_env(
            ICHAT_PROD_APP_DIR=str(self.host.app_dir),
            # Everything else deliberately unset: this is the attack.
        )
        self.host.write_smoke_stub()

        result = self.run_deploy()
        output = result.stdout + result.stderr
        self.assertNotEqual(result.returncode, 0, msg=output)
        self.assertIn("ICHAT_PROD_APP_DIR", output, msg=output)
        self.assertEqual(
            self.host.read("SYSTEMCTL_LOG"), "",
            msg=f"a self-declared production tree restarted a unit:\n{output}",
        )
        self.assertEqual(self.host.read("GITLOG"), "", msg="the tree was touched")
        self.assertNotIn(
            "kind=deploy", self.host.read_file(self.host.deployments_log),
            msg="the refused release was recorded",
        )

    def test_env_cannot_name_the_production_unit(self):
        """ICHAT_SERVICE_NAME=ichat in .env must be refused."""
        self.host.write_env(**self.staging_env_file(ICHAT_SERVICE_NAME="ichat"))
        self.host.write_smoke_stub()

        result = self.run_deploy()
        output = result.stdout + result.stderr
        self.assertNotEqual(result.returncode, 0, msg=output)
        self.assertIn("ICHAT_SERVICE_NAME", output, msg=output)
        self.assertEqual(self.host.read("SYSTEMCTL_LOG"), "", msg=output)

    def test_env_cannot_name_the_production_user_or_ports(self):
        for key, value in (
            ("ICHAT_SERVICE_USER", "ichat"),
            ("ICHAT_TLS_PORT", "8443"),
            ("ICHAT_HTTP_PORT", "8000"),
        ):
            with self.subTest(key=key):
                self.host.write_env(**self.staging_env_file(**{key: value}))
                self.host.write_smoke_stub()
                result = self.run_deploy()
                output = result.stdout + result.stderr
                self.assertNotEqual(result.returncode, 0, msg=output)
                self.assertIn(key, output, msg=output)
                self.assertEqual(self.host.read("SYSTEMCTL_LOG"), "", msg=output)

    def test_env_cannot_skip_the_smoke_gate(self):
        """ICHAT_SKIP_SMOKE=1 in .env would silently disable the T23 gate."""
        self.host.write_env(**self.staging_env_file(ICHAT_SKIP_SMOKE="1"))
        self.host.write_smoke_stub(exit_code=1)

        result = self.run_deploy()
        output = result.stdout + result.stderr
        self.assertNotEqual(result.returncode, 0, msg=output)
        self.assertIn("ICHAT_SKIP_SMOKE", output, msg=output)
        self.assertNotIn("SKIPPED for this deploy", output)

    def test_a_clean_env_still_works(self):
        """The guard must not reject an ordinary staging .env (all caller-side)."""
        env = self.staging_env_file()
        self.host.write_env()  # only non-sovereign keys
        self.host.write_smoke_stub()

        result = self.host.run("--branch", "main", env=env)
        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, msg=output)
        self.assertIn(
            "systemctl restart ichat-staging.service", self.host.read("SYSTEMCTL_LOG")
        )

    # --- F-02: the audit line records the caller, not the file -----------
    def test_env_cannot_forge_the_recorded_actor(self):
        self.host.write_env(**self.staging_env_file(ICHAT_DEPLOYER="somebody-else"))
        self.host.write_smoke_stub()

        result = self.host.run(
            "--branch", "main",
            env={
                **self.staging_env_file(),
                "ICHAT_DEPLOYER": "real-operator",
                "ICHAT_APPROVAL": "#111-approved",
            },
        )
        output = result.stdout + result.stderr
        self.assertNotEqual(result.returncode, 0, msg=output)
        self.assertIn("ICHAT_DEPLOYER", output)

    def test_env_cannot_forge_the_recorded_approval(self):
        self.host.write_env(**self.staging_env_file(ICHAT_APPROVAL="#999-forged"))
        self.host.write_smoke_stub()

        result = self.host.run(
            "--branch", "main",
            env={**self.staging_env_file(), "ICHAT_APPROVAL": "#111-approved"},
        )
        output = result.stdout + result.stderr
        self.assertNotEqual(result.returncode, 0, msg=output)
        self.assertIn("ICHAT_APPROVAL", output)

    def test_recorded_actor_and_approval_are_the_callers(self):
        """The clean path: what the gate validated is what the log records."""
        self.host.write_env()
        self.host.write_smoke_stub()

        # A staging tree (this fixture is not /opt/ichat) needs its identity on
        # the command line now that identity keys are caller-only.
        result = self.host.run(
            "--branch", "main",
            env={
                "STUB_WIN_PROD_APP": str(self.host.app_dir),
                "ICHAT_SERVICE_NAME": "ichat-staging",
                "ICHAT_SERVICE_USER": "ichat-staging",
                "ICHAT_MEDIA_DIR": str(self.host.workdir / "staging-media"),
                "ICHAT_TLS_PORT": "8444",
                "ICHAT_HTTP_PORT": "8001",
                "ICHAT_DEPLOYER": "real-operator",
                "ICHAT_APPROVAL": "#111-approved",
            },
        )
        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, msg=output)
        log = self.host.read_file(self.host.deployments_log)
        self.assertIn("actor=real-operator", log, msg=log)
        self.assertIn("approval=#111-approved", log, msg=log)
        self.assertNotIn("forged", log)


class ProbeOriginGuardTests(StubbedHostTestCase, unittest.TestCase):
    """F-03: an explicit origin must not smuggle production's port back in."""

    @classmethod
    def setUpClass(cls):
        reason = bash_unavailable_reason()
        if reason:
            raise AssertionError(reason)

    def setUp(self):
        self.host = self.make_host()

    def test_staging_ready_origin_on_production_port_is_refused(self):
        self.host.write_env()
        self.host.write_smoke_stub()
        previous_sha = "0123456789abcdef0123456789abcdef01234567"

        result = self.host.run(
            "--commit", previous_sha,
            env={
                "ICHAT_APP_DIR": str(self.host.app_dir),
                "ICHAT_SERVICE_NAME": "ichat-staging",
                "ICHAT_SERVICE_USER": "ichat-staging",
                "ICHAT_MEDIA_DIR": str(self.host.workdir / "staging-media"),
                "ICHAT_TLS_PORT": "8444",
                "ICHAT_HTTP_PORT": "8001",
                "ICHAT_READY_ORIGIN": "https://127.0.0.1:8443",
            },
        )
        output = result.stdout + result.stderr
        self.assertNotEqual(result.returncode, 0, msg=output)
        self.assertIn("PRODUCTION", output)
        self.assertEqual(self.host.read("CURL_LOG"), "", msg="a probe still ran")
        self.assertEqual(self.host.read("SYSTEMCTL_LOG"), "")

    def test_staging_loopback_origin_on_daphne_port_is_refused(self):
        self.host.write_env()
        self.host.write_smoke_stub()

        result = self.host.run(
            "--branch", "main",
            env={
                "ICHAT_APP_DIR": str(self.host.app_dir),
                "ICHAT_SERVICE_NAME": "ichat-staging",
                "ICHAT_SERVICE_USER": "ichat-staging",
                "ICHAT_MEDIA_DIR": str(self.host.workdir / "staging-media"),
                "ICHAT_TLS_PORT": "8444",
                "ICHAT_HTTP_PORT": "8001",
                "ICHAT_LOOPBACK_ORIGIN": "http://127.0.0.1:8000",
            },
        )
        output = result.stdout + result.stderr
        self.assertNotEqual(result.returncode, 0, msg=output)
        self.assertIn("PRODUCTION", output)
        self.assertEqual(self.host.read("SYSTEMCTL_LOG"), "")

    def test_a_staging_origin_on_its_own_port_is_accepted(self):
        self.host.write_env()
        self.host.write_smoke_stub()

        result = self.host.run(
            "--branch", "main",
            env={
                "ICHAT_APP_DIR": str(self.host.app_dir),
                "ICHAT_SERVICE_NAME": "ichat-staging",
                "ICHAT_SERVICE_USER": "ichat-staging",
                "ICHAT_MEDIA_DIR": str(self.host.workdir / "staging-media"),
                "ICHAT_TLS_PORT": "8444",
                "ICHAT_HTTP_PORT": "8001",
                "ICHAT_READY_ORIGIN": "https://127.0.0.1:8444",
            },
        )
        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, msg=output)
        self.assertIn("https://127.0.0.1:8444/health/ready/", self.host.read("CURL_LOG"))


class MediaRootSpellingTests(StubbedHostTestCase, unittest.TestCase):
    """F-04: duplicate separators, '.', and '..' cannot spell production."""

    @classmethod
    def setUpClass(cls):
        reason = bash_unavailable_reason()
        if reason:
            raise AssertionError(reason)

    def setUp(self):
        self.host = self.make_host()

    def assert_refused(self, media_dir):
        self.host.write_env()
        self.host.write_smoke_stub()
        result = self.host.run(
            "--branch", "main",
            env={
                "ICHAT_APP_DIR": str(self.host.app_dir),
                "ICHAT_SERVICE_NAME": "ichat-staging",
                "ICHAT_SERVICE_USER": "ichat-staging",
                "ICHAT_MEDIA_DIR": media_dir,
                "ICHAT_TLS_PORT": "8444",
                "ICHAT_HTTP_PORT": "8001",
            },
        )
        output = result.stdout + result.stderr
        self.assertNotEqual(
            result.returncode, 0,
            msg=f"MEDIA_DIR={media_dir!r} reached production's media root:\n{output}",
        )
        install_log = self.host.read("INSTALL_LOG")
        self.assertNotIn(
            "-o ichat-staging", install_log,
            msg=f"the staging user was made owner of production media:\n{install_log}",
        )

    def test_plain_production_media_is_refused(self):
        self.assert_refused("/var/lib/ichat/media")

    def test_duplicate_separators_are_refused(self):
        self.assert_refused("/var/lib//ichat/media")

    def test_trailing_dot_component_is_refused(self):
        self.assert_refused("/var/lib/ichat/media/.")

    def test_dotdot_component_is_refused(self):
        self.assert_refused("/var/lib/ichat-staging/../ichat/media")

    def test_dotdot_with_extra_separators_is_refused(self):
        self.assert_refused("/var/lib/ichat-staging//..//ichat/media")

    def test_a_genuinely_separate_media_root_is_accepted(self):
        self.host.write_env()
        self.host.write_smoke_stub()
        result = self.host.run(
            "--branch", "main",
            env={
                "ICHAT_APP_DIR": str(self.host.app_dir),
                "ICHAT_SERVICE_NAME": "ichat-staging",
                "ICHAT_SERVICE_USER": "ichat-staging",
                "ICHAT_MEDIA_DIR": "/var/lib/ichat-staging//media/.",
                "ICHAT_TLS_PORT": "8444",
                "ICHAT_HTTP_PORT": "8001",
            },
        )
        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, msg=output)
        self.assertIn("systemctl restart ichat-staging.service", self.host.read("SYSTEMCTL_LOG"))


if __name__ == "__main__":
    unittest.main()
