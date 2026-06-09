import base64
import binascii
from datetime import UTC, datetime

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from django.db import IntegrityError, transaction
from django.db.models import F

from .models import (
    Conversation,
    ConversationMember,
    EncryptedMessage,
    GroupMessage,
    GroupMessageRecipient,
)


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
    group_message_algorithm = 'AES-256-GCM'

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
                await self.send_event('message.single.accepted', request_id=request_id, data=message)
                await self.channel_layer.group_send(
                    self.user_group(message['receiver_id']),
                    {'type': 'message.single.new', 'data': message},
                )
                return

            if event == 'message.group.send':
                accepted, recipients = await self.create_group_message(
                    self.scope['user'].pk, content.get('data'),
                )
                await self.send_event('message.group.accepted', request_id=request_id, data=accepted)
                for recipient_data in recipients:
                    await self.channel_layer.group_send(
                        self.user_group(recipient_data['receiver_id']),
                        {'type': 'message.group.new', 'data': recipient_data},
                    )
                return

            if event == 'message.receipt.update':
                update = await self.update_private_message_status(
                    self.scope['user'].pk,
                    content.get('data'),
                )
                await self.channel_layer.group_send(
                    self.user_group(update['sender_id']),
                    {'type': 'message.receipt.updated', 'data': update},
                )
                await self.send_event('message.receipt.updated', request_id=request_id, data=update)
                return
        except ClientPayloadError as error:
            await self.send_error(request_id=request_id, code=error.code, message=error.message)
            return

        await self.send_error(
            request_id=request_id,
            code='not_implemented',
            message='该实时通信事件尚未实现',
        )

    async def message_single_new(self, event):
        await self.send_event('message.single.new', data=event['data'])

    async def message_receipt_updated(self, event):
        await self.send_event('message.receipt.updated', data=event['data'])

    async def message_group_new(self, event):
        await self.send_event('message.group.new', data=event['data'])

    async def group_members_changed(self, event):
        await self.send_event('group.members.changed', data=event['data'])

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

    @staticmethod
    async def broadcast_group_members_changed(channel_layer, group_id, change, actor_id,
                                              affected_user_id, membership_version):
        """Push group.members.changed to all active group members via their user groups."""
        member_ids = await database_sync_to_async(list)(
            ConversationMember.objects.filter(
                conversation_id=group_id,
                status=ConversationMember.Status.ACTIVE,
            ).values_list('user_id', flat=True)
        )
        for user_id in member_ids:
            await channel_layer.group_send(
                f'user_{user_id}',
                {
                    'type': 'group.members.changed',
                    'data': {
                        'group_id': group_id,
                        'change': change,
                        'actor_id': actor_id,
                        'affected_user_id': affected_user_id,
                        'membership_version': membership_version,
                    },
                },
            )

    @classmethod
    @database_sync_to_async
    def create_private_message(cls, sender_id, data):
        data = cls.validate_private_message(data)
        client_message_id = data.get('client_message_id')
        with transaction.atomic():
            if client_message_id:
                existing = EncryptedMessage.objects.filter(
                    sender_id=sender_id,
                    client_message_id=client_message_id,
                ).first()
                if existing:
                    return cls.serialize_private_message(existing)
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

            try:
                with transaction.atomic():
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
                        client_message_id=client_message_id,
                    )
            except IntegrityError:
                # Savepoint rolled back; outer transaction is still healthy.
                # A concurrent request created the same client_message_id.
                if client_message_id:
                    existing = EncryptedMessage.objects.get(
                        sender_id=sender_id,
                        client_message_id=client_message_id,
                    )
                    return cls.serialize_private_message(existing)
                raise

            conversation.last_message_id = message.pk
            conversation.last_message_at = message.created_at
            conversation.save(update_fields=['last_message_id', 'last_message_at', 'updated_at'])
            active_members.filter(user_id=data['receiver_id']).update(
                unread_count=F('unread_count') + 1,
            )
        return cls.serialize_private_message(message)

    @classmethod
    @database_sync_to_async
    def update_private_message_status(cls, receiver_id, data):
        if not isinstance(data, dict):
            raise ClientPayloadError('invalid_payload', '消息数据格式错误')
        conversation_type = data.get('conversation_type')
        if conversation_type != 'single':
            raise ClientPayloadError('invalid_payload', 'conversation_type 必须为 single')
        status = data.get('status')
        if status not in {EncryptedMessage.Status.DELIVERED, EncryptedMessage.Status.READ}:
            raise ClientPayloadError('invalid_payload', 'status 必须为 delivered 或 read')
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
            'conversation_type': 'single',
            'message_id': message.pk,
            'conversation_id': message.conversation_id,
            'sender_id': message.sender_id,
            'receiver_id': message.receiver_id,
            'user_id': receiver_id,
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

        cls.require_base64(data, 'ciphertext', max_decoded_length=65536)
        cls.require_base64(data, 'nonce', decoded_length=12)
        cls.require_base64(data, 'auth_tag', decoded_length=16)
        client_message_id = data.get('client_message_id')
        if not isinstance(client_message_id, str) or not client_message_id or len(client_message_id) > 64:
            raise ClientPayloadError('invalid_payload', 'client_message_id 缺失或格式无效')
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
            'client_message_id': client_message_id,
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
    def require_base64(data, field, *, decoded_length=None, max_decoded_length=None):
        value = data.get(field)
        if not isinstance(value, str) or not value:
            raise ClientPayloadError('invalid_payload', f'{field} 必须为 Base64 文本')
        try:
            decoded = base64.b64decode(value, validate=True)
        except (ValueError, binascii.Error) as error:
            raise ClientPayloadError('invalid_payload', f'{field} 必须为有效 Base64 文本') from error
        if decoded_length is not None and len(decoded) != decoded_length:
            raise ClientPayloadError('invalid_payload', f'{field} 长度无效')
        if max_decoded_length is not None and len(decoded) > max_decoded_length:
            raise ClientPayloadError('invalid_payload', f'{field} 超过最大长度限制')

    @staticmethod
    def serialize_private_message(message):
        return {
            'client_message_id': message.client_message_id,
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

    max_group_active_members = 50

    @classmethod
    @database_sync_to_async
    def create_group_message(cls, sender_id, data):
        data = cls.validate_group_message(data)
        with transaction.atomic():
            try:
                conversation = Conversation.objects.select_for_update().get(
                    pk=data['group_id'],
                    type=Conversation.Type.GROUP,
                    status=Conversation.Status.ACTIVE,
                )
            except Conversation.DoesNotExist as error:
                raise ClientPayloadError('conversation_not_found', '群聊会话不存在或不可用') from error

            active_members = ConversationMember.objects.filter(
                conversation=conversation,
                status=ConversationMember.Status.ACTIVE,
            )
            active_member_ids = set(active_members.values_list('user_id', flat=True))
            if len(active_member_ids) > cls.max_group_active_members:
                raise ClientPayloadError('group_too_large', f'群聊活跃成员数超过上限 {cls.max_group_active_members}')
            if sender_id not in active_member_ids:
                raise ClientPayloadError('conversation_forbidden', '无权在该群聊中发送消息')

            if data['membership_version'] != conversation.membership_version:
                raise ClientPayloadError('membership_conflict', '群成员版本已变更，请重新拉取成员列表')

            recipient_user_ids = {r['receiver_id'] for r in data['recipients']}
            if recipient_user_ids != active_member_ids:
                raise ClientPayloadError('recipients_mismatch', '接收者列表与当前活跃成员不一致')

            client_message_id = data.get('client_message_id')
            existing = GroupMessage.objects.filter(
                sender_id=sender_id,
                client_message_id=client_message_id,
            ).first()
            if existing:
                return cls._build_group_accepted(existing, conversation)

            try:
                with transaction.atomic():
                    group_message = GroupMessage.objects.create(
                        conversation=conversation,
                        sender_id=sender_id,
                        message_type=data['message_type'],
                        client_message_id=client_message_id,
                    )
            except IntegrityError:
                existing = GroupMessage.objects.get(
                    sender_id=sender_id,
                    client_message_id=client_message_id,
                )
                return cls._build_group_accepted(existing, conversation)
            recipient_objs = [
                GroupMessageRecipient(
                    group_message=group_message,
                    receiver_id=r['receiver_id'],
                    ciphertext=r['ciphertext'],
                    nonce=r['nonce'],
                    auth_tag=r['auth_tag'],
                    algorithm=data['algorithm'],
                    sender_key_version=data['sender_key_version'],
                    receiver_key_version=r['receiver_key_version'],
                    membership_version=data['membership_version'],
                )
                for r in data['recipients']
            ]
            GroupMessageRecipient.objects.bulk_create(recipient_objs)

            conversation.last_message_id = group_message.pk
            conversation.last_message_at = group_message.created_at
            conversation.save(update_fields=['last_message_id', 'last_message_at', 'updated_at'])

            active_members.exclude(user_id=sender_id).update(
                unread_count=F('unread_count') + 1,
            )

        return cls._build_group_result(group_message, conversation)

    @classmethod
    def _build_group_accepted(cls, group_message, conversation):
        return (
            {
                'client_message_id': group_message.client_message_id,
                'message_id': group_message.pk,
                'group_id': conversation.pk,
                'membership_version': conversation.membership_version,
                'status': 'sent',
                'created_at': group_message.created_at.isoformat(),
            },
            cls._build_recipients_payload(group_message, conversation),
        )

    @classmethod
    def _build_group_result(cls, group_message, conversation):
        return (
            {
                'client_message_id': group_message.client_message_id,
                'message_id': group_message.pk,
                'group_id': conversation.pk,
                'membership_version': conversation.membership_version,
                'status': 'sent',
                'created_at': group_message.created_at.isoformat(),
            },
            cls._build_recipients_payload(group_message, conversation),
        )

    @classmethod
    def _build_recipients_payload(cls, group_message, conversation):
        recipients = GroupMessageRecipient.objects.filter(
            group_message=group_message,
        ).select_related('group_message')
        return [
            cls.serialize_group_recipient(r)
            for r in recipients
        ]

    @classmethod
    def validate_group_message(cls, data):
        if not isinstance(data, dict):
            raise ClientPayloadError('invalid_payload', '消息数据格式错误')
        if data.get('algorithm') != cls.group_message_algorithm:
            raise ClientPayloadError('unsupported_algorithm', '不支持的群聊加密算法')

        message_type = data.get('message_type', GroupMessage.MessageType.TEXT)
        if message_type not in GroupMessage.MessageType.values:
            raise ClientPayloadError('invalid_payload', '消息类型无效')

        recipients = data.get('recipients')
        if not isinstance(recipients, list) or not recipients:
            raise ClientPayloadError('invalid_payload', 'recipients 必须为非空数组')
        seen_receivers = set()
        for r in recipients:
            if not isinstance(r, dict):
                raise ClientPayloadError('invalid_payload', 'recipients 元素必须为对象')
            receiver_id = cls.require_positive_integer(r, 'receiver_id')
            if receiver_id in seen_receivers:
                raise ClientPayloadError('invalid_payload', f'receiver_id {receiver_id} 重复')
            seen_receivers.add(receiver_id)
            cls.require_base64(r, 'ciphertext', max_decoded_length=65536)
            cls.require_base64(r, 'nonce', decoded_length=12)
            cls.require_base64(r, 'auth_tag', decoded_length=16)
            cls.require_positive_integer(r, 'receiver_key_version')

        client_message_id = data.get('client_message_id')
        if not isinstance(client_message_id, str) or not client_message_id or len(client_message_id) > 64:
            raise ClientPayloadError('invalid_payload', 'client_message_id 缺失或格式无效')

        return {
            'group_id': cls.require_positive_integer(data, 'group_id'),
            'membership_version': cls.require_positive_integer(data, 'membership_version'),
            'sender_key_version': cls.require_positive_integer(data, 'sender_key_version'),
            'message_type': message_type,
            'algorithm': data['algorithm'],
            'client_message_id': client_message_id,
            'recipients': [
                {
                    'receiver_id': r['receiver_id'],
                    'receiver_key_version': r['receiver_key_version'],
                    'ciphertext': r['ciphertext'],
                    'nonce': r['nonce'],
                    'auth_tag': r['auth_tag'],
                }
                for r in recipients
            ],
        }

    @staticmethod
    def serialize_group_recipient(recipient, membership_version=None):
        mv = membership_version if membership_version is not None else recipient.membership_version
        return {
            'message_id': recipient.group_message_id,
            'group_id': recipient.group_message.conversation_id,
            'membership_version': mv,
            'sender_id': recipient.group_message.sender_id,
            'receiver_id': recipient.receiver_id,
            'message_type': recipient.group_message.message_type,
            'ciphertext': recipient.ciphertext,
            'nonce': recipient.nonce,
            'auth_tag': recipient.auth_tag,
            'algorithm': recipient.algorithm,
            'sender_key_version': recipient.sender_key_version,
            'receiver_key_version': recipient.receiver_key_version,
            'status': recipient.status,
            'created_at': recipient.group_message.created_at.isoformat(),
        }
