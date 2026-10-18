"""Tests for deploy/monitor.sh (P4 T12 / Issue #139).

The acceptance criterion is "able to notice service, resource, certificate and
critical error states". The script is exercised for real — bash subprocess,
stub HTTP server for readiness, stub psql/redis-cli/openssl on a fake PATH —
so the thresholds, the consecutive-failure counter and the alert rate limit
are all driven the way they run in production.

What is NOT tested here (recorded as unverified in the delivery note): a real
PostgreSQL/Redis/certbot host, webhook delivery to an actual endpoint, and
cron scheduling.
"""

import os
import shutil
import subprocess
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
MONITOR = REPO_ROOT / "deploy" / "monitor.sh"

BASH = shutil.which("bash")
HAS_BASH = BASH is not None and MONITOR.is_file()


def make_stub_bin(dirpath: Path, name: str, body: str):
    path = dirpath / name
    path.write_text("#!/usr/bin/env bash\n" + body + "\n", encoding="utf-8")
    path.chmod(0o755)
    return path


class MonitorTestBase(unittest.TestCase):
    """Shared scaffolding: fake PATH, isolated state dir, live stub server."""

    def setUp(self):
        if not HAS_BASH:
            self.skipTest("bash is not available; monitor.sh not executed")
        self.workdir = Path(tempfile.mkdtemp(prefix="ichat-monitor-"))
        self.addCleanup(shutil.rmtree, self.workdir, True)
        self.bindir = self.workdir / "bin"
        self.bindir.mkdir()
        self.state = self.workdir / "state"

        # curl talks to the stub server; everything else is a controllable stub.
        make_stub_bin(
            self.bindir, "psql",
            'echo 5\n',  # 5 connections by default: under the threshold
        )
        make_stub_bin(
            self.bindir, "redis-cli",
            'echo "used_memory:104857600"',  # 100 MB: under the threshold
        )
        make_stub_bin(
            self.bindir, "openssl",
            'echo "notAfter=$($STUB_DATE)"',
        )
        make_stub_bin(
            self.bindir, "journalctl",
            'echo "header line 1"; echo "header line 2"\n',  # zero real errors
        )
        make_stub_bin(self.bindir, "logger", ":")  # swallow syslog calls

    def tearDown(self):
        if getattr(self, "server", None) is not None:
            self.server.shutdown()
            self.server.server_close()

    def start_server(self, handler):
        self.server = HTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        return f"http://127.0.0.1:{self.server.server_port}"

    def run_monitor(self, extra_env=None, live_url=None, state_dir=None):
        # Git Bash needs an MSYS-style PATH (/c/... colon-separated). Neither
        # a Windows PATH nor a D:/... form is honoured, so the stubs would be
        # shadowed by the real tools and the thresholds would never fire.
        # MSYS mounts (/c/...) only exist when bash inherits a PATH it can
        # resolve, so the stub directory is converted *inside* the wrapper
        # with cygpath instead of being pre-computed from Windows.
        wrapper = self.workdir / "run-monitor.sh"
        wrapper.write_text(
            "#!/usr/bin/env bash\n"
            'export PATH="C:\\Program Files\\Git\\bin:$PATH"\n'
            'STUB_MSYS=$(cygpath "$ICHAT_STUB_WIN")\n'
            'export PATH="$STUB_MSYS:/mingw64/bin:/usr/bin"\n'
            # Path-typed knobs must be converted too, or openssl -in receives
            # a Windows path it cannot open from inside bash.
            '[[ -n "$ICHAT_LETLS_DIR" ]] && ICHAT_LETLS_DIR=$(cygpath "$ICHAT_LETLS_DIR")\n'
            '[[ -n "$ICHAT_PG_DATA_DIR" ]] && ICHAT_PG_DATA_DIR=$(cygpath "$ICHAT_PG_DATA_DIR")\n'
            'export ICHAT_LETLS_DIR ICHAT_PG_DATA_DIR\n'
            f'exec bash "{MONITOR.as_posix()}"\n',
            encoding="utf-8",
        )
        wrapper.chmod(0o755)
        env = {
            "PATH": os.environ.get("PATH", ""),
            "TMPDIR": os.environ.get("TMPDIR") or os.environ.get("TEMP") or "",
            "ICHAT_MONITOR_STATE": str(state_dir or self.state),
            "ICHAT_ENV_FILE": str(self.workdir / "missing.env"),  # no webhook
            "ICHAT_ALERT_WEBHOOK": "",
            # Windows form; converted by cygpath inside the wrapper.
            "ICHAT_STUB_WIN": str(self.bindir),
            # Consumed by the openssl stub; defaults to a far-future expiry.
            "STUB_DAYS": getattr(self, "cert_days", "3650"),
        }
        if live_url:
            env["ICHAT_BASE_URL"] = live_url
        env.update(extra_env or {})
        return subprocess.run(
            [BASH, str(wrapper)],
            env=env,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=120,
        )


