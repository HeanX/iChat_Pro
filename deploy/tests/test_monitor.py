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
import sys
import subprocess
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from deploy.tests.deploy_harness import TO_POSIX_SH
from deploy.tests.support import bash, bash_unavailable_reason

REPO_ROOT = Path(__file__).resolve().parents[2]
MONITOR = REPO_ROOT / "deploy" / "monitor.sh"


def make_stub_bin(dirpath: Path, name: str, body: str):
    path = dirpath / name
    path.write_text("#!/usr/bin/env bash\n" + body + "\n", encoding="utf-8")
    path.chmod(0o755)
    return path


class MonitorTestBase(unittest.TestCase):
    """Shared scaffolding: fake PATH, isolated state dir, live stub server."""

    def setUp(self):
        # Fail, never skip: an unexecuted monitor must not look green.
        reason = bash_unavailable_reason()
        if reason:
            raise AssertionError(reason)
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
            self.bindir, "journalctl",
            'echo "header line 1"; echo "header line 2"\n',  # zero real errors
        )
        make_stub_bin(self.bindir, "logger", ":")  # swallow syslog calls
        make_stub_bin(self.bindir, "df", ":")  # replaced by prepare_disk()

        # A fully observable host by default; individual tests override the
        # bit they are pinning (openssl stub below, thresholds, failures).
        self.cert_days = "3650"
        self.pgdata = self.workdir / "pgdata"
        self.pgdata.mkdir(exist_ok=True)
        self.live = self.workdir / "letsencrypt" / "live"
        (self.live / "chat.example.com").mkdir(parents=True, exist_ok=True)
        (self.live / "chat.example.com" / "cert.pem").write_text("stub", encoding="utf-8")
        # A syntactically valid postgres URL (never dialed: psql is stubbed)
        # so the pg-conn check counts as runnable. Written to the env file the
        # way production would.
        self.env_file = self.workdir / "monitor.env"
        self.env_file.write_text(
            "DATABASE_URL=postgres://ichat:secret@127.0.0.1:5432/ichat\n",
            encoding="utf-8",
        )

    def prepare_disk(self, pct):
        stub = self.bindir / "df"
        stub.write_text(
            "#!/usr/bin/env bash\n"
            'echo "Filesystem 1024-blocks Used Available Capacity Mounted on"\n'
            f'echo "/dev/vda1    83886080   10000000   73886080      {pct}% /pgdata"\n',
            encoding="utf-8",
        )
        stub.chmod(0o755)
        return {"ICHAT_PG_DATA_DIR": str(self.pgdata)}

    def prepare_openssl(self):
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
        return {"ICHAT_LETLS_DIR": str(self.live)}

    def observable_env(self):
        """Env additions that make every check runnable (nothing skipped)."""
        return {
            "ICHAT_PG_DATA_DIR": str(self.pgdata),
            "ICHAT_LETLS_DIR": str(self.live),
        }

    def prepare_cert(self, days_left):
        """Install an openssl stub reporting a certificate expiry in N days."""
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
        # Git Bash needs an MSYS-style PATH (/c/... colon-separated); a
        # Linux runner has no cygpath and must not be handed Windows paths
        # at all (review finding F-06). The stub directory and the
        # path-typed knobs are therefore converted inside the wrapper by
        # the generated to_posix helper, which is a no-op for paths that
        # are already POSIX.
        wrapper = self.workdir / "run-monitor.sh"
        wrapper.write_text(
            "#!/usr/bin/env bash\n"
            + TO_POSIX_SH
            + 'export PATH="$PATH"\n'
            'STUB_MSYS=$(to_posix "$ICHAT_STUB_WIN")\n'
            'export PATH="$STUB_MSYS:/usr/bin:/mingw64/bin"\n'
            # Path-typed knobs must be converted too, or openssl -in receives
            # a Windows path it cannot open from inside bash.
            '[[ -n "$ICHAT_LETLS_DIR" ]] && ICHAT_LETLS_DIR=$(to_posix "$ICHAT_LETLS_DIR")\n'
            '[[ -n "$ICHAT_PG_DATA_DIR" ]] && ICHAT_PG_DATA_DIR=$(to_posix "$ICHAT_PG_DATA_DIR")\n'
            'export ICHAT_LETLS_DIR ICHAT_PG_DATA_DIR\n'
            f'exec bash "{MONITOR.as_posix()}"\n',
            encoding="utf-8",
        )
        wrapper.chmod(0o755)
        env = {
            "PATH": os.environ.get("PATH", ""),
            "TMPDIR": os.environ.get("TMPDIR") or os.environ.get("TEMP") or "",
            "ICHAT_MONITOR_STATE": str(state_dir or self.state),
            "ICHAT_ENV_FILE": str(self.env_file),
            "ICHAT_ALERT_WEBHOOK": "",
            # Windows form on Git Bash; converted inside the wrapper by
            # to_posix, which passes POSIX paths through unchanged.
            "ICHAT_STUB_WIN": str(self.bindir),
            # Consumed by the openssl stub; defaults to a far-future expiry.
            "STUB_DAYS": getattr(self, "cert_days", "3650"),
            "ICHAT_PYTHON": sys.executable,
        }
        if live_url:
            env["ICHAT_BASE_URL"] = live_url
        env.update(extra_env or {})
        return subprocess.run(
            [bash(), str(wrapper)],
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
    # Recorded so a test can prove WHICH origin the probe actually dialed
    # (review finding OPS-05: ICHAT_BASE_URL from .env was ignored).
    requests: list[str] = []

    def do_GET(self):  # noqa: N802
        type(self).requests.append(f"{self.command} {self.path}")
        self.send_response(503)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *args):
        pass


