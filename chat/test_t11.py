from asgiref.sync import async_to_sync
from channels.db import database_sync_to_async
from channels.testing import WebsocketCommunicator
from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import Client, TransactionTestCase

from ichat_pro.asgi import application
from .models import Conversation, ConversationMember, EncryptedMessage


class PrivateRealtimeMessageTests(TransactionTestCase):
    def setUp(self):
        self.sender = get_user_model().objects.create_user(username='sender', password='password')
        self.receiver = get_user_model().objects.create_user(username='receiver', password='password')
        self.conversation = Conversation.objects.create(
            type=Conversation.Type.SINGLE,
            created_by=self.sender,
        )
        ConversationMember.objects.create(conversation=self.conversation, user=self.sender)
        ConversationMember.objects.create(conversation=self.conversation, user=self.receiver)
        self.sender_headers = self._session_headers(self.sender)
        self.receiver_headers = self._session_headers(self.receiver)

    def test_ciphertext_is_saved_and_forwarded(self):
        async_to_sync(self._assert_ciphertext_is_saved_and_forwarded)()

    def test_non_member_cannot_send(self):
        async_to_sync(self._assert_non_member_cannot_send)()

    def test_receiver_can_mark_message_delivered_and_read(self):
        async_to_sync(self._assert_receiver_can_mark_message_delivered_and_read)()

    def test_plaintext_field_is_not_forwarded(self):
        async_to_sync(self._assert_plaintext_field_is_not_forwarded)()

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
            'conversation_id': self.conversation.pk,
            'receiver_id': self.receiver.pk,
            'ciphertext': 'c2VjcmV0',
            'nonce': 'AAAAAAAAAAAAAAAA',
            'auth_tag': 'AAAAAAAAAAAAAAAAAAAAAA==',
            'algorithm': 'AES-256-GCM',
            'sender_key_version': 1,
            'receiver_key_version': 1,
        }

    async def _assert_ciphertext_is_saved_and_forwarded(self):
        sender = await self._connect(self.sender_headers)
        receiver = await self._connect(self.receiver_headers)
        await sender.send_json_to({
            'event': 'message.single.send',
            'request_id': 'send',
            'data': self._payload(),
        })
        sent = await sender.receive_json_from()
        received = await receiver.receive_json_from()
        self.assertEqual(sent['event'], 'message.single.sent')
        self.assertEqual(received['event'], 'message.single.received')
        self.assertEqual(received['data'], sent['data'])
        self.assertNotIn('plaintext', received['data'])
        message = await self._message(sent['data']['message_id'])
        membership = await self._membership(self.receiver.pk)
        self.assertEqual(message.ciphertext, 'c2VjcmV0')
        self.assertEqual(message.status, EncryptedMessage.Status.SENT)
        self.assertEqual(membership.unread_count, 1)
        await sender.disconnect()
        await receiver.disconnect()

    async def _assert_non_member_cannot_send(self):
        outsider = await database_sync_to_async(get_user_model().objects.create_user)(
            username='outsider',
            password='password',
        )
        headers = await database_sync_to_async(self._session_headers)(outsider)
        communicator = await self._connect(headers)
        await communicator.send_json_to({
            'event': 'message.single.send',
            'request_id': 'forbidden',
            'data': self._payload(),
        })
        error = await communicator.receive_json_from()
        self.assertEqual(error['event'], 'error')
        self.assertEqual(error['data']['code'], 'conversation_forbidden')
        self.assertEqual(await self._message_count(), 0)
        await communicator.disconnect()

    async def _assert_receiver_can_mark_message_delivered_and_read(self):
        sender = await self._connect(self.sender_headers)
        receiver = await self._connect(self.receiver_headers)
        await sender.send_json_to({'event': 'message.single.send', 'data': self._payload()})
        message_id = (await sender.receive_json_from())['data']['message_id']
        await receiver.receive_json_from()

        await receiver.send_json_to({
            'event': 'message.single.delivered',
            'request_id': 'delivered',
            'data': {'message_id': message_id},
        })
        self.assertEqual((await sender.receive_json_from())['data']['status'], 'delivered')
        self.assertEqual((await receiver.receive_json_from())['request_id'], 'delivered')

        await receiver.send_json_to({
            'event': 'message.single.read',
            'request_id': 'read',
            'data': {'message_id': message_id},
        })
        self.assertEqual((await sender.receive_json_from())['data']['status'], 'read')
        self.assertEqual((await receiver.receive_json_from())['request_id'], 'read')
        message = await self._message(message_id)
        membership = await self._membership(self.receiver.pk)
        self.assertEqual(message.status, EncryptedMessage.Status.READ)
        self.assertEqual(membership.unread_count, 0)
        self.assertEqual(membership.last_read_message_id, message_id)
        await sender.disconnect()
        await receiver.disconnect()

    async def _assert_plaintext_field_is_not_forwarded(self):
        sender = await self._connect(self.sender_headers)
        payload = self._payload()
        payload['plaintext'] = 'must-not-reach-server'
        await sender.send_json_to({'event': 'message.single.send', 'data': payload})
        sent = await sender.receive_json_from()
        self.assertEqual(sent['event'], 'message.single.sent')
        self.assertNotIn('plaintext', sent['data'])
        await sender.disconnect()

    async def _connect(self, headers):
        communicator = WebsocketCommunicator(application, '/ws/chat/', headers=headers)
        connected, _ = await communicator.connect()
        self.assertTrue(connected)
        await communicator.receive_json_from()
        return communicator

    @database_sync_to_async
    def _message(self, message_id):
        return EncryptedMessage.objects.get(pk=message_id)

    @database_sync_to_async
    def _membership(self, user_id):
        return ConversationMember.objects.get(conversation=self.conversation, user_id=user_id)

    @database_sync_to_async
    def _message_count(self):
        return EncryptedMessage.objects.count()
