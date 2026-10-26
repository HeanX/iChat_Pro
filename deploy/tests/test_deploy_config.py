"""Install-configuration wiring in deploy.sh (P4 T49; findings OPS-01 / OPS-02).

Before this module existed, ``deploy.sh`` resolved ``SERVICE_USER`` and
``SERVICE_NAME`` from the *caller's* environment before it read ``$APP_DIR/.env``,
and the readiness/smoke origins were the literals 8443 and 8000. On a staging
install — whose entire configuration lives in its own ``.env`` — that meant:

  * ``systemctl restart ichat.service``: the PRODUCTION unit (OPS-01);
  * ``https://127.0.0.1:8443/health/ready/``: the PRODUCTION front door,
    and the same origins handed to the smoke gate (OPS-02).

Both are driven here through the real script on a stubbed host (see
``deploy_harness``); the recorded argv is the evidence, and the pre-fix script
produces the production values for these same fixtures.

OPS-03 (a forward release must not skip an absent smoke.sh) lives in
``test_deploy_gate`` because it is a property of the gate block itself.
"""

import unittest

from deploy.tests.deploy_harness import StubbedHostTestCase
from deploy.tests.support import bash_unavailable_reason


class DeployConfigTests(StubbedHostTestCase, unittest.TestCase):
    """The service unit, media root and probe origins come from one config."""

    @classmethod
    def setUpClass(cls):
        reason = bash_unavailable_reason()
        if reason:
            # Not a skip: an unexecuted gate must not look green.
            raise AssertionError(reason)

    def setUp(self):
        self.host = self.make_host()

    def staging_env(self, **overrides):
        """Staging identity for the CALLER's environment.

        These keys are caller-only (review finding F-01): a deployed tree must
        not be able to select the unit it restarts or the ports it probes, so
        they travel on the command line, not in $APP_DIR/.env.
        """
        values = {
            "ICHAT_SERVICE_NAME": "ichat-staging",
            "ICHAT_SERVICE_USER": "ichat-staging",
            "ICHAT_MEDIA_DIR": str(self.host.workdir / "staging-media"),
            "ICHAT_TLS_PORT": "8444",
            "ICHAT_HTTP_PORT": "8001",
        }
        values.update(overrides)
        return values

    # --- OPS-01: the runtime identity comes from the resolved config ------
    def test_identity_on_the_command_line_restarts_the_staging_unit(self):
        """The caller's identity reaches systemctl and install verbatim."""
        self.host.write_env()
        self.host.write_smoke_stub()

        result = self.host.run("--branch", "main", env=self.staging_env())
        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, msg=output)

        calls = self.host.read("SYSTEMCTL_LOG")
        self.assertIn("systemctl restart ichat-staging.service", calls, msg=output)
        self.assertNotIn(
            "restart ichat.service",
            calls,
            msg="the production unit must never be restarted for a staging tree",
        )
        # Ownership follows the same resolved config, not a hard-coded user.
        self.assertIn(
            "-o ichat-staging -g ichat-staging",
            self.host.read("INSTALL_LOG"),
            msg=output,
        )

    def test_explicit_caller_identity_is_used_verbatim(self):
        """Whatever the operator types is what gets restarted."""
        self.host.write_env()
        self.host.write_smoke_stub()

        env = self.staging_env(
            ICHAT_SERVICE_NAME="explicit-unit", ICHAT_SERVICE_USER="explicit-user"
        )
        result = self.host.run("--branch", "main", env=env)
        self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)
        self.assertIn(
            "systemctl restart explicit-unit.service",
            self.host.read("SYSTEMCTL_LOG"),
        )

    def test_incomplete_staging_identity_is_refused_before_any_restart(self):
        """A non-production tree that would touch production must not run."""
        self.host.write_env()
        self.host.write_smoke_stub()

        # Only the media root is supplied: the unit and the user still resolve
        # to production, so the run must be refused.
        result = self.host.run(
            "--branch", "main",
            env={"ICHAT_MEDIA_DIR": str(self.host.workdir / "staging-media")},
        )
        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 2, msg=output)
        self.assertIn("PRODUCTION unit", output)
        self.assertIn("Nothing was changed", output)
        self.assertEqual(self.host.read("SYSTEMCTL_LOG"), "", msg="nothing may restart")
        self.assertEqual(self.host.read("GITLOG"), "", msg="the tree must not be touched")

    def test_staging_guard_names_the_missing_media_root(self):
        self.host.write_env()
        self.host.write_smoke_stub()

        result = self.host.run(
            "--branch", "main",
            env=self.staging_env(ICHAT_MEDIA_DIR="/var/lib/ichat/media"),
        )
        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 2, msg=output)
        self.assertIn("PRODUCTION media root", output)
        self.assertEqual(self.host.read("SYSTEMCTL_LOG"), "")

    def test_staging_guard_names_the_production_ports_it_would_probe(self):
        env = self.staging_env()
        del env["ICHAT_TLS_PORT"]
        del env["ICHAT_HTTP_PORT"]
        self.host.write_env()
        self.host.write_smoke_stub()

        result = self.host.run("--branch", "main", env=env)
        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 2, msg=output)
        self.assertIn("PRODUCTION's ports", output)
        self.assertIn("8443", output)
        self.assertEqual(self.host.read("SYSTEMCTL_LOG"), "")

    # --- OPS-02: probes follow the resolved config, never production -----
    def test_staging_readiness_probes_the_staging_ports(self):
        self.host.write_env()
        self.host.write_smoke_stub()

        result = self.host.run("--branch", "main", env=self.staging_env())
        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, msg=output)

        requests = self.host.read("CURL_LOG")
        self.assertIn("https://127.0.0.1:8444/health/ready/", requests, msg=requests)
        self.assertNotIn(":8443", requests, msg="must never probe production's TLS port")
        self.assertNotIn(":8000", requests, msg="must never probe production's Daphne port")

        # ...and the smoke gate receives the same origins, not its own defaults.
        smoke_env = self.host.read("SMOKE_ENV_LOG")
        self.assertIn("SMOKE_TLS_ORIGIN=https://127.0.0.1:8444", smoke_env, msg=smoke_env)
        self.assertIn(
            "SMOKE_LOOPBACK_ORIGIN=http://127.0.0.1:8001", smoke_env, msg=smoke_env
        )

    def test_production_install_keeps_the_production_ports(self):
        """The production layout is unchanged: 8443/8000 and the ichat unit."""
        self.host.write_env()
        self.host.write_smoke_stub()

        result = self.host.run(
            "--branch", "main", env={"STUB_WIN_PROD_APP": str(self.host.app_dir)}
        )
        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, msg=output)
        requests = self.host.read("CURL_LOG")
        self.assertIn("https://127.0.0.1:8443/health/ready/", requests, msg=requests)
        self.assertIn(
            "systemctl restart ichat.service", self.host.read("SYSTEMCTL_LOG")
        )
        smoke_env = self.host.read("SMOKE_ENV_LOG")
        self.assertIn("SMOKE_TLS_ORIGIN=https://127.0.0.1:8443", smoke_env, msg=smoke_env)
        self.assertIn("SMOKE_LOOPBACK_ORIGIN=http://127.0.0.1:8000", smoke_env, msg=smoke_env)

    def test_bad_port_is_a_config_error_not_port_zero(self):
        self.host.write_env()
        self.host.write_smoke_stub()

        result = self.host.run(
            "--branch", "main",
            env=self.staging_env(ICHAT_TLS_PORT="84o4"),
        )
        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 2, msg=output)
        self.assertIn("is not a TCP port", output)
        self.assertEqual(self.host.read("SYSTEMCTL_LOG"), "")


if __name__ == "__main__":
    unittest.main()