class HealthyHostTests(MonitorTestBase):
    def test_all_checks_quiet_when_everything_is_fine(self):
        url = self.start_server(_HealthyReadiness)
        result = self.run_monitor(
            live_url=url, extra_env={**self.observable_env(), **self.prepare_openssl(), **self.prepare_disk(12)}
        )
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
            live_url=url,
            extra_env={"ICHAT_PG_MAX_CONN": "40", **self.observable_env(), **self.prepare_openssl()},
        )
        self.assertIn("ALERT [pg-conn]", result.stdout + result.stderr)

    def test_redis_memory_over_threshold_alert(self):
        url = self.start_server(_HealthyReadiness)
        (self.bindir / "redis-cli").write_text(
            'echo "used_memory:639631360"', encoding="utf-8"  # 610 MB
        )
        result = self.run_monitor(
            live_url=url,
            extra_env={"ICHAT_REDIS_MAX_MB": "512", **self.observable_env(), **self.prepare_openssl()},
        )
        self.assertIn("ALERT [redis-mem]", result.stdout + result.stderr)


class CertificateAndLogTests(MonitorTestBase):
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


class UnobservableHostTests(MonitorTestBase):
    """A check that cannot run must be visible, never read as "all fine"."""

    def test_missing_tool_is_a_check_failure_not_ok(self):
        url = self.start_server(_HealthyReadiness)
        (self.bindir / "redis-cli").unlink()  # tool gone
        result = self.run_monitor(
            live_url=url, extra_env={**self.observable_env(), **self.prepare_openssl(), **self.prepare_disk(12)}
        )
        output = result.stdout + result.stderr
        self.assertIn("CHECK FAILED: redis-mem", output)
        self.assertIn("UNMONITORED", output)
        self.assertNotIn("redis memory:", output.replace("redis-cli not installed", ""))

    def test_unobservable_run_exits_nonzero(self):
        url = self.start_server(_HealthyReadiness)
        (self.bindir / "redis-cli").unlink()
        result = self.run_monitor(
            live_url=url, extra_env={**self.observable_env(), **self.prepare_openssl(), **self.prepare_disk(12)}
        )
        self.assertEqual(
            result.returncode, 1,
            msg="a run with unrunnable checks must exit 1 so cron logs show it",
        )

    def test_missing_db_url_fails_the_pg_check_loudly(self):
        url = self.start_server(_HealthyReadiness)
        self.env_file.write_text("", encoding="utf-8")  # no DATABASE_URL
        result = self.run_monitor(
            live_url=url, extra_env={**self.observable_env(), **self.prepare_openssl(), **self.prepare_disk(12)}
        )
        output = result.stdout + result.stderr
        self.assertIn("CHECK FAILED: pg-conn", output)
        self.assertIn("peer auth", output)
        self.assertEqual(result.returncode, 1)

    def test_unwritable_state_is_reported(self):
        url = self.start_server(_DeadReadiness)
        # A regular file where the state directory should be.
        blocker = self.workdir / "state-blocker"
        blocker.write_text("not a dir", encoding="utf-8")
        result = self.run_monitor(
            live_url=url, state_dir=blocker,
            extra_env={**self.observable_env(), **self.prepare_openssl(), **self.prepare_disk(12)},
        )
        output = result.stdout + result.stderr
        self.assertIn("CHECK FAILED: ready", output)
        self.assertIn("not writable", output)
        self.assertEqual(result.returncode, 1)


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


