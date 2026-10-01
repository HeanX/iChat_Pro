"""P4 T34/T35 tests: durable sync events, cursor paging, and stable ordering."""

import base64
import json
import time
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import Client, TestCase

from chat.models import (
    Conversation,
    ConversationEvent,
    ConversationMember,
    EncryptedFile,
    EncryptedFileKey,
    EncryptedMessage,
    UserMessageDeletion,
)
from chat.services.sync import CURSOR_TTL_SECONDS, make_sync_cursor

B64_12 = "AAAAAAAAAAAAAAAA"
B64_16 = "AAAAAAAAAAAAAAAAAAAAAA=="
CT = "Y2lwaGVydGV4dA=="


def _b64(text):
    return base64.b64encode(text.encode()).decode()


class SyncApiTestBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.alice = get_user_model().objects.create_user("synca", password="pass1234")
        cls.bob = get_user_model().objects.create_user("syncb", password="pass1234")

    def setUp(self):
        self.client_a = Client()
        self.client_a.force_login(self.alice)

    def _post_private(self, conversation_id, receiver_id, client_message_id, ciphertext=CT, expect=201, sender_copy=None):
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
        response = self.client_a.post(
            f"/api/conversations/{conversation_id}/messages/send/",
            data=json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, expect, response.content)
        return response.json()

    def _sync(self, conversation_id, cursor="", limit=None, client=None):
        query = f"?cursor={cursor}" if cursor else ""
        if limit:
            query += f"&limit={limit}" if query else f"?limit={limit}"
        return (client or self.client_a).get(f"/api/conversations/{conversation_id}/sync/{query}")


class SyncEventRecordingTests(SyncApiTestBase):
    def setUp(self):
        super().setUp()
        self.conv = Conversation.objects.create(type=Conversation.Type.SINGLE, created_by=self.alice)
        ConversationMember.objects.create(conversation=self.conv, user=self.alice)
        ConversationMember.objects.create(conversation=self.conv, user=self.bob)

    def test_sequence_is_dense_across_replays(self):
        self._post_private(self.conv.pk, self.bob.pk, "seq-1")
        self._post_private(self.conv.pk, self.bob.pk, "seq-2")
        # ACK-lost replay: accepted but NOT re-sequenced
        self._post_private(self.conv.pk, self.bob.pk, "seq-2", expect=200)

        self.conv.refresh_from_db()
        self.assertEqual(self.conv.sync_sequence, 2)
        sequences = list(
            ConversationEvent.objects.filter(conversation=self.conv)
            .order_by("sequence")
            .values_list("sequence", "kind", "message_id")
        )
        self.assertEqual([s for s, _, _ in sequences], [1, 2])
        self.assertTrue(all(kind == "message" for _, kind, _ in sequences))

    def test_sync_returns_viewer_projected_payloads(self):
        # Real clients send a distinct sender_copy; the sender must get the
        # sender_copy projection while the receiver gets their own copy.
        self._post_private(
            self.conv.pk, self.bob.pk, "view-1",
            sender_copy={
                "ciphertext": _b64("sender-copy"),
                "nonce": B64_12,
                "auth_tag": B64_16,
                "sender_ephemeral_public_key": _b64("ephemeral"),
            },
        )

        sender_view = self._sync(self.conv.pk)
        self.assertEqual(sender_view.status_code, 200)
        data = sender_view.json()
        self.assertIs(data["has_more"], False)
        self.assertEqual(data["high_water"], 1)
        self.assertEqual(len(data["items"]), 1)
        item = data["items"][0]
        self.assertEqual(item["sequence"], 1)
        self.assertEqual(item["kind"], "message")
        self.assertEqual(item["message"]["sender_id"], self.alice.pk)
        self.assertEqual(item["message"]["ciphertext"], _b64("sender-copy"))

        client_b = Client()
        client_b.force_login(self.bob)
        receiver_item = self._sync(self.conv.pk, client=client_b).json()["items"][0]
        # the sender sees the sender_copy projection, the receiver their own
        self.assertEqual(receiver_item["message"]["ciphertext"], CT)

    def test_non_member_cannot_sync(self):
        carol = get_user_model().objects.create_user("synccarol", password="pass1234")
        client = Client()
        client.force_login(carol)
        response = self._sync(self.conv.pk, client=client)
        self.assertEqual(response.status_code, 404)


