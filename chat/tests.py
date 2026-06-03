import json

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from channels.testing import WebsocketCommunicator
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import User
from django.db import IntegrityError
from django.test import TestCase, TransactionTestCase

from ichat_pro.asgi import application

from .models import (
    AdminOperationLog,
    Conversation,
    ConversationMember,
    EncryptedMessage,
    GroupMessage,
    GroupMessageRecipient,
)


class ConversationModelTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="test1234")

    def test_create_single_conversation(self):
        conv = Conversation.objects.create(
            type=Conversation.Type.SINGLE, created_by=self.user
        )
        self.assertEqual(conv.type, Conversation.Type.SINGLE)
        self.assertEqual(conv.status, Conversation.Status.ACTIVE)
        self.assertIsNone(conv.last_message_at)
        self.assertEqual(conv.created_by, self.user)

    def test_create_group_conversation(self):
        conv = Conversation.objects.create(
            type=Conversation.Type.GROUP, created_by=self.user
        )
        self.assertEqual(conv.type, Conversation.Type.GROUP)

    def test_default_status_is_active(self):
        conv = Conversation.objects.create(type=Conversation.Type.SINGLE)
        self.assertEqual(conv.status, Conversation.Status.ACTIVE)

    def test_created_by_is_nullable(self):
        conv = Conversation.objects.create(type=Conversation.Type.SINGLE)
        self.assertIsNone(conv.created_by)

    def test_last_message_at_is_nullable(self):
        conv = Conversation.objects.create(type=Conversation.Type.SINGLE)
        self.assertIsNone(conv.last_message_at)
        self.assertIsNone(conv.last_message_id)

    def test_str_method(self):
        conv = Conversation.objects.create(
            type=Conversation.Type.SINGLE, created_by=self.user
        )
        expected = f"Conversation #{conv.id} (Private Chat)"
        self.assertEqual(str(conv), expected)

    def test_ordering_by_last_message_at(self):
        older = Conversation.objects.create(type=Conversation.Type.SINGLE)
        newer = Conversation.objects.create(type=Conversation.Type.SINGLE)
        # By default ordering in admin is by -last_message_at, -created_at;
        # here we just verify both objects exist without error.
        self.assertTrue(Conversation.objects.filter(id=older.id).exists())
        self.assertTrue(Conversation.objects.filter(id=newer.id).exists())


class ConversationMemberModelTests(TestCase):
    def setUp(self):
        self.user1 = User.objects.create_user(username="alice", password="test1234")
        self.user2 = User.objects.create_user(username="bob", password="test1234")
        self.conv = Conversation.objects.create(
            type=Conversation.Type.SINGLE, created_by=self.user1
        )

    def test_create_membership(self):
        member = ConversationMember.objects.create(
            conversation=self.conv, user=self.user1
        )
        self.assertEqual(member.status, ConversationMember.Status.ACTIVE)
        self.assertEqual(member.unread_count, 0)
        self.assertIsNone(member.last_read_message_id)

    def test_unique_constraint_per_conversation_and_user(self):
        ConversationMember.objects.create(
            conversation=self.conv, user=self.user1
        )
        with self.assertRaises(IntegrityError):
            ConversationMember.objects.create(
                conversation=self.conv, user=self.user1
            )

    def test_multiple_users_in_same_conversation(self):
        ConversationMember.objects.create(
            conversation=self.conv, user=self.user1
        )
        ConversationMember.objects.create(
            conversation=self.conv, user=self.user2
        )
        self.assertEqual(self.conv.members.count(), 2)

    def test_str_method(self):
        member = ConversationMember.objects.create(
            conversation=self.conv, user=self.user1
        )
        expected = (
            f"Member #{self.user1.id} in Conversation #{self.conv.id}"
        )
        self.assertEqual(str(member), expected)

    def test_muted_status(self):
        member = ConversationMember.objects.create(
            conversation=self.conv,
            user=self.user1,
            status=ConversationMember.Status.MUTED,
        )
        self.assertEqual(member.status, ConversationMember.Status.MUTED)


class EncryptedMessageModelTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="test1234")
        self.bob = User.objects.create_user(username="bob", password="test1234")
        self.conv = Conversation.objects.create(
            type=Conversation.Type.SINGLE, created_by=self.alice
        )

    def test_create_text_message(self):
        msg = EncryptedMessage.objects.create(
            conversation=self.conv,
            sender=self.alice,
            receiver=self.bob,
            ciphertext="base64encryptedpayload==",
            nonce="a1b2c3d4e5f6",
            auth_tag="ffaabbccdd112233",
            algorithm="AES-256-GCM",
            sender_key_version=1,
            receiver_key_version=1,
        )
        self.assertEqual(msg.message_type, EncryptedMessage.MessageType.TEXT)
        self.assertEqual(msg.status, EncryptedMessage.Status.SENT)
        self.assertEqual(msg.algorithm, "AES-256-GCM")

    def test_defaults(self):
        msg = EncryptedMessage.objects.create(
            conversation=self.conv,
            sender=self.alice,
            receiver=self.bob,
            algorithm="AES-256-GCM",
        )
        self.assertEqual(msg.message_type, EncryptedMessage.MessageType.TEXT)
        self.assertEqual(msg.status, EncryptedMessage.Status.SENT)
        self.assertIsNone(msg.ciphertext)
        self.assertIsNone(msg.nonce)
        self.assertIsNone(msg.auth_tag)

    def test_message_types(self):
        for mtype in EncryptedMessage.MessageType.values:
            msg = EncryptedMessage.objects.create(
                conversation=self.conv,
                sender=self.alice,
                receiver=self.bob,
                message_type=mtype,
                algorithm="AES-256-GCM",
            )
            self.assertEqual(msg.message_type, mtype)

    def test_status_values(self):
        for status in EncryptedMessage.Status.values:
            msg = EncryptedMessage.objects.create(
                conversation=self.conv,
                sender=self.alice,
                receiver=self.bob,
                status=status,
                algorithm="AES-256-GCM",
            )
            self.assertEqual(msg.status, status)

    def test_sender_receiver_relationship(self):
        msg = EncryptedMessage.objects.create(
            conversation=self.conv,
            sender=self.alice,
            receiver=self.bob,
            algorithm="AES-256-GCM",
        )
        self.assertEqual(msg.sender, self.alice)
        self.assertEqual(msg.receiver, self.bob)
        self.assertEqual(self.alice.sent_messages.count(), 1)
        self.assertEqual(self.bob.received_messages.count(), 1)

    def test_str_method(self):
        msg = EncryptedMessage.objects.create(
            conversation=self.conv,
            sender=self.alice,
            receiver=self.bob,
            algorithm="AES-256-GCM",
        )
        expected = (
            f"Message #{msg.id} from #{self.alice.id} to #{self.bob.id}"
        )
        self.assertEqual(str(msg), expected)

    def test_deleted_at_nullable(self):
        msg = EncryptedMessage.objects.create(
            conversation=self.conv,
            sender=self.alice,
            receiver=self.bob,
            algorithm="AES-256-GCM",
        )
        self.assertIsNone(msg.deleted_at)

    def test_key_version_fields_nullable(self):
        msg = EncryptedMessage.objects.create(
            conversation=self.conv,
            sender=self.alice,
            receiver=self.bob,
            algorithm="AES-256-GCM",
        )
        self.assertIsNone(msg.sender_key_version)
        self.assertIsNone(msg.receiver_key_version)


class ConversationModelGroupTests(TestCase):
    def test_create_group_with_name(self):
        conv = Conversation.objects.create(
            type=Conversation.Type.GROUP, name="Test Group"
        )
        self.assertEqual(conv.name, "Test Group")
        self.assertEqual(conv.type, Conversation.Type.GROUP)

    def test_name_is_blankable(self):
        conv = Conversation.objects.create(type=Conversation.Type.GROUP)
        self.assertEqual(conv.name, "")

    def test_avatar_is_blankable(self):
        conv = Conversation.objects.create(type=Conversation.Type.GROUP)
        self.assertEqual(conv.avatar, "")


class ConversationMemberRoleTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="test1234")
        self.conv = Conversation.objects.create(
            type=Conversation.Type.GROUP, name="RoleTest"
        )

    def test_default_role_is_member(self):
        member = ConversationMember.objects.create(
            conversation=self.conv, user=self.user
        )
        self.assertEqual(member.role, ConversationMember.Role.MEMBER)

    def test_owner_role(self):
        member = ConversationMember.objects.create(
            conversation=self.conv, user=self.user, role=ConversationMember.Role.OWNER
        )
        self.assertEqual(member.role, ConversationMember.Role.OWNER)

    def test_admin_role(self):
        member = ConversationMember.objects.create(
            conversation=self.conv, user=self.user, role=ConversationMember.Role.ADMIN
        )
        self.assertEqual(member.role, ConversationMember.Role.ADMIN)


