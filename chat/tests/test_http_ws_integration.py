"""P4 Test T13 / Issue #194 — HTTP + WebSocket integration suite.

Scope
-----
The P0 interfaces of the messaging module, exercised through their real
entry points (Django test client / Channels ``WebsocketCommunicator``),
each covered in four categories:

* **成功** (happy path)
* **异常** (registered error codes, wrong method, malformed input)
* **边界** (length/size/limit boundaries, clamping)
* **越权** (another user's id, non-member, deactivated peer)

This file is the *case index* the Issue asks for: every case id below maps
to exactly one test method, and the same table is mirrored in
``docs/testing/p4-test-t13-http-ws-integration.md``.

Rule for this module: assertions encode the **contract** (``chat/errors.py``
REGISTRY + ADR-P4-02), not whatever the implementation happens to return.
Where the implementation currently deviates, the test pins the deviation
explicitly and is marked ``KNOWN DEVIATION`` instead of pretending it is
correct — see ``test_file_unavailable_status_mismatch_is_still_open``.

Case index
----------
79 test methods. Every id below maps to exactly one method named
``test_<id lowercased>_...``; the same table is mirrored in
``docs/testing/p4-test-t13-http-ws-integration.md``. The ``-02`` id in some
families is intentionally absent — it lives in an existing suite and is
cited there (see the notes column in that document).

Private send (HTTP) — 16
  HTTP-PRIV-01  success persists + 201 + created=true
  HTTP-PRIV-02  ACK-lost replay returns 200 + created=false, no second row
  HTTP-PRIV-03  same id, different content -> 409 idempotency_conflict
  HTTP-PRIV-04  malformed payload -> 400 invalid_payload (6 sub-cases)
  HTTP-PRIV-05  unsupported algorithm -> 400 unsupported_algorithm
  HTTP-PRIV-06  ciphertext at the 64 KiB limit accepted
  HTTP-PRIV-07  ciphertext one byte over the limit rejected
  HTTP-PRIV-08  client_message_id at 64 chars accepted
  HTTP-PRIV-09  client_message_id at 65 chars rejected
  HTTP-PRIV-10  越权 non-member -> 403 conversation_forbidden
  HTTP-PRIV-11  越权 self-send -> 403 conversation_forbidden
  HTTP-PRIV-12  越权 deactivated receiver -> 404 receiver_not_found
  HTTP-PRIV-13  越权 blocked pair -> 403 conversation_forbidden
  HTTP-PRIV-14  private endpoint on a group conversation -> 404
  HTTP-PRIV-15  GET on the send endpoint -> 405
  HTTP-PRIV-16  unauthenticated -> 401 authentication_required (JSON)

Group send (HTTP) — 10
  HTTP-GRP-01   success: per-recipient copies + 201
  HTTP-GRP-02   recipients_mismatch -> 409
  HTTP-GRP-03   duplicated receiver -> 400 invalid_payload
  HTTP-GRP-04   stale membership_version -> 409 membership_conflict
  HTTP-GRP-05   muted group, plain member -> 403 group_muted
  HTTP-GRP-06   muted group, owner -> 201 (role exemption)
  HTTP-GRP-07   member cap boundary: 50 members OK
  HTTP-GRP-08   member cap boundary: 51 members -> 400 group_too_large
  HTTP-GRP-09   越权 non-member -> 403 conversation_forbidden
  HTTP-GRP-10   replay is a zero-write op: no rows, unread, sequence or event

Sync catch-up (HTTP) — 6
  HTTP-SYNC-01  non-integer limit -> 400 invalid_payload
  HTTP-SYNC-02  limit below range clamps to 1
  HTTP-SYNC-03  limit above range clamps to 200
  HTTP-SYNC-04  tampered cursor -> 400 sync_cursor_invalid
  HTTP-SYNC-05  越权 non-member -> 404 conversation_not_found
  HTTP-SYNC-06  inactive conversation -> 404 conversation_not_found

Recall / status / delete (HTTP) — 7
  HTTP-RCL-01   sender recalls own message within the window
  HTTP-RCL-02   already recalled -> 409
  HTTP-RCL-03   recall after 30 minutes -> 400 with the time-limit text
  HTTP-RCL-04   越权 non-sender -> 403
  HTTP-RCL-05   越权 non-member -> 404
  HTTP-MSG-01   status endpoint visible to both participants
  HTTP-DEL-01   per-user delete only affects the caller

Group branch of recall / status / delete (HTTP) — 8
  HTTP-GOPS-01  owner recalls a group message, per-recipient copies flip
  HTTP-GOPS-02  second group recall -> 409
  HTTP-GOPS-03  越权 non-sender -> 403, status unchanged
  HTTP-GOPS-04  group recall after the window -> 400
  HTTP-GOPS-05  status lists every recipient with its status
  HTTP-GOPS-06  status visible to a plain member
  HTTP-GOPS-07  delete recorded as a GROUP deletion, row survives
  HTTP-GOPS-08  message id from another group -> 404

File API — 14
  HTTP-FILE-01  upload session: missing metadata -> 400
  HTTP-FILE-02  upload session: over the per-kind size limit -> 400 file_too_large
  HTTP-FILE-03  越权 upload into a conversation you are not in -> 403
  HTTP-FILE-04  越权 upload into a stranger's conversation -> 403
  HTTP-FILE-05  chunk upload on an unknown upload_id -> 403 upload_forbidden
  HTTP-FILE-06  越权 chunk upload on someone else's session -> 403
  HTTP-FILE-07  member-only file metadata: non-holder -> 403 file_forbidden
  HTTP-FILE-08  deleted file -> 410 (KNOWN DEVIATION vs registry 409)
  HTTP-FILE-09  send_file_message: message_kind mismatch -> 400
  HTTP-FILE-10  send_file_message: unknown file -> 404 file_not_found
  HTTP-FILE-11  unified service path -> 400 registered file_type_mismatch
  HTTP-FILE-12  both file_forbidden guards (shared helper + metadata view)
  HTTP-FILE-12b shared helper guard pinned through the download path
  HTTP-FILE-13  pins the 409-vs-410 drift so it cannot change silently
WebSocket envelope — 5
  WS-ENV-01     server envelope carries protocol_version/event/request_id/sent_at
  WS-ENV-03     unknown event -> 501 not_implemented
  WS-ENV-04     non-object JSON payload -> 400 invalid_payload
  WS-ENV-05     unauthenticated connect -> close 4401
  WS-ENV-06     unsupported protocol version -> error + close 4003
  (WS-ENV-02 ping->pong already lives in test_core / test_protocol)

WebSocket receipts — 3
  WS-RCP-01     receipt on a foreign message -> message_not_found
  WS-RCP-02     receipt with an invalid status -> invalid_payload
  WS-RCP-03     read receipt zeroes unread, sender is notified once

WebSocket recall (single) — 4
  WS-RCL-01     message.recall by sender -> message.recalled
  WS-RCL-02     message.recall twice -> already_recalled
  WS-RCL-03     message.recall after the window -> recall_timeout
  WS-RCL-04     越权 recall by non-sender -> message_not_found

WebSocket typing — 3
  WS-TYP-01     typing.start / typing.stop acks and peer indicator
  WS-TYP-02     typing without conversation_id -> invalid_payload
  WS-TYP-03     越权 typing in a foreign conversation -> conversation_forbidden

WebSocket group recall — 3
  WS-GOPS-01    sender recalls a group message over WS, copies flip
  WS-GOPS-02    越权 group recall by non-sender -> message_not_found
  WS-GOPS-03    recall frame reaches the other members
"""

import base64
import json
import shutil
import tempfile
from pathlib import Path

from asgiref.sync import async_to_sync
from channels.db import database_sync_to_async
from channels.testing import WebsocketCommunicator
from django.conf import settings as dj_settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import Client, TestCase, TransactionTestCase, override_settings

from chat.errors import REGISTRY, UNSUPPORTED_PROTOCOL_CLOSE_CODE
from chat.models import (
    Conversation,
    ConversationEvent,
    ConversationMember,
    EncryptedFile,
    EncryptedFileKey,
    EncryptedMessage,
    GroupMessage,
    GroupMessageRecipient,
    UserMessageDeletion,
)
from ichat_pro.asgi import application

