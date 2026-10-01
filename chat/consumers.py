from datetime import UTC, datetime

from asgiref.sync import sync_to_async
from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from django.db import OperationalError, transaction
from django.db.models import F
from django.utils import timezone

from .errors import (
    PROTOCOL_VERSION,
    SUPPORTED_PROTOCOL_VERSIONS,
    UNSUPPORTED_PROTOCOL_CLOSE_CODE,
    get_error,
)
from .services import messaging
from .models import (
    Conversation,
    ConversationMember,
    EncryptedFile,
    EncryptedFileKey,
    EncryptedMessage,
    GroupMessage,
    GroupMessageRecipient,
    UserPresence,
)


# Historical name for the payload/protocol error; now defined on the errors
# module next to the registry it indexes.
ClientPayloadError = messaging.PayloadError


class ChatConsumer(AsyncJsonWebsocketConsumer):
    protocol_version = '1.0'
    heartbeat_interval_seconds = 30
    unauthenticated_close_code = 4401

    async def connect(self):
        user = self.scope['user']
        if user.is_anonymous:
            await self.close(code=self.unauthenticated_close_code)
            return

        self.user_group_name = self.user_group(user.pk)

        # R-05 fix: track per-user live connections in the shared cache
        # (Redis across Daphne workers in production, LocMem in single-process
        # dev/tests) instead of the channel layer's non-standard group_channels.
        # Presence flips and broadcasts only on the FIRST connect / LAST
        # disconnect, so extra tabs no longer fake each other offline.
        connection_count = await self._bump_connection_count(user.pk, +1)
        if connection_count == 1:
            # T22: mark online and broadcast BEFORE adding self to the group
            # so the connecting client doesn't receive its own event.
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

        if user_group_name:
            await self.channel_layer.group_discard(user_group_name, self.channel_name)

        if user and not user.is_anonymous:
            remaining = await self._bump_connection_count(user.pk, -1)
            if remaining == 0:
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

    @staticmethod
    def _connection_count_key(user_id):
        return f"ws:connections:{user_id}"

    @classmethod
    async def _bump_connection_count(cls, user_id, delta):
        """Atomically adjust the per-user live-connection counter (R-05 fix).

        Returns the counter value after the bump, or None when the cache is
        unavailable (callers then fall back to the legacy always-on/alway-
        offline presence behaviour).

        @sync_to_async is used (not database_sync_to_async) because the cache
        is not the ORM; thread_sensitive=False keeps it off the test main
        thread where the TestCase connection lives.
        """

        @sync_to_async
        def bump():
            from django.core.cache import cache

            key = cls._connection_count_key(user_id)
            try:
                if delta > 0:
                    cache.add(key, 0, timeout=None)
                    try:
                        return cache.incr(key)
                    except ValueError:
                        return 1
                value = cache.decr(key)
                if value <= 0:
                    cache.delete(key)
                    return 0
                return value
            except ValueError:
                return 0 if delta < 0 else 1
            except Exception:
                return None

        return await bump()

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

        # T31: protocol version gate (ADR-P4-02). Requests SHOULD carry
        # protocol_version at the envelope top level; omitting it is accepted
        # as "1.0" during the legacy compat window. Unsupported versions get
        # the registered error and a documented close code.
        version = content.get('protocol_version', PROTOCOL_VERSION)
        if version not in SUPPORTED_PROTOCOL_VERSIONS:
            await self.send_error(
                request_id=request_id,
                code='unsupported_protocol_version',
                message=f'不支持的协议版本: {version}',
            )
            await self.close(code=UNSUPPORTED_PROTOCOL_CLOSE_CODE)
            return

        event = content.get('event')
        if event == 'connection.ping':
            await self.send_event('connection.pong', request_id=request_id, data={})
            return

        try:
            if event == 'message.single.send':
                result = await self.send_private_message(
                    self.scope['user'].pk, content.get('data'),
                )
                sender_message = await self.serialize_private_message_for_viewer(
                    result.message.pk,
                    self.scope['user'].pk,
                )
                # T32: accepted carries the created/replayed distinction so
                # the client can merge an ACK-lost retry with its pending item.
                sender_message['created'] = result.created
                await self.send_event('message.single.accepted', request_id=request_id, data=sender_message)
                # T36/R-04: the receiver is pushed viewer-projected copies of
                # NEW messages only — replays never re-broadcast.
                if result.created:
                    receiver_message = await self.serialize_private_message_for_viewer(
                        result.message.pk,
                        result.message.receiver_id,
                    )
                    await self.channel_layer.group_send(
                        self.user_group(result.message.receiver_id),
                        {'type': 'message.single.new', 'data': receiver_message},
                    )
                return

            if event == 'message.group.send':
                result, accepted, recipients_payload = await self.send_group_message(
                    self.scope['user'].pk, content.get('data'),
                )
                accepted['created'] = result.created
                await self.send_event('message.group.accepted', request_id=request_id, data=accepted)
                # T36/R-04: recipients receive viewer-projected copies of NEW
                # messages only — replays never re-broadcast.
                if result.created:
                    for recipient_data in recipients_payload:
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
        except OperationalError as error:
            if 'database is locked' in str(error).lower():
                await self.send_error(
                    request_id=request_id,
                    code='storage_unavailable',
                    message='Database is busy. Please retry shortly.',
                )
                return
            raise

        await self.send_error(
            request_id=request_id,
            code='not_implemented',
            message='该实时通信事件尚未实现',
        )

    # ──── Channel-layer event handlers ────────────────────────────────────────────────────────────────

    async def message_single_new(self, event):
        await self.send_event('message.single.new', data=event['data'])

    async def message_receipt_updated(self, event):
        await self.send_event('message.receipt.updated', data=event['data'])

    async def message_group_new(self, event):
        await self.send_event('message.group.new', data=event['data'])

    async def group_members_changed(self, event):
        await self.send_event('group.members.changed', data=event['data'])

    async def group_invitation_new(self, event):
        await self.send_event('group.invitation.new', data=event['data'])

    async def key_verification_new(self, event):
        await self.send_event('key.verification.new', data=event['data'])

    async def message_recalled(self, event):
        await self.send_event('message.recalled', data=event['data'])

    async def typing_indicator(self, event):
        await self.send_event('typing', data=event['data'])

    async def presence_updated(self, event):
        await self.send_event('presence.updated', data=event['data'])

    async def profile_updated(self, event):
        await self.send_event('profile.updated', data=event['data'])

    async def message_deleted(self, event):
        await self.send_event('message.deleted', data=event['data'])

    async def file_upload_completed(self, event):
        await self.send_event('file.upload.completed', data=event['data'])

    # ──── Helpers ──────────────────────────────────────────────────────────────────────────────────────────────────────────

    async def send_error(self, *, request_id, code, message):
        error = get_error(code)
        await self.send_event(
            'error',
            request_id=request_id,
            data={'code': code, 'message': message, 'retryable': error.retryable},
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

    # ──── T22: Presence helpers ────────────────────────────────────────────────────────────────────────────────

    @staticmethod
    async def broadcast_group_invitation(channel_layer, invitation_data, invitee_id):
        """Push group.invitation.new to the invitee's user channel."""
        await channel_layer.group_send(
            f'user_{invitee_id}',
            {
                'type': 'group.invitation.new',
                'data': invitation_data,
            },
        )

    @staticmethod
    async def broadcast_group_invitation_to_admins(channel_layer, group_id):
        """Push group.invitation.new to all admins/owner of the group."""
        admin_ids = await database_sync_to_async(list)(
            ConversationMember.objects.filter(
                conversation_id=group_id,
                status=ConversationMember.Status.ACTIVE,
                role__in=[ConversationMember.Role.OWNER, ConversationMember.Role.ADMIN],
            ).values_list('user_id', flat=True)
        )
        for user_id in admin_ids:
            await channel_layer.group_send(
                f'user_{user_id}',
                {
                    'type': 'group.invitation.new',
                    'data': {
                        'group_id': group_id,
                        'status': 'pending_admin',
                    },
                },
            )

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
            raise ClientPayloadError('conversation_forbidden', 'Not a member of this conversation.')

    # ──── Private message send (service delegation) ───────────────────────────────────────

    @classmethod
    @database_sync_to_async
    def send_private_message(cls, sender_id, data):
        """Run the unified messaging service inside a committed transaction.

        Returns a services.messaging.SendResult (created + message); callers
        own viewer serialization and post-commit broadcasting.
        """
        return messaging.send_private_message(sender_id, data)

    # ──── Private message receipt updates ──────────────────────────────────────────────────────────

    @classmethod
    @database_sync_to_async
    def update_private_message_status(cls, receiver_id, data):
        if not isinstance(data, dict):
            raise ClientPayloadError('invalid_payload', 'Message payload must be an object.')
        conversation_type = data.get('conversation_type')
        if conversation_type != 'single':
            raise ClientPayloadError('invalid_payload', 'conversation_type 必须为 single')
        status = data.get('status')
        if status not in {EncryptedMessage.Status.DELIVERED, EncryptedMessage.Status.READ}:
            raise ClientPayloadError('invalid_payload', 'status 必须为 delivered 或 read')
        message_id = messaging.require_positive_integer(data, 'message_id')
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
            status_changed = status_order.get(message.status, -1) < status_order[status]
            if status_changed:
                message.status = status
                message.save(update_fields=['status', 'updated_at'])
            if status == EncryptedMessage.Status.READ:
                member = ConversationMember.objects.filter(
                    conversation=message.conversation,
                    user_id=receiver_id,
                ).only('pk', 'unread_count', 'last_read_message_id').first()
                if member and (
                    member.unread_count != 0
                    or not member.last_read_message_id
                    or member.last_read_message_id < message.pk
                ):
                    member.unread_count = 0
                    member.last_read_message_id = message.pk
                    member.save(update_fields=['unread_count', 'last_read_message_id'])
        return {
            'conversation_type': 'single',
            'message_id': message.pk,
            'conversation_id': message.conversation_id,
            'sender_id': message.sender_id,
            'receiver_id': message.receiver_id,
            'user_id': receiver_id,
            'status': message.status,
        }

    # ──── T21: Group message receipt updates ──────────────────────────────────────────────────

    @classmethod
    @database_sync_to_async
    def update_group_message_status(cls, receiver_id, data):
        if not isinstance(data, dict):
            raise ClientPayloadError('invalid_payload', '消息数据格式错误')
        status = data.get('status')
        if status not in {GroupMessageRecipient.Status.DELIVERED,
                          GroupMessageRecipient.Status.READ}:
            raise ClientPayloadError('invalid_payload', 'status 必须为 delivered 或 read')
        message_id = messaging.require_positive_integer(data, 'message_id')
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
            status_changed = status_order.get(recipient.status, -1) < status_order[status]
            if status_changed:
                recipient.status = status
                recipient.save(update_fields=['status'])

            if status == GroupMessageRecipient.Status.READ:
                member = ConversationMember.objects.filter(
                    conversation_id=recipient.group_message.conversation_id,
                    user_id=receiver_id,
                ).only('pk', 'unread_count', 'last_read_message_id').first()
                if member and (
                    member.unread_count != 0
                    or not member.last_read_message_id
                    or member.last_read_message_id < message_id
                ):
                    member.unread_count = 0
                    member.last_read_message_id = message_id
                    member.save(update_fields=['unread_count', 'last_read_message_id'])

        return {
            'conversation_type': 'group',
            'message_id': recipient.group_message_id,
            'conversation_id': recipient.group_message.conversation_id,
            'sender_id': recipient.group_message.sender_id,
            'receiver_id': receiver_id,
            'user_id': receiver_id,
            'status': recipient.status,
        }

    # ──── T20: Message recall ────────────────────────────────────────────────────────────────────────────────

    recall_limit_minutes = 30

    @classmethod
    @database_sync_to_async
    def recall_message(cls, user_id, data):
        if not isinstance(data, dict):
            raise ClientPayloadError('invalid_payload', '消息数据格式错误')
        conversation_type = data.get('conversation_type', 'single')
        message_id = messaging.require_positive_integer(data, 'message_id')

        if conversation_type == 'single':
            return cls._recall_private_message(user_id, message_id)
        elif conversation_type == 'group':
            return cls._recall_group_message(user_id, message_id)
        else:
            raise ClientPayloadError('invalid_payload', 'conversation_type must be single or group')

    @classmethod
    def _recall_private_message(cls, user_id, message_id):
        with transaction.atomic():
            try:
                message = EncryptedMessage.objects.select_for_update().get(
                    pk=message_id,
                    sender_id=user_id,
                )
            except EncryptedMessage.DoesNotExist as error:
                raise ClientPayloadError('message_not_found', 'Message not found or cannot be recalled.') from error

            if message.status == EncryptedMessage.Status.RECALLED:
                raise ClientPayloadError('already_recalled', 'Message has already been recalled.')

            elapsed = (timezone.now() - message.created_at).total_seconds()
            if elapsed > cls.recall_limit_minutes * 60:
                raise ClientPayloadError('recall_timeout', f'Messages can only be recalled within {cls.recall_limit_minutes} minutes.')

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
                raise ClientPayloadError('message_not_found', 'Message not found or cannot be recalled.') from error

            if group_message.status == GroupMessage.Status.RECALLED:
                raise ClientPayloadError('already_recalled', 'Message has already been recalled.')

            elapsed = (timezone.now() - group_message.created_at).total_seconds()
            if elapsed > cls.recall_limit_minutes * 60:
                raise ClientPayloadError('recall_timeout', f'Messages can only be recalled within {cls.recall_limit_minutes} minutes.')

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

    # ──── Validation helpers (delegating to the service) ──────────────────────────────────

    @classmethod
    def validate_private_message(cls, data):
        return messaging.validate_private_message(data)

    @classmethod
    def validate_group_message(cls, data):
        return messaging.validate_group_message(data)

    # ──── Serialization ────────────────────────────────────────────────────────────────────────────────────────────

    @staticmethod
    def serialize_private_message(message, viewer_id=None):
        use_sender_copy = (
            viewer_id is not None
            and int(viewer_id) == message.sender_id
            and message.sender_copy_ciphertext
            and message.sender_copy_nonce
            and message.sender_copy_auth_tag
        )
        result = {
            'client_message_id': message.client_message_id,
            'message_id': message.pk,
            'conversation_id': message.conversation_id,
            'sender_id': message.sender_id,
            'receiver_id': message.receiver_id,
            'message_type': message.message_type,
            'ciphertext': message.sender_copy_ciphertext if use_sender_copy else message.ciphertext,
            'nonce': message.sender_copy_nonce if use_sender_copy else message.nonce,
            'auth_tag': message.sender_copy_auth_tag if use_sender_copy else message.auth_tag,
            'algorithm': message.algorithm,
            'sender_key_version': message.sender_key_version,
            'receiver_key_version': message.receiver_key_version,
            'sender_ephemeral_public_key': (
                message.sender_copy_ephemeral_public_key
                if use_sender_copy
                else message.sender_ephemeral_public_key
            ),
            'reply_to_message_id': message.reply_to_message_id,
            'status': message.status,
            'recalled_at': message.recalled_at.isoformat() if message.recalled_at else None,
            'created_at': message.created_at.isoformat(),
            'file_id': message.file_id_id,
        }
        # Attach file sub-object for file messages
        if message.file_id_id:
            file_holder_id = viewer_id if viewer_id is not None else message.receiver_id
            file_data = ChatConsumer._build_file_payload(message.file_id_id, file_holder_id)
            if file_data:
                result['file'] = file_data
        return result

    @staticmethod
    def serialize_private_message_by_id(message_id, viewer_id=None):
        message = EncryptedMessage.objects.get(pk=message_id)
        return ChatConsumer.serialize_private_message(message, viewer_id=viewer_id)

    @classmethod
    @database_sync_to_async
    def serialize_private_message_for_viewer(cls, message_id, viewer_id=None):
        return cls.serialize_private_message_by_id(message_id, viewer_id=viewer_id)

    # ──── Group message send (service delegation) ─────────────────────────────────────────

    @classmethod
    @database_sync_to_async
    def send_group_message(cls, sender_id, data):
        """Run the service and build the accepted/recipients payloads in the
        same sync context (they need ORM access)."""
        result = messaging.send_group_message(sender_id, data)
        accepted = cls._build_group_accepted(result.message)
        recipients_payload = (
            cls._build_recipients_payload(result.message, conversation=None)
            if result.created else None
        )
        return result, accepted, recipients_payload

    @classmethod
    def _build_group_accepted(cls, group_message):
        membership_version = None
        first = group_message.recipients.order_by("receiver_id").first()
        if first is not None:
            membership_version = first.membership_version
        return {
            "client_message_id": group_message.client_message_id,
            "message_id": group_message.pk,
            "group_id": group_message.conversation_id,
            "membership_version": membership_version or 0,
            "status": "sent",
            "created_at": group_message.created_at.isoformat(),
        }

    @classmethod
    def _build_recipients_payload(cls, group_message, conversation):
        recipients = GroupMessageRecipient.objects.filter(
            group_message=group_message,
        ).select_related('group_message__sender__profile')
        return [
            cls.serialize_group_recipient(r, viewer_id=r.receiver_id)
            for r in recipients
        ]

    @staticmethod
    def serialize_group_recipient(recipient, membership_version=None, viewer_id=None):
        mv = membership_version if membership_version is not None else (recipient.membership_version or 0)
        sender_name = ChatConsumer.display_name(recipient.group_message.sender)
        group_msg = recipient.group_message

        # Serve sender_copy when the viewer is the sender (multi-device support)
        is_sender = viewer_id is not None and int(viewer_id) == group_msg.sender_id
        use_sender_copy = (
            is_sender
            and group_msg.sender_copy_ciphertext
            and group_msg.sender_copy_nonce
            and group_msg.sender_copy_auth_tag
        )

        result = {
            'message_id': group_msg.id,
            'group_id': group_msg.conversation_id,
            'membership_version': mv or 0,
            'sender_id': group_msg.sender_id,
            'sender_username': group_msg.sender.username,
            'sender_name': sender_name,
            'sender_initials': ChatConsumer.initials(sender_name),
            'sender_avatar_color': ChatConsumer.avatar_color(sender_name),
            'sender_avatar_url': ChatConsumer.avatar_url(group_msg.sender),
            'receiver_id': recipient.receiver_id,
            'message_type': group_msg.message_type,
            'ciphertext': (
                group_msg.sender_copy_ciphertext if use_sender_copy
                else recipient.ciphertext
            ),
            'nonce': (
                group_msg.sender_copy_nonce if use_sender_copy
                else recipient.nonce
            ),
            'auth_tag': (
                group_msg.sender_copy_auth_tag if use_sender_copy
                else recipient.auth_tag
            ),
            'algorithm': recipient.algorithm,
            'sender_key_version': recipient.sender_key_version or 0,
            'receiver_key_version': recipient.receiver_key_version or 0,
            'sender_ephemeral_public_key': (
                group_msg.sender_copy_ephemeral_public_key if use_sender_copy
                else recipient.sender_ephemeral_public_key
            ),
            'reply_to_message_id': group_msg.reply_to_message_id,
            'status': recipient.status,
            'recalled_at': group_msg.recalled_at.isoformat() if group_msg.recalled_at else None,
            'created_at': group_msg.created_at.isoformat(),
            'file_id': group_msg.file_id_id,
        }
        # Attach file sub-object for file messages
        if group_msg.file_id_id:
            file_data = ChatConsumer._build_file_payload(group_msg.file_id_id, recipient.receiver_id)
            if file_data:
                result['file'] = file_data
        return result

    @staticmethod
    def _build_file_payload(file_id, holder_id):
        """Build the ``file`` sub-object for a serialized message.

        Called inside ``database_sync_to_async`` so ORM access is safe.
        Returns None if the file or its key cannot be found.
        """
        try:
            ef = EncryptedFile.objects.get(pk=file_id)
        except EncryptedFile.DoesNotExist:
            return None
        file_obj = {
            'file_id': ef.id,
            'conversation_id': ef.conversation_id,
            'message_kind': ef.message_kind,
            'chunk_count': ef.chunk_count,
            'total_size_bytes': ef.total_size_bytes,
            'algorithm': ef.algorithm,
            'encrypted_metadata': ef.encrypted_metadata,
            'metadata_nonce': ef.metadata_nonce,
            'metadata_auth_tag': ef.metadata_auth_tag,
            'ciphertext_sha256': ef.ciphertext_sha256,
        }
        fk = EncryptedFileKey.objects.filter(file=ef, holder_id=holder_id).first()
        if fk:
            file_obj['file_key'] = {
                'encrypted_file_key': fk.encrypted_file_key,
                'nonce': fk.nonce,
                'auth_tag': fk.auth_tag,
                'algorithm': fk.algorithm,
                'sender_key_version': fk.sender_key_version,
                'receiver_key_version': fk.receiver_key_version,
                'sender_ephemeral_public_key': fk.sender_ephemeral_public_key,
            }
        return file_obj

    @staticmethod
    def avatar_url(user):
        try:
            if user.profile and user.profile.avatar:
                timestamp = int(user.profile.updated_at.timestamp())
                return f"{user.profile.avatar.url}?t={timestamp}"
        except Exception:
            try:
                if user.profile and user.profile.avatar:
                    return user.profile.avatar.url
            except Exception:
                pass
        return ''

    @staticmethod
    def display_name(user):
        try:
            nickname = user.profile.nickname
        except Exception:
            nickname = ''
        return nickname or user.get_full_name() or user.username

    @staticmethod
    def initials(name):
        parts = name.strip().split()
        if len(parts) >= 2:
            return (parts[0][0] + parts[-1][0]).upper()
        return (name.strip()[:2] or '?').upper()

    @staticmethod
    def avatar_color(name):
        colors = [
            '#5c6bc0', '#26a69a', '#42a5f5', '#ffa726', '#ef5350',
            '#ab47bc', '#66bb6a', '#ec407a', '#8d6e63', '#78909c',
        ]
        checksum = sum(ord(char) for char in name)
        return colors[checksum % len(colors)]