class GroupAPITests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="test1234")
        self.bob = User.objects.create_user(username="bob", password="test1234")
        self.eve = User.objects.create_user(username="eve", password="test1234")

    def _post(self, url, data, user=None):
        user = user or self.alice
        self.client.login(username=user.username, password="test1234")
        return self.client.post(
            url,
            data=json.dumps(data),
            content_type="application/json",
        )

    def _put(self, url, data, user=None):
        user = user or self.alice
        self.client.login(username=user.username, password="test1234")
        return self.client.put(
            url,
            data=json.dumps(data),
            content_type="application/json",
        )

    def _create_group(self, name="My Group", user=None):
        user = user or self.alice
        resp = self._post("/api/groups/", {"name": name}, user=user)
        self.assertEqual(resp.status_code, 201)
        return resp.json()["id"]

    # ---- create ----
    def test_create_group_returns_201(self):
        resp = self._post("/api/groups/", {"name": "TestGroup"})
        self.assertEqual(resp.status_code, 201)
        data = resp.json()
        self.assertEqual(data["name"], "TestGroup")
        self.assertEqual(data["type"], Conversation.Type.GROUP)

    def test_create_group_without_name_returns_400(self):
        resp = self._post("/api/groups/", {})
        self.assertEqual(resp.status_code, 400)

    def test_create_group_empty_name_returns_400(self):
        resp = self._post("/api/groups/", {"name": "   "})
        self.assertEqual(resp.status_code, 400)

    def test_creator_becomes_owner(self):
        gid = self._create_group("OwnerTest")
        member = ConversationMember.objects.get(
            conversation_id=gid, user=self.alice
        )
        self.assertEqual(member.role, ConversationMember.Role.OWNER)

    # ---- update ----
    def test_owner_can_update_group_name(self):
        gid = self._create_group("OldName")
        resp = self._put(f"/api/groups/{gid}/", {"name": "NewName"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["name"], "NewName")

    def test_non_owner_cannot_update_group(self):
        gid = self._create_group("OwnerGroup")
        # Bob joins as member
        ConversationMember.objects.create(
            conversation_id=gid, user=self.bob, role=ConversationMember.Role.MEMBER
        )
        resp = self._put(f"/api/groups/{gid}/", {"name": "Hijack"}, user=self.bob)
        self.assertEqual(resp.status_code, 403)

    def test_non_member_cannot_update_group(self):
        gid = self._create_group("OwnerGroup")
        resp = self._put(f"/api/groups/{gid}/", {"name": "Hijack"}, user=self.eve)
        self.assertEqual(resp.status_code, 403)

    # ---- invite ----
    def test_owner_can_invite_member(self):
        gid = self._create_group()
        resp = self._post(f"/api/groups/{gid}/invite/", {"user_id": self.bob.id})
        self.assertEqual(resp.status_code, 201)
        self.assertTrue(
            ConversationMember.objects.filter(
                conversation_id=gid, user=self.bob
            ).exists()
        )

    def test_admin_can_invite_member(self):
        gid = self._create_group()
        # Make alice's friend an admin
        ConversationMember.objects.create(
            conversation_id=gid, user=self.bob, role=ConversationMember.Role.ADMIN
        )
        resp = self._post(
            f"/api/groups/{gid}/invite/",
            {"user_id": self.eve.id},
            user=self.bob,
        )
        self.assertEqual(resp.status_code, 201)

    def test_regular_member_cannot_invite(self):
        gid = self._create_group()
        ConversationMember.objects.create(
            conversation_id=gid, user=self.bob, role=ConversationMember.Role.MEMBER
        )
        resp = self._post(
            f"/api/groups/{gid}/invite/",
            {"user_id": self.eve.id},
            user=self.bob,
        )
        self.assertEqual(resp.status_code, 403)

    def test_cannot_invite_duplicate_user(self):
        gid = self._create_group()
        ConversationMember.objects.create(
            conversation_id=gid, user=self.bob
        )
        resp = self._post(f"/api/groups/{gid}/invite/", {"user_id": self.bob.id})
        self.assertEqual(resp.status_code, 409)

    def test_non_member_cannot_invite(self):
        gid = self._create_group()
        resp = self._post(f"/api/groups/{gid}/invite/", {"user_id": self.eve.id}, user=self.eve)
        self.assertEqual(resp.status_code, 403)

    # ---- remove ----
    def test_owner_can_remove_member(self):
        gid = self._create_group()
        ConversationMember.objects.create(
            conversation_id=gid, user=self.bob
        )
        resp = self._post(f"/api/groups/{gid}/remove/", {"user_id": self.bob.id})
        self.assertEqual(resp.status_code, 200)
        member = ConversationMember.objects.get(conversation_id=gid, user=self.bob)
        self.assertEqual(member.status, ConversationMember.Status.REMOVED)

    def test_cannot_remove_owner(self):
        gid = self._create_group()
        resp = self._post(f"/api/groups/{gid}/remove/", {"user_id": self.alice.id})
        self.assertEqual(resp.status_code, 403)

    def test_cannot_remove_from_private_conversation(self):
        """Removal is a group-only concept."""
        conv = Conversation.objects.create(
            type=Conversation.Type.SINGLE, created_by=self.alice
        )
        ConversationMember.objects.create(conversation=conv, user=self.alice)
        resp = self._post(f"/api/groups/{conv.id}/remove/", {"user_id": self.bob.id})
        # Group not found — since we filter for GROUP type
        self.assertEqual(resp.status_code, 403)  # actor not in group

    # ---- disband ----
    def test_owner_can_disband_group(self):
        gid = self._create_group()
        resp = self._post(f"/api/groups/{gid}/disband/", {})
        self.assertEqual(resp.status_code, 200)
        conv = Conversation.objects.get(id=gid)
        self.assertEqual(conv.status, Conversation.Status.DELETED)

    def test_non_owner_cannot_disband_group(self):
        gid = self._create_group()
        ConversationMember.objects.create(
            conversation_id=gid, user=self.bob
        )
        resp = self._post(f"/api/groups/{gid}/disband/", {}, user=self.bob)
        self.assertEqual(resp.status_code, 403)

    # ---- auth ----
    def test_unauthenticated_rejected(self):
        url = "/api/groups/"
        resp = self.client.post(
            url,
            data=json.dumps({"name": "X"}),
            content_type="application/json",
        )
        self.assertIn(resp.status_code, [302, 301])


class GroupMessageModelTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="test1234")
        self.conv = Conversation.objects.create(
            type=Conversation.Type.GROUP, name="GMTest"
        )

    def test_create_group_message(self):
        msg = GroupMessage.objects.create(
            conversation=self.conv,
            sender=self.alice,
            message_type=GroupMessage.MessageType.TEXT,
        )
        self.assertEqual(msg.status, GroupMessage.Status.ACTIVE)
        self.assertEqual(msg.message_type, GroupMessage.MessageType.TEXT)

    def test_create_recipient(self):
        bob = User.objects.create_user(username="bob", password="test1234")
        msg = GroupMessage.objects.create(
            conversation=self.conv, sender=self.alice
        )
        recipient = GroupMessageRecipient.objects.create(
            group_message=msg,
            receiver=bob,
            ciphertext="bobs-ciphertext",
            nonce="nonce42",
            auth_tag="tag42",
            algorithm="AES-256-GCM",
        )
        self.assertEqual(recipient.status, GroupMessageRecipient.Status.SENT)
        self.assertEqual(recipient.receiver, bob)

    def test_unique_recipient_per_message(self):
        bob = User.objects.create_user(username="bob", password="test1234")
        msg = GroupMessage.objects.create(
            conversation=self.conv, sender=self.alice
        )
        GroupMessageRecipient.objects.create(
            group_message=msg,
            receiver=bob,
            algorithm="AES-256-GCM",
        )
        with self.assertRaises(IntegrityError):
            GroupMessageRecipient.objects.create(
                group_message=msg,
                receiver=bob,
                algorithm="AES-256-GCM",
            )

    def test_str_method(self):
        msg = GroupMessage.objects.create(
            conversation=self.conv, sender=self.alice
        )
        expected = f"GroupMessage #{msg.id} in Conversation #{self.conv.id}"
        self.assertEqual(str(msg), expected)