B64_12 = "AAAAAAAAAAAAAAAA"  # 12 bytes
B64_16 = "AAAAAAAAAAAAAAAAAAAAAA=="  # 16 bytes
CT = "Y2lwaGVydGV4dA=="  # "ciphertext"

PRIVATE_SEND_URL = "/api/conversations/{cid}/messages/send/"
GROUP_SEND_URL = "/api/conversations/{cid}/messages/send-group/"
SYNC_URL = "/api/conversations/{cid}/sync/"
RECALL_URL = "/api/conversations/{cid}/messages/{mid}/recall/"
STATUS_URL = "/api/conversations/{cid}/messages/{mid}/status/"
DELETE_URL = "/api/conversations/{cid}/messages/{mid}/"

MAX_CIPHERTEXT_BYTES = 65536
MAX_CLIENT_MESSAGE_ID = 64
MAX_GROUP_MEMBERS = 50


def _b64(raw):
    if isinstance(raw, str):
        raw = raw.encode()
    return base64.b64encode(raw).decode()


def _session_headers(user):
    """Return WebsocketCommunicator headers carrying a real session cookie."""
    client = Client()
    client.force_login(user)
    cookie_name = dj_settings.SESSION_COOKIE_NAME
    session_id = client.cookies[cookie_name].value
    return [
        (b"origin", b"http://testserver"),
        (b"cookie", f"{cookie_name}={session_id}".encode()),
    ]


def _private_payload(conversation_id, receiver_id, client_message_id="c-1", **overrides):
    payload = {
        "conversation_id": conversation_id,
        "receiver_id": receiver_id,
        "client_message_id": client_message_id,
        "message_type": "text",
        "algorithm": "AES-256-GCM",
        "sender_key_version": 1,
        "receiver_key_version": 1,
        "ciphertext": CT,
        "nonce": B64_12,
        "auth_tag": B64_16,
    }
    payload.update(overrides)
    return payload


def _group_payload(group_id, member_ids, client_message_id="g-1", membership_version=1, **overrides):
    payload = {
        "client_message_id": client_message_id,
        "group_id": group_id,
        "membership_version": membership_version,
        "message_type": "text",
        "algorithm": "AES-256-GCM",
        "sender_key_version": 1,
        "recipients": [
            {
                "receiver_id": uid,
                "receiver_key_version": 1,
                "ciphertext": _b64(f"ct-{uid}"),
                "nonce": B64_12,
                "auth_tag": B64_16,
            }
            for uid in member_ids
        ],
    }
    payload.update(overrides)
    return payload


class MessagingHttpBase(TestCase):
    """Fixtures shared by the HTTP cases."""

    @classmethod
    def setUpTestData(cls):
        users = [get_user_model().objects.create_user(f"t13u{i}", password="pass1234") for i in range(4)]
        cls.alice, cls.bob, cls.carol, cls.dave = users

        cls.conv_ab = Conversation.objects.create(type=Conversation.Type.SINGLE, created_by=cls.alice)
        ConversationMember.objects.create(conversation=cls.conv_ab, user=cls.alice)
        ConversationMember.objects.create(conversation=cls.conv_ab, user=cls.bob)

        cls.group = Conversation.objects.create(
            type=Conversation.Type.GROUP, name="t13-group", created_by=cls.alice, membership_version=1,
        )
        ConversationMember.objects.create(
            conversation=cls.group, user=cls.alice, role=ConversationMember.Role.OWNER,
        )
        ConversationMember.objects.create(
            conversation=cls.group, user=cls.bob, role=ConversationMember.Role.MEMBER,
        )

    def client_for(self, user):
        client = Client()
        client.force_login(user)
        return client

    def post_private(self, payload, conversation_id=None, client=None):
        cid = conversation_id or payload.get("conversation_id") or self.conv_ab.pk
        return (client or self.client_for(self.alice)).post(
            PRIVATE_SEND_URL.format(cid=cid),
            data=json.dumps(payload),
            content_type="application/json",
        )

    def post_group(self, payload, client=None):
        return (client or self.client_for(self.alice)).post(
            GROUP_SEND_URL.format(cid=payload["group_id"]),
            data=json.dumps(payload),
            content_type="application/json",
        )

    def group_member_ids(self, group=None):
        return list(
            ConversationMember.objects.filter(
                conversation=group or self.group,
                status=ConversationMember.Status.ACTIVE,
            ).values_list("user_id", flat=True)
        )

    def create_available_file(self, *, owner=None, conversation=None, client_file_id="t13-file",
                              message_kind=EncryptedFile.MessageKind.FILE, status=None):
        return EncryptedFile.objects.create(
            upload_id=f"t13-{client_file_id}",
            client_file_id=client_file_id,
            owner=owner or self.alice,
            conversation=conversation or self.conv_ab,
            message_kind=message_kind,
            status=status or EncryptedFile.Status.AVAILABLE,
            total_size_bytes=32,
            chunk_count=1,
        )


# ── Private send (HTTP) ───────────────────────────────────────────────────


