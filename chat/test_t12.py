from asgiref.sync import async_to_sync
from channels.db import database_sync_to_async
from channels.testing import WebsocketCommunicator
from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import Client, TransactionTestCase

from ichat_pro.asgi import application
from .models import (
    Conversation,
    ConversationMember,
    GroupMessage,
    GroupMessageRecipient,
)


class GroupRealtimeMessageTests(TransactionTestCase):
    def setUp(self):
        self.sender = get_user_model().objects.create_user(username='sender', password='password')
        self.receiver_a = get_user_model().objects.create_user(username='receiver_a', password='password')
        self.receiver_b = get_user_model().objects.create_user(username='receiver_b', password='password')
        self.conversation = Conversation.objects.create(
            type=Conversation.Type.GROUP,
            name='TestGroup',
            created_by=self.sender,
        )
        ConversationMember.objects.create(
            conversation=self.conversation, user=self.sender, role=ConversationMember.Role.OWNER,
        )
        ConversationMember.objects.create(
            conversation=self.conversation, user=self.receiver_a, role=ConversationMember.Role.MEMBER,
        )
        ConversationMember.objects.create(
            conversation=self.conversation, user=self.receiver_b, role=ConversationMember.Role.MEMBER,
        )
        self.sender_headers = self._session_headers(self.sender)
        self.receiver_a_headers = self._session_headers(self.receiver_a)
        self.receiver_b_headers = self._session_headers(self.receiver_b)

    def test_group_message_is_saved_and_forwarded(self):
        async_to_sync(self._assert_group_message_is_saved_and_forwarded)()

    def test_non_member_cannot_send(self):
        async_to_sync(self._assert_non_member_cannot_send)()

    def test_left_member_does_not_receive(self):
        async_to_sync(self._assert_left_member_does_not_receive)()

    def test_membership_version_mismatch(self):
        async_to_sync(self._assert_membership_version_mismatch)()

    def test_idempotent_resend(self):
        async_to_sync(self._assert_idempotent_resend)()

    def _session_headers(self, user):
        client = Client()
        client.force_login(user)
        session_id = client.cookies[settings.SESSION_COOKIE_NAME].value
        return [
            (b'origin', b'http://testserver'),
            (b'cookie', f'{settings.SESSION_COOKIE_NAME}={session_id}'.encode()),
        ]

    def _payload(self):
        return {
            'client_message_id': 'test-uuid-001',
            'group_id': self.conversation.pk,
            'membership_version': 1,
            'message_type': 'text',
            'algorithm': 'AES-256-GCM',
            'sender_key_version': 1,
            'recipients': [
                {
                    'receiver_id': self.sender.pk,
                    'receiver_key_version': 1,
                    'ciphertext': 'c2VuZGVyX2NpcGhlcg==',
                    'nonce': 'AAAAAAAAAAAAAAAA',
                    'auth_tag': 'AAAAAAAAAAAAAAAAAAAAAA==',
                },
                {
                    'receiver_id': self.receiver_a.pk,
                    'receiver_key_version': 1,
                    'ciphertext': 'cmVjZWl2ZXJfYV9jaXBoZXI=',
                    'nonce': 'AAAAAAAAAAAAAAAA',
                    'auth_tag': 'AAAAAAAAAAAAAAAAAAAAAA==',
                },
                {
                    'receiver_id': self.receiver_b.pk,
                    'receiver_key_version': 1,
                    'ciphertext': 'cmVjZWl2ZXJfYl9jaXBoZXI=',
                    'nonce': 'AAAAAAAAAAAAAAAA',
                    'auth_tag': 'AAAAAAAAAAAAAAAAAAAAAA==',
                },
            ],
        }

    async def _assert_group_message_is_saved_and_forwarded(self):
        sender = await self._connect(self.sender_headers)
        recv_a = await self._connect(self.receiver_a_headers)
        recv_b = await self._connect(self.receiver_b_headers)

        await sender.send_json_to({
            'event': 'message.group.send',
            'request_id': 'group-send',
            'data': self._payload(),
        })

        accepted = await sender.receive_json_from()
        self.assertEqual(accepted['event'], 'message.group.accepted')
        self.assertEqual(accepted['data']['group_id'], self.conversation.pk)
        self.assertEqual(accepted['data']['membership_version'], 1)
        self.assertEqual(accepted['data']['status'], 'sent')
        message_id = accepted['data']['message_id']

        new_a = await recv_a.receive_json_from()
        new_b = await recv_b.receive_json_from()
        new_sender = await sender.receive_json_from()

        for event, expected_receiver_id, expected_ciphertext in [
            (new_a, self.receiver_a.pk, 'cmVjZWl2ZXJfYV9jaXBoZXI='),
            (new_b, self.receiver_b.pk, 'cmVjZWl2ZXJfYl9jaXBoZXI='),
            (new_sender, self.sender.pk, 'c2VuZGVyX2NpcGhlcg=='),
        ]:
            self.assertEqual(event['event'], 'message.group.new')
            self.assertEqual(event['data']['message_id'], message_id)
            self.assertEqual(event['data']['group_id'], self.conversation.pk)
            self.assertEqual(event['data']['sender_id'], self.sender.pk)
            self.assertEqual(event['data']['receiver_id'], expected_receiver_id)
            self.assertEqual(event['data']['ciphertext'], expected_ciphertext)
            self.assertNotIn('plaintext', event['data'])

        group_message = await self._group_message(message_id)
        self.assertEqual(group_message.message_type, 'text')
        self.assertEqual(group_message.client_message_id, 'test-uuid-001')
        recipients = await self._recipient_count(message_id)
        self.assertEqual(recipients, 3)

        membership_a = await self._membership(self.receiver_a.pk)
        membership_b = await self._membership(self.receiver_b.pk)
        membership_sender = await self._membership(self.sender.pk)
        self.assertEqual(membership_a.unread_count, 1)
        self.assertEqual(membership_b.unread_count, 1)
        self.assertEqual(membership_sender.unread_count, 0)

        await sender.disconnect()
        await recv_a.disconnect()
        await recv_b.disconnect()

    async def _assert_non_member_cannot_send(self):
        outsider = await database_sync_to_async(get_user_model().objects.create_user)(
            username='outsider', password='password',
        )
        headers = await database_sync_to_async(self._session_headers)(outsider)
        communicator = await self._connect(headers)
        await communicator.send_json_to({
            'event': 'message.group.send',
            'request_id': 'forbidden',
            'data': self._payload(),
        })
        error = await communicator.receive_json_from()
        self.assertEqual(error['event'], 'error')
        self.assertEqual(error['data']['code'], 'conversation_forbidden')
        self.assertEqual(await self._group_message_count(), 0)
        await communicator.disconnect()

    async def _assert_left_member_does_not_receive(self):
        left_member = await database_sync_to_async(get_user_model().objects.create_user)(
            username='left_member', password='password',
        )
        await database_sync_to_async(ConversationMember.objects.create)(
            conversation=self.conversation, user=left_member, role=ConversationMember.Role.MEMBER,
        )
        left_headers = await database_sync_to_async(self._session_headers)(left_member)
        left_ws = await self._connect(left_headers)

        sender = await self._connect(self.sender_headers)

        payload = {
            'client_message_id': 'test-uuid-001',
            'group_id': self.conversation.pk,
            'membership_version': 1,
            'message_type': 'text',
            'algorithm': 'AES-256-GCM',
            'sender_key_version': 1,
            'recipients': [
                {
                    'receiver_id': self.sender.pk,
                    'receiver_key_version': 1,
                    'ciphertext': 'c2VuZGVyX2NpcGhlcg==',
                    'nonce': 'AAAAAAAAAAAAAAAA',
                    'auth_tag': 'AAAAAAAAAAAAAAAAAAAAAA==',
                },
                {
                    'receiver_id': self.receiver_a.pk,
                    'receiver_key_version': 1,
                    'ciphertext': 'cmVjZWl2ZXJfYV9jaXBoZXI=',
                    'nonce': 'AAAAAAAAAAAAAAAA',
                    'auth_tag': 'AAAAAAAAAAAAAAAAAAAAAA==',
                },
                {
                    'receiver_id': self.receiver_b.pk,
                    'receiver_key_version': 1,
                    'ciphertext': 'cmVjZWl2ZXJfYl9jaXBoZXI=',
                    'nonce': 'AAAAAAAAAAAAAAAA',
                    'auth_tag': 'AAAAAAAAAAAAAAAAAAAAAA==',
                },
                {
                    'receiver_id': left_member.pk,
                    'receiver_key_version': 1,
                    'ciphertext': 'bGVmdF9tZW1iZXJfY2lwaGVy',
                    'nonce': 'AAAAAAAAAAAAAAAA',
                    'auth_tag': 'AAAAAAAAAAAAAAAAAAAAAA==',
                },
            ],
        }
        await sender.send_json_to({
            'event': 'message.group.send',
            'request_id': 'before-leave',
            'data': payload,
        })
        await sender.receive_json_from()  # message.group.accepted
        await sender.receive_json_from()  # message.group.new (sender's own copy)
        await left_ws.receive_json_from()  # message.group.new (left_member's copy)

        await database_sync_to_async(lambda: ConversationMember.objects.filter(
            conversation=self.conversation, user=left_member,
        ).update(status=ConversationMember.Status.LEFT))()

        await database_sync_to_async(lambda: Conversation.objects.filter(
            pk=self.conversation.pk,
        ).update(membership_version=2))()

        payload2 = self._payload()
        payload2['client_message_id'] = 'test-uuid-002'
        payload2['membership_version'] = 2
        payload2['recipients'] = [
            {
                'receiver_id': self.sender.pk,
                'receiver_key_version': 1,
                'ciphertext': 'c2VuZGVyX2NpcGhlcjI=',
                'nonce': 'AAAAAAAAAAAAAAAA',
                'auth_tag': 'AAAAAAAAAAAAAAAAAAAAAA==',
            },
            {
                'receiver_id': self.receiver_a.pk,
                'receiver_key_version': 1,
                'ciphertext': 'cmVjZWl2ZXJfYV9jaXBoZXIy',
                'nonce': 'AAAAAAAAAAAAAAAA',
                'auth_tag': 'AAAAAAAAAAAAAAAAAAAAAA==',
            },
            {
                'receiver_id': self.receiver_b.pk,
                'receiver_key_version': 1,
                'ciphertext': 'cmVjZWl2ZXJfYl9jaXBoZXIy',
                'nonce': 'AAAAAAAAAAAAAAAA',
                'auth_tag': 'AAAAAAAAAAAAAAAAAAAAAA==',
            },
        ]

        await sender.send_json_to({
            'event': 'message.group.send',
            'request_id': 'after-leave',
            'data': payload2,
        })
        accepted = await sender.receive_json_from()
        self.assertEqual(accepted['event'], 'message.group.accepted')
        second_message_id = accepted['data']['message_id']

        await sender.receive_json_from()
        await left_ws.disconnect()

        recipient_count = await self._recipient_count(second_message_id)
        self.assertEqual(recipient_count, 3)

        await sender.disconnect()

    async def _assert_membership_version_mismatch(self):
        sender = await self._connect(self.sender_headers)
        payload = self._payload()
        payload['membership_version'] = 999
        await sender.send_json_to({
            'event': 'message.group.send',
            'request_id': 'version-mismatch',
            'data': payload,
        })
        error = await sender.receive_json_from()
        self.assertEqual(error['event'], 'error')
        self.assertEqual(error['data']['code'], 'membership_conflict')
        await sender.disconnect()

    async def _assert_idempotent_resend(self):
        sender = await self._connect(self.sender_headers)
        recv_a = await self._connect(self.receiver_a_headers)

        await sender.send_json_to({
            'event': 'message.group.send',
            'request_id': 'first-send',
            'data': self._payload(),
        })
        first_accepted = await sender.receive_json_from()
        first_message_id = first_accepted['data']['message_id']
        await recv_a.receive_json_from()

        await sender.send_json_to({
            'event': 'message.group.send',
            'request_id': 'second-send',
            'data': self._payload(),
        })
        second_accepted = await sender.receive_json_from()

        self.assertEqual(second_accepted['data']['message_id'], first_message_id)
        self.assertEqual(await self._group_message_count(), 1)

        await sender.disconnect()
        await recv_a.disconnect()

    async def _connect(self, headers):
        communicator = WebsocketCommunicator(application, '/ws/chat/', headers=headers)
        connected, _ = await communicator.connect()
        self.assertTrue(connected)
        await communicator.receive_json_from()
        return communicator

    @database_sync_to_async
    def _group_message(self, message_id):
        return GroupMessage.objects.get(pk=message_id)

    @database_sync_to_async
    def _group_message_count(self):
        return GroupMessage.objects.count()

    @database_sync_to_async
    def _recipient_count(self, message_id):
        return GroupMessageRecipient.objects.filter(group_message_id=message_id).count()

    @database_sync_to_async
    def _membership(self, user_id):
        return ConversationMember.objects.get(conversation=self.conversation, user_id=user_id)