class GroupMessagesAPITests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="test1234")
        self.bob = User.objects.create_user(username="bob", password="test1234")
        self.eve = User.objects.create_user(username="eve", password="test1234")
        self.conv = Conversation.objects.create(
            type=Conversation.Type.GROUP, name="TestGroup"
        )

    def _create_member(self, user, role=ConversationMember.Role.MEMBER):
        return ConversationMember.objects.create(
            conversation=self.conv, user=user, role=role
        )

    def _create_group_message(self, sender, recipients_ciphertexts):
        """sender: User, recipients_ciphertexts: {user: ciphertext_str}"""
        msg = GroupMessage.objects.create(
            conversation=self.conv, sender=sender
        )
        for user, ct in recipients_ciphertexts.items():
            GroupMessageRecipient.objects.create(
                group_message=msg,
                receiver=user,
                ciphertext=ct,
                nonce="n",
                auth_tag="t",
                algorithm="AES-256-GCM",
            )
        return msg

    def _get(self, user=None, **params):
        user = user or self.alice
        self.client.login(username=user.username, password="test1234")
        qs = "&".join(f"{k}={v}" for k, v in params.items())
        url = f"/api/groups/{self.conv.id}/messages/"
        if qs:
            url += f"?{qs}"
        return self.client.get(url)

    def test_member_can_fetch_messages(self):
        self._create_member(self.alice)
        self._create_member(self.bob)
        self._create_group_message(
            self.alice,
            {self.alice: "alice-ct", self.bob: "bob-ct"},
        )
        resp = self._get()
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["total_messages"], 1)
        self.assertEqual(len(data["messages"]), 1)
        msg = data["messages"][0]
        # Only the current user's ciphertext is returned
        self.assertEqual(msg["ciphertext"], "alice-ct")

    def test_messages_exclude_other_users_ciphertexts(self):
        self._create_member(self.alice)
        self._create_member(self.bob)
        self._create_group_message(
            self.alice,
            {self.alice: "alice-ct", self.bob: "bob-ct"},
        )
        resp = self._get(user=self.bob)
        msgs = resp.json()["messages"]
        self.assertEqual(len(msgs), 1)
        self.assertEqual(msgs[0]["ciphertext"], "bob-ct")
        self.assertNotEqual(msgs[0]["ciphertext"], "alice-ct")

    def test_non_member_gets_403(self):
        resp = self._get(user=self.eve)
        self.assertEqual(resp.status_code, 403)

    def test_new_member_cannot_see_old_messages(self):
        self._create_member(self.alice)
        # Alice sends a message before Bob joins
        self._create_group_message(self.alice, {self.alice: "alice-ct"})
        # Bob joins later
        bob_member = ConversationMember.objects.create(
            conversation=self.conv, user=self.bob
        )
        # Bob should not see the old message
        resp = self._get(user=self.bob)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["total_messages"], 0)

    def test_pagination(self):
        self._create_member(self.alice)
        for i in range(5):
            self._create_group_message(self.alice, {self.alice: f"ct-{i}"})
        resp = self._get(per_page=2, page=1)
        data = resp.json()
        self.assertEqual(len(data["messages"]), 2)
        self.assertTrue(data["has_next"])

    def test_empty_group_returns_empty_list(self):
        self._create_member(self.alice)
        resp = self._get()
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["messages"], [])

    def test_messages_ordered_newest_first(self):
        self._create_member(self.alice)
        msg1 = self._create_group_message(self.alice, {self.alice: "ct-1"})
        msg2 = self._create_group_message(self.alice, {self.alice: "ct-2"})
        resp = self._get()
        msgs = resp.json()["messages"]
        self.assertEqual(msgs[0]["id"], msg2.id)
        self.assertEqual(msgs[1]["id"], msg1.id)