class SyncCursorPagingTests(SyncApiTestBase):
    def setUp(self):
        super().setUp()
        self.conv = Conversation.objects.create(type=Conversation.Type.SINGLE, created_by=self.alice)
        ConversationMember.objects.create(conversation=self.conv, user=self.alice)
        ConversationMember.objects.create(conversation=self.conv, user=self.bob)
        for i in range(5):
            self._post_private(self.conv.pk, self.bob.pk, f"page-{i}")

    def test_paged_catch_up_walks_the_snapshot(self):
        page1 = self._sync(self.conv.pk, limit=2).json()
        self.assertEqual([i["sequence"] for i in page1["items"]], [1, 2])
        self.assertTrue(page1["has_more"])
        self.assertEqual(page1["high_water"], 5)
        self.assertIsNotNone(page1["next_cursor"])

        page2 = self._sync(self.conv.pk, cursor=page1["next_cursor"], limit=2).json()
        self.assertEqual([i["sequence"] for i in page2["items"]], [3, 4])
        self.assertTrue(page2["has_more"])

        page3 = self._sync(self.conv.pk, cursor=page2["next_cursor"], limit=2).json()
        self.assertEqual([i["sequence"] for i in page3["items"]], [5])
        self.assertFalse(page3["has_more"])
        # The cursor is ALWAYS returned: after the walk completes it binds to
        # the high-water mark so the client's next sync gets only the delta.
        self.assertIsNotNone(page3["next_cursor"])

        # New message after the walk completed: the released-snapshot cursor
        # re-snapshots and returns exactly the delta.
        self._post_private(self.conv.pk, self.bob.pk, "page-5")
        delta = self._sync(self.conv.pk, cursor=page3["next_cursor"], limit=2).json()
        self.assertEqual([i["sequence"] for i in delta["items"]], [6])
        self.assertEqual(delta["high_water"], 6)
        self.assertFalse(delta["has_more"])

    def test_cursor_is_bound_to_conversation_and_user(self):
        page1 = self._sync(self.conv.pk, limit=1).json()
        cursor = page1["next_cursor"]

        # Reuse alice's cursor against a different conversation of hers.
        other = Conversation.objects.create(type=Conversation.Type.SINGLE, created_by=self.alice)
        ConversationMember.objects.create(conversation=other, user=self.alice)
        ConversationMember.objects.create(conversation=other, user=self.bob)
        wrong_conv = self._sync(other.pk, cursor=cursor)
        self.assertEqual(wrong_conv.status_code, 400)
        self.assertEqual(wrong_conv.json()["error"], "sync_cursor_invalid")

        # Tampered signature.
        tampered = cursor.split(".")[0] + ".deadbeef"
        wrong_sig = self._sync(self.conv.pk, cursor=tampered)
        self.assertEqual(wrong_sig.status_code, 400)
        self.assertEqual(wrong_sig.json()["error"], "sync_cursor_invalid")

    def test_expired_cursor_asks_for_resnapshot(self):
        stale_cursor = make_sync_cursor(
            conversation_id=self.conv.pk,
            user_id=self.alice.pk,
            last_sequence=0,
            high_water=5,
        )
        with mock.patch("chat.services.sync.time.time", return_value=time.time() + CURSOR_TTL_SECONDS + 60):
            response = self._sync(self.conv.pk, cursor=stale_cursor)
        self.assertEqual(response.status_code, 410)
        self.assertEqual(response.json()["error"], "sync_cursor_expired")