class EnvFileThresholdTests(MonitorTestBase):
    """The knobs the runbook tells operators to put in .env must be honoured.

    Review finding OPS-05: runbook §3b says to write ICHAT_DISK_PCT,
    ICHAT_ALERT_MIN_SECS, ICHAT_PG_MAX_CONN and ICHAT_CERT_MIN_DAYS into
    /opt/ichat/.env, but the loader only whitelisted four names, so from cron
    every threshold stayed at its default. The tests below set the threshold
    ONLY in the env file (nothing in the process environment) and assert the
    script acts on it.
    """

    def test_disk_threshold_is_read_from_the_env_file(self):
        url = self.start_server(_HealthyReadiness)
        self.env_file.write_text(
            "DATABASE_URL=postgres://ichat:secret@127.0.0.1:5432/ichat\n"
            "ICHAT_DISK_PCT=1\n",
            encoding="utf-8",
        )
        result = self.run_monitor(
            live_url=url,
            extra_env={**self.observable_env(), **self.prepare_openssl(),
                       **self.prepare_disk(12)},
        )
        output = result.stdout + result.stderr
        # 12% crosses a 1% threshold that exists only in the .env file.
        self.assertIn("ALERT [disk]", output, msg=output)
        self.assertIn("12%", output, msg=output)

    def test_alert_min_secs_is_read_from_the_env_file(self):
        url = self.start_server(_DeadReadiness)
        state = self.workdir / "envfile-rate-state"
        state.mkdir()
        self.env_file.write_text(
            "DATABASE_URL=postgres://ichat:secret@127.0.0.1:5432/ichat\n"
            "ICHAT_ALERT_MIN_SECS=3600\n",
            encoding="utf-8",
        )
        for _ in range(4):
            result = self.run_monitor(live_url=url, state_dir=state)
        output = result.stdout + result.stderr
        self.assertIn("alert suppressed (rate limit): ready", output, msg=output)

    def test_pg_max_conn_is_read_from_the_env_file(self):
        url = self.start_server(_HealthyReadiness)
        (self.bindir / "psql").write_text("echo 5\n", encoding="utf-8")
        self.env_file.write_text(
            "DATABASE_URL=postgres://ichat:secret@127.0.0.1:5432/ichat\n"
            "ICHAT_PG_MAX_CONN=3\n",
            encoding="utf-8",
        )
        result = self.run_monitor(
            live_url=url,
            extra_env={**self.observable_env(), **self.prepare_openssl(),
                       **self.prepare_disk(12)},
        )
        output = result.stdout + result.stderr
        # 5 connections crosses a 3-connection threshold set only in .env.
        self.assertIn("ALERT [pg-conn]", output, msg=output)

    def test_cert_min_days_is_read_from_the_env_file(self):
        url = self.start_server(_HealthyReadiness)
        self.prepare_cert(30)
        self.cert_days = "30"
        self.env_file.write_text(
            "DATABASE_URL=postgres://ichat:secret@127.0.0.1:5432/ichat\n"
            "ICHAT_CERT_MIN_DAYS=60\n",
            encoding="utf-8",
        )
        result = self.run_monitor(
            live_url=url,
            extra_env={**self.observable_env(), **self.cert_env,
                       **self.prepare_disk(12)},
        )
        output = result.stdout + result.stderr
        self.assertIn("ALERT [cert]", output, msg=output)

    def test_base_url_is_read_from_the_env_file(self):
        """The readiness origin documented in .env must be the one probed."""
        _DeadReadiness.requests = []
        url = self.start_server(_DeadReadiness)
        self.env_file.write_text(
            "DATABASE_URL=postgres://ichat:secret@127.0.0.1:5432/ichat\n"
            f"ICHAT_BASE_URL={url}\n",
            encoding="utf-8",
        )
        result = self.run_monitor(
            # live_url is deliberately NOT passed: the origin must come from
            # the env file alone.
            extra_env={**self.observable_env(), **self.prepare_openssl(),
                       **self.prepare_disk(12)},
        )
        output = result.stdout + result.stderr
        # The stub server answers 503; a probe that ignored ICHAT_BASE_URL
        # would have hit the default 8443 and never reached this server.
        self.assertIn("readiness check 1/3 failed", output, msg=output)
        self.assertTrue(
            _DeadReadiness.requests,
            msg=f"the .env origin was not probed:\n{output}",
        )

    def test_process_environment_still_beats_the_env_file(self):
        url = self.start_server(_HealthyReadiness)
        self.env_file.write_text(
            "DATABASE_URL=postgres://ichat:secret@127.0.0.1:5432/ichat\n"
            "ICHAT_DISK_PCT=1\n",
            encoding="utf-8",
        )
        result = self.run_monitor(
            live_url=url,
            extra_env={"ICHAT_DISK_PCT": "99", **self.observable_env(),
                       **self.prepare_openssl(), **self.prepare_disk(12)},
        )
        output = result.stdout + result.stderr
        self.assertNotIn("ALERT [disk]", output, msg=output)

    def test_the_loader_never_sources_arbitrary_lines(self):
        """Whitelist, not `source`: a hostile line must not execute."""
        marker = self.workdir / "pwned"
        self.env_file.write_text(
            "DATABASE_URL=postgres://ichat:secret@127.0.0.1:5432/ichat\n"
            f"ICHAT_DISK_PCT=$(touch {marker.as_posix()})\n"
            "UNLISTED_KEY=$(touch "
            + (self.workdir / "pwned2").as_posix()
            + ")\n",
            encoding="utf-8",
        )
        url = self.start_server(_HealthyReadiness)
        self.run_monitor(
            live_url=url,
            extra_env={**self.observable_env(), **self.prepare_openssl(),
                       **self.prepare_disk(12)},
        )
        self.assertFalse(marker.exists(), msg=".env was executed, not parsed")
        self.assertFalse((self.workdir / "pwned2").exists())