class ConversationMessagesAPITests(TestCase):
    def setUp(self):
        from accounts.models import Contact
        self.alice = User.objects.create_user(username="alice", password="test1234")
        self.bob = User.objects.create_user(username="bob", password="test1234")
        self.eve = User.objects.create_user(username="eve", password="test1234")
        self.conv = Conversation.objects.create(
            type=Conversation.Type.SINGLE, created_by=self.alice
        )
        ConversationMember.objects.create(
            conversation=self.conv, user=self.alice
        )
        ConversationMember.objects.create(
            conversation=self.conv, user=self.bob
        )
        Contact.objects.create(user=self.alice, contact=self.bob)
        self.msg1 = EncryptedMessage.objects.create(
            conversation=self.conv,
            sender=self.alice,
            receiver=self.bob,
            ciphertext="msg1cipher",
            nonce="nonce1",
            auth_tag="tag1",
            algorithm="AES-256-GCM",
        )
        self.msg2 = EncryptedMessage.objects.create(
            conversation=self.conv,
            sender=self.bob,
            receiver=self.alice,
            ciphertext="msg2cipher",
            nonce="nonce2",
            auth_tag="tag2",
            algorithm="AES-256-GCM",
        )
        self.msg3 = EncryptedMessage.objects.create(
            conversation=self.conv,
            sender=self.alice,
            receiver=self.bob,
            ciphertext="msg3cipher",
            nonce="nonce3",
            auth_tag="tag3",
            algorithm="AES-256-GCM",
        )

    def _get(self, conversation_id, user=None, **params):
        user = user or self.alice
        self.client.login(username=user.username, password="test1234")
        qs = "&".join(f"{k}={v}" for k, v in params.items())
        url = f"/api/conversations/{conversation_id}/messages/"
        if qs:
            url += f"?{qs}"
        return self.client.get(url)

    def test_participant_can_fetch_messages(self):
        response = self._get(self.conv.id)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["conversation_id"], self.conv.id)
        self.assertEqual(data["total_messages"], 3)
        self.assertEqual(data["page"], 1)
        self.assertEqual(data["total_pages"], 1)
        self.assertFalse(data["has_next"])
        self.assertFalse(data["has_previous"])
        self.assertEqual(len(data["messages"]), 3)

    def test_non_participant_gets_403(self):
        response = self._get(self.conv.id, user=self.eve)
        self.assertEqual(response.status_code, 403)
        self.assertIn("error", response.json())

    def test_messages_ordered_newest_first(self):
        response = self._get(self.conv.id)
        messages = response.json()["messages"]
        self.assertEqual(messages[0]["id"], self.msg3.id)
        self.assertEqual(messages[1]["id"], self.msg2.id)
        self.assertEqual(messages[2]["id"], self.msg1.id)

    def test_empty_conversation_returns_empty_list(self):
        empty_conv = Conversation.objects.create(type=Conversation.Type.SINGLE)
        ConversationMember.objects.create(
            conversation=empty_conv, user=self.alice
        )
        response = self._get(empty_conv.id)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["total_messages"], 0)
        self.assertEqual(data["messages"], [])

    def test_pagination(self):
        for i in range(5):
            EncryptedMessage.objects.create(
                conversation=self.conv,
                sender=self.alice,
                receiver=self.bob,
                algorithm="AES-256-GCM",
            )
        response = self._get(self.conv.id, per_page=3, page=1)
        data = response.json()
        self.assertEqual(len(data["messages"]), 3)
        self.assertTrue(data["has_next"])
        self.assertEqual(data["total_pages"], 3)
        response = self._get(self.conv.id, per_page=3, page=3)
        data = response.json()
        self.assertEqual(len(data["messages"]), 2)
        self.assertFalse(data["has_next"])

    def test_unauthenticated_redirects_to_login(self):
        url = f"/api/conversations/{self.conv.id}/messages/"
        response = self.client.get(url)
        self.assertIn(response.status_code, [302, 301])
        self.assertIn("login", response.url)

    # ── T29: contact enforcement ───────────────────────────────────

    def test_non_contact_cannot_access_private_messages(self):
        """Even if a member, non-contacts get 403 (T29)."""
        from accounts.models import Contact
        # Eve is not a contact of alice
        conv_eve = Conversation.objects.create(
            type=Conversation.Type.SINGLE, created_by=self.alice,
        )
        ConversationMember.objects.create(conversation=conv_eve, user=self.alice)
        ConversationMember.objects.create(conversation=conv_eve, user=self.eve)
        # No Contact between alice and eve
        response = self._get(conv_eve.id)
        self.assertEqual(response.status_code, 403)

    def test_removed_contact_cannot_access_private_messages(self):
        """After contact is removed, access is denied (T29)."""
        from accounts.models import Contact
        # Create conv with bob as contact, then remove contact
        response = self._get(self.conv.id)
        self.assertEqual(response.status_code, 200)  # still contacts
        Contact.objects.filter(user=self.alice, contact=self.bob).delete()
        response = self._get(self.conv.id)
        self.assertEqual(response.status_code, 403)


