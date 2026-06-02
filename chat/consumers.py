from datetime import UTC, datetime

from channels.generic.websocket import AsyncJsonWebsocketConsumer


class ChatConsumer(AsyncJsonWebsocketConsumer):
    protocol_version = '1.0'
    heartbeat_interval_seconds = 30
    unauthenticated_close_code = 4401

    async def connect(self):
        user = self.scope['user']
        if user.is_anonymous:
            await self.close(code=self.unauthenticated_close_code)
            return

        self.user_group_name = f'user_{user.pk}'
        await self.channel_layer.group_add(
            self.user_group_name,
            self.channel_name,
        )
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
            await self.channel_layer.group_discard(
                user_group_name,
                self.channel_name,
            )

    async def receive(self, text_data=None, bytes_data=None, **kwargs):
        try:
            await super().receive(
                text_data=text_data,
                bytes_data=bytes_data,
                **kwargs,
            )
        except ValueError:
            await self.send_error(
                request_id=None,
                code='invalid_payload',
                message='消息格式错误',
            )

    async def receive_json(self, content, **kwargs):
        if not isinstance(content, dict):
            await self.send_error(
                request_id=None,
                code='invalid_payload',
                message='消息格式错误',
            )
            return

        request_id = content.get('request_id')
        event = content.get('event')

        if event == 'connection.ping':
            await self.send_event(
                'connection.pong',
                request_id=request_id,
                data={},
            )
            return

        await self.send_error(
            request_id=request_id,
            code='not_implemented',
            message='该实时通信事件尚未实现',
        )

    async def send_error(self, *, request_id, code, message):
        await self.send_event(
            'error',
            request_id=request_id,
            data={
                'code': code,
                'message': message,
                'retryable': False,
            },
        )

    async def send_event(self, event, *, data, request_id=None):
        await self.send_json({
            'protocol_version': self.protocol_version,
            'event': event,
            'request_id': request_id,
            'sent_at': datetime.now(UTC).isoformat().replace('+00:00', 'Z'),
            'data': data,
        })
