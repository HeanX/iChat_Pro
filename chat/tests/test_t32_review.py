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

from chat.models import Conversation, ConversationMember, EncryptedFile, EncryptedMessage
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
