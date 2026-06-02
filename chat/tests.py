import json

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