"""P4 T32 review-round remediation tests.

Covers the four gaps found in the isolated re-review:
1. concurrent same-ID across DIFFERENT conversations hits the IntegrityError
   recovery branch (savepoint) without TransactionManagementError;
2. file-message sends share the service idempotency (retry = 200 replay);
3. forwards through the service conflict (409) on foreign client_message_id;
4. deactivated receivers are rejected identically on HTTP and WS;
5. extra client fields inside sender_copy do not change the request digest.
"""

import base64
import json
import threading
import uuid

from asgiref.sync import async_to_sync
from channels.db import database_sync_to_async
from channels.testing import WebsocketCommunicator
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import connection
from django.test import Client, TestCase, TransactionTestCase
from unittest import skipUnless

from chat.models import (
    Conversation,
    ConversationMember,
    EncryptedFile,
    EncryptedFileKey,
    EncryptedMessage,
)
from chat.services import messaging
from chat.errors import PayloadError
from ichat_pro.asgi import application

B64_12 = "AAAAAAAAAAAAAAAA"
B64_16 = "AAAAAAAAAAAAAAAAAAAAAA=="
CT = "Y2lwaGVydGV4dA=="


def _b64(text):
    return base64.b64encode(text.encode()).decode()


def _private_payload(conversation_id, receiver_id, client_message_id, ciphertext=CT, sender_copy=None):
    payload = {
        "conversation_id": conversation_id,
        "receiver_id": receiver_id,
        "client_message_id": client_message_id,
        "message_type": "text",
        "algorithm": "AES-256-GCM",
        "sender_key_version": 1,
        "receiver_key_version": 1,
        "ciphertext": ciphertext,
        "nonce": B64_12,
        "auth_tag": B64_16,
    }
    if sender_copy is not None:
        payload["sender_copy"] = sender_copy
    return payload


class ReviewFixtures(TestCase):
    @classmethod
    def setUpTestData(cls):
        users = [get_user_model().objects.create_user(f"rev{i}", password="pass1234") for i in range(3)]
        cls.alice, cls.bob, cls.carol = users
        cls.conv_ab = Conversation.objects.create(type=Conversation.Type.SINGLE, created_by=cls.alice)
        ConversationMember.objects.create(conversation=cls.conv_ab, user=cls.alice)
        ConversationMember.objects.create(conversation=cls.conv_ab, user=cls.bob)
        cls.conv_ac = Conversation.objects.create(type=Conversation.Type.SINGLE, created_by=cls.alice)
        ConversationMember.objects.create(conversation=cls.conv_ac, user=cls.alice)
        ConversationMember.objects.create(conversation=cls.conv_ac, user=cls.carol)

    def _client(self, user):
        client = Client()
        client.force_login(user)
        return client

    def _post_private(self, conversation_id, payload):
        return self._client(self.alice).post(
            f"/api/conversations/{conversation_id}/messages/send/",
            data=json.dumps(payload),
            content_type="application/json",
        )

    def _create_file(self, client_file_id):
        return EncryptedFile.objects.create(
            upload_id=str(uuid.uuid4()),
            client_file_id=client_file_id,
            owner=self.alice,
            conversation=self.conv_ab,
            message_kind=EncryptedFile.MessageKind.FILE,
            status=EncryptedFile.Status.AVAILABLE,
            total_size_bytes=32,
            chunk_count=1,
        )