class PrivateSendHttpTests(MessagingHttpBase):
    """HTTP-PRIV-* — POST /api/conversations/<id>/messages/send/"""

    def test_priv_01_success_persists_and_reports_created(self):
        response = self.post_private(_private_payload(self.conv_ab.pk, self.bob.pk, "priv-01"))
        self.assertEqual(response.status_code, 201, response.content)
        body = response.json()
        self.assertTrue(body["created"])
        self.assertEqual(body["conversation_id"], self.conv_ab.pk)
        self.assertEqual(body["sender_id"], self.alice.pk)
        self.assertEqual(body["receiver_id"], self.bob.pk)

        message = EncryptedMessage.objects.get(pk=body["message_id"])
        self.assertEqual(message.ciphertext, CT)
        self.assertEqual(message.client_message_id, "priv-01")
        self.assertEqual(
            ConversationMember.objects.get(conversation=self.conv_ab, user=self.bob).unread_count, 1,
        )

    def test_priv_02_replay_returns_200_without_a_second_row(self):
        first = self.post_private(_private_payload(self.conv_ab.pk, self.bob.pk, "priv-02"))
        self.assertEqual(first.status_code, 201)
        unread_after_create = ConversationMember.objects.get(
            conversation=self.conv_ab, user=self.bob,
        ).unread_count

        second = self.post_private(_private_payload(self.conv_ab.pk, self.bob.pk, "priv-02"))
        self.assertEqual(second.status_code, 200, second.content)
        self.assertFalse(second.json()["created"])
        self.assertEqual(second.json()["message_id"], first.json()["message_id"])
        self.assertEqual(EncryptedMessage.objects.filter(client_message_id="priv-02").count(), 1)
        # A replay must not re-count unread (created=False => zero writes).
        self.assertEqual(
            ConversationMember.objects.get(conversation=self.conv_ab, user=self.bob).unread_count,
            unread_after_create,
        )
        # ...nor allocate a second sync sequence or durable event.
        self.conv_ab.refresh_from_db()
        self.assertEqual(self.conv_ab.sync_sequence, 1)
        self.assertEqual(ConversationEvent.objects.filter(conversation=self.conv_ab).count(), 1)

    def test_priv_03_same_id_different_content_conflicts(self):
        self.post_private(_private_payload(self.conv_ab.pk, self.bob.pk, "priv-03"))
        response = self.post_private(
            _private_payload(self.conv_ab.pk, self.bob.pk, "priv-03", ciphertext=_b64("tampered"))
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"], "idempotency_conflict")
        # Clients must not retry a conflict: the registry declares it
        # non-retryable and the status must match the registry, not a literal.
        error = REGISTRY["idempotency_conflict"]
        self.assertFalse(error.retryable)
        self.assertEqual(response.status_code, error.http_status)

    def test_priv_04_malformed_payload_is_invalid_payload(self):
        cases = {
            "missing ciphertext": _private_payload(self.conv_ab.pk, self.bob.pk, "priv-04a"),
            "bad base64": _private_payload(self.conv_ab.pk, self.bob.pk, "priv-04b", ciphertext="!!!not-base64!!!"),
            "wrong nonce length": _private_payload(self.conv_ab.pk, self.bob.pk, "priv-04c", nonce="AAAA"),
            "wrong auth_tag length": _private_payload(self.conv_ab.pk, self.bob.pk, "priv-04d", auth_tag="AAAA"),
            "unknown message_type": _private_payload(
                self.conv_ab.pk, self.bob.pk, "priv-04e", message_type="hologram"
            ),
            "negative receiver": _private_payload(self.conv_ab.pk, -1, "priv-04f"),
        }
        cases["missing ciphertext"].pop("ciphertext")

        for label, payload in cases.items():
            with self.subTest(case=label):
                response = self.post_private(payload)
                self.assertEqual(response.status_code, 400, f"{label}: {response.content}")
                self.assertEqual(response.json()["error"], "invalid_payload", label)

    def test_priv_05_unsupported_algorithm_is_rejected(self):
        response = self.post_private(
            _private_payload(self.conv_ab.pk, self.bob.pk, "priv-05", algorithm="RSA-OAEP-256")
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "unsupported_algorithm")

    def test_priv_06_ciphertext_at_the_limit_is_accepted(self):
        at_limit = _b64(b"A" * MAX_CIPHERTEXT_BYTES)
        response = self.post_private(
            _private_payload(self.conv_ab.pk, self.bob.pk, "priv-06", ciphertext=at_limit)
        )
        self.assertEqual(response.status_code, 201, response.content)

    def test_priv_07_ciphertext_over_the_limit_is_rejected(self):
        over = _b64(b"A" * (MAX_CIPHERTEXT_BYTES + 1))
        response = self.post_private(
            _private_payload(self.conv_ab.pk, self.bob.pk, "priv-07", ciphertext=over)
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "invalid_payload")
        self.assertFalse(EncryptedMessage.objects.filter(client_message_id="priv-07").exists())

    def test_priv_08_client_message_id_at_the_limit_is_accepted(self):
        response = self.post_private(
            _private_payload(self.conv_ab.pk, self.bob.pk, "i" * MAX_CLIENT_MESSAGE_ID)
        )
        self.assertEqual(response.status_code, 201, response.content)

    def test_priv_09_client_message_id_over_the_limit_is_rejected(self):
        response = self.post_private(
            _private_payload(self.conv_ab.pk, self.bob.pk, "i" * (MAX_CLIENT_MESSAGE_ID + 1))
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "invalid_payload")

    def test_priv_10_non_member_cannot_send(self):
        response = self.post_private(
            _private_payload(self.conv_ab.pk, self.bob.pk, "priv-10"),
            client=self.client_for(self.carol),
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"], "conversation_forbidden")
        self.assertEqual(EncryptedMessage.objects.count(), 0)

    def test_priv_11_self_send_is_forbidden(self):
        response = self.post_private(_private_payload(self.conv_ab.pk, self.alice.pk, "priv-11"))
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"], "conversation_forbidden")

    def test_priv_12_deactivated_receiver_is_not_found(self):
        self.bob.is_active = False
        self.bob.save(update_fields=["is_active"])
        try:
            response = self.post_private(_private_payload(self.conv_ab.pk, self.bob.pk, "priv-12"))
            self.assertEqual(response.status_code, 404)
            self.assertEqual(response.json()["error"], "receiver_not_found")
        finally:
            self.bob.is_active = True
            self.bob.save(update_fields=["is_active"])

    def test_priv_13_blocked_pair_is_forbidden(self):
        from accounts.models import BlockedUser

        BlockedUser.objects.create(blocker=self.bob, blocked=self.alice)
        response = self.post_private(_private_payload(self.conv_ab.pk, self.bob.pk, "priv-13"))
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"], "conversation_forbidden")

    def test_priv_14_private_endpoint_rejects_a_group_conversation(self):
        response = self.post_private(
            _private_payload(self.group.pk, self.bob.pk, "priv-14"), conversation_id=self.group.pk,
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["error"], "conversation_not_found")

    def test_priv_15_get_is_method_not_allowed(self):
        # `generic` keeps the JSON body on a GET (client.get would turn a dict
        # into query params), so this really is "GET with a valid body".
        response = self.client_for(self.alice).generic(
            "GET",
            PRIVATE_SEND_URL.format(cid=self.conv_ab.pk),
            data=json.dumps(_private_payload(self.conv_ab.pk, self.bob.pk, "priv-15")),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 405)
        self.assertFalse(EncryptedMessage.objects.filter(client_message_id="priv-15").exists())

    def test_priv_16_unauthenticated_gets_401_json(self):
        response = Client().post(
            PRIVATE_SEND_URL.format(cid=self.conv_ab.pk),
            data=json.dumps(_private_payload(self.conv_ab.pk, self.bob.pk, "priv-16")),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["error"], "authentication_required")
        self.assertEqual(response["Content-Type"].split(";")[0], "application/json")


# ── Group send (HTTP) ────────────────────────────────────────────────────


class GroupSendHttpTests(MessagingHttpBase):
    """HTTP-GRP-* — POST /api/conversations/<id>/messages/send-group/"""

    def test_grp_01_success_stores_one_copy_per_member(self):
        payload = _group_payload(self.group.pk, self.group_member_ids(), "grp-01")
        response = self.post_group(payload)
        self.assertEqual(response.status_code, 201, response.content)
        body = response.json()
        self.assertTrue(body["created"])

        recipients = GroupMessageRecipient.objects.filter(group_message_id=body["message_id"])
        self.assertEqual(recipients.count(), 2)
        self.assertEqual(
            set(recipients.values_list("receiver_id", flat=True)),
            {self.alice.pk, self.bob.pk},
        )
        # Every recipient got its own copy, not the sender's.
        self.assertEqual(len({r.ciphertext for r in recipients}), 2)

    def test_grp_02_recipients_mismatch_is_rejected(self):
        payload = _group_payload(self.group.pk, [self.alice.pk, self.bob.pk], "grp-02")
        payload["recipients"] = payload["recipients"][:1]  # bob missing
        response = self.post_group(payload)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"], "recipients_mismatch")
        self.assertFalse(GroupMessage.objects.filter(client_message_id="grp-02").exists())

    def test_grp_03_duplicate_receiver_is_invalid_payload(self):
        payload = _group_payload(self.group.pk, [self.alice.pk, self.bob.pk], "grp-03")
        payload["recipients"].append(dict(payload["recipients"][0]))
        response = self.post_group(payload)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "invalid_payload")

    def test_grp_04_stale_membership_version_conflicts(self):
        Conversation.objects.filter(pk=self.group.pk).update(membership_version=2)
        payload = _group_payload(self.group.pk, self.group_member_ids(), "grp-04", membership_version=1)
        response = self.post_group(payload)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"], "membership_conflict")

    def test_grp_05_muted_group_blocks_plain_members(self):
        from django.utils import timezone as dj_timezone

        Conversation.objects.filter(pk=self.group.pk).update(
            muted_until=dj_timezone.now() + dj_timezone.timedelta(minutes=30)
        )
        payload = _group_payload(self.group.pk, self.group_member_ids(), "grp-05")
        response = self.post_group(payload, client=self.client_for(self.bob))
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"], "group_muted")

    def test_grp_06_muted_group_still_allows_the_owner(self):
        from django.utils import timezone as dj_timezone

        Conversation.objects.filter(pk=self.group.pk).update(
            muted_until=dj_timezone.now() + dj_timezone.timedelta(minutes=30)
        )
        payload = _group_payload(self.group.pk, self.group_member_ids(), "grp-06")
        response = self.post_group(payload)
        self.assertEqual(response.status_code, 201, response.content)

    def test_grp_07_fifty_members_is_allowed(self):
        group = self._group_with_members(MAX_GROUP_MEMBERS)
        payload = _group_payload(group.pk, self.group_member_ids(group), "grp-07")
        response = self.post_group(payload)
        self.assertEqual(response.status_code, 201, response.content)

    def test_grp_08_fifty_one_members_hits_the_cap(self):
        group = self._group_with_members(MAX_GROUP_MEMBERS + 1)
        payload = _group_payload(group.pk, self.group_member_ids(group), "grp-08")
        response = self.post_group(payload)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "group_too_large")
        self.assertFalse(GroupMessage.objects.filter(client_message_id="grp-08").exists())

    def test_grp_09_non_member_cannot_send(self):
        # carol is not a member of the group.
        payload = _group_payload(self.group.pk, self.group_member_ids(), "grp-09")
        payload["recipients"].append(
            {"receiver_id": self.carol.pk, "receiver_key_version": 1,
             "ciphertext": _b64("ct-carol"), "nonce": B64_12, "auth_tag": B64_16}
        )
        response = self.post_group(payload, client=self.client_for(self.carol))
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"], "conversation_forbidden")

    def test_grp_10_replay_is_a_zero_write_operation(self):
        """Group replay must not re-create rows, re-count unread or re-sequence."""
        payload = _group_payload(self.group.pk, self.group_member_ids(), "grp-10")
        first = self.post_group(payload)
        self.assertEqual(first.status_code, 201, first.content)
        message_id = first.json()["message_id"]
        unread_before = ConversationMember.objects.get(
            conversation=self.group, user=self.bob,
        ).unread_count
        self.group.refresh_from_db()
        sequence_before = self.group.sync_sequence
        self.assertEqual(sequence_before, 1)

        second = self.post_group(payload)
        self.assertEqual(second.status_code, 200, second.content)
        self.assertFalse(second.json()["created"])
        self.assertEqual(second.json()["message_id"], message_id)

        self.assertEqual(GroupMessage.objects.filter(client_message_id="grp-10").count(), 1)
        self.assertEqual(GroupMessageRecipient.objects.filter(group_message_id=message_id).count(), 2)
        self.assertEqual(
            ConversationMember.objects.get(conversation=self.group, user=self.bob).unread_count,
            unread_before,
        )
        self.group.refresh_from_db()
        self.assertEqual(self.group.sync_sequence, sequence_before)
        self.assertEqual(ConversationEvent.objects.filter(conversation=self.group).count(), 1)

    def _group_with_members(self, total):
        """Owner is alice; fill up to *total* active members with fresh users."""
        group = Conversation.objects.create(
            type=Conversation.Type.GROUP, name=f"cap-{total}", created_by=self.alice, membership_version=1,
        )
        ConversationMember.objects.create(
            conversation=group, user=self.alice, role=ConversationMember.Role.OWNER,
        )
        User = get_user_model()
        filler = [
            User.objects.create_user(f"cap{total}_{i}", password="pass1234")
            for i in range(total - 1)
        ]
        ConversationMember.objects.bulk_create(
            [ConversationMember(conversation=group, user=user) for user in filler]
        )
        return group