class JournalFailureTests(MonitorTestBase):
    """Review finding OPS-06: a failed journalctl query is not "zero errors"."""

    def test_journalctl_failure_is_a_check_failure_and_exits_nonzero(self):
        url = self.start_server(_HealthyReadiness)
        (self.bindir / "journalctl").write_text(
            "#!/usr/bin/env bash\n"
            "echo 'Failed to open the system journal' >&2\n"
            "exit 77\n",
            encoding="utf-8",
        )
        result = self.run_monitor(
            live_url=url,
            extra_env={**self.observable_env(), **self.prepare_openssl(),
                       **self.prepare_disk(12)},
        )
        output = result.stdout + result.stderr
        self.assertIn("CHECK FAILED: journal", output, msg=output)
        self.assertIn("UNMONITORED", output, msg=output)
        self.assertNotIn(
            "journal errors (10 min): 0",
            output,
            msg="an unqueryable journal must never read as zero errors",
        )
        self.assertEqual(result.returncode, 1, msg=output)

    def test_journalctl_failure_alerts(self):
        url = self.start_server(_HealthyReadiness)
        (self.bindir / "journalctl").write_text(
            "#!/usr/bin/env bash\nexit 77\n",
            encoding="utf-8",
        )
        result = self.run_monitor(
            live_url=url,
            extra_env={**self.observable_env(), **self.prepare_openssl(),
                       **self.prepare_disk(12)},
        )
        self.assertIn("ALERT [monitor]", result.stdout + result.stderr)

    def test_a_working_journal_still_reports_zero(self):
        """The healthy path must keep working (no false CHECK FAILED)."""
        url = self.start_server(_HealthyReadiness)
        result = self.run_monitor(
            live_url=url,
            extra_env={**self.observable_env(), **self.prepare_openssl(),
                       **self.prepare_disk(12)},
        )
        output = result.stdout + result.stderr
        self.assertIn("journal errors (10 min): 0 (ok)", output, msg=output)
        self.assertNotIn("CHECK FAILED: journal", output)
        self.assertEqual(result.returncode, 0, msg=output)