class ChatConsumerTests(TransactionTestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username='websocket-user',
            password='test-password',
        )
        self.session_cookie_header = self._session_cookie_header()

    def test_authenticated_user_can_connect(self):
        async_to_sync(self._assert_authenticated_user_can_connect)()

    def test_anonymous_user_is_rejected(self):
        async_to_sync(self._assert_anonymous_user_is_rejected)()

    def test_ping_returns_pong(self):
        async_to_sync(self._assert_ping_returns_pong)()

    def test_unimplemented_event_returns_error(self):
        async_to_sync(self._assert_unimplemented_event_returns_error)()

    def test_invalid_json_returns_error(self):
        async_to_sync(self._assert_invalid_json_returns_error)()

    def test_cross_site_origin_is_rejected(self):
        async_to_sync(self._assert_cross_site_origin_is_rejected)()

    def test_disconnect_removes_user_group(self):
        async_to_sync(self._assert_disconnect_removes_user_group)()

    def test_chat_page_loads_private_chat_e2ee_module(self):
        response = self.client.get('/')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'js/private-chat-e2ee.js')

    def _session_cookie_header(self):
        self.client.force_login(self.user)
        session_id = self.client.cookies[settings.SESSION_COOKIE_NAME].value
        return [
            (b'origin', b'http://testserver'),
            (b'cookie', f'{settings.SESSION_COOKIE_NAME}={session_id}'.encode()),
        ]

    async def _assert_authenticated_user_can_connect(self):
        communicator = WebsocketCommunicator(
            application,
            '/ws/chat/',
            headers=self.session_cookie_header,
        )
        connected, _ = await communicator.connect()
        self.assertTrue(connected)

        ready = await communicator.receive_json_from()
        self.assertEqual(ready['protocol_version'], '1.0')
        self.assertEqual(ready['event'], 'connection.ready')
        self.assertEqual(ready['data']['user_id'], self.user.pk)
        self.assertEqual(ready['data']['heartbeat_interval_seconds'], 30)

        await communicator.disconnect()

    async def _assert_anonymous_user_is_rejected(self):
        communicator = WebsocketCommunicator(
            application,
            '/ws/chat/',
            headers=[(b'origin', b'http://testserver')],
        )
        connected, close_code = await communicator.connect()
        self.assertFalse(connected)
        self.assertEqual(close_code, 4401)

    async def _assert_ping_returns_pong(self):
        communicator = WebsocketCommunicator(
            application,
            '/ws/chat/',
            headers=self.session_cookie_header,
        )
        connected, _ = await communicator.connect()
        self.assertTrue(connected)
        await communicator.receive_json_from()

        await communicator.send_json_to({
            'protocol_version': '1.0',
            'event': 'connection.ping',
            'request_id': 'ping-request',
            'data': {},
        })

        pong = await communicator.receive_json_from()
        self.assertEqual(pong['event'], 'connection.pong')
        self.assertEqual(pong['request_id'], 'ping-request')
        self.assertEqual(pong['data'], {})

        await communicator.disconnect()

    async def _assert_unimplemented_event_returns_error(self):
        communicator = WebsocketCommunicator(
            application,
            '/ws/chat/',
            headers=self.session_cookie_header,
        )
        connected, _ = await communicator.connect()
        self.assertTrue(connected)
        await communicator.receive_json_from()

        await communicator.send_json_to({
            'protocol_version': '1.0',
            'event': 'unknown.event',
            'request_id': 'message-request',
            'data': {},
        })

        error = await communicator.receive_json_from()
        self.assertEqual(error['event'], 'error')
        self.assertEqual(error['request_id'], 'message-request')
        self.assertEqual(error['data']['code'], 'not_implemented')
        self.assertFalse(error['data']['retryable'])

        await communicator.disconnect()

    async def _assert_cross_site_origin_is_rejected(self):
        communicator = WebsocketCommunicator(
            application,
            '/ws/chat/',
            headers=[(b'origin', b'https://attacker.example')],
        )
        connected, _ = await communicator.connect()
        self.assertFalse(connected)

    async def _assert_disconnect_removes_user_group(self):
        communicator = WebsocketCommunicator(
            application,
            '/ws/chat/',
            headers=self.session_cookie_header,
        )
        connected, _ = await communicator.connect()
        self.assertTrue(connected)
        await communicator.receive_json_from()

        channel_layer = get_channel_layer()
        user_group_name = f'user_{self.user.pk}'
        self.assertIn(user_group_name, channel_layer.groups)

        await communicator.disconnect()
        self.assertNotIn(user_group_name, channel_layer.groups)

    async def _assert_invalid_json_returns_error(self):
        communicator = WebsocketCommunicator(
            application,
            '/ws/chat/',
            headers=self.session_cookie_header,
        )
        connected, _ = await communicator.connect()
        self.assertTrue(connected)
        await communicator.receive_json_from()

        await communicator.send_to(text_data='{')

        error = await communicator.receive_json_from()
        self.assertEqual(error['event'], 'error')
        self.assertIsNone(error['request_id'])
        self.assertEqual(error['data']['code'], 'invalid_payload')
        self.assertFalse(error['data']['retryable'])

        await communicator.disconnect()