class ConcurrentConflictAcrossConversationsTests(TransactionTestCase):
    """The IntegrityError recovery branch: two DIFFERENT conversations have
    independent row locks, so both threads can reach INSERT with the same
    (sender, client_message_id); the loser must get a clean 409 conflict —
    not a TransactionManagementError from querying inside a failed tx."""

    @skipUnless(connection.vendor == "postgresql", "requires PostgreSQL concurrency")
    def test_concurrent_conflicting_id_across_conversations(self):
        users = [get_user_model().objects.create_user(f"revcc{i}", password="pass1234") for i in range(3)]
        alice, bob, carol = users
        conv_ab = Conversation.objects.create(type=Conversation.Type.SINGLE, created_by=alice)
        ConversationMember.objects.create(conversation=conv_ab, user=alice)
        ConversationMember.objects.create(conversation=conv_ab, user=bob)
        conv_ac = Conversation.objects.create(type=Conversation.Type.SINGLE, created_by=alice)
        ConversationMember.objects.create(conversation=conv_ac, user=alice)
        ConversationMember.objects.create(conversation=conv_ac, user=carol)

        barrier = threading.Barrier(2)
        outcomes = []

        def worker(conv, receiver, ciphertext):
            payload = _private_payload(conv.pk, receiver.pk, "shared-id", ciphertext=ciphertext)
            try:
                barrier.wait()
                outcomes.append(("ok", messaging.send_private_message(alice.pk, payload)))
            except PayloadError as error:
                outcomes.append(("error", error))
            finally:
                connection.close()

        threads = [
            threading.Thread(target=worker, args=(conv_ab, bob, CT)),
            threading.Thread(target=worker, args=(conv_ac, carol, _b64("to-carol"))),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        kinds = sorted(kind for kind, _ in outcomes)
        self.assertEqual(kinds, ["error", "ok"])
        created = [v for kind, v in outcomes if kind == "ok"]
        failed = [v for kind, v in outcomes if kind == "error"]
        self.assertTrue(created[0].created)
        self.assertEqual(failed[0].code, "idempotency_conflict")
        self.assertEqual(EncryptedMessage.objects.count(), 1)


class FileMessageServiceRoutingTests(ReviewFixtures):
    def test_file_message_same_id_retry_replays(self):
        ef = self._create_file("rev-file-1")
        body = {
            "conversation_id": self.conv_ab.pk,
            "conversation_type": "single",
            "receiver_id": self.bob.pk,
            "client_message_id": "rev-file-msg-1",
            "message_type": "file",
            "ciphertext": CT,
            "nonce": B64_12,
            "auth_tag": B64_16,
            "algorithm": "AES-256-GCM",
            "sender_key_version": 1,
            "receiver_key_version": 1,
            "file_keys": [
                {"holder_id": self.alice.pk, "encrypted_file_key": "k-a", "nonce": B64_12, "auth_tag": B64_16},
                {"holder_id": self.bob.pk, "encrypted_file_key": "k-b", "nonce": B64_12, "auth_tag": B64_16},
            ],
        }
        url = f"/api/files/{ef.pk}/messages/"
        first = self._client(self.alice).post(url, data=json.dumps(body), content_type="application/json")
        self.assertEqual(first.status_code, 201, first.content)
        self.assertTrue(first.json()["created"])

        second = self._client(self.alice).post(url, data=json.dumps(body), content_type="application/json")
        self.assertEqual(second.status_code, 200, second.content)
        self.assertFalse(second.json()["created"])
        self.assertEqual(second.json()["message_id"], first.json()["message_id"])
        self.assertEqual(EncryptedMessage.objects.filter(client_message_id="rev-file-msg-1").count(), 1)


class ForwardServiceRoutingTests(ReviewFixtures):
    def _forward(self, target_conversation, peer_id, client_message_id):
        body = {
            "peer_id": peer_id,
            "client_message_id": client_message_id,
            "message_type": "text",
            "algorithm": "AES-256-GCM",
            "sender_key_version": 1,
            "receiver_key_version": 1,
            "ciphertext": CT,
            "nonce": B64_12,
            "auth_tag": B64_16,
        }
        return self._client(self.alice).post(
            f"/api/conversations/{target_conversation.pk}/messages/forward/",
            data=json.dumps(body),
            content_type="application/json",
        )

    def test_forward_with_foreign_client_message_id_conflicts(self):
        # The ID already belongs to a message in conv_ab.
        original = self._post_private(self.conv_ab.pk, _private_payload(
            self.conv_ab.pk, self.bob.pk, "fwd-used-id"))
        self.assertEqual(original.status_code, 201)

        response = self._forward(self.conv_ac, self.carol.pk, "fwd-used-id")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"], "idempotency_conflict")
        self.assertEqual(EncryptedMessage.objects.count(), 1)

    def test_forward_replay_returns_created_false(self):
        first = self._forward(self.conv_ac, self.carol.pk, "fwd-fresh-id")
        self.assertEqual(first.status_code, 201)
        self.assertTrue(first.json()["created"])

        second = self._forward(self.conv_ac, self.carol.pk, "fwd-fresh-id")
        self.assertEqual(second.status_code, 200)
        self.assertFalse(second.json()["created"])
        self.assertEqual(second.json()["message_id"], first.json()["message_id"])
        self.assertEqual(EncryptedMessage.objects.count(), 1)


class DeactivatedReceiverTests(TransactionTestCase):
    def setUp(self):
        cache.clear()
        users = [get_user_model().objects.create_user(f"revdeact{i}", password="pass1234") for i in range(2)]
        self.alice, self.bob = users
        self.conv = Conversation.objects.create(type=Conversation.Type.SINGLE, created_by=self.alice)
        ConversationMember.objects.create(conversation=self.conv, user=self.alice)
        ConversationMember.objects.create(conversation=self.conv, user=self.bob)

    def _payload(self):
        return _private_payload(self.conv.pk, self.bob.pk, "deact-1")

    def test_http_rejects_deactivated_receiver(self):
        self.bob.is_active = False
        self.bob.save(update_fields=["is_active"])
        client = Client()
        client.force_login(self.alice)
        response = client.post(
            f"/api/conversations/{self.conv.pk}/messages/send/",
            data=json.dumps(self._payload()),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["error"], "receiver_not_found")
        self.assertFalse(EncryptedMessage.objects.exists())

        self.bob.is_active = True
        self.bob.save(update_fields=["is_active"])
        response = client.post(
            f"/api/conversations/{self.conv.pk}/messages/send/",
            data=json.dumps(self._payload()),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 201)

    def test_ws_rejects_deactivated_receiver(self):
        self.bob.is_active = False
        self.bob.save(update_fields=["is_active"])

        client = Client()
        client.force_login(self.alice)
        from django.conf import settings as dj_settings

        session_id = client.cookies[dj_settings.SESSION_COOKIE_NAME].value
        headers = [
            (b"origin", b"http://testserver"),
            (b"cookie", f"{dj_settings.SESSION_COOKIE_NAME}={session_id}".encode()),
        ]

        async def run():
            comm = WebsocketCommunicator(application, "/ws/chat/", headers=headers)
            connected, _ = await comm.connect()
            self.assertTrue(connected)
            await comm.receive_json_from()  # connection.ready
            await comm.send_json_to({
                "event": "message.single.send", "request_id": "w1",
                "data": self._payload(),
            })
            error = await comm.receive_json_from()
            self.assertEqual(error["event"], "error")
            self.assertEqual(error["data"]["code"], "receiver_not_found")
            await comm.disconnect()

        async_to_sync(run)()
        self.assertFalse(EncryptedMessage.objects.exists())

        @database_sync_to_async
        def reactivate():
            self.bob.is_active = True
            self.bob.save(update_fields=["is_active"])

        async_to_sync(reactivate)()


class SenderCopyDigestTests(ReviewFixtures):
    def test_extra_fields_in_sender_copy_do_not_break_replay(self):
        client = Client()
        client.force_login(self.alice)
        payload = _private_payload(
            self.conv_ab.pk, self.bob.pk, "sc-extra-1",
            sender_copy={
                "ciphertext": _b64("sender-copy"),
                "nonce": B64_12,
                "auth_tag": B64_16,
                "sender_ephemeral_public_key": _b64("ephemeral-key"),
                # clients may echo extra metadata inside sender_copy; the
                # digest must only consider the persisted fields.
                "algorithm": "AES-256-GCM",
            },
        )
        first = client.post(
            f"/api/conversations/{self.conv_ab.pk}/messages/send/",
            data=json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(first.status_code, 201)

        second = client.post(
            f"/api/conversations/{self.conv_ab.pk}/messages/send/",
            data=json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(second.status_code, 200)
        self.assertFalse(second.json()["created"])
        self.assertEqual(EncryptedMessage.objects.count(), 1)


class FileKeyWriteTimingTests(ReviewFixtures):
    """Review round 2: wrapped keys are written on the CREATED path only."""

    def _file_body(self, ef, client_message_id):
        return {
            "conversation_id": self.conv_ab.pk,
            "conversation_type": "single",
            "receiver_id": self.bob.pk,
            "client_message_id": client_message_id,
            "message_type": "file",
            "ciphertext": CT,
            "nonce": B64_12,
            "auth_tag": B64_16,
            "algorithm": "AES-256-GCM",
            "sender_key_version": 1,
            "receiver_key_version": 1,
            "file_keys": [
                {"holder_id": self.alice.pk, "encrypted_file_key": "k-a", "nonce": B64_12, "auth_tag": B64_16},
                {"holder_id": self.bob.pk, "encrypted_file_key": "k-b", "nonce": B64_12, "auth_tag": B64_16},
            ],
        }

    def test_rejected_forward_does_not_write_file_keys(self):
        # carol must NOT gain access when the forward is rejected (409).
        original = self._post_private(self.conv_ab.pk, _private_payload(
            self.conv_ab.pk, self.bob.pk, "fk-timing-used"))
        self.assertEqual(original.status_code, 201)

        ef = self._create_file("rev-file-fwd-timing")
        body = {
            "peer_id": self.carol.pk,
            "client_message_id": "fk-timing-used",  # foreign id -> 409
            "message_type": "file",
            "file_id": ef.pk,
            "ciphertext": CT,
            "nonce": B64_12,
            "auth_tag": B64_16,
            "algorithm": "AES-256-GCM",
            "sender_key_version": 1,
            "receiver_key_version": 1,
            "file_keys": [
                {"holder_id": self.alice.pk, "encrypted_file_key": "k-a", "nonce": B64_12, "auth_tag": B64_16},
                {"holder_id": self.carol.pk, "encrypted_file_key": "k-carol", "nonce": B64_12, "auth_tag": B64_16},
            ],
        }
        response = self._client(self.alice).post(
            f"/api/conversations/{self.conv_ac.pk}/messages/forward/",
            data=json.dumps(body),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 409)
        self.assertFalse(
            EncryptedFileKey.objects.filter(file=ef, holder_id=self.carol.pk).exists()
        )
        self.assertEqual(
            EncryptedMessage.objects.filter(client_message_id="fk-timing-used").count(), 1
        )

    def test_replay_does_not_overwrite_existing_keys(self):
        ef = self._create_file("rev-file-replay-keys")
        url = f"/api/files/{ef.pk}/messages/"
        body = self._file_body(ef, "fk-replay-1")
        first = self._client(self.alice).post(url, data=json.dumps(body), content_type="application/json")
        self.assertEqual(first.status_code, 201)
        keys_before = list(
            EncryptedFileKey.objects.filter(file=ef).order_by("holder_id").values_list(
                "holder_id", "encrypted_file_key"
            )
        )

        tampered = self._file_body(ef, "fk-replay-1")
        for fk in tampered["file_keys"]:
            fk["encrypted_file_key"] = "overwritten-" + fk["encrypted_file_key"]
        second = self._client(self.alice).post(url, data=json.dumps(tampered), content_type="application/json")
        self.assertEqual(second.status_code, 200)
        self.assertFalse(second.json()["created"])
        keys_after = list(
            EncryptedFileKey.objects.filter(file=ef).order_by("holder_id").values_list(
                "holder_id", "encrypted_file_key"
            )
        )
        self.assertEqual(keys_after, keys_before)


class GroupFileReplayTests(ReviewFixtures):
    """Review round 2, gap 2: group file sends/forwards replay after a
    membership change just like text messages."""

    def setUp(self):
        super().setUp()
        self.group = Conversation.objects.create(
            type=Conversation.Type.GROUP, name="rev2-group", created_by=self.alice, membership_version=1,
        )
        for user in (self.alice, self.bob, self.carol):
            ConversationMember.objects.create(conversation=self.group, user=user)
        self.ef = self._create_file("rev-file-group")
        self.ef.conversation = self.group
        self.ef.save(update_fields=["conversation"])

    def _group_file_body(self, client_message_id):
        return {
            "conversation_id": self.group.pk,
            "conversation_type": "group",
            "client_message_id": client_message_id,
            "message_type": "file",
            "membership_version": 1,
            "ciphertext": CT,
            "nonce": B64_12,
            "auth_tag": B64_16,
            "algorithm": "AES-256-GCM",
            "sender_key_version": 1,
            "recipients": [
                {"receiver_id": self.alice.pk, "receiver_key_version": 1,
                 "ciphertext": _b64("ct-a"), "nonce": B64_12, "auth_tag": B64_16},
                {"receiver_id": self.bob.pk, "receiver_key_version": 1,
                 "ciphertext": _b64("ct-b"), "nonce": B64_12, "auth_tag": B64_16},
                {"receiver_id": self.carol.pk, "receiver_key_version": 1,
                 "ciphertext": _b64("ct-c"), "nonce": B64_12, "auth_tag": B64_16},
            ],
            "file_keys": [
                {"holder_id": self.alice.pk, "encrypted_file_key": "k-a", "nonce": B64_12, "auth_tag": B64_16},
                {"holder_id": self.bob.pk, "encrypted_file_key": "k-b", "nonce": B64_12, "auth_tag": B64_16},
                {"holder_id": self.carol.pk, "encrypted_file_key": "k-c", "nonce": B64_12, "auth_tag": B64_16},
            ],
        }

    def _bump_membership(self):
        dave = get_user_model().objects.create_user("rev2dave", password="pass1234")
        ConversationMember.objects.create(conversation=self.group, user=dave)
        self.group.membership_version = 2
        self.group.save(update_fields=["membership_version", "updated_at"])

    def test_group_file_send_replays_after_membership_change(self):
        url = f"/api/files/{self.ef.pk}/messages/"
        client = self._client(self.alice)
        body = self._group_file_body("gf-replay-1")
        first = client.post(url, data=json.dumps(body), content_type="application/json")
        self.assertEqual(first.status_code, 201, first.content)

        self._bump_membership()
        retry = client.post(url, data=json.dumps(body), content_type="application/json")
        self.assertEqual(retry.status_code, 200, retry.content)
        self.assertFalse(retry.json()["created"])
        self.assertEqual(retry.json()["message_id"], first.json()["message_id"])

    def test_group_file_forward_replays_after_membership_change(self):
        body = {
            "client_message_id": "gff-replay-1",
            "membership_version": 1,
            "message_type": "file",
            "file_id": self.ef.pk,
            "ciphertext": CT,
            "nonce": B64_12,
            "auth_tag": B64_16,
            "algorithm": "AES-256-GCM",
            "sender_key_version": 1,
            "recipients": [
                {"receiver_id": self.alice.pk, "receiver_key_version": 1,
                 "ciphertext": _b64("ct-a"), "nonce": B64_12, "auth_tag": B64_16},
                {"receiver_id": self.bob.pk, "receiver_key_version": 1,
                 "ciphertext": _b64("ct-b"), "nonce": B64_12, "auth_tag": B64_16},
                {"receiver_id": self.carol.pk, "receiver_key_version": 1,
                 "ciphertext": _b64("ct-c"), "nonce": B64_12, "auth_tag": B64_16},
            ],
            "file_keys": [
                {"holder_id": self.alice.pk, "encrypted_file_key": "k-a", "nonce": B64_12, "auth_tag": B64_16},
                {"holder_id": self.bob.pk, "encrypted_file_key": "k-b", "nonce": B64_12, "auth_tag": B64_16},
                {"holder_id": self.carol.pk, "encrypted_file_key": "k-c", "nonce": B64_12, "auth_tag": B64_16},
            ],
        }
        client = self._client(self.alice)
        first = client.post(
            f"/api/conversations/{self.group.pk}/messages/forward/",
            data=json.dumps(body),
            content_type="application/json",
        )
        self.assertEqual(first.status_code, 201, first.content)

        self._bump_membership()
        retry = client.post(
            f"/api/conversations/{self.group.pk}/messages/forward/",
            data=json.dumps(body),
            content_type="application/json",
        )
        self.assertEqual(retry.status_code, 200, retry.content)
        self.assertFalse(retry.json()["created"])


class GroupSenderCopyDigestTests(ReviewFixtures):
    def test_group_sender_copy_extra_fields_do_not_break_replay(self):
        group = Conversation.objects.create(
            type=Conversation.Type.GROUP, name="rev2-sc", created_by=self.alice, membership_version=1,
        )
        for user in (self.alice, self.bob):
            ConversationMember.objects.create(conversation=group, user=user)
        client = self._client(self.alice)
        payload = {
            "client_message_id": "g-sc-1",
            "group_id": group.pk,
            "membership_version": 1,
            "message_type": "text",
            "algorithm": "AES-256-GCM",
            "sender_key_version": 1,
            "recipients": [
                {"receiver_id": self.alice.pk, "receiver_key_version": 1,
                 "ciphertext": _b64("ct-a"), "nonce": B64_12, "auth_tag": B64_16},
                {"receiver_id": self.bob.pk, "receiver_key_version": 1,
                 "ciphertext": _b64("ct-b"), "nonce": B64_12, "auth_tag": B64_16},
            ],
            "sender_copy": {
                "ciphertext": _b64("sender-copy"),
                "nonce": B64_12,
                "auth_tag": B64_16,
                "sender_ephemeral_public_key": _b64("ephemeral"),
                "algorithm": "AES-256-GCM",  # echoed extra field
            },
        }
        url = f"/api/conversations/{group.pk}/messages/send-group/"
        first = client.post(url, data=json.dumps(payload), content_type="application/json")
        self.assertEqual(first.status_code, 201, first.content)

        second = client.post(url, data=json.dumps(payload), content_type="application/json")
        self.assertEqual(second.status_code, 200, second.content)
        self.assertFalse(second.json()["created"])


class ForwardMethodGuardTests(ReviewFixtures):
    """Review round 3: the positional helper deletion silently dropped the
    forward view decorators - a GET with a JSON body used to create messages."""

    def test_forward_rejects_get_with_body(self):
        client = self._client(self.alice)
        body = {
            "peer_id": self.bob.pk,
            "client_message_id": "get-forward-1",
            "message_type": "text",
            "algorithm": "AES-256-GCM",
            "sender_key_version": 1,
            "receiver_key_version": 1,
            "ciphertext": CT,
            "nonce": B64_12,
            "auth_tag": B64_16,
        }
        response = client.get(
            f"/api/conversations/{self.conv_ab.pk}/messages/forward/",
            data=body,
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 405)
        self.assertFalse(EncryptedMessage.objects.filter(client_message_id="get-forward-1").exists())


class StrictFileKeyValidationTests(ReviewFixtures):
    """Review round 3: key material is validated strictly before anything is
    written - holder-only entries used to overwrite existing wrapped keys."""

    def _post_file(self, ef, file_keys):
        client = self._client(self.alice)
        body = {
            "conversation_id": self.conv_ab.pk,
            "conversation_type": "single",
            "receiver_id": self.bob.pk,
            "client_message_id": "strict-keys-1",
            "message_type": "file",
            "ciphertext": CT,
            "nonce": B64_12,
            "auth_tag": B64_16,
            "algorithm": "AES-256-GCM",
            "sender_key_version": 1,
            "receiver_key_version": 1,
            "file_keys": file_keys,
        }
        return client.post(f"/api/files/{ef.pk}/messages/", data=json.dumps(body), content_type="application/json")

    def _valid_keys(self):
        return [
            {"holder_id": self.alice.pk, "encrypted_file_key": "k-a", "nonce": B64_12, "auth_tag": B64_16},
            {"holder_id": self.bob.pk, "encrypted_file_key": "k-b", "nonce": B64_12, "auth_tag": B64_16},
        ]

    def test_missing_key_material_is_rejected(self):
        ef = self._create_file("strict-keys-missing")
        keys = self._valid_keys()
        keys[1] = {"holder_id": self.bob.pk}  # holder-only entry
        response = self._post_file(ef, keys)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "invalid_file_metadata")
        self.assertFalse(EncryptedMessage.objects.filter(client_message_id="strict-keys-1").exists())
        # nothing was written, let alone existing key material cleared
        self.assertFalse(EncryptedFileKey.objects.filter(file=ef).exists())

    def test_unsupported_key_algorithm_is_rejected(self):
        ef = self._create_file("strict-keys-alg")
        keys = self._valid_keys()
        keys[1]["algorithm"] = "RSA-OAEP-256"
        response = self._post_file(ef, keys)
        self.assertEqual(response.status_code, 400)
        self.assertFalse(EncryptedFileKey.objects.filter(file=ef).exists())

    def test_malformed_version_fields_are_rejected(self):
        ef = self._create_file("strict-keys-ver")
        keys = self._valid_keys()
        keys[1]["receiver_key_version"] = "abc"
        response = self._post_file(ef, keys)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "invalid_file_metadata")
        self.assertFalse(EncryptedMessage.objects.filter(client_message_id="strict-keys-1").exists())

    def test_wrong_nonce_length_is_rejected(self):
        ef = self._create_file("strict-keys-nonce")
        keys = self._valid_keys()
        keys[1]["nonce"] = "AAAAAAAA"  # 6 bytes, must be 12
        response = self._post_file(ef, keys)
        self.assertEqual(response.status_code, 400)
        self.assertFalse(EncryptedFileKey.objects.filter(file=ef).exists())

    def test_valid_keys_still_pass(self):
        ef = self._create_file("strict-keys-ok")
        response = self._post_file(ef, self._valid_keys())
        self.assertEqual(response.status_code, 201)
        self.assertTrue(
            EncryptedFileKey.objects.filter(file=ef, holder_id=self.bob.pk, encrypted_file_key="k-b").exists()
        )
