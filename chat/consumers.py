import base64
import binascii
from datetime import UTC, datetime

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from django.db import transaction
from django.db.models import F

from .models import Conversation, ConversationMember, EncryptedMessage


class ClientPayloadError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


class ChatConsumer(AsyncJsonWebsocketConsumer):
    protocol_version = '1.0'
    heartbeat_interval_seconds = 30
    unauthenticated_close_code = 4401
    private_message_algorithm = 'AES-256-GCM'

    async def connect(self):
        user = self.scope['user']
        if user.is_anonymous:
            await self.close(code=self.unauthenticated_close_code)
            return

        self.user_group_name = self.user_group(user.pk)
        await self.channel_layer.group_add(self.user_group_name, self.channel_name)
        await self.accept()
        await self.send_event(
            'connection.ready',
            data={
                'user_id': user.pk,
                'heartbeat_interval_seconds': self.heartbeat_interval_seconds,
            },
        )

    async def disconnect(self, close_code):
        user_group_name = getattr(self, 'user_group_name', None)
        if user_group_name:
            await self.channel_layer.group_discard(user_group_name, self.channel_name)

    async def receive(self, text_data=None, bytes_data=None, **kwargs):
        try:
            await super().receive(text_data=text_data, bytes_data=bytes_data, **kwargs)
        except ValueError:
            await self.send_error(request_id=None, code='invalid_payload', message='消息格式错误')

    async def receive_json(self, content, **kwargs):
        if not isinstance(content, dict):
            await self.send_error(request_id=None, code='invalid_payload', message='消息格式错误')
            return

        request_id = content.get('request_id')
        event = content.get('event')
        if event == 'connection.ping':
            await self.send_event('connection.pong', request_id=request_id, data={})
            return

        try:
            if event == 'message.single.send':
                message = await self.create_private_message(self.scope['user'].pk, content.get('data'))
                await self.send_event('message.single.sent', request_id=request_id, data=message)
                await self.channel_layer.group_send(
                    self.user_group(message['receiver_id']),
                    {'type': 'message.single.forward', 'data': message},
                )
                return

            if event in {'message.single.delivered', 'message.single.read'}:
                update = await self.update_private_message_status(
                    self.scope['user'].pk,
                    content.get('data'),
                    event.rsplit('.', 1)[-1],
                )
                await self.channel_layer.group_send(
                    self.user_group(update['sender_id']),
                    {'type': 'message.single.status.forward', 'data': update},
                )
                await self.send_event('message.single.status', request_id=request_id, data=update)
                return
        except ClientPayloadError as error:
            await self.send_error(request_id=request_id, code=error.code, message=error.message)
            return

        await self.send_error(
            request_id=request_id,
            code='not_implemented',
            message='该实时通信事件尚未实现',
        )

    async def message_single_forward(self, event):
        await self.send_event('message.single.received', data=event['data'])

    async def message_single_status_forward(self, event):
        await self.send_event('message.single.status', data=event['data'])

    async def send_error(self, *, request_id, code, message):
        await self.send_event(
            'error',
            request_id=request_id,
            data={'code': code, 'message': message, 'retryable': False},
        )

    async def send_event(self, event, *, data, request_id=None):
        await self.send_json({
            'protocol_version': self.protocol_version,
            'event': event,
            'request_id': request_id,
            'sent_at': datetime.now(UTC).isoformat().replace('+00:00', 'Z'),
            'data': data,
        })

    @staticmethod
    def user_group(user_id):
        return f'user_{user_id}'

    @classmethod
    @database_sync_to_async
    def create_private_message(cls, sender_id, data):
        data = cls.validate_private_message(data)
        with transaction.atomic():
            try:
                conversation = Conversation.objects.select_for_update().get(
                    pk=data['conversation_id'],
                    type=Conversation.Type.SINGLE,
                    status=Conversation.Status.ACTIVE,
                )
            except Conversation.DoesNotExist as error:
                raise ClientPayloadError('conversation_not_found', '私聊会话不存在或不可用') from error

            active_members = ConversationMember.objects.filter(
                conversation=conversation,
                status=ConversationMember.Status.ACTIVE,
            )
            if (
                sender_id == data['receiver_id']
                or active_members.count() != 2
                or not active_members.filter(user_id=sender_id).exists()
                or not active_members.filter(user_id=data['receiver_id']).exists()
            ):
                raise ClientPayloadError('conversation_forbidden', '无权在该私聊会话中发送消息')

            message = EncryptedMessage.objects.create(
                conversation=conversation,
                sender_id=sender_id,
                receiver_id=data['receiver_id'],
                message_type=data['message_type'],
                ciphertext=data['ciphertext'],
                nonce=data['nonce'],
                auth_tag=data['auth_tag'],
                algorithm=data['algorithm'],
                sender_key_version=data['sender_key_version'],
                receiver_key_version=data['receiver_key_version'],
            )
            conversation.last_message_id = message.pk
            conversation.last_message_at = message.created_at
            conversation.save(update_fields=['last_message_id', 'last_message_at', 'updated_at'])
            active_members.filter(user_id=data['receiver_id']).update(
                unread_count=F('unread_count') + 1,
            )
        return cls.serialize_private_message(message)

    @classmethod
    @database_sync_to_async
    def update_private_message_status(cls, receiver_id, data, status):
        message_id = cls.require_positive_integer(data, 'message_id')
        with transaction.atomic():
            try:
                message = EncryptedMessage.objects.select_for_update().get(
                    pk=message_id,
                    receiver_id=receiver_id,
                )
            except EncryptedMessage.DoesNotExist as error:
                raise ClientPayloadError('message_not_found', '私聊消息不存在或无权更新') from error

            status_order = {
                EncryptedMessage.Status.SENT: 0,
                EncryptedMessage.Status.DELIVERED: 1,
                EncryptedMessage.Status.READ: 2,
            }
            if status_order.get(message.status, -1) < status_order[status]:
                message.status = status
                message.save(update_fields=['status', 'updated_at'])
            if status == EncryptedMessage.Status.READ:
                ConversationMember.objects.filter(
                    conversation=message.conversation,
                    user_id=receiver_id,
                ).update(unread_count=0, last_read_message_id=message.pk)
        return {
            'message_id': message.pk,
            'conversation_id': message.conversation_id,
            'sender_id': message.sender_id,
            'receiver_id': message.receiver_id,
            'status': message.status,
        }

    @classmethod
    def validate_private_message(cls, data):
        if not isinstance(data, dict):
            raise ClientPayloadError('invalid_payload', '消息数据格式错误')
        if data.get('algorithm') != cls.private_message_algorithm:
            raise ClientPayloadError('unsupported_algorithm', '不支持的私聊加密算法')

        message_type = data.get('message_type', EncryptedMessage.MessageType.TEXT)
        if message_type not in EncryptedMessage.MessageType.values:
            raise ClientPayloadError('invalid_payload', '消息类型无效')

        cls.require_base64(data, 'ciphertext')
        cls.require_base64(data, 'nonce', decoded_length=12)
        cls.require_base64(data, 'auth_tag', decoded_length=16)
        return {
            'conversation_id': cls.require_positive_integer(data, 'conversation_id'),
            'receiver_id': cls.require_positive_integer(data, 'receiver_id'),
            'sender_key_version': cls.require_positive_integer(data, 'sender_key_version'),
            'receiver_key_version': cls.require_positive_integer(data, 'receiver_key_version'),
            'message_type': message_type,
            'ciphertext': data['ciphertext'],
            'nonce': data['nonce'],
            'auth_tag': data['auth_tag'],
            'algorithm': data['algorithm'],
        }

    @staticmethod
    def require_positive_integer(data, field):
        if not isinstance(data, dict):
            raise ClientPayloadError('invalid_payload', '消息数据格式错误')
        value = data.get(field)
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ClientPayloadError('invalid_payload', f'{field} 必须为正整数')
        return value

    @staticmethod
    def require_base64(data, field, *, decoded_length=None):
        value = data.get(field)
        if not isinstance(value, str) or not value:
            raise ClientPayloadError('invalid_payload', f'{field} 必须为 Base64 文本')
        try:
            decoded = base64.b64decode(value, validate=True)
        except (ValueError, binascii.Error) as error:
            raise ClientPayloadError('invalid_payload', f'{field} 必须为有效 Base64 文本') from error
        if decoded_length is not None and len(decoded) != decoded_length:
            raise ClientPayloadError('invalid_payload', f'{field} 长度无效')

    @staticmethod
    def serialize_private_message(message):
        return {
            'message_id': message.pk,
            'conversation_id': message.conversation_id,
            'sender_id': message.sender_id,
            'receiver_id': message.receiver_id,
            'message_type': message.message_type,
            'ciphertext': message.ciphertext,
            'nonce': message.nonce,
            'auth_tag': message.auth_tag,
            'algorithm': message.algorithm,
            'sender_key_version': message.sender_key_version,
            'receiver_key_version': message.receiver_key_version,
            'status': message.status,
            'created_at': message.created_at.isoformat(),
        }