class _HealthyReadiness(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        body = b'{"status": "ok"}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class _DeadReadiness(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        self.send_response(503)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *args):
        pass


@unittest.skipUnless(HAS_BASH, "bash is not available; monitor.sh not executed")
class HealthyHostTests(MonitorTestBase):
    def test_all_checks_quiet_when_everything_is_fine(self):
        url = self.start_server(_HealthyReadiness)
        result = self.run_monitor(live_url=url)
        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, msg=output)
        self.assertIn("checks complete", output)
        self.assertNotIn("ALERT", output, msg=f"unexpected alert:\n{output}")
        for marker in ("disk:", "pg connections:", "redis memory:", "cert:"):
            self.assertIn(marker, output, msg=f"{marker} check did not run:\n{output}")

    def test_readiness_recovery_resets_the_counter(self):
        url = self.start_server(_HealthyReadiness)
        state = self.workdir / "recovery-state"
        state.mkdir()
        (state / "ready-fails").write_text("3\n", encoding="utf-8")
        result = self.run_monitor(live_url=url, state_dir=state)
        output = result.stdout + result.stderr
        self.assertIn("readiness recovered after 3 failed probe(s)", output)
        self.assertEqual((state / "ready-fails").read_text().strip(), "0")


@unittest.skipUnless(HAS_BASH, "bash is not available; monitor.sh not executed")
class ServiceFailureTests(MonitorTestBase):
    def test_consecutive_failures_alert_after_the_threshold(self):
        url = self.start_server(_DeadReadiness)
        state = self.workdir / "ready-state"
        state.mkdir()
        alerts = []

        # Probe 1 and 2: below the threshold, no alert. Probe 3: alert.
        for i in (1, 2, 3):
            result = self.run_monitor(live_url=url, state_dir=state)
            output = result.stdout + result.stderr
            alerts.append("ALERT [ready]" in output)
        self.assertEqual(alerts, [False, False, True], msg=str(alerts))
        self.assertIn("readiness failed 3 consecutive probes", alerts and output)

    def test_single_failure_does_not_alert(self):
        url = self.start_server(_DeadReadiness)
        result = self.run_monitor(live_url=url)
        output = result.stdout + result.stderr
        self.assertNotIn("ALERT [ready]", output)
        self.assertIn("1/3 failed", output)

    def test_alert_is_rate_limited_by_key(self):
        url = self.start_server(_DeadReadiness)
        state = self.workdir / "rate-state"
        state.mkdir()
        # First three runs trip the alert; the fourth is inside the rate
        # window even though failures keep climbing.
        for _ in range(4):
            result = self.run_monitor(
                live_url=url, state_dir=state,
                extra_env={"ICHAT_ALERT_MIN_SECS": "3600"},
            )
        self.assertIn("alert suppressed (rate limit): ready", result.stdout + result.stderr)


