"""P4 T32/T36 tests: idempotency semantics of the unified messaging service.

Covers the critical scenarios from technical-design section 11:
same-ID replays (HTTP+WS cross-channel), content conflicts (409
idempotency_conflict), group replay after membership change (R-02),
no double unread, no duplicate broadcast (R-04), and concurrent same-ID
inserts (PostgreSQL-only, exercised by the CI integration job).
"""

import base64
import json
from unittest import skipUnless

from asgiref.sync import async_to_sync
from channels.db import database_sync_to_async
from channels.testing import WebsocketCommunicator
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import connection
from django.test import Client, TestCase, TransactionTestCase

from chat.models import Conversation, ConversationMember, GroupMessageRecipient
from ichat_pro.asgi import application

B64_12 = "AAAAAAAAAAAAAAAA"  # 12 bytes
B64_16 = "AAAAAAAAAAAAAAAAAAAAAA=="  # 16 bytes
CT = "Y2lwaGVydGV4dA=="


def _b64(text):
    return base64.b64encode(text.encode()).decode()


class IdempotencyTestBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        users = [get_user_model().objects.create_user(f"proto{i}", password="pass1234") for i in range(3)]
        cls.alice, cls.bob, cls.carol = users
        cls.conv_ab = Conversation.objects.create(type=Conversation.Type.SINGLE, created_by=cls.alice)
        ConversationMember.objects.create(conversation=cls.conv_ab, user=cls.alice)
        ConversationMember.objects.create(conversation=cls.conv_ab, user=cls.bob)
        cls.conv_ac = Conversation.objects.create(type=Conversation.Type.SINGLE, created_by=cls.alice)
        ConversationMember.objects.create(conversation=cls.conv_ac, user=cls.alice)
        ConversationMember.objects.create(conversation=cls.conv_ac, user=cls.carol)

    def _private_payload(self, conversation_id=None, receiver_id=None, client_message_id="c-1", ciphertext=CT):
        return {
            "conversation_id": conversation_id or self.conv_ab.pk,
            "receiver_id": receiver_id or self.bob.pk,
            "client_message_id": client_message_id,
            "message_type": "text",
            "algorithm": "AES-256-GCM",
            "sender_key_version": 1,
            "receiver_key_version": 1,
            "ciphertext": ciphertext,
            "nonce": B64_12,
            "auth_tag": B64_16,
        }

    def _post_private(self, conversation_id, payload):
        client = Client()
        client.force_login(self.alice)
        return client.post(
            f"/api/conversations/{conversation_id}/messages/send/",
            data=json.dumps(payload),
            content_type="application/json",
        )

    def _unread(self, conversation, user):
        return ConversationMember.objects.get(conversation=conversation, user=user).unread_count

    def _message_count(self):
        from chat.models import EncryptedMessage

        return EncryptedMessage.objects.count()