# ── Sync catch-up (HTTP) ─────────────────────────────────────────────────


class SyncHttpTests(MessagingHttpBase):
    """HTTP-SYNC-* — GET /api/conversations/<id>/sync/"""

    def setUp(self):
        super().setUp()
        self.client_a = self.client_for(self.alice)
        for i in range(3):
            self.post_private(_private_payload(self.conv_ab.pk, self.bob.pk, f"sync-{i}"))

    def sync(self, *, cursor="", limit=None, client=None, conversation_id=None):
        query = ""
        if cursor:
            query += f"?cursor={cursor}"
        if limit is not None:
            query += ("&" if query else "?") + f"limit={limit}"
        cid = conversation_id or self.conv_ab.pk
        return (client or self.client_a).get(SYNC_URL.format(cid=cid) + query)

    def test_sync_01_non_integer_limit_is_invalid_payload(self):
        response = self.sync(limit="ten")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "invalid_payload")

    def test_sync_02_limit_below_range_clamps_to_one(self):
        response = self.sync(limit=0)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()["items"]), 1)
        self.assertTrue(response.json()["has_more"])

    def test_sync_03_limit_above_range_clamps_to_two_hundred(self):
        """The 200 upper bound must be OBSERVED, not just "not rejected".

        The shared fixture only holds 3 events, so any cap >= 3 would satisfy
        a weaker assertion. This case therefore builds its own conversation
        with 205 events: with the clamp in place the page is exactly 200 and
        `has_more` is true; with the clamp relaxed or removed the page would
        be 205 and the test goes red.
        """
        conv = Conversation.objects.create(type=Conversation.Type.SINGLE, created_by=self.alice)
        ConversationMember.objects.create(conversation=conv, user=self.alice)
        ConversationMember.objects.create(conversation=conv, user=self.bob)
        payload = _private_payload(conv.pk, self.bob.pk, "sync-03")
        for i in range(205):
            payload["client_message_id"] = f"sync-03-{i}"
            response = self.post_private(payload, conversation_id=conv.pk)
            self.assertEqual(response.status_code, 201, response.content)
        conv.refresh_from_db()
        self.assertEqual(conv.sync_sequence, 205)
        self.assertEqual(ConversationEvent.objects.filter(conversation=conv).count(), 205)

        page = self.sync(limit=999, client=self.client_a, conversation_id=conv.pk).json()
        self.assertEqual(len(page["items"]), 200)
        self.assertTrue(page["has_more"])
        self.assertEqual(page["high_water"], 205)

        # One more page drains the remaining 5 and releases the snapshot.
        rest = self.sync(
            cursor=page["next_cursor"], limit=999, client=self.client_a, conversation_id=conv.pk,
        ).json()
        self.assertEqual([i["sequence"] for i in rest["items"]], [201, 202, 203, 204, 205])
        self.assertFalse(rest["has_more"])

    def test_sync_04_tampered_cursor_is_rejected(self):
        good = self.sync(limit=1).json()["next_cursor"]
        response = self.sync(cursor=good.split(".")[0] + ".deadbeef")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "sync_cursor_invalid")

    def test_sync_05_non_member_is_not_found(self):
        response = self.sync(client=self.client_for(self.carol))
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["error"], "conversation_not_found")

    def test_sync_06_inactive_conversation_is_not_found(self):
        Conversation.objects.filter(pk=self.conv_ab.pk).update(status=Conversation.Status.DELETED)
        response = self.sync()
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["error"], "conversation_not_found")


# ── Recall / status / delete (HTTP) ──────────────────────────────────────


