"""The runbook and the scripts must describe the same knobs.

Review finding OPS-05 was a documentation/implementation split: runbook 3b told
operators to write thresholds into `.env` while the loader ignored them. The
tests here pin both halves of the chosen resolution -- the documented keys are
in the whitelist, and the documented behaviour is what the script does -- so a
future edit to one without the other fails.
"""

import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
MONITOR = REPO_ROOT / "deploy" / "monitor.sh"
RUNBOOK = REPO_ROOT / "deploy" / "runbook.md"

# Every knob runbook 3b/3c documents as living in .env. This is the contract;
# the script's ENV_FILE_KEYS must cover all of it.
DOCUMENTED_ENV_KEYS = (
    "ICHAT_ALERT_WEBHOOK",
    "ICHAT_ALERT_MIN_SECS",
    "ICHAT_BASE_URL",
    "ICHAT_SERVICE_NAME",
    "ICHAT_DISK_PCT",
    "ICHAT_PG_MAX_CONN",
    "ICHAT_REDIS_MAX_MB",
    "ICHAT_READY_MAX_FAILS",
    "ICHAT_CERT_MIN_DAYS",
    "DATABASE_URL",
)


def monitor_source() -> str:
    return MONITOR.read_text(encoding="utf-8")


def env_file_keys_block(source: str) -> str:
    """The ENV_FILE_KEYS=( ... ) array literal, as text."""
    start = source.index("ENV_FILE_KEYS=(")
    stop = source.index(")", start)
    return source[start:stop]


class DocumentedKeysAreLoadedTests(unittest.TestCase):
    def test_every_documented_key_is_in_the_whitelist(self):
        block = env_file_keys_block(monitor_source())
        missing = [key for key in DOCUMENTED_ENV_KEYS if key not in block]
        self.assertEqual(
            missing,
            [],
            msg=(
                "runbook documents these .env keys but monitor.sh would ignore "
                "them (add them to ENV_FILE_KEYS or stop documenting them):\n"
                + "\n".join(f"  {k}" for k in missing)
            ),
        )

    def test_every_whitelisted_ichat_key_is_documented(self):
        """The other direction: a whitelisted key nobody documents is suspect."""
        block = env_file_keys_block(monitor_source())
        documented = set(DOCUMENTED_ENV_KEYS)
        runbook = RUNBOOK.read_text(encoding="utf-8")
        orphans = []
        for line in block.splitlines():
            key = line.strip().rstrip(",")
            if not key or key.startswith("ENV_FILE_KEYS"):
                continue
            if key in documented:
                continue
            if key not in runbook:
                orphans.append(key)
        self.assertEqual(
            orphans,
            [],
            msg=f"whitelisted but never mentioned in the runbook: {orphans}",
        )

    def test_the_file_is_parsed_not_sourced(self):
        """`source .env` would execute it and export every secret."""
        source = monitor_source()
        self.assertNotIn("source \"$ENV_FILE\"", source)
        self.assertNotIn(". \"$ENV_FILE\"", source)
        # The loader assigns by reference instead, leaving other children clean.
        self.assertIn('printf -v "$key"', source)


class RunbookDescribesTheRealBehaviourTests(unittest.TestCase):
    """The claims an operator will rely on when reading section 3/3b/3c."""

    def setUp(self):
        self.runbook = RUNBOOK.read_text(encoding="utf-8")
        self.monitor = monitor_source()

    def test_runbook_says_the_env_file_is_the_source(self):
        self.assertIn("按白名单逐行解析", self.runbook)
        self.assertIn("ENV_FILE_KEYS", self.runbook)

    def test_runbook_documents_the_journal_failure_behaviour(self):
        self.assertIn("查询失败", self.runbook)

    def test_runbook_documents_stdin_for_the_db_url(self):
        self.assertIn("stdin", self.runbook)

    def test_runbook_states_the_forward_release_rule(self):
        self.assertIn("smoke=skipped-no-script", self.runbook)
        self.assertIn("IS_ROLLBACK", self.runbook)

    def test_runbook_does_not_tell_operators_to_re_export_staging_identity(self):
        """The staging recipe must rely on .env, which is what the fix enables."""
        self.assertNotIn("ICHAT_SERVICE_NAME=ichat-staging \\", self.runbook)

    def test_runbook_names_both_storage_roots(self):
        self.assertIn("media 与 static 两类根目录", self.runbook)


if __name__ == "__main__":
    unittest.main()
