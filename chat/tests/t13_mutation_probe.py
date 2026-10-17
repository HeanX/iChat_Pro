"""Mutation probe for the P4 Test T13 suite (Issue #194).

Why this file exists
--------------------
A test that cannot fail proves nothing. The review checklist
(``docs/handoff/conventions.md`` §1 item 6) requires evidence that the new
integration tests actually catch regressions, so this script injects a
defect, runs the affected cases, and restores the file **byte-for-byte**.

Two of the entries below exist because the first round of this suite got it
wrong and an independent reviewer proved it:

* ``M4c`` (sync upper clamp relaxed) and ``M4b`` (upper clamp removed but the
  lower clamp kept) used to SURVIVE, because ``test_sync_03`` only asserted
  ``len(items) == 3`` against a 3-event fixture.
* ``B1`` / ``B1b`` (close code 4003 changed or removed) used to SURVIVE,
  because ``test_env_06`` waited for "some exception" instead of reading the
  actual ``websocket.close`` frame.

Both are now CAUGHT. Keep them here so a future refactor cannot quietly
regress the assertions back to their weaker shape.

Usage
-----
    .\\.venv\\Scripts\\python.exe chat/tests/t13_mutation_probe.py

Exit code 0 means every mutation was caught and every file restored.
Evidence for the docs/testing/p4-test-t13-*.md table.
"""

import hashlib
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PYTHON = sys.executable

SUITE = "chat.tests.test_http_ws_integration"

MUTATIONS = [
    {
        "id": "M1",
        "target": "chat/services/messaging.py",
        "find": "max_group_active_members = 50",
        "replace": "max_group_active_members = 60",
        "selector": f"{SUITE}.GroupSendHttpTests",
        "why": "group member cap widened 50 -> 60",
    },
    {
        "id": "M3",
        "target": "chat/services/messaging.py",
        "find": '        if recipient_user_ids != active_member_ids:',
        "replace": '        if False and recipient_user_ids != active_member_ids:',
        "selector": f"{SUITE}.GroupSendHttpTests",
        "why": "recipients consistency check disabled",
    },
    {
        "id": "M4",
        "target": "chat/views.py",
        "find": "    limit = max(1, min(limit, 200))",
        "replace": "    limit = limit",
        "selector": f"{SUITE}.SyncHttpTests",
        "why": "sync clamp removed entirely (lower bound too)",
    },
    {
        "id": "M4b",
        "target": "chat/views.py",
        "find": "    limit = max(1, min(limit, 200))",
        "replace": "    limit = max(1, limit)",
        "selector": f"{SUITE}.SyncHttpTests",
        "why": "sync UPPER clamp removed, lower clamp kept",
    },
    {
        "id": "M4c",
        "target": "chat/views.py",
        "find": "    limit = max(1, min(limit, 200))",
        "replace": "    limit = max(1, min(limit, 500))",
        "selector": f"{SUITE}.SyncHttpTests",
        "why": "sync upper clamp relaxed 200 -> 500",
    },
    {
        "id": "M5",
        "target": "chat/consumers.py",
        "find": "            if message.status == EncryptedMessage.Status.RECALLED:",
        "replace": "            if False:",
        "selector": f"{SUITE}.WsRecallTests",
        "why": "double-recall guard removed",
    },
    {
        "id": "M6",
        "target": "chat/consumers.py",
        "find": "        if user.is_anonymous:",
        "replace": "        if False:",
        "selector": f"{SUITE}.WsEnvelopeTests",
        "why": "anonymous connect gate removed",
    },
    {
        "id": "M7",
        "target": "chat/views.py",
        "find": "    if ef.owner_id != request.user.pk:\n        return JsonResponse({'error': 'upload_forbidden'}, status=403)\n\n    if ef.status != EncryptedFile.Status.UPLOADING:\n        return JsonResponse({'error': 'upload_forbidden', 'detail': 'Upload session is not in uploading state.'}, status=403)",
        "replace": "    if ef.status != EncryptedFile.Status.UPLOADING:\n        return JsonResponse({'error': 'upload_forbidden', 'detail': 'Upload session is not in uploading state.'}, status=403)",
        "selector": f"{SUITE}.FileApiHttpTests.test_file_06_foreign_chunk_upload_is_forbidden",
        "why": "chunk upload ownership check removed",
    },
    {
        "id": "M8",
        "target": "chat/services/messaging.py",
        "find": '            raise PayloadError("conversation_forbidden", "Blocked users cannot send messages.")',
        "replace": "            pass",
        "selector": f"{SUITE}.PrivateSendHttpTests",
        "why": "blocked-pair check removed",
    },
    {
        "id": "B1",
        "target": "chat/consumers.py",
        "find": "await self.close(code=UNSUPPORTED_PROTOCOL_CLOSE_CODE)",
        "replace": "await self.close(code=4400)",
        "selector": f"{SUITE}.WsEnvelopeTests",
        "why": "unsupported-version close code 4003 -> 4400",
    },
    {
        "id": "B1b",
        "target": "chat/consumers.py",
        "find": "            await self.close(code=UNSUPPORTED_PROTOCOL_CLOSE_CODE)\n",
        "replace": "",
        "selector": f"{SUITE}.WsEnvelopeTests",
        "why": "unsupported-version close call removed",
    },
]


def _digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _run(selector):
    result = subprocess.run(
        [PYTHON, "manage.py", "test", selector, "-v", "0"],
        cwd=REPO_ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    blob = (result.stderr or "") + (result.stdout or "")
    tail = [line for line in blob.splitlines() if line.startswith(("Ran ", "OK", "FAILED"))]
    return result.returncode, tail


def main():
    survivors = []
    for mutation in MUTATIONS:
        path = REPO_ROOT / mutation["target"]
        original = path.read_bytes()
        before = _digest(path)
        text = original.decode("utf-8")

        # Source files in this checkout use CRLF; anchors are written with LF.
        newline = "\r\n" if "\r\n" in text else "\n"
        find = mutation["find"].replace("\n", newline)
        replace = mutation["replace"].replace("\n", newline)

        if find not in text:
            print(f"[{mutation['id']}] ANCHOR MISSING in {mutation['target']}")
            survivors.append(mutation["id"])
            continue

        path.write_bytes(text.replace(find, replace, 1).encode("utf-8"))
        try:
            code, tail = _run(mutation["selector"])
        finally:
            path.write_bytes(original)

        restored = _digest(path) == before
        verdict = "CAUGHT" if code != 0 else "SURVIVED"
        print(f"[{mutation['id']}] {verdict:<8} restored={restored}  {mutation['why']}")
        for line in tail:
            print(f"        {line}")
        if code == 0 or not restored:
            survivors.append(mutation["id"])

    print()
    if survivors:
        print(f"SURVIVORS / NOT RESTORED: {', '.join(survivors)}")
        return 1
    print(f"All {len(MUTATIONS)} mutations caught; every file restored byte-identically.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