class PrivateHttpIdempotencyTests(IdempotencyTestBase):
    def test_replay_returns_same_message_without_double_unread(self):
        first = self._post_private(self.conv_ab.pk, self._private_payload())
        self.assertEqual(first.status_code, 201)
        body = first.json()
        self.assertTrue(body["created"])
        self.assertEqual(self._unread(self.conv_ab, self.bob), 1)

        second = self._post_private(self.conv_ab.pk, self._private_payload())
        self.assertEqual(second.status_code, 200)
        data = second.json()
        self.assertFalse(data["created"])
        self.assertEqual(data["message_id"], body["message_id"])
        self.assertEqual(self._message_count(), 1)
        self.assertEqual(self._unread(self.conv_ab, self.bob), 1)

    def test_same_id_in_other_conversation_conflicts(self):
        first = self._post_private(self.conv_ab.pk, self._private_payload())
        self.assertEqual(first.status_code, 201)

        # R-01 regression: the same client_message_id used against a DIFFERENT
        # conversation must conflict, never replay or create a second message.
        response = self._post_private(
            self.conv_ac.pk, self._private_payload(conversation_id=self.conv_ac.pk, receiver_id=self.carol.pk)
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"], "idempotency_conflict")
        self.assertEqual(self._message_count(), 1)
        self.assertEqual(self._unread(self.conv_ac, self.carol), 0)

    def test_same_id_with_different_ciphertext_conflicts(self):
        first = self._post_private(self.conv_ab.pk, self._private_payload())
        self.assertEqual(first.status_code, 201)

        tampered = self._private_payload(ciphertext=_b64("tampered-ciphertext"))
        response = self._post_private(self.conv_ab.pk, tampered)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"], "idempotency_conflict")
        self.assertEqual(self._message_count(), 1)

    def test_ws_send_then_http_retry_is_a_replay(self):
        """T36: the two entry points share one idempotency scope."""
        headers = self._ws_headers_client()

        async def run():
            comm = WebsocketCommunicator(application, "/ws/chat/", headers=headers)
            connected, _ = await comm.connect()
            self.assertTrue(connected)
            await comm.receive_json_from()  # connection.ready
            await comm.send_json_to({
                "event": "message.single.send",
                "request_id": "r-ws-1",
                "data": self._private_payload(),
            })
            accepted = await comm.receive_json_from()
            self.assertEqual(accepted["event"], "message.single.accepted")
            self.assertTrue(accepted["data"]["created"])
            await comm.disconnect()
            return accepted["data"]["message_id"]

        message_id = async_to_sync(run)()

        retry = self._post_private(self.conv_ab.pk, self._private_payload())
        self.assertEqual(retry.status_code, 200)
        self.assertEqual(retry.json()["message_id"], message_id)
        self.assertFalse(retry.json()["created"])
        self.assertEqual(self._message_count(), 1)
        self.assertEqual(self._unread(self.conv_ab, self.bob), 1)

    def _ws_headers_client(self):
        client = Client()
        client.force_login(self.alice)
        from django.conf import settings as dj_settings

        session_id = client.cookies[dj_settings.SESSION_COOKIE_NAME].value
        return [
            (b"origin", b"http://testserver"),
            (b"cookie", f"{dj_settings.SESSION_COOKIE_NAME}={session_id}".encode()),
        ]


class PrivateWsReplayTests(TransactionTestCase):
    def setUp(self):
        cache.clear()
        users = [get_user_model().objects.create_user(f"wsreplay{i}", password="pass1234") for i in range(2)]
        self.alice, self.bob = users
        self.conv = Conversation.objects.create(type=Conversation.Type.SINGLE, created_by=self.alice)
        ConversationMember.objects.create(conversation=self.conv, user=self.alice)
        ConversationMember.objects.create(conversation=self.conv, user=self.bob)

    def _headers(self, user):
        client = Client()
        client.force_login(user)
        from django.conf import settings as dj_settings

        session_id = client.cookies[dj_settings.SESSION_COOKIE_NAME].value
        return [
            (b"origin", b"http://testserver"),
            (b"cookie", f"{dj_settings.SESSION_COOKIE_NAME}={session_id}".encode()),
        ]

    def _payload(self, client_message_id):
        return {
            "conversation_id": self.conv.pk,
            "receiver_id": self.bob.pk,
            "client_message_id": client_message_id,
            "message_type": "text",
            "algorithm": "AES-256-GCM",
            "sender_key_version": 1,
            "receiver_key_version": 1,
            "ciphertext": CT,
            "nonce": B64_12,
            "auth_tag": B64_16,
        }

    def test_ws_replay_does_not_re_broadcast(self):
        """R-04: the receiver is pushed a new message exactly once."""
        sender_headers = self._headers(self.alice)
        receiver_headers = self._headers(self.bob)

        async def run():
            sender = WebsocketCommunicator(application, "/ws/chat/", headers=sender_headers)
            receiver = WebsocketCommunicator(application, "/ws/chat/", headers=receiver_headers)
            for comm in (sender, receiver):
                connected, _ = await comm.connect()
                self.assertTrue(connected)
                await comm.receive_json_from()

            await sender.send_json_to({
                "event": "message.single.send", "request_id": "w1",
                "data": self._payload("ws-id-1"),
            })
            accepted = await sender.receive_json_from()
            self.assertTrue(accepted["data"]["created"])
            pushed = await receiver.receive_json_from()
            self.assertEqual(pushed["event"], "message.single.new")

            # ACK-lost retry with the SAME payload: accepted again, but no
            # second push reaches the receiver.
            await sender.send_json_to({
                "event": "message.single.send", "request_id": "w2",
                "data": self._payload("ws-id-1"),
            })
            replay = await sender.receive_json_from()
            self.assertEqual(replay["event"], "message.single.accepted")
            self.assertFalse(replay["data"]["created"])
            # Deterministic negative check: the receiver pings; the FIRST
            # frame back must be the pong - a replayed push would have
            # queued ahead of it.
            await receiver.send_json_to({"event": "connection.ping", "request_id": "probe"})
            probe = await receiver.receive_json_from()
            self.assertEqual(probe["event"], "connection.pong")

            await sender.disconnect()
            await receiver.disconnect()

        async_to_sync(run)()

    def test_ws_same_id_different_content_gets_conflict(self):
        headers = self._headers(self.alice)

        async def run():
            sender = WebsocketCommunicator(application, "/ws/chat/", headers=headers)
            connected, _ = await sender.connect()
            self.assertTrue(connected)
            await sender.receive_json_from()

            await sender.send_json_to({
                "event": "message.single.send", "request_id": "w1",
                "data": self._payload("ws-id-2"),
            })
            await sender.receive_json_from()

            tampered = self._payload("ws-id-2")
            tampered["ciphertext"] = _b64("different-content")
            await sender.send_json_to({
                "event": "message.single.send", "request_id": "w2",
                "data": tampered,
            })
            error = await sender.receive_json_from()
            self.assertEqual(error["event"], "error")
            self.assertEqual(error["data"]["code"], "idempotency_conflict")
            self.assertFalse(error["data"]["retryable"])
            await sender.disconnect()

        async_to_sync(run)()


class GroupIdempotencyTests(IdempotencyTestBase):
    def setUp(self):
        self.group = Conversation.objects.create(
            type=Conversation.Type.GROUP, name="idem-group", created_by=self.alice, membership_version=1,
        )
        for user in (self.alice, self.bob, self.carol):
            ConversationMember.objects.create(conversation=self.group, user=user)

    def _group_payload(self, client_message_id="g-1", membership_version=1):
        return {
            "client_message_id": client_message_id,
            "group_id": self.group.pk,
            "membership_version": membership_version,
            "message_type": "text",
            "algorithm": "AES-256-GCM",
            "sender_key_version": 1,
            "recipients": [
                {"receiver_id": self.alice.pk, "receiver_key_version": 1,
                 "ciphertext": _b64("ct-alice"), "nonce": B64_12, "auth_tag": B64_16},
                {"receiver_id": self.bob.pk, "receiver_key_version": 1,
                 "ciphertext": _b64("ct-bob"), "nonce": B64_12, "auth_tag": B64_16},
                {"receiver_id": self.carol.pk, "receiver_key_version": 1,
                 "ciphertext": _b64("ct-carol"), "nonce": B64_12, "auth_tag": B64_16},
            ],
        }

    def _post_group(self, payload):
        client = Client()
        client.force_login(self.alice)
        return client.post(
            f"/api/conversations/{self.group.pk}/messages/send-group/",
            data=json.dumps(payload),
            content_type="application/json",
        )

    def _recipients(self, message_id):
        return GroupMessageRecipient.objects.filter(group_message_id=message_id)

    def test_group_replay_after_membership_change(self):
        """R-02: an ACK-lost retry replays even after the membership changed."""
        first = self._post_group(self._group_payload())
        self.assertEqual(first.status_code, 201)
        message_id = first.json()["message_id"]

        # Membership evolves: carol invites dave, version bumps.
        dave = get_user_model().objects.create_user("protodave", password="pass1234")
        ConversationMember.objects.create(conversation=self.group, user=dave)
        self.group.membership_version = 2
        self.group.save(update_fields=["membership_version", "updated_at"])

        retry = self._post_group(self._group_payload())
        self.assertEqual(retry.status_code, 200)
        self.assertFalse(retry.json()["created"])
        self.assertEqual(retry.json()["message_id"], message_id)
        # The original distribution is untouched: still the 3 old copies.
        self.assertEqual(self._recipients(message_id).count(), 3)

    def test_group_new_message_with_stale_version_conflicts(self):
        first = self._post_group(self._group_payload())
        self.assertEqual(first.status_code, 201)

        dave = get_user_model().objects.create_user("protodave2", password="pass1234")
        ConversationMember.objects.create(conversation=self.group, user=dave)
        self.group.membership_version = 2
        self.group.save(update_fields=["membership_version", "updated_at"])

        stale = self._post_group(self._group_payload(client_message_id="g-2"))
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(stale.json()["error"], "membership_conflict")

    def test_group_replay_with_different_recipients_conflicts(self):
        first = self._post_group(self._group_payload())
        self.assertEqual(first.status_code, 201)

        payload = self._group_payload()
        payload["recipients"][2]["ciphertext"] = _b64("tampered")
        retry = self._post_group(payload)
        self.assertEqual(retry.status_code, 409)
        self.assertEqual(retry.json()["error"], "idempotency_conflict")

    def test_group_unread_counted_once(self):
        self._post_group(self._group_payload())
        self._post_group(self._group_payload())
        self.assertEqual(self._unread(self.group, self.bob), 1)
        self.assertEqual(self._unread(self.group, self.carol), 1)
        self.assertEqual(self._unread(self.group, self.alice), 0)


class ConcurrentSameIdTests(TransactionTestCase):
    """Concurrent duplicate inserts (R-01 hardening).

    Only meaningful under PostgreSQL's real concurrency; SQLite serializes
    writers, so this runs in the CI integration job. TransactionTestCase is
    required: the worker threads use their own connections and must see the
    seeded rows, which a TestCase-wrapped (uncommitted) transaction hides.
    """

    @skipUnless(connection.vendor == "postgresql", "requires PostgreSQL concurrency")
    def test_concurrent_same_id_creates_one_message(self):
        import threading

        users = [get_user_model().objects.create_user(f"conc{i}", password="pass1234") for i in range(2)]
        alice, bob = users
        conv = Conversation.objects.create(type=Conversation.Type.SINGLE, created_by=alice)
        ConversationMember.objects.create(conversation=conv, user=alice)
        ConversationMember.objects.create(conversation=conv, user=bob)
        payload = {
            "conversation_id": conv.pk,
            "receiver_id": bob.pk,
            "client_message_id": "conc-1",
            "message_type": "text",
            "algorithm": "AES-256-GCM",
            "sender_key_version": 1,
            "receiver_key_version": 1,
            "ciphertext": CT,
            "nonce": B64_12,
            "auth_tag": B64_16,
        }

        from chat.services import messaging

        results = []
        barrier = threading.Barrier(2)
        errors = []

        def worker():
            try:
                barrier.wait()
                results.append(messaging.send_private_message(alice.pk, payload))
            except Exception as error:  # pragma: no cover - surfaced below
                errors.append(error)

        threads = [threading.Thread(target=worker) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [])
        self.assertEqual(len(results), 2)
        created = [r.created for r in results]
        self.assertEqual(sorted(created), [False, True])
        self.assertEqual(results[0].message.pk, results[1].message.pk)

        from chat.models import EncryptedMessage

        self.assertEqual(EncryptedMessage.objects.count(), 1)
        self.assertEqual(
            ConversationMember.objects.get(conversation=conv, user=bob).unread_count, 1,
        )
