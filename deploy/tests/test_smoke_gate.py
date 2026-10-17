"""Tests for the post-deploy smoke gate (P4 Test T23 / Issue #204).

The gate that actually runs in production is ``deploy/smoke.sh``, driven by
``deploy/deploy.sh``. These tests exercise that real script, not a Python
re-implementation of it: Django's live server stands in for Daphne behind
Nginx, and the script is executed with the same shell it uses on the server.

What is proven here:
  * a healthy release passes every check and exits 0;
  * each broken behaviour the Issue names (dead readiness, broken login,
    broken core API) makes it exit non-zero — i.e. it fails *closed*;
  * an unauthenticated API that answers with an HTML redirect instead of 401
    is caught (the T31 contract);
  * missing credentials are a configuration error, never a silent pass.

Not proven here (recorded as unverified in the delivery note): the TLS front
door, the real Nginx/Daphne/PostgreSQL/Redis stack, and deploy.sh's
integration of the gate. Those need a real host.
"""

import os
import shutil
import stat
import subprocess
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from django.contrib.auth import get_user_model
from django.test import LiveServerTestCase

REPO_ROOT = Path(__file__).resolve().parents[2]
SMOKE_SCRIPT = REPO_ROOT / "deploy" / "smoke.sh"

# The gate is a bash script; without bash (e.g. a bare Windows CI image) the
# honest outcome is "not executed", not "passed".
BASH = shutil.which("bash")
HAS_BASH = BASH is not None and SMOKE_SCRIPT.is_file()


def _smoke_env(**overrides):
    env = {
        "PATH": os.environ.get("PATH", ""),
        "TMPDIR": os.environ.get("TMPDIR") or os.environ.get("TEMP") or "",
        "SMOKE_TIMEOUT": "10",
        "SMOKE_USERNAME": "",
        "SMOKE_PASSWORD": "",
    }
    env.update(overrides)
    return {k: v for k, v in env.items() if v is not None}