class GroupSyncTests(SyncApiTestBase):
    def setUp(self):
        super().setUp()
        self.group = Conversation.objects.create(
            type=Conversation.Type.GROUP, name="sync-group", created_by=self.alice, membership_version=1,
        )
        for user in (self.alice, self.bob):
            ConversationMember.objects.create(conversation=self.group, user=user)

    def _post_group(self, client, client_message_id):
        members = list(
            ConversationMember.objects.filter(
                conversation=self.group, status=ConversationMember.Status.ACTIVE,
            ).values_list("user_id", flat=True)
        )
        payload = {
            "client_message_id": client_message_id,
            "group_id": self.group.pk,
            "membership_version": self.group.membership_version,
            "message_type": "text",
            "algorithm": "AES-256-GCM",
            "sender_key_version": 1,
            "recipients": [
                {"receiver_id": uid, "receiver_key_version": 1,
                 "ciphertext": _b64(f"ct-{uid}"), "nonce": B64_12, "auth_tag": B64_16}
                for uid in members
            ],
        }
        response = client.post(
            f"/api/conversations/{self.group.pk}/messages/send-group/",
            data=json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 201, response.content)
        return response.json()

    def test_group_sync_projects_per_viewer_copies(self):
        client_b = Client()
        client_b.force_login(self.bob)
        self._post_group(self.client_a, "gsync-1")

        for client, viewer in ((self.client_a, self.alice), (client_b, self.bob)):
            data = self._sync(self.group.pk, client=client).json()
            self.assertEqual(len(data["items"]), 1)
            message = data["items"][0]["message"]
            self.assertEqual(message["receiver_id"], viewer.pk)
            self.assertTrue(message["ciphertext"])

    def test_new_member_sync_hides_pre_join_events(self):
        self._post_group(self.client_a, "before-join")

        carol = get_user_model().objects.create_user("synccarol2", password="pass1234")
        ConversationMember.objects.create(conversation=self.group, user=carol)
        self.group.membership_version = 2
        self.group.save(update_fields=["membership_version", "updated_at"])

        client_c = Client()
        client_c.force_login(carol)
        data = self._sync(self.group.pk, client=client_c).json()
        self.assertEqual(data["items"], [])

        # Post-join events are visible to the new member.
        self._post_group(self.client_a, "after-join")
        data = self._sync(self.group.pk, client=client_c).json()
        self.assertEqual(len(data["items"]), 1)
        self.assertEqual(data["items"][0]["message"]["sender_id"], self.alice.pk)


class WritePathEventCoverageTests(SyncApiTestBase):
    """Every message write path must land in the durable event log."""

    def setUp(self):
        super().setUp()
        self.group = Conversation.objects.create(
            type=Conversation.Type.GROUP, name="sync-paths", created_by=self.alice, membership_version=1,
        )
        for user in (self.alice, self.bob):
            ConversationMember.objects.create(conversation=self.group, user=user)
        self.private = Conversation.objects.create(type=Conversation.Type.SINGLE, created_by=self.alice)
        ConversationMember.objects.create(conversation=self.private, user=self.alice)
        ConversationMember.objects.create(conversation=self.private, user=self.bob)

    def test_file_and_forward_messages_produce_events(self):
        ef = EncryptedFile.objects.create(
            upload_id="11111111-1111-1111-1111-111111111111",
            client_file_id="sync-file-1",
            owner=self.alice,
            conversation=self.group,
            message_kind=EncryptedFile.MessageKind.FILE,
            status=EncryptedFile.Status.AVAILABLE,
            total_size_bytes=32,
            chunk_count=1,
        )
        for holder in (self.alice, self.bob):
            EncryptedFileKey.objects.create(
                file=ef, holder=holder, sender=self.alice,
                encrypted_file_key="k", nonce=B64_12, auth_tag=B64_16,
                algorithm="AES-256-GCM",
            )
        file_body = {
            "conversation_id": self.group.pk,
            "conversation_type": "group",
            "client_message_id": "sync-file-msg",
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
            ],
            "file_keys": [
                {"holder_id": self.alice.pk, "encrypted_file_key": "k-a", "nonce": B64_12, "auth_tag": B64_16},
                {"holder_id": self.bob.pk, "encrypted_file_key": "k-b", "nonce": B64_12, "auth_tag": B64_16},
            ],
        }
        response = self.client_a.post(
            f"/api/files/{ef.pk}/messages/",
            data=json.dumps(file_body),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 201, response.content)

        # Forward a file message into the private conversation.
        forward_body = {
            "peer_id": self.bob.pk,
            "client_message_id": "sync-fwd-1",
            "message_type": "file",
            "file_id": ef.pk,
            "ciphertext": CT,
            "nonce": B64_12,
            "auth_tag": B64_16,
            "algorithm": "AES-256-GCM",
            "sender_key_version": 1,
            "receiver_key_version": 1,
            "file_keys": [
                {"holder_id": self.alice.pk, "encrypted_file_key": "k-a2", "nonce": B64_12, "auth_tag": B64_16},
                {"holder_id": self.bob.pk, "encrypted_file_key": "k-b2", "nonce": B64_12, "auth_tag": B64_16},
            ],
        }
        forward = self.client_a.post(
            f"/api/conversations/{self.private.pk}/messages/forward/",
            data=json.dumps(forward_body),
            content_type="application/json",
        )
        self.assertEqual(forward.status_code, 201, forward.content)

        self.group.refresh_from_db()
        self.private.refresh_from_db()
        self.assertEqual(self.group.sync_sequence, 1)
        self.assertEqual(self.private.sync_sequence, 1)
        group_data = self._sync(self.group.pk).json()
        private_data = self._sync(self.private.pk).json()
        self.assertEqual(
            [i["message"]["message_type"] for i in group_data["items"]], ["file"]
        )
        self.assertEqual(
            [i["message"]["message_type"] for i in private_data["items"]], ["file"]
        )