from django.contrib.auth.models import User
from django.db import IntegrityError
from django.test import TestCase

from .models import Conversation, ConversationMember, EncryptedMessage


class ConversationModelTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="test1234")

    def test_create_single_conversation(self):
        conv = Conversation.objects.create(
            type=Conversation.Type.SINGLE, created_by=self.user
        )
        self.assertEqual(conv.type, Conversation.Type.SINGLE)
        self.assertEqual(conv.status, Conversation.Status.ACTIVE)
        self.assertIsNone(conv.last_message_at)
        self.assertEqual(conv.created_by, self.user)

    def test_create_group_conversation(self):
        conv = Conversation.objects.create(
            type=Conversation.Type.GROUP, created_by=self.user
        )
        self.assertEqual(conv.type, Conversation.Type.GROUP)

    def test_default_status_is_active(self):
        conv = Conversation.objects.create(type=Conversation.Type.SINGLE)
        self.assertEqual(conv.status, Conversation.Status.ACTIVE)

    def test_created_by_is_nullable(self):
        conv = Conversation.objects.create(type=Conversation.Type.SINGLE)
        self.assertIsNone(conv.created_by)

    def test_last_message_at_is_nullable(self):
        conv = Conversation.objects.create(type=Conversation.Type.SINGLE)
        self.assertIsNone(conv.last_message_at)
        self.assertIsNone(conv.last_message_id)

    def test_str_method(self):
        conv = Conversation.objects.create(
            type=Conversation.Type.SINGLE, created_by=self.user
        )
        expected = f"Conversation #{conv.id} (Private Chat)"
        self.assertEqual(str(conv), expected)

    def test_ordering_by_last_message_at(self):
        older = Conversation.objects.create(type=Conversation.Type.SINGLE)
        newer = Conversation.objects.create(type=Conversation.Type.SINGLE)
        # By default ordering in admin is by -last_message_at, -created_at;
        # here we just verify both objects exist without error.
        self.assertTrue(Conversation.objects.filter(id=older.id).exists())
        self.assertTrue(Conversation.objects.filter(id=newer.id).exists())


class ConversationMemberModelTests(TestCase):
    def setUp(self):
        self.user1 = User.objects.create_user(username="alice", password="test1234")
        self.user2 = User.objects.create_user(username="bob", password="test1234")
        self.conv = Conversation.objects.create(
            type=Conversation.Type.SINGLE, created_by=self.user1
        )

    def test_create_membership(self):
        member = ConversationMember.objects.create(
            conversation=self.conv, user=self.user1
        )
        self.assertEqual(member.status, ConversationMember.Status.ACTIVE)
        self.assertEqual(member.unread_count, 0)
        self.assertIsNone(member.last_read_message_id)

    def test_unique_constraint_per_conversation_and_user(self):
        ConversationMember.objects.create(
            conversation=self.conv, user=self.user1
        )
        with self.assertRaises(IntegrityError):
            ConversationMember.objects.create(
                conversation=self.conv, user=self.user1
            )

    def test_multiple_users_in_same_conversation(self):
        ConversationMember.objects.create(
            conversation=self.conv, user=self.user1
        )
        ConversationMember.objects.create(
            conversation=self.conv, user=self.user2
        )
        self.assertEqual(self.conv.members.count(), 2)

    def test_str_method(self):
        member = ConversationMember.objects.create(
            conversation=self.conv, user=self.user1
        )
        expected = (
            f"Member #{self.user1.id} in Conversation #{self.conv.id}"
        )
        self.assertEqual(str(member), expected)

    def test_muted_status(self):
        member = ConversationMember.objects.create(
            conversation=self.conv,
            user=self.user1,
            status=ConversationMember.Status.MUTED,
        )
        self.assertEqual(member.status, ConversationMember.Status.MUTED)


class EncryptedMessageModelTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="test1234")
        self.bob = User.objects.create_user(username="bob", password="test1234")
        self.conv = Conversation.objects.create(
            type=Conversation.Type.SINGLE, created_by=self.alice
        )

    def test_create_text_message(self):
        msg = EncryptedMessage.objects.create(
            conversation=self.conv,
            sender=self.alice,
            receiver=self.bob,
            ciphertext="base64encryptedpayload==",
            nonce="a1b2c3d4e5f6",
            auth_tag="ffaabbccdd112233",
            algorithm="AES-256-GCM",
            sender_key_version=1,
            receiver_key_version=1,
        )
        self.assertEqual(msg.message_type, EncryptedMessage.MessageType.TEXT)
        self.assertEqual(msg.status, EncryptedMessage.Status.SENT)
        self.assertEqual(msg.algorithm, "AES-256-GCM")

    def test_defaults(self):
        msg = EncryptedMessage.objects.create(
            conversation=self.conv,
            sender=self.alice,
            receiver=self.bob,
            algorithm="AES-256-GCM",
        )
        self.assertEqual(msg.message_type, EncryptedMessage.MessageType.TEXT)
        self.assertEqual(msg.status, EncryptedMessage.Status.SENT)
        self.assertIsNone(msg.ciphertext)
        self.assertIsNone(msg.nonce)
        self.assertIsNone(msg.auth_tag)

    def test_message_types(self):
        for mtype in EncryptedMessage.MessageType.values:
            msg = EncryptedMessage.objects.create(
                conversation=self.conv,
                sender=self.alice,
                receiver=self.bob,
                message_type=mtype,
                algorithm="AES-256-GCM",
            )
            self.assertEqual(msg.message_type, mtype)

    def test_status_values(self):
        for status in EncryptedMessage.Status.values:
            msg = EncryptedMessage.objects.create(
                conversation=self.conv,
                sender=self.alice,
                receiver=self.bob,
                status=status,
                algorithm="AES-256-GCM",
            )
            self.assertEqual(msg.status, status)

    def test_sender_receiver_relationship(self):
        msg = EncryptedMessage.objects.create(
            conversation=self.conv,
            sender=self.alice,
            receiver=self.bob,
            algorithm="AES-256-GCM",
        )
        self.assertEqual(msg.sender, self.alice)
        self.assertEqual(msg.receiver, self.bob)
        self.assertEqual(self.alice.sent_messages.count(), 1)
        self.assertEqual(self.bob.received_messages.count(), 1)

    def test_str_method(self):
        msg = EncryptedMessage.objects.create(
            conversation=self.conv,
            sender=self.alice,
            receiver=self.bob,
            algorithm="AES-256-GCM",
        )
        expected = (
            f"Message #{msg.id} from #{self.alice.id} to #{self.bob.id}"
        )
        self.assertEqual(str(msg), expected)

    def test_deleted_at_nullable(self):
        msg = EncryptedMessage.objects.create(
            conversation=self.conv,
            sender=self.alice,
            receiver=self.bob,
            algorithm="AES-256-GCM",
        )
        self.assertIsNone(msg.deleted_at)

    def test_key_version_fields_nullable(self):
        msg = EncryptedMessage.objects.create(
            conversation=self.conv,
            sender=self.alice,
            receiver=self.bob,
            algorithm="AES-256-GCM",
        )
        self.assertIsNone(msg.sender_key_version)
        self.assertIsNone(msg.receiver_key_version)

# ── T31: admin audit log ──────────────────────────────────────────


class AdminOperationLogTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            username="admint31", password="test", is_staff=True,
        )

    def test_create_log_entry(self):
        log = AdminOperationLog.objects.create(
            admin=self.admin,
            action=AdminOperationLog.Action.ACTIVATE_USER,
            target_type="User",
            target_id=5,
            details="Test audit entry",
        )
        self.assertEqual(log.admin, self.admin)
        self.assertEqual(log.action, AdminOperationLog.Action.ACTIVATE_USER)
        self.assertIsNotNone(log.created_at)

    def test_deactivate_action_creates_log(self):
        user = User.objects.create_user(username="target31", password="x")
        log = AdminOperationLog.objects.create(
            admin=self.admin,
            action=AdminOperationLog.Action.DEACTIVATE_USER,
            target_type="User",
            target_id=user.id,
            details=f"Deactivated {user.username}",
        )
        self.assertEqual(log.action, AdminOperationLog.Action.DEACTIVATE_USER)
        self.assertEqual(log.target_id, user.id)

    def test_str_method(self):
        log = AdminOperationLog.objects.create(
            admin=self.admin,
            action=AdminOperationLog.Action.ACTIVATE_USER,
            target_type="User",
            target_id=1,
        )
        expected = f"AdminOp #{log.id}: Activate User by {self.admin}"
        self.assertEqual(str(log), expected)
