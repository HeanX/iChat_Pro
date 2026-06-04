import base64
import binascii
from datetime import UTC, datetime

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from django.db import IntegrityError, transaction
from django.db.models import F
from django.utils import timezone

from .models import (
    Conversation,
    ConversationMember,
    EncryptedMessage,
    GroupMessage,
    GroupMessageRecipient,
    UserPresence,
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

        # T22: Mark user online and broadcast presence BEFORE adding self
        # to group so the connecting client doesn't receive its own event.
        await self._set_presence_online(user.pk)
        await self.channel_layer.group_send(
            self.user_group_name,
            {
                'type': 'presence.updated',
                'data': {
                    'user_id': user.pk,
                    'is_online': True,
                    'status': 'online',
                },
            },
        )

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
        user = self.scope.get('user')
        user_group_name = getattr(self, 'user_group_name', None)

        # T22: Discard first so current connection doesn't receive its own
        # offline presence event, then broadcast to other sessions.
        if user_group_name:
            await self.channel_layer.group_discard(user_group_name, self.channel_name)

        if user and not user.is_anonymous:
            await self._set_presence_offline(user.pk)
            await self.channel_layer.group_send(
                user_group_name or self.user_group(user.pk),
                {
                    'type': 'presence.updated',
                    'data': {
                        'user_id': user.pk,
                        'is_online': False,
                        'status': 'offline',
                        'last_seen': timezone.now().isoformat(),
                    },
                },
            )

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
                data = content.get('data', {})
                conv_type = data.get('conversation_type', 'single')
                if conv_type == 'single':
                    update = await self.update_private_message_status(
                        self.scope['user'].pk, data,
                    )
                elif conv_type == 'group':
                    update = await self.update_group_message_status(
                        self.scope['user'].pk, data,
                    )
                else:
                    raise ClientPayloadError('invalid_payload', 'conversation_type 必须为 single 或 group')
                await self.channel_layer.group_send(
                    self.user_group(update['sender_id']),
                    {'type': 'message.receipt.updated', 'data': update},
                )
                await self.send_event('message.receipt.updated', request_id=request_id, data=update)
                return

            # T20: Message recall via WebSocket
            if event == 'message.recall':
                result = await self.recall_message(
                    self.scope['user'].pk, content.get('data'),
                )
                await self.send_event('message.recalled', request_id=request_id, data=result)
                if result['conversation_type'] == 'single':
                    await self.channel_layer.group_send(
                        self.user_group(result['other_user_id']),
                        {'type': 'message.recalled', 'data': result},
                    )
                elif result['conversation_type'] == 'group':
                    member_ids = await database_sync_to_async(list)(
                        ConversationMember.objects.filter(
                            conversation_id=result['conversation_id'],
                            status=ConversationMember.Status.ACTIVE,
                        ).values_list('user_id', flat=True)
                    )
                    for uid in member_ids:
                        if uid != self.scope['user'].pk:
                            await self.channel_layer.group_send(
                                self.user_group(uid),
                                {'type': 'message.recalled', 'data': result},
                            )
                return

            # T22: Typing indicators
            if event == 'typing.start':
                data = content.get('data', {})
                conversation_id = data.get('conversation_id')
                if not conversation_id:
                    raise ClientPayloadError('invalid_payload', 'conversation_id 缺失')
                await self._verify_conversation_membership(self.scope['user'].pk, conversation_id)
                member_ids = await self._get_active_member_ids(conversation_id)
                typing_data = {
                    'conversation_id': conversation_id,
                    'user_id': self.scope['user'].pk,
                    'action': 'typing',
                }
                for uid in member_ids:
                    if uid != self.scope['user'].pk:
                        await self.channel_layer.group_send(
                            self.user_group(uid),
                            {'type': 'typing.indicator', 'data': typing_data},
                        )
                await self.send_event('typing.start.ack', request_id=request_id, data={'status': 'ok'})
                return

            if event == 'typing.stop':
                data = content.get('data', {})
                conversation_id = data.get('conversation_id')
                if not conversation_id:
                    raise ClientPayloadError('invalid_payload', 'conversation_id 缺失')
                await self._verify_conversation_membership(self.scope['user'].pk, conversation_id)
                member_ids = await self._get_active_member_ids(conversation_id)
                typing_data = {
                    'conversation_id': conversation_id,
                    'user_id': self.scope['user'].pk,
                    'action': 'stop',
                }
                for uid in member_ids:
                    if uid != self.scope['user'].pk:
                        await self.channel_layer.group_send(
                            self.user_group(uid),
                            {'type': 'typing.indicator', 'data': typing_data},
                        )
                await self.send_event('typing.stop.ack', request_id=request_id, data={'status': 'ok'})
                return
        except ClientPayloadError as error:
            await self.send_error(request_id=request_id, code=error.code, message=error.message)
            return

        await self.send_error(
            request_id=request_id,
            code='not_implemented',
            message='该实时通信事件尚未实现',
        )

    # ── Channel-layer event handlers ────────────────────────────────

    async def message_single_new(self, event):
        await self.send_event('message.single.new', data=event['data'])

    async def message_receipt_updated(self, event):
        await self.send_event('message.receipt.updated', data=event['data'])

    async def message_group_new(self, event):
        await self.send_event('message.group.new', data=event['data'])

    async def group_members_changed(self, event):
        await self.send_event('group.members.changed', data=event['data'])

    async def message_recalled(self, event):
        await self.send_event('message.recalled', data=event['data'])

    async def typing_indicator(self, event):
        await self.send_event('typing', data=event['data'])

    async def presence_updated(self, event):
        await self.send_event('presence.updated', data=event['data'])

    async def message_deleted(self, event):
        await self.send_event('message.deleted', data=event['data'])

    # ── Helpers ─────────────────────────────────────────────────────

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

    # ── T22: Presence helpers ────────────────────────────────────────

    @database_sync_to_async
    def _set_presence_online(self, user_id):
        presence, _ = UserPresence.objects.get_or_create(user_id=user_id)
        presence.is_online = True
        presence.status = UserPresence.Status.ONLINE
        presence.save(update_fields=['is_online', 'status', 'updated_at'])

    @database_sync_to_async
    def _set_presence_offline(self, user_id):
        now = timezone.now()
        UserPresence.objects.filter(user_id=user_id).update(
            is_online=False,
            status=UserPresence.Status.OFFLINE,
            last_seen=now,
            updated_at=now,
        )

    @database_sync_to_async
    def _get_active_member_ids(self, conversation_id):
        return list(
            ConversationMember.objects.filter(
                conversation_id=conversation_id,
                status=ConversationMember.Status.ACTIVE,
            ).values_list('user_id', flat=True)
        )

    @database_sync_to_async
    def _verify_conversation_membership(self, user_id, conversation_id):
        if not ConversationMember.objects.filter(
            conversation_id=conversation_id,
            user_id=user_id,
            status=ConversationMember.Status.ACTIVE,
        ).exists():
            raise ClientPayloadError('conversation_forbidden', '无权在该会话中操作')

    # ── Private message creation ────────────────────────────────────

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
                        reply_to_message_id=data.get('reply_to_message_id'),
                    )
            except IntegrityError:
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

    # ── Private message receipt updates ─────────────────────────────

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

    # ── T21: Group message receipt updates ─────────────────────────

    @classmethod
    @database_sync_to_async
    def update_group_message_status(cls, receiver_id, data):
        if not isinstance(data, dict):
            raise ClientPayloadError('invalid_payload', '消息数据格式错误')
        status = data.get('status')
        if status not in {GroupMessageRecipient.Status.DELIVERED,
                          GroupMessageRecipient.Status.READ}:
            raise ClientPayloadError('invalid_payload', 'status 必须为 delivered 或 read')
        message_id = cls.require_positive_integer(data, 'message_id')
        with transaction.atomic():
            try:
                recipient = GroupMessageRecipient.objects.select_for_update().get(
                    group_message_id=message_id,
                    receiver_id=receiver_id,
                )
            except GroupMessageRecipient.DoesNotExist as error:
                raise ClientPayloadError('message_not_found', '群聊消息不存在或无权更新') from error

            status_order = {
                GroupMessageRecipient.Status.SENT: 0,
                GroupMessageRecipient.Status.DELIVERED: 1,
                GroupMessageRecipient.Status.READ: 2,
            }
            if status_order.get(recipient.status, -1) < status_order[status]:
                recipient.status = status
                recipient.save(update_fields=['status'])

            if status == GroupMessageRecipient.Status.READ:
                ConversationMember.objects.filter(
                    conversation_id=recipient.group_message.conversation_id,
                    user_id=receiver_id,
                ).update(unread_count=0, last_read_message_id=message_id)

        return {
            'conversation_type': 'group',
            'message_id': recipient.group_message_id,
            'conversation_id': recipient.group_message.conversation_id,
            'sender_id': recipient.group_message.sender_id,
            'receiver_id': receiver_id,
            'user_id': receiver_id,
            'status': recipient.status,
        }

    # ── T20: Message recall ────────────────────────────────────────

    recall_limit_minutes = 30

    @classmethod
    @database_sync_to_async
    def recall_message(cls, user_id, data):
        if not isinstance(data, dict):
            raise ClientPayloadError('invalid_payload', '消息数据格式错误')
        conversation_type = data.get('conversation_type', 'single')
        message_id = cls.require_positive_integer(data, 'message_id')

        if conversation_type == 'single':
            return cls._recall_private_message(user_id, message_id)
        elif conversation_type == 'group':
            return cls._recall_group_message(user_id, message_id)
        else:
            raise ClientPayloadError('invalid_payload', 'conversation_type 必须为 single 或 group')

    @classmethod
    def _recall_private_message(cls, user_id, message_id):
        with transaction.atomic():
            try:
                message = EncryptedMessage.objects.select_for_update().get(
                    pk=message_id,
                    sender_id=user_id,
                )
            except EncryptedMessage.DoesNotExist as error:
                raise ClientPayloadError('message_not_found', '消息不存在或无权撤回') from error

            if message.status == EncryptedMessage.Status.RECALLED:
                raise ClientPayloadError('already_recalled', '消息已被撤回')

            elapsed = (timezone.now() - message.created_at).total_seconds()
            if elapsed > cls.recall_limit_minutes * 60:
                raise ClientPayloadError('recall_timeout', f'消息发送已超过{cls.recall_limit_minutes}分钟，无法撤回')

            message.status = EncryptedMessage.Status.RECALLED
            message.recalled_at = timezone.now()
            message.save(update_fields=['status', 'recalled_at', 'updated_at'])

        return {
            'conversation_type': 'single',
            'message_id': message.pk,
            'conversation_id': message.conversation_id,
            'sender_id': message.sender_id,
            'other_user_id': message.receiver_id,
            'recalled_at': message.recalled_at.isoformat(),
        }

    @classmethod
    def _recall_group_message(cls, user_id, message_id):
        with transaction.atomic():
            try:
                group_message = GroupMessage.objects.select_for_update().get(
                    pk=message_id,
                    sender_id=user_id,
                )
            except GroupMessage.DoesNotExist as error:
                raise ClientPayloadError('message_not_found', '消息不存在或无权撤回') from error

            if group_message.status == GroupMessage.Status.RECALLED:
                raise ClientPayloadError('already_recalled', '消息已被撤回')

            elapsed = (timezone.now() - group_message.created_at).total_seconds()
            if elapsed > cls.recall_limit_minutes * 60:
                raise ClientPayloadError('recall_timeout', f'消息发送已超过{cls.recall_limit_minutes}分钟，无法撤回')

            group_message.status = GroupMessage.Status.RECALLED
            group_message.recalled_at = timezone.now()
            group_message.save(update_fields=['status', 'recalled_at', 'updated_at'])

            GroupMessageRecipient.objects.filter(
                group_message=group_message,
            ).update(status=GroupMessageRecipient.Status.RECALLED)

        return {
            'conversation_type': 'group',
            'message_id': group_message.pk,
            'conversation_id': group_message.conversation_id,
            'sender_id': group_message.sender_id,
            'recalled_at': group_message.recalled_at.isoformat(),
        }

    # ── Validation helpers ─────────────────────────────────────────

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

        reply_to = data.get('reply_to_message_id')
        if reply_to is not None and not isinstance(reply_to, int):
            raise ClientPayloadError('invalid_payload', 'reply_to_message_id 必须为整数')

        result = {
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
        if reply_to is not None:
            result['reply_to_message_id'] = reply_to
        return result

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

    # ── Serialization ──────────────────────────────────────────────

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
            'reply_to_message_id': message.reply_to_message_id,
            'status': message.status,
            'recalled_at': message.recalled_at.isoformat() if message.recalled_at else None,
            'created_at': message.created_at.isoformat(),
        }

    # ── Group message creation ─────────────────────────────────────

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
                        reply_to_message_id=data.get('reply_to_message_id'),
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
            cls.serialize_group_recipient(r, conversation.membership_version)
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

        reply_to = data.get('reply_to_message_id')
        if reply_to is not None and not isinstance(reply_to, int):
            raise ClientPayloadError('invalid_payload', 'reply_to_message_id 必须为整数')

        result = {
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
        if reply_to is not None:
            result['reply_to_message_id'] = reply_to
        return result

    @staticmethod
    def serialize_group_recipient(recipient, membership_version):
        return {
            'message_id': recipient.group_message_id,
            'group_id': recipient.group_message.conversation_id,
            'membership_version': membership_version,
            'sender_id': recipient.group_message.sender_id,
            'receiver_id': recipient.receiver_id,
            'message_type': recipient.group_message.message_type,
            'ciphertext': recipient.ciphertext,
            'nonce': recipient.nonce,
            'auth_tag': recipient.auth_tag,
            'algorithm': recipient.algorithm,
            'sender_key_version': recipient.sender_key_version,
            'receiver_key_version': recipient.receiver_key_version,
            'reply_to_message_id': recipient.group_message.reply_to_message_id,
            'status': recipient.status,
            'recalled_at': recipient.group_message.recalled_at.isoformat() if recipient.group_message.recalled_at else None,
            'created_at': recipient.group_message.created_at.isoformat(),
        }