class SecretArgvTests(MonitorTestBase):
    """Review finding OPS-07: the DB password must never reach a child's argv.

    The password travels through PGPASSWORD (an environment variable) for
    psql already. The URL *parser* was the leak: it received the whole
    connection URL -- userinfo included -- as a command-line argument, which
    is visible in `ps` and in any argv recorder. These tests install an argv
    recorder as the interpreter and assert the password is absent from every
    recorded invocation.
    """

    PASSWORD = "synth3tic-DB-passw0rd"

    def database_url(self):
        return (
            f"postgres://ichat:{self.PASSWORD}@127.0.0.1:5432/ichat"
        )

    def argv_log_setup(self):
        """Replace the python wrapper so every argv is recorded first."""
        log = self.workdir / "python-argv.log"
        wrapper = self.workdir / "argv-python.sh"
        wrapper.write_text(
            "#!/usr/bin/env bash\n"
            'LOG="$(to_posix "$ARGV_LOG_WIN")"\n'
            "for arg in \"$@\"; do printf '%s\\n' \"$arg\" >> \"$LOG\"; done\n"
            'printf -- "----\\n" >> "$LOG"\n'
            f'exec "{sys.executable}" "$@"\n',
            encoding="utf-8",
            newline="\n",
        )
        wrapper.chmod(0o755)
        return log, {"ICHAT_PYTHON": str(wrapper), "ARGV_LOG_WIN": str(log)}

    def test_password_never_appears_in_a_child_process_argv(self):
        url = self.start_server(_HealthyReadiness)
        self.env_file.write_text(
            f"DATABASE_URL={self.database_url()}\n", encoding="utf-8"
        )
        log, env = self.argv_log_setup()
        result = self.run_monitor(
            live_url=url,
            extra_env={**env, **self.observable_env(), **self.prepare_openssl(),
                       **self.prepare_disk(12)},
        )
        output = result.stdout + result.stderr
        self.assertTrue(log.exists(), msg=f"the parser never ran:\n{output}")
        recorded = log.read_text(encoding="utf-8", errors="replace")
        self.assertNotIn(self.PASSWORD, recorded, msg=recorded)
        self.assertNotIn(self.database_url(), recorded, msg=recorded)

    def test_the_parser_still_produces_the_connection_parameters(self):
        """Security must not cost correctness: psql still gets its env."""
        url = self.start_server(_HealthyReadiness)
        self.env_file.write_text(
            f"DATABASE_URL={self.database_url()}\n", encoding="utf-8"
        )
        env_log = self.workdir / "psql-env.log"
        (self.bindir / "psql").write_text(
            "#!/usr/bin/env bash\n"
            'printf "PGHOST=%s\\n" "$PGHOST" > "$(to_posix "$PSQL_ENV_LOG_WIN")"\n'
            'printf "PGPORT=%s\\n" "$PGPORT" >> "$(to_posix "$PSQL_ENV_LOG_WIN")"\n'
            'printf "PGUSER=%s\\n" "$PGUSER" >> "$(to_posix "$PSQL_ENV_LOG_WIN")"\n'
            'printf "PGDATABASE=%s\\n" "$PGDATABASE" >> "$(to_posix "$PSQL_ENV_LOG_WIN")"\n'
            '[[ -n "$PGPASSWORD" ]] && printf "PGPASSWORD=set\\n" '
            '>> "$(to_posix "$PSQL_ENV_LOG_WIN")"\n'
            "echo 5\n",
            encoding="utf-8",
            newline="\n",
        )
        result = self.run_monitor(
            live_url=url,
            extra_env={**self.observable_env(), **self.prepare_openssl(),
                       **self.prepare_disk(12), "PSQL_ENV_LOG_WIN": str(env_log)},
        )
        output = result.stdout + result.stderr
        recorded = env_log.read_text(encoding="utf-8") if env_log.exists() else ""
        self.assertIn("PGHOST=127.0.0.1", recorded, msg=output)
        self.assertIn("PGUSER=ichat", recorded, msg=output)
        self.assertIn("PGDATABASE=ichat", recorded, msg=output)
        self.assertIn("PGPORT=5432", recorded, msg=output)
        self.assertIn("PGPASSWORD=set", recorded, msg=output)
        self.assertNotIn(self.PASSWORD, recorded)

    def test_the_password_never_reaches_an_alert_or_the_log(self):
        url = self.start_server(_HealthyReadiness)
        self.env_file.write_text(
            f"DATABASE_URL={self.database_url()}\n", encoding="utf-8"
        )
        (self.bindir / "psql").write_text(
            "#!/usr/bin/env bash\nexit 1\n", encoding="utf-8"
        )
        result = self.run_monitor(
            live_url=url,
            extra_env={**self.observable_env(), **self.prepare_openssl(),
                       **self.prepare_disk(12)},
        )
        output = result.stdout + result.stderr
        self.assertIn("CHECK FAILED: pg-conn", output, msg=output)
        self.assertNotIn(self.PASSWORD, output, msg=output)



if __name__ == "__main__":
    unittest.main()