class RecallAndDeleteHttpTests(MessagingHttpBase):
    """HTTP-RCL-*, HTTP-MSG-*, HTTP-DEL-*"""

    def setUp(self):
        super().setUp()
        self.client_a = self.client_for(self.alice)
        response = self.post_private(_private_payload(self.conv_ab.pk, self.bob.pk, "rcl-1"))
        self.message_id = response.json()["message_id"]

    def test_rcl_01_sender_recalls_within_the_window(self):
        response = self.client_a.post(RECALL_URL.format(cid=self.conv_ab.pk, mid=self.message_id))
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["status"], "recalled")
        message = EncryptedMessage.objects.get(pk=self.message_id)
        self.assertEqual(message.status, EncryptedMessage.Status.RECALLED)
        self.assertIsNotNone(message.recalled_at)

    def test_rcl_02_already_recalled_is_a_conflict(self):
        self.client_a.post(RECALL_URL.format(cid=self.conv_ab.pk, mid=self.message_id))
        second = self.client_a.post(RECALL_URL.format(cid=self.conv_ab.pk, mid=self.message_id))
        self.assertEqual(second.status_code, 409)

    def test_rcl_03_recall_after_the_window_fails(self):
        from django.utils import timezone as dj_timezone

        stale = dj_timezone.now() - dj_timezone.timedelta(minutes=31)
        EncryptedMessage.objects.filter(pk=self.message_id).update(created_at=stale)
        response = self.client_a.post(RECALL_URL.format(cid=self.conv_ab.pk, mid=self.message_id))
        self.assertEqual(response.status_code, 400)
        # KNOWN DEVIATION (messaging-protocol.md §8 pit 3): the view returns a
        # raw Chinese sentence, not the registered `recall_timeout` code.
        self.assertIn("Recall time limit exceeded", response.json()["error"])

    def test_rcl_04_non_sender_cannot_recall(self):
        response = self.client_for(self.bob).post(
            RECALL_URL.format(cid=self.conv_ab.pk, mid=self.message_id)
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(
            EncryptedMessage.objects.get(pk=self.message_id).status, EncryptedMessage.Status.SENT,
        )

    def test_rcl_05_non_member_cannot_recall(self):
        response = self.client_for(self.carol).post(
            RECALL_URL.format(cid=self.conv_ab.pk, mid=self.message_id)
        )
        self.assertEqual(response.status_code, 404)

    def test_msg_01_status_is_visible_to_both_participants(self):
        for user in (self.alice, self.bob):
            with self.subTest(viewer=user.username):
                response = self.client_for(user).get(
                    STATUS_URL.format(cid=self.conv_ab.pk, mid=self.message_id)
                )
                self.assertEqual(response.status_code, 200, response.content)
                self.assertEqual(response.json()["message_id"], self.message_id)

    def test_del_01_delete_is_per_user(self):
        response = self.client_for(self.bob).delete(
            DELETE_URL.format(cid=self.conv_ab.pk, mid=self.message_id)
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertTrue(
            UserMessageDeletion.objects.filter(
                user=self.bob, message_id=self.message_id,
                message_type=UserMessageDeletion.MessageType.PRIVATE,
            ).exists()
        )
        # Alice'w own view is untouched by Bob's per-user delete.
        self.assertFalse(
            UserMessageDeletion.objects.filter(user=self.alice, message_id=self.message_id).exists()
        )
        self.assertTrue(EncryptedMessage.objects.filter(pk=self.message_id).exists())


class GroupMessageOpsHttpTests(MessagingHttpBase):
    """HTTP-GOPS-* — the GROUP branch of recall / status / delete.

    These three endpoints branch on ``conversation.type``. Until T13 only the
    private branch was covered, so the whole group branch could be made
    unreachable (``elif False:``) with the entire suite still green. Each test
    below is written so that deleting the group branch fails it.
    """

    def setUp(self):
        super().setUp()
        self.client_a = self.client_for(self.alice)
        payload = _group_payload(self.group.pk, self.group_member_ids(), "gops-1")
        response = self.post_group(payload)
        self.assertEqual(response.status_code, 201, response.content)
        self.message_id = response.json()["message_id"]

    def _recipient(self, user):
        return GroupMessageRecipient.objects.get(
            group_message_id=self.message_id, receiver=user,
        )

    def test_gops_01_owner_can_recall_a_group_message(self):
        response = self.client_a.post(
            RECALL_URL.format(cid=self.group.pk, mid=self.message_id)
        )
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(body["status"], "recalled")

        group_message = GroupMessage.objects.get(pk=self.message_id)
        self.assertEqual(group_message.status, GroupMessage.Status.RECALLED)
        self.assertIsNotNone(group_message.recalled_at)
        # Every per-recipient copy is recalled too (the group-only side effect
        # that has no private equivalent).
        statuses = set(
            GroupMessageRecipient.objects.filter(group_message_id=self.message_id)
            .values_list("status", flat=True)
        )
        self.assertEqual(statuses, {GroupMessageRecipient.Status.RECALLED})

    def test_gops_02_second_group_recall_is_a_conflict(self):
        self.client_a.post(RECALL_URL.format(cid=self.group.pk, mid=self.message_id))
        second = self.client_a.post(RECALL_URL.format(cid=self.group.pk, mid=self.message_id))
        self.assertEqual(second.status_code, 409)

    def test_gops_03_group_recall_by_non_sender_is_forbidden(self):
        response = self.client_for(self.bob).post(
            RECALL_URL.format(cid=self.group.pk, mid=self.message_id)
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(
            GroupMessage.objects.get(pk=self.message_id).status, GroupMessage.Status.ACTIVE,
        )

    def test_gops_04_group_recall_after_the_window_fails(self):
        from django.utils import timezone as dj_timezone

        GroupMessage.objects.filter(pk=self.message_id).update(
            created_at=dj_timezone.now() - dj_timezone.timedelta(minutes=31)
        )
        response = self.client_a.post(
            RECALL_URL.format(cid=self.group.pk, mid=self.message_id)
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("Recall time limit exceeded", response.json()["error"])

    def test_gops_05_group_status_lists_every_recipient(self):
        response = self.client_a.get(
            STATUS_URL.format(cid=self.group.pk, mid=self.message_id)
        )
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(body["conversation_type"], "group")
        self.assertEqual(body["message_id"], self.message_id)
        self.assertEqual(body["status"], GroupMessage.Status.ACTIVE)
        self.assertEqual(
            {r["user_id"] for r in body["recipients"]},
            {self.alice.pk, self.bob.pk},
        )
        self.assertTrue(all(r["status"] for r in body["recipients"]))

    def test_gops_06_group_status_is_visible_to_a_plain_member(self):
        response = self.client_for(self.bob).get(
            STATUS_URL.format(cid=self.group.pk, mid=self.message_id)
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["conversation_type"], "group")

    def test_gops_07_group_delete_is_recorded_as_a_group_deletion(self):
        response = self.client_for(self.bob).delete(
            DELETE_URL.format(cid=self.group.pk, mid=self.message_id)
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertTrue(
            UserMessageDeletion.objects.filter(
                user=self.bob, message_id=self.message_id,
                message_type=UserMessageDeletion.MessageType.GROUP,
            ).exists()
        )
        # The shared row survives; only Bob's view changes.
        self.assertTrue(GroupMessage.objects.filter(pk=self.message_id).exists())

    def test_gops_08_group_message_not_found_in_this_conversation(self):
        other_group = Conversation.objects.create(
            type=Conversation.Type.GROUP, name="gops-other", created_by=self.alice,
            membership_version=1,
        )
        ConversationMember.objects.create(
            conversation=other_group, user=self.alice, role=ConversationMember.Role.OWNER,
        )
        response = self.client_a.get(
            STATUS_URL.format(cid=other_group.pk, mid=self.message_id)
        )
        self.assertEqual(response.status_code, 404)


class FileApiHttpTests(MessagingHttpBase):
    """HTTP-FILE-* — upload session, chunk, download, send-file-message."""

    @classmethod
    def setUpClass(cls):
        cls._media_root = Path(tempfile.mkdtemp(prefix="ichat-t13-media-"))
        cls.enterClassContext(override_settings(MEDIA_ROOT=cls._media_root))
        cls.addClassCleanup(shutil.rmtree, cls._media_root, ignore_errors=True)
        super().setUpClass()

    def upload_payload(self, **overrides):
        payload = {
            "client_file_id": "t13-upload",
            "conversation_id": self.conv_ab.pk,
            "conversation_type": "single",
            "message_kind": "file",
            "total_size_bytes": 32,
            "chunk_count": 1,
        }
        payload.update(overrides)
        return payload

    def post_upload(self, payload, client=None):
        return (client or self.client_for(self.alice)).post(
            "/api/files/uploads/",
            data=json.dumps(payload),
            content_type="application/json",
        )

    def test_file_01_missing_metadata_is_rejected(self):
        cases = {
            "client_file_id": self.upload_payload(client_file_id=""),
            "conversation_type": self.upload_payload(conversation_type="team"),
            "message_kind": self.upload_payload(message_kind="hologram"),
            "chunk_count": self.upload_payload(chunk_count=0),
        }
        for label, payload in cases.items():
            with self.subTest(field=label):
                response = self.post_upload(payload)
                self.assertEqual(response.status_code, 400, f"{label}: {response.content}")
                self.assertEqual(response.json()["error"], "invalid_file_metadata", label)

    def test_file_02_over_the_size_limit_is_rejected(self):
        response = self.post_upload(self.upload_payload(total_size_bytes=10 ** 12))
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "file_too_large")

    def test_file_03_non_member_cannot_open_an_upload_session(self):
        response = self.post_upload(self.upload_payload(), client=self.client_for(self.carol))
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"], "conversation_forbidden")
        self.assertFalse(EncryptedFile.objects.filter(client_file_id="t13-upload").exists())

    def test_file_04_upload_into_a_foreign_conversation_is_forbidden(self):
        stranger_conv = Conversation.objects.create(type=Conversation.Type.SINGLE, created_by=self.carol)
        ConversationMember.objects.create(conversation=stranger_conv, user=self.carol)
        ConversationMember.objects.create(conversation=stranger_conv, user=self.dave)
        response = self.post_upload(
            self.upload_payload(conversation_id=stranger_conv.pk), client=self.client_for(self.alice),
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"], "conversation_forbidden")

    def test_file_05_chunk_on_an_unknown_upload_is_forbidden(self):
        response = self.client_for(self.alice).put(
            "/api/files/uploads/does-not-exist/chunks/0/",
            data=b"x", content_type="application/octet-stream",
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"], "upload_forbidden")

    def test_file_06_foreign_chunk_upload_is_forbidden(self):
        session = self.post_upload(self.upload_payload(client_file_id="t13-foreign"))
        self.assertEqual(session.status_code, 201, session.content)
        upload_id = session.json()["upload_id"]

        response = self.client_for(self.carol).put(
            f"/api/files/uploads/{upload_id}/chunks/0/",
            data=b"x", content_type="application/octet-stream",
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"], "upload_forbidden")

    def test_file_07_non_holder_cannot_read_metadata(self):
        ef = self.create_available_file(client_file_id="t13-noKey")
        response = self.client_for(self.carol).get(f"/api/files/{ef.pk}/")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"], "file_forbidden")

    def test_file_08_deleted_file_is_unavailable(self):
        ef = self.create_available_file(
            client_file_id="t13-deleted", status=EncryptedFile.Status.DELETED,
        )
        response = self.client_for(self.alice).get(f"/api/files/{ef.pk}/")
        # KNOWN DEVIATION: the file views answer 410 while the registry
        # declares 409 for `file_unavailable`. Pinned by
        # test_file_unavailable_status_mismatch_is_still_open below.
        self.assertEqual(response.status_code, 410)
        self.assertEqual(response.json()["error"], "file_unavailable")

    def test_file_09_send_message_type_mismatch(self):
        ef = self.create_available_file(
            client_file_id="t13-kind", message_kind=EncryptedFile.MessageKind.FILE,
        )
        response = self.client_for(self.alice).post(
            f"/api/files/{ef.pk}/messages/",
            data=json.dumps({
                "conversation_id": self.conv_ab.pk,
                "conversation_type": "single",
                "client_message_id": "t13-kind-1",
                "message_type": "sticker",  # file != sticker
            }),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "message_type_mismatch")

    def test_file_10_send_file_message_rejects_unknown_file(self):
        response = self.client_for(self.alice).post(
            "/api/files/999999/messages/",
            data=json.dumps({
                "conversation_id": self.conv_ab.pk,
                "conversation_type": "single",
                "client_message_id": "t13-missing-1",
                "message_type": "file",
            }),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["error"], "file_not_found")

    def test_file_11_registered_file_type_mismatch_is_reachable(self):
        """The REGISTERED `file_type_mismatch` code, not its unregistered twin.

        There are two distinct checks and they return different codes:

        * ``POST /api/files/<id>/messages/`` (``send_file_message_view``)
          compares ``message_type`` with the upload's ``message_kind`` and
          answers the **unregistered** ``message_type_mismatch``
          (covered by HTTP-FILE-09).
        * the unified service (``send_private_message`` with ``file_id``)
          reaches ``validate_attached_file`` and raises the **registered**
          ``file_type_mismatch``.

        Only the second one is a code clients can branch on per
        ``docs/phase4/protocol.md`` §2, so it needs its own case.
        """
        ef = self.create_available_file(
            client_file_id="t13-reg-kind", message_kind=EncryptedFile.MessageKind.FILE,
        )
        for holder in (self.alice, self.bob):
            EncryptedFileKey.objects.create(
                file=ef, holder=holder, sender=self.alice,
                encrypted_file_key="k", nonce=B64_12, auth_tag=B64_16,
            )

        response = self.post_private({
            "conversation_id": self.conv_ab.pk,
            "receiver_id": self.bob.pk,
            "client_message_id": "t13-reg-kind-1",
            "message_type": "image",  # upload kind is "file"
            "file_id": ef.pk,
            "ciphertext": CT,
            "nonce": B64_12,
            "auth_tag": B64_16,
            "algorithm": "AES-256-GCM",
            "sender_key_version": 1,
            "receiver_key_version": 1,
        })
        self.assertEqual(response.status_code, REGISTRY["file_type_mismatch"].http_status)
        self.assertEqual(response.json()["error"], "file_type_mismatch")
        self.assertFalse(EncryptedMessage.objects.filter(client_message_id="t13-reg-kind-1").exists())

    def test_file_12_non_holder_cannot_read_metadata_without_a_key(self):
        """Both `file_forbidden` guards, not just one of them.

        Guard A lives in the shared ``_get_encrypted_file_or_error`` helper
        (non-holder without a key row); guard B is ``get_file_metadata_view``'s
        own key check (a user who passes the helper but has no key row). The
        reviewer showed that deleting guard A left the suite green, so both
        halves are asserted here.
        """
        ef = self.create_available_file(client_file_id="t13-noKey2")

        # Guard A: carol is neither the owner nor a key holder.
        response = self.client_for(self.carol).get(f"/api/files/{ef.pk}/")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"], "file_forbidden")

        # Guard B: the owner passes the helper, but without a key row the
        # metadata view must still refuse.
        EncryptedFileKey.objects.all().delete()
        response = self.client_for(self.alice).get(f"/api/files/{ef.pk}/")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"], "file_forbidden")

    def test_file_12b_shared_helper_rejects_a_keyless_member_for_download(self):
        """Pins guard A through the *download* path as well.

        If the shared helper's final ``file_forbidden`` return is replaced by
        a success value, this case fails because carol would then reach the
        storage check instead of being refused outright.
        """
        ef = self.create_available_file(client_file_id="t13-noKey3")
        response = self.client_for(self.carol).get(f"/api/files/{ef.pk}/download/")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"], "file_forbidden")

    def test_file_unavailable_status_mismatch_is_still_open(self):
        """KNOWN DEVIATION — pinned on purpose.

        ``messaging-protocol.md`` §8 pit 3 records that `file_unavailable` is
        registered as 409 but returned as 410 by the file views. This test
        fixes BOTH numbers so the mismatch cannot drift silently: whoever
        fixes either side must come here and update the case index too.
        """
        self.assertEqual(REGISTRY["file_unavailable"].http_status, 409)

        ef = self.create_available_file(
            client_file_id="t13-drift", status=EncryptedFile.Status.DELETED,
        )
        response = self.client_for(self.alice).get(f"/api/files/{ef.pk}/")
        self.assertEqual(response.status_code, 410)


# ── WebSocket envelope and events ────────────────────────────────────────


class MessagingWsBase(TransactionTestCase):
    """Fixtures for the WebSocket cases (real consumers via the ASGI app)."""

    def setUp(self):
        cache.clear()
        self.alice = get_user_model().objects.create_user("t13ws_a", password="pass1234")
        self.bob = get_user_model().objects.create_user("t13ws_b", password="pass1234")
        self.carol = get_user_model().objects.create_user("t13ws_c", password="pass1234")

        self.conv = Conversation.objects.create(type=Conversation.Type.SINGLE, created_by=self.alice)
        ConversationMember.objects.create(conversation=self.conv, user=self.alice)
        ConversationMember.objects.create(conversation=self.conv, user=self.bob)

        self.group = Conversation.objects.create(
            type=Conversation.Type.GROUP, name="t13-ws-group", created_by=self.alice, membership_version=1,
        )
        ConversationMember.objects.create(
            conversation=self.group, user=self.alice, role=ConversationMember.Role.OWNER,
        )
        ConversationMember.objects.create(
            conversation=self.group, user=self.bob, role=ConversationMember.Role.MEMBER,
        )

        # Session headers MUST be built in the synchronous setUp: force_login
        # writes the session row and cannot run inside the async test body.
        self._headers = {
            self.alice.pk: _session_headers(self.alice),
            self.bob.pk: _session_headers(self.bob),
            self.carol.pk: _session_headers(self.carol),
        }

    def headers(self, user):
        return self._headers[user.pk]

    async def connect(self, user):
        comm = WebsocketCommunicator(application, "/ws/chat/", headers=self.headers(user))
        connected, _ = await comm.connect()
        self.assertTrue(connected)
        ready = await comm.receive_json_from()
        self.assertEqual(ready["event"], "connection.ready")
        return comm

    def ws_payload(self, client_message_id, **overrides):
        return _private_payload(self.conv.pk, self.bob.pk, client_message_id, **overrides)


class WsEnvelopeTests(MessagingWsBase):
    """WS-ENV-*"""

    def test_env_01_envelope_shape(self):
        async def run():
            comm = await self.connect(self.alice)
            await comm.send_json_to({"event": "connection.ping", "request_id": "env-1"})
            frame = await comm.receive_json_from()
            self.assertEqual(
                set(frame.keys()), {"protocol_version", "event", "request_id", "sent_at", "data"},
            )
            self.assertEqual(frame["protocol_version"], "1.0")
            self.assertEqual(frame["request_id"], "env-1")
            await comm.disconnect()

        async_to_sync(run)()

    def test_env_03_unknown_event_is_not_implemented(self):
        async def run():
            comm = await self.connect(self.alice)
            await comm.send_json_to({"event": "message.teleport", "request_id": "env-3", "data": {}})
            error = await comm.receive_json_from()
            self.assertEqual(error["event"], "error")
            self.assertEqual(error["request_id"], "env-3")
            self.assertEqual(error["data"]["code"], "not_implemented")
            self.assertFalse(error["data"]["retryable"])
            await comm.disconnect()

        async_to_sync(run)()

    def test_env_04_non_object_json_is_invalid_payload(self):
        async def run():
            comm = await self.connect(self.alice)
            await comm.send_json_to(["not", "an", "object"])
            error = await comm.receive_json_from()
            self.assertEqual(error["event"], "error")
            self.assertEqual(error["data"]["code"], "invalid_payload")
            self.assertIsNone(error["request_id"])
            await comm.disconnect()

        async_to_sync(run)()

    def test_env_05_anonymous_connect_closes_with_4401(self):
        async def run():
            comm = WebsocketCommunicator(
                application, "/ws/chat/", headers=[(b"origin", b"http://testserver")],
            )
            connected, close_code = await comm.connect()
            self.assertFalse(connected)
            self.assertEqual(close_code, 4401)

        async_to_sync(run)()

    def test_env_06_unsupported_version_reports_then_closes_4003(self):
        async def run():
            comm = await self.connect(self.alice)
            await comm.send_json_to({
                "protocol_version": "9.9", "event": "connection.ping", "request_id": "env-6",
            })
            error = await comm.receive_json_from()
            self.assertEqual(error["event"], "error")
            self.assertEqual(error["data"]["code"], "unsupported_protocol_version")

            # Read the ACTUAL close frame. `assertRaises(Exception)` around a
            # timeout (the old shape, still present in test_protocol.py) passes
            # for any reason the socket goes quiet, including a wrong close
            # code or no close at all — it cannot prove 4003.
            message = await comm.receive_output(timeout=3)
            self.assertEqual(message["type"], "websocket.close")
            self.assertEqual(message["code"], UNSUPPORTED_PROTOCOL_CLOSE_CODE)
            self.assertEqual(message["code"], 4003)

        async_to_sync(run)()


class WsReceiptTests(MessagingWsBase):
    """WS-RCP-*"""

    async def send_message(self, comm, client_message_id="rcp-1"):
        await comm.send_json_to({
            "event": "message.single.send", "request_id": "rcp-send",
            "data": self.ws_payload(client_message_id),
        })
        accepted = await comm.receive_json_from()
        self.assertEqual(accepted["event"], "message.single.accepted")
        return accepted["data"]["message_id"]

    def test_rcp_01_receipt_for_a_foreign_message_is_not_found(self):
        async def run():
            sender = await self.connect(self.alice)
            message_id = await self.send_message(sender)
            # carol is not the receiver of this message.
            outsider = await self.connect(self.carol)
            await outsider.send_json_to({
                "event": "message.receipt.update", "request_id": "rcp-1",
                "data": {"conversation_type": "single", "message_id": message_id, "status": "read"},
            })
            error = await outsider.receive_json_from()
            self.assertEqual(error["data"]["code"], "message_not_found")
            await sender.disconnect()
            await outsider.disconnect()

        async_to_sync(run)()

    def test_rcp_02_invalid_status_is_rejected(self):
        async def run():
            sender = await self.connect(self.alice)
            message_id = await self.send_message(sender)
            receiver = await self.connect(self.bob)
            await receiver.send_json_to({
                "event": "message.receipt.update", "request_id": "rcp-2",
                "data": {"conversation_type": "single", "message_id": message_id, "status": "exploded"},
            })
            error = await receiver.receive_json_from()
            self.assertEqual(error["data"]["code"], "invalid_payload")
            await sender.disconnect()
            await receiver.disconnect()

        async_to_sync(run)()

    def test_rcp_03_read_receipt_clears_unread_and_notifies_once(self):
        """WS-RCP-03: one `read` receipt zeroes unread and notifies the sender once.

        Scope note (MSG-02): the name says exactly what is asserted — a single
        receipt, a single ``message.receipt.updated`` event for the sender, one
        ack carrying ``request_id``, and ``unread_count == 0`` afterwards.

        "once" is checked, not assumed (MSG-02-A): after the sender reads its
        one event, ``receive_nothing()`` asserts the queue is empty. An extra
        ``message.receipt.updated`` would therefore turn this case red.

        It deliberately does NOT claim monotonicity. "A later ``delivered`` (or
        a new message) must not re-bump the count" is *not* exercised here:
        there is no second receipt and no follow-up message in this case.
        """
        async def run():
            sender = await self.connect(self.alice)
            receiver = await self.connect(self.bob)
            message_id = await self.send_message(sender)
            await receiver.receive_json_from()  # message.single.new

            await receiver.send_json_to({
                "event": "message.receipt.update", "request_id": "rcp-3",
                "data": {"conversation_type": "single", "message_id": message_id, "status": "read"},
            })
            # the sender's first (and only) status event
            update = await sender.receive_json_from()
            self.assertEqual(update["event"], "message.receipt.updated")
            self.assertEqual(update["data"]["status"], "read")
            ack = await receiver.receive_json_from()
            self.assertEqual(ack["request_id"], "rcp-3")

            unread = await database_sync_to_async(
                lambda: ConversationMember.objects.get(
                    conversation_id=self.conv.pk, user_id=self.bob.pk,
                ).unread_count
            )()
            self.assertEqual(unread, 0)

            # ...and nothing after it: one receipt must not fan out into a
            # second status event for the sender.
            self.assertTrue(
                await sender.receive_nothing(timeout=0.2),
                "sender received a second message.receipt.updated for one read receipt",
            )
            await sender.disconnect()
            await receiver.disconnect()

        async_to_sync(run)()


class WsRecallTests(MessagingWsBase):
    """WS-RCL-* — the `message.recall` event had zero coverage before T13."""

    async def send_message(self, comm, client_message_id="wsrc-1"):
        await comm.send_json_to({
            "event": "message.single.send", "request_id": "wsrc-send",
            "data": self.ws_payload(client_message_id),
        })
        accepted = await comm.receive_json_from()
        return accepted["data"]["message_id"]

    def test_rcl_01_sender_recalls_over_websocket(self):
        async def run():
            sender = await self.connect(self.alice)
            message_id = await self.send_message(sender)
            await sender.send_json_to({
                "event": "message.recall", "request_id": "wsrc-1",
                "data": {"conversation_type": "single", "message_id": message_id},
            })
            recalled = await sender.receive_json_from()
            self.assertEqual(recalled["event"], "message.recalled")
            self.assertEqual(recalled["data"]["message_id"], message_id)
            status = await database_sync_to_async(
                lambda: EncryptedMessage.objects.get(pk=message_id).status
            )()
            self.assertEqual(status, EncryptedMessage.Status.RECALLED)
            await sender.disconnect()

        async_to_sync(run)()

    def test_rcl_02_second_recall_reports_already_recalled(self):
        async def run():
            sender = await self.connect(self.alice)
            message_id = await self.send_message(sender)
            payload = {"event": "message.recall", "request_id": "wsrc-2a",
                       "data": {"conversation_type": "single", "message_id": message_id}}
            await sender.send_json_to(payload)
            await sender.receive_json_from()
            payload["request_id"] = "wsrc-2b"
            await sender.send_json_to(payload)
            error = await sender.receive_json_from()
            self.assertEqual(error["event"], "error")
            self.assertEqual(error["data"]["code"], "already_recalled")
            await sender.disconnect()

        async_to_sync(run)()

    def test_rcl_03_recall_after_the_window_reports_recall_timeout(self):
        async def run():
            sender = await self.connect(self.alice)
            message_id = await self.send_message(sender)
            stale = await database_sync_to_async(self._make_message_stale)(message_id)
            self.assertIsNotNone(stale)
            await sender.send_json_to({
                "event": "message.recall", "request_id": "wsrc-3",
                "data": {"conversation_type": "single", "message_id": message_id},
            })
            error = await sender.receive_json_from()
            self.assertEqual(error["data"]["code"], "recall_timeout")
            await sender.disconnect()

        async_to_sync(run)()

    def _make_message_stale(self, message_id):
        from django.utils import timezone as dj_timezone

        return EncryptedMessage.objects.filter(pk=message_id).update(
            created_at=dj_timezone.now() - dj_timezone.timedelta(minutes=31)
        )

    def test_rcl_04_non_sender_cannot_recall(self):
        async def run():
            sender = await self.connect(self.alice)
            message_id = await self.send_message(sender)
            receiver = await self.connect(self.bob)
            await receiver.send_json_to({
                "event": "message.recall", "request_id": "wsrc-4",
                "data": {"conversation_type": "single", "message_id": message_id},
            })
            error = await receiver.receive_json_from()
            self.assertEqual(error["data"]["code"], "message_not_found")
            status = await database_sync_to_async(
                lambda: EncryptedMessage.objects.get(pk=message_id).status
            )()
            self.assertEqual(status, EncryptedMessage.Status.SENT)
            await sender.disconnect()
            await receiver.disconnect()

        async_to_sync(run)()


class WsTypingTests(MessagingWsBase):
    """WS-TYP-*"""

    def test_typ_01_typing_acks_and_reaches_the_peer(self):
        async def run():
            sender = await self.connect(self.alice)
            receiver = await self.connect(self.bob)

            await sender.send_json_to({
                "event": "typing.start", "request_id": "typ-1a",
                "data": {"conversation_id": self.conv.pk},
            })
            indicator = await receiver.receive_json_from()
            self.assertEqual(indicator["event"], "typing")
            self.assertEqual(indicator["data"]["user_id"], self.alice.pk)
            self.assertEqual(indicator["data"]["action"], "typing")
            ack = await sender.receive_json_from()
            self.assertEqual(ack["event"], "typing.start.ack")

            await sender.send_json_to({
                "event": "typing.stop", "request_id": "typ-1b",
                "data": {"conversation_id": self.conv.pk},
            })
            stop = await receiver.receive_json_from()
            self.assertEqual(stop["data"]["action"], "stop")
            ack2 = await sender.receive_json_from()
            self.assertEqual(ack2["event"], "typing.stop.ack")

            await sender.disconnect()
            await receiver.disconnect()

        async_to_sync(run)()

    def test_typ_02_typing_without_conversation_id_is_invalid_payload(self):
        async def run():
            comm = await self.connect(self.alice)
            await comm.send_json_to({"event": "typing.start", "request_id": "typ-2", "data": {}})
            error = await comm.receive_json_from()
            self.assertEqual(error["data"]["code"], "invalid_payload")
            await comm.disconnect()

        async_to_sync(run)()

    def test_typ_03_typing_in_a_foreign_conversation_is_forbidden(self):
        async def run():
            comm = await self.connect(self.carol)
            await comm.send_json_to({
                "event": "typing.start", "request_id": "typ-3",
                "data": {"conversation_id": self.conv.pk},
            })
            error = await comm.receive_json_from()
            self.assertEqual(error["data"]["code"], "conversation_forbidden")
            await comm.disconnect()

        async_to_sync(run)()


class WsGroupRecallTests(MessagingWsBase):
    """WS-GOPS-* — `message.recall` with conversation_type=group."""

    def setUp(self):
        super().setUp()
        member_ids = [self.alice.pk, self.bob.pk]
        self.payload = _group_payload(
            self.group.pk, member_ids, "wsg-1", membership_version=1,
        )
        Conversation.objects.filter(pk=self.group.pk).update(membership_version=1)

    async def send_group_message(self, comm):
        await comm.send_json_to({
            "event": "message.group.send", "request_id": "wsg-send", "data": self.payload,
        })
        accepted = await comm.receive_json_from()
        self.assertEqual(accepted["event"], "message.group.accepted")
        # The sender is also an active member, so it receives its own
        # `message.group.new` push; drain it before asserting on the recall.
        pushed = await comm.receive_json_from()
        self.assertEqual(pushed["event"], "message.group.new")
        return accepted["data"]["message_id"]

    def test_gops_ws_01_recall_group_message_over_websocket(self):
        async def run():
            comm = await self.connect(self.alice)
            message_id = await self.send_group_message(comm)
            await comm.send_json_to({
                "event": "message.recall", "request_id": "wsg-1",
                "data": {"conversation_type": "group", "message_id": message_id},
            })
            recalled = await comm.receive_json_from()
            self.assertEqual(recalled["event"], "message.recalled")
            self.assertEqual(recalled["data"]["conversation_type"], "group")
            self.assertEqual(recalled["data"]["message_id"], message_id)
            statuses = await database_sync_to_async(
                lambda: set(
                    GroupMessageRecipient.objects.filter(group_message_id=message_id)
                    .values_list("status", flat=True)
                )
            )()
            self.assertEqual(statuses, {GroupMessageRecipient.Status.RECALLED})
            await comm.disconnect()

        async_to_sync(run)()

    def test_gops_ws_02_group_recall_by_non_sender_reports_not_found(self):
        async def run():
            sender = await self.connect(self.alice)
            message_id = await self.send_group_message(sender)
            other = await self.connect(self.bob)
            await other.send_json_to({
                "event": "message.recall", "request_id": "wsg-2",
                "data": {"conversation_type": "group", "message_id": message_id},
            })
            error = await other.receive_json_from()
            self.assertEqual(error["event"], "error")
            self.assertEqual(error["data"]["code"], "message_not_found")
            await sender.disconnect()
            await other.disconnect()

        async_to_sync(run)()

    def test_gops_ws_03_group_recall_reaches_other_members(self):
        async def run():
            sender = await self.connect(self.alice)
            receiver = await self.connect(self.bob)
            message_id = await self.send_group_message(sender)
            # bob already consumed its `message.group.new` inside the helper.

            await sender.send_json_to({
                "event": "message.recall", "request_id": "wsg-3",
                "data": {"conversation_type": "group", "message_id": message_id},
            })
            ack = await sender.receive_json_from()
            self.assertEqual(ack["event"], "message.recalled")

            # bob was connected before the send, so its own new-message push
            # queues ahead of the recall frame; drain it first.
            new_msg = await receiver.receive_json_from()
            self.assertEqual(new_msg["event"], "message.group.new")
            self.assertEqual(new_msg["data"]["message_id"], message_id)

            pushed = await receiver.receive_json_from()
            self.assertEqual(pushed["event"], "message.recalled")
            self.assertEqual(pushed["data"]["conversation_type"], "group")
            self.assertEqual(pushed["data"]["message_id"], message_id)
            await sender.disconnect()
            await receiver.disconnect()

        async_to_sync(run)()


# ── File API (HTTP) ──────────────────────────────────────────────────────