class SyncVisibilityParityTests(SyncApiTestBase):
    """Review round on T34: sync must apply the exact same read projections
    as the history endpoints (cleared_at, personal deletion, block rule)."""

    def setUp(self):
        super().setUp()
        self.conv = Conversation.objects.create(type=Conversation.Type.SINGLE, created_by=self.alice)
        ConversationMember.objects.create(conversation=self.conv, user=self.alice)
        ConversationMember.objects.create(conversation=self.conv, user=self.bob)
        for i in range(3):
            self._post_private(self.conv.pk, self.bob.pk, f"vis-{i}")

    def test_sync_hides_personally_deleted_messages(self):
        from chat.models import UserMessageDeletion

        m2 = EncryptedMessage.objects.get(client_message_id="vis-1")
        UserMessageDeletion.objects.create(
            user=self.alice, conversation=self.conv,
            message_type=UserMessageDeletion.MessageType.PRIVATE, message_id=m2.pk,
        )
        data = self._sync(self.conv.pk).json()
        self.assertEqual([i["sequence"] for i in data["items"]], [1, 3])

    def test_sync_respects_cleared_at(self):
        from datetime import timedelta

        member = ConversationMember.objects.get(conversation=self.conv, user=self.alice)
        m2 = EncryptedMessage.objects.get(client_message_id="vis-1")
        member.cleared_at = m2.created_at + timedelta(milliseconds=1)
        member.save(update_fields=["cleared_at"])
        data = self._sync(self.conv.pk).json()
        self.assertEqual([i["sequence"] for i in data["items"]], [3])

    def test_private_sync_blocked_returns_403(self):
        from accounts.models import BlockedUser

        BlockedUser.objects.create(blocker=self.alice, blocked=self.bob)
        response = self._sync(self.conv.pk)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"], "conversation_forbidden")

    def test_group_sync_hides_personally_deleted(self):
        group = Conversation.objects.create(
            type=Conversation.Type.GROUP, name="vis-group", created_by=self.alice, membership_version=1,
        )
        for user in (self.alice, self.bob):
            ConversationMember.objects.create(conversation=group, user=user)
        client_b = Client()
        client_b.force_login(self.bob)
        members = list(group.members.values_list("user_id", flat=True))
        payload = {
            "client_message_id": "vis-g1",
            "group_id": group.pk,
            "membership_version": 1,
            "message_type": "text",
            "algorithm": "AES-256-GCM",
            "sender_key_version": 1,
            "recipients": [
                {"receiver_id": uid, "receiver_key_version": 1,
                 "ciphertext": _b64(f"ct-{uid}"), "nonce": B64_12, "auth_tag": B64_16}
                for uid in members
            ],
        }
        response = self.client_a.post(
            f"/api/conversations/{group.pk}/messages/send-group/",
            data=json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 201)
        message_id = response.json()["message_id"]

        UserMessageDeletion.objects.create(
            user=self.alice, conversation=group,
            message_type=UserMessageDeletion.MessageType.GROUP, message_id=message_id,
        )
        alice_view = self._sync(group.pk).json()
        self.assertEqual(alice_view["items"], [])
        bob_view = self._sync(group.pk, client=client_b).json()
        self.assertEqual(len(bob_view["items"]), 1)


class CursorNonAsciiTests(SyncApiTestBase):
    def setUp(self):
        super().setUp()
        self.conv = Conversation.objects.create(type=Conversation.Type.SINGLE, created_by=self.alice)
        ConversationMember.objects.create(conversation=self.conv, user=self.alice)
        ConversationMember.objects.create(conversation=self.conv, user=self.bob)
        self._post_private(self.conv.pk, self.bob.pk, "na-1")

    def test_non_ascii_signature_returns_invalid_not_500(self):
        response = self._sync(self.conv.pk, cursor="ascii.中")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "sync_cursor_invalid")