@unittest.skipUnless(HAS_BASH, "bash is not available; smoke gate not executed")
class SmokeGateTests(LiveServerTestCase):
    """Run the real deploy/smoke.sh against Django's live server."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Belt and braces: the gate must be executable-by-invocation, and a
        # modified bit lost by a Windows checkout should not silently pass.
        SMOKE_SCRIPT.chmod(SMOKE_SCRIPT.stat().st_mode | stat.S_IXUSR)

    def setUp(self):
        User = get_user_model()
        self.smoke_user = User.objects.create_user(
            username="deploy_smoke_probe",
            password="Sm0ke-Gate-Passw0rd!",
        )

    def run_smoke(self, base_url=None, env_overrides=None):
        overrides = {
            "BASE_URL": base_url or self.live_server_url,
            "SMOKE_USERNAME": "deploy_smoke_probe",
            "SMOKE_PASSWORD": "Sm0ke-Gate-Passw0rd!",
        }
        overrides.update(env_overrides or {})
        env = _smoke_env(**overrides)
        return subprocess.run(
            [BASH, str(SMOKE_SCRIPT)],
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )

    # --- the gate must pass a working release ---------------------------
    def test_healthy_release_passes_and_reports_each_check(self):
        result = self.run_smoke()
        output = result.stdout + result.stderr
        self.assertEqual(
            result.returncode, 0,
            msg=f"smoke gate rejected a healthy release:\n{output}",
        )
        self.assertIn("Smoke PASSED", output)
        for check in (
            "health: GET /health/live/",
            "readiness: GET /health/ready/",
            "contract: POST /health/ready/",
            "login form: GET /login/",
            "login: POST /login/",
            "session: sessionid cookie issued",
            "core API: GET /api/conversations/",
            "core API: GET /api/profile/me/",
            "auth contract: unauthenticated",
        ):
            self.assertIn(check, output, msg=f"missing check output: {check}\n{output}")

    def test_gate_does_not_leak_the_password(self):
        result = self.run_smoke()
        self.assertNotIn("Sm0ke-Gate-Passw0rd!", result.stdout + result.stderr)

    # --- the gate must fail closed --------------------------------------
    def test_wrong_password_blocks_the_release(self):
        result = self.run_smoke(env_overrides={"SMOKE_PASSWORD": "definitely-not-the-password"})
        output = result.stdout + result.stderr
        self.assertNotEqual(result.returncode, 0, msg=output)
        self.assertIn("login: POST /login/", output)
        self.assertIn("Smoke FAILED", output)

    def test_unknown_base_url_blocks_the_release(self):
        # Nothing listens on this port: every HTTP check must fail, and the
        # script must still exit non-zero rather than treating "no response"
        # as success.
        result = self.run_smoke(base_url="http://127.0.0.1:1")
        output = result.stdout + result.stderr
        self.assertNotEqual(result.returncode, 0, msg=output)
        self.assertIn("000", output)  # curl's "connection failed" code
        self.assertIn("Smoke FAILED", output)

    def test_missing_credentials_are_a_config_error(self):
        result = self.run_smoke(env_overrides={"SMOKE_USERNAME": "", "SMOKE_PASSWORD": ""})
        output = result.stdout + result.stderr
        self.assertEqual(
            result.returncode, 2,
            msg=f"missing credentials must be a usage error (2), not a pass:\n{output}",
        )
        self.assertIn("SMOKE_USERNAME and SMOKE_PASSWORD", output)

    def test_missing_base_url_is_a_config_error(self):
        result = self.run_smoke(env_overrides={"BASE_URL": ""})
        self.assertEqual(result.returncode, 2, msg=result.stdout + result.stderr)


class _BrokenApiHandler(BaseHTTPRequestHandler):
    """Stands in for a release whose API layer regressed.

    /health/live/ and /health/ready/ answer correctly on GET (so readiness
    alone would still pass) and 405 on POST like Django's ``@require_GET``,
    but every other path answers 302 to /login/ — the exact failure mode T31
    removed and that a naive `curl -f` gate would accept.
    """

    def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler API
        if self.path.startswith("/health/"):
            body = b'{"status": "ok"}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(302)
        self.send_header("Location", "/login/")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_POST(self):  # noqa: N802
        if self.path.startswith("/health/"):
            # Mirrors @require_GET: a POST to the gate endpoint must not pass.
            self.send_response(405)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self.send_response(302)
        self.send_header("Location", "/login/")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *args):  # keep test output clean
        pass


@unittest.skipUnless(HAS_BASH, "bash is not available; smoke gate not executed")
class SmokeGateCatchesRedirectedApiTests(unittest.TestCase):
    """The API regression a status-code-only gate would miss."""

    def setUp(self):
        self.server = HTTPServer(("127.0.0.1", 0), _BrokenApiHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def test_redirected_api_with_healthy_readiness_is_rejected(self):
        env = _smoke_env(
            BASE_URL=self.base_url,
            SMOKE_USERNAME="deploy_smoke_probe",
            SMOKE_PASSWORD="whatever",
        )
        result = subprocess.run(
            [BASH, str(SMOKE_SCRIPT)],
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )
        output = result.stdout + result.stderr
        self.assertNotEqual(result.returncode, 0, msg=output)
        # Readiness itself passed; the gate still refused the release.
        self.assertIn("readiness: GET /health/ready/ -> 200", output)
        self.assertIn("login form: GET /login/", output)
        self.assertIn("Smoke FAILED", output)


class SiteOriginTests(unittest.TestCase):
    """AUTO_BASE_URL builds the Referer Django's CSRF check will compare.

    This path is only exercised on the server, so a mistake here would make
    every real deploy fail its own smoke gate (CSRF rejects the login POST).
    Sourcing the script would run it, so the origin logic is reproduced and
    pinned against the values deploy.sh relies on.
    """

    def _origin(self, base_url, host_name):
        # Mirrors deploy/smoke.sh's configuration block.
        rest = base_url.split("://", 1)[1]
        auth = rest.split("/", 1)[0]
        port = ":" + auth.rsplit(":", 1)[1] if ":" in auth else ""
        scheme = "https" if base_url.startswith("https://") else "http"
        return f"{scheme}://{host_name}{port}"

    def test_tls_front_door_keeps_the_port(self):
        self.assertEqual(
            self._origin("https://127.0.0.1:8443", "chat.example.com"),
            "https://chat.example.com:8443",
        )

    def test_loopback_fallback_keeps_the_port(self):
        self.assertEqual(
            self._origin("http://127.0.0.1:8000", "chat.example.com"),
            "http://chat.example.com:8000",
        )

    def test_default_port_is_omitted(self):
        self.assertEqual(
            self._origin("https://127.0.0.1", "chat.example.com"),
            "https://chat.example.com",
        )

    def test_script_matches_these_expectations(self):
        """Guard against the script drifting from the pinned logic above."""
        source = SMOKE_SCRIPT.read_text(encoding="utf-8")
        self.assertIn('BASE_REST=${BASE_URL#*://}', source)
        self.assertIn('PORT_PART=":${BASE_AUTH##*:}"', source)
        # The old broken form put the whole URL into PORT_PART.
        self.assertNotIn('PORT_PART=${BASE_URL#"${BASE_URL%%:*}"}', source)