@unittest.skipUnless(HAS_BASH, "bash is not available; monitor.sh not executed")
class ResourceThresholdTests(MonitorTestBase):
    def prepare_disk(self, pct):
        # A directory that exists on every OS, injected as the "PG data dir"
        # so df's mount point check never falls back to /var on Windows.
        mount = self.workdir / "pgdata"
        mount.mkdir(exist_ok=True)
        stub = self.bindir / "df"
        stub.write_text(
            "#!/usr/bin/env bash\n"
            'echo "Filesystem 1024-blocks Used Available Capacity Mounted on"\n'
            f'echo "/dev/vda1    83886080   10000000   73886080      {pct}% /pgdata"\n',
            encoding="utf-8",
        )
        stub.chmod(0o755)
        return {"ICHAT_PG_DATA_DIR": str(mount)}

    def test_disk_threshold_alerts(self):
        url = self.start_server(_HealthyReadiness)
        env = self.prepare_disk(92)
        result = self.run_monitor(
            live_url=url, extra_env={"ICHAT_DISK_PCT": "85", **env}
        )
        output = result.stdout + result.stderr
        self.assertIn("ALERT [disk]", output)
        self.assertIn("92%", output)

    def test_disk_below_threshold_stays_quiet(self):
        url = self.start_server(_HealthyReadiness)
        env = self.prepare_disk(12)
        result = self.run_monitor(
            live_url=url, extra_env={"ICHAT_DISK_PCT": "85", **env}
        )
        self.assertNotIn("ALERT [disk]", result.stdout + result.stderr)

    def test_pg_connections_over_threshold_alert(self):
        url = self.start_server(_HealthyReadiness)
        (self.bindir / "psql").write_text("echo 55\n", encoding="utf-8")
        result = self.run_monitor(
            live_url=url, extra_env={"ICHAT_PG_MAX_CONN": "40"}
        )
        self.assertIn("ALERT [pg-conn]", result.stdout + result.stderr)

    def test_redis_memory_over_threshold_alert(self):
        url = self.start_server(_HealthyReadiness)
        (self.bindir / "redis-cli").write_text(
            'echo "used_memory:639631360"', encoding="utf-8"  # 610 MB
        )
        result = self.run_monitor(
            live_url=url, extra_env={"ICHAT_REDIS_MAX_MB": "512"}
        )
        self.assertIn("ALERT [redis-mem]", result.stdout + result.stderr)


@unittest.skipUnless(HAS_BASH, "bash is not available; monitor.sh not executed")
class CertificateAndLogTests(MonitorTestBase):
    def prepare_cert(self, days_left):
        live = self.workdir / "letsencrypt" / "live"
        certdir = live / "chat.example.com"
        certdir.mkdir(parents=True, exist_ok=True)
        (certdir / "cert.pem").write_text("stub", encoding="utf-8")
        # The stub takes the offset in days via $STUB_DAYS and computes the
        # date itself — a pre-built date string would carry quotes that bash
        # passes literally inside $(...).
        stub = self.bindir / "openssl"
        stub.write_text(
            "#!/usr/bin/env bash\n"
            'echo "notAfter=$(date -u -d "+$STUB_DAYS days" "+%b %e %H:%M:%S %Y GMT")"\n',
            encoding="utf-8",
        )
        stub.chmod(0o755)
        self.cert_days = str(days_left)
        self.cert_env = {"ICHAT_LETLS_DIR": str(live)}

    def test_expiring_cert_alerts(self):
        url = self.start_server(_HealthyReadiness)
        self.prepare_cert(7)
        result = self.run_monitor(
            live_url=url,
            extra_env={"ICHAT_CERT_MIN_DAYS": "14", **self.cert_env},
        )
        output = result.stdout + result.stderr
        self.assertIn("ALERT [cert]", output)
        self.assertIn("expires in", output)

    def test_fresh_cert_stays_quiet(self):
        url = self.start_server(_HealthyReadiness)
        self.prepare_cert(90)
        result = self.run_monitor(
            live_url=url,
            extra_env={"ICHAT_CERT_MIN_DAYS": "14", **self.cert_env},
        )
        self.assertNotIn("ALERT [cert]", result.stdout + result.stderr)

    def test_journal_error_burst_alerts(self):
        url = self.start_server(_HealthyReadiness)
        (self.bindir / "journalctl").write_text(
            'echo "header line 1"; echo "header line 2"; '
            'for i in $(seq 1 12); do echo "ERROR something broke"; done',
            encoding="utf-8",
        )
        result = self.run_monitor(live_url=url)
        self.assertIn("ALERT [journal]", result.stdout + result.stderr)


@unittest.skipUnless(HAS_BASH, "bash is not available; monitor.sh not executed")
class AlertDeliveryTests(MonitorTestBase):
    def test_test_alert_flag_sends_without_thresholds(self):
        url = self.start_server(_HealthyReadiness)
        result = self.run_monitor(
            live_url=url, extra_env={"ICHAT_ALERT_TEST": "1"}
        )
        output = result.stdout + result.stderr
        self.assertIn("ALERT [test]", output)

    def test_without_webhook_the_alert_is_still_logged(self):
        url = self.start_server(_DeadReadiness)
        state = self.workdir / "nowebhook"
        state.mkdir()
        for _ in range(3):
            result = self.run_monitor(
                live_url=url, state_dir=state,
                extra_env={"ICHAT_ALERT_MIN_SECS": "0"},
            )
        output = result.stdout + result.stderr
        self.assertIn("ALERT [ready]", output)
        self.assertIn("no ICHAT_ALERT_WEBHOOK configured", output)


if __name__ == "__main__":
    unittest.main()
