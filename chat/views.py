import json

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import F, Q
from django.http import JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_POST, require_GET

from accounts.models import Contact
from .consumers import ChatConsumer
from .models import (
    Conversation,
    ConversationMember,
    EncryptedMessage,
    GroupAnnouncement,
    GroupMessage,
    GroupMessageRecipient,
    UserMessageDeletion,
    UserPresence,
)

User = get_user_model()

AVATAR_COLORS = [
    '#5c6bc0', '#26a69a', '#42a5f5', '#ffa726', '#ef5350',
    '#ab47bc', '#66bb6a', '#ec407a', '#8d6e63', '#78909c',
]


def _broadcast_member_change(group_id, change, actor_id, affected_user_id, membership_version):
    """Sync wrapper around ChatConsumer.broadcast_group_members_changed."""
    channel_layer = get_channel_layer()
    async_to_sync(ChatConsumer.broadcast_group_members_changed)(
        channel_layer, group_id, change, actor_id, affected_user_id, membership_version,
    )


def _avatar_color(name: str) -> str:
    checksum = sum(ord(char) for char in name)
    return AVATAR_COLORS[checksum % len(AVATAR_COLORS)]


def _initials(name: str) -> str:
    parts = name.strip().split()
    if len(parts) >= 2:
        return (parts[0][0] + parts[-1][0]).upper()
    return (name.strip()[:2] or '?').upper()


def _display_name(user):
    try:
        nickname = user.profile.nickname
    except Exception:
        nickname = ''
    return nickname or user.get_full_name() or user.username


def _are_contacts(user, peer):
    return Contact.objects.filter(
        (Q(user=user) & Q(contact=peer))
        | (Q(user=peer) & Q(contact=user)),
    ).exists()


def _json_body(request):
    """Parse and return the JSON body of a request, or empty dict on error."""
    try:
        return json.loads(request.body.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return {}


def _get_active_member(conversation_id, user):
    """Return active ConversationMember or None."""
    try:
        return ConversationMember.objects.get(
            conversation_id=conversation_id,
            user=user,
            status=ConversationMember.Status.ACTIVE,
        )
    except ConversationMember.DoesNotExist:
        return None


@login_required(login_url='login')
def index_view(request):
    return render(request, 'pages/chat.html', {'open_settings': False})


@login_required(login_url='login')
def settings_view(request):
    return render(request, 'pages/chat.html', {'open_settings': True})


# ---------------------------------------------------------------------------
# Conversation list & creation API
# ---------------------------------------------------------------------------

@login_required(login_url='login')
def conversations_list_view(request):
    """Return active conversations for the authenticated user's sidebar."""
    filter_param = request.GET.get('filter', '')
    status_filter = Q(
        user=request.user,
        status=ConversationMember.Status.ACTIVE,
        conversation__status=Conversation.Status.ACTIVE,
    )

    if filter_param == 'archived':
        status_filter = Q(
            user=request.user,
            status=ConversationMember.Status.ACTIVE,
            archived_at__isnull=False,
            hidden_at__isnull=True,
        )
    elif filter_param == 'hidden':
        status_filter = Q(
            user=request.user,
            hidden_at__isnull=False,
        )
    else:
        # Default: exclude archived and hidden
        status_filter = Q(
            user=request.user,
            status=ConversationMember.Status.ACTIVE,
            conversation__status=Conversation.Status.ACTIVE,
            archived_at__isnull=True,
            hidden_at__isnull=True,
        )

    memberships = (
        ConversationMember.objects
        .filter(status_filter)
        .select_related('conversation', 'conversation__created_by')
        .order_by('-is_pinned', '-conversation__last_message_at', '-conversation__updated_at')
    )

    conversations = []
    for membership in memberships:
        conversation = membership.conversation
        is_muted = (
            membership.muted_until is not None
            and membership.muted_until > timezone.now()
        )
        item = {
            'id': conversation.id,
            'type': conversation.type,
            'unread': membership.unread_count,
            'last_message_at': (
                conversation.last_message_at.isoformat()
                if conversation.last_message_at
                else None
            ),
            'last_message_preview': 'Encrypted message' if conversation.last_message_at else '',
            'last_message_id': conversation.last_message_id,
            'is_pinned': membership.is_pinned,
            'is_muted': is_muted,
            'muted_until': membership.muted_until.isoformat() if membership.muted_until else None,
            'is_archived': membership.archived_at is not None,
            'cleared_at': membership.cleared_at.isoformat() if membership.cleared_at else None,
        }

        if conversation.type == Conversation.Type.SINGLE:
            peer_member = (
                ConversationMember.objects
                .filter(
                    conversation=conversation,
                    status=ConversationMember.Status.ACTIVE,
                )
                .exclude(user=request.user)
                .select_related('user__profile')
                .first()
            )
            if not peer_member:
                item.update({
                    'name': 'Unknown User',
                    'initials': '??',
                    'avatar_color': AVATAR_COLORS[0],
                    'peer_id': None,
                    'peer_username': None,
                    'is_secure': False,
                })
            else:
                peer = peer_member.user
                name = _display_name(peer)
                item.update({
                    'peer_id': peer.id,
                    'peer_username': peer.username,
                    'name': name,
                    'initials': _initials(name),
                    'avatar_color': _avatar_color(name),
                    'is_secure': peer.public_keys.filter(is_active=True).exists(),
                })
        else:
            name = conversation.name or f'Group #{conversation.id}'
            item.update({
                'name': name,
                'initials': _initials(name),
                'avatar_color': _avatar_color(name),
                'member_count': ConversationMember.objects.filter(
                    conversation=conversation,
                    status=ConversationMember.Status.ACTIVE,
                ).count(),
                'membership_version': conversation.membership_version,
                'is_secure': True,
            })

        conversations.append(item)

    return JsonResponse({'conversations': conversations})


@login_required(login_url='login')
def get_or_create_single_conversation_view(request):
    """Create or reuse a private conversation, limited to established contacts."""
    if request.method != 'POST':
        return JsonResponse({'error': 'Method not allowed.'}, status=405)

    peer_id = _json_body(request).get('peer_id')
    if not peer_id:
        return JsonResponse({'error': 'peer_id is required.'}, status=400)

    try:
        peer = User.objects.get(id=peer_id, is_active=True)
    except User.DoesNotExist:
        return JsonResponse({'error': 'User not found.'}, status=404)

    if peer == request.user:
        return JsonResponse({'error': 'Cannot chat with yourself.'}, status=400)
    if not _are_contacts(request.user, peer):
        return JsonResponse({'error': 'Private chats are limited to contacts.'}, status=403)

    my_conversation_ids = ConversationMember.objects.filter(
        user=request.user,
        status=ConversationMember.Status.ACTIVE,
        conversation__type=Conversation.Type.SINGLE,
        conversation__status=Conversation.Status.ACTIVE,
    ).values_list('conversation_id', flat=True)
    existing = (
        ConversationMember.objects
        .filter(
            user=peer,
            status=ConversationMember.Status.ACTIVE,
            conversation_id__in=my_conversation_ids,
        )
        .select_related('conversation')
        .first()
    )
    if existing:
        return JsonResponse({
            'conversation_id': existing.conversation_id,
            'created': False,
        })

    with transaction.atomic():
        conversation = Conversation.objects.create(
            type=Conversation.Type.SINGLE,
            created_by=request.user,
        )
        ConversationMember.objects.bulk_create([
            ConversationMember(
                conversation=conversation,
                user=request.user,
                role=ConversationMember.Role.MEMBER,
            ),
            ConversationMember(
                conversation=conversation,
                user=peer,
                role=ConversationMember.Role.MEMBER,
            ),
        ])

    return JsonResponse({
        'conversation_id': conversation.id,
        'created': True,
    }, status=201)


# ---------------------------------------------------------------------------
# T19: Conversation management API
# ---------------------------------------------------------------------------

@login_required(login_url='login')
def pin_conversation_view(request, conversation_id):
    """Toggle pin on a conversation. POST to pin, DELETE to unpin."""
    member = _get_active_member(conversation_id, request.user)
    if not member:
        return JsonResponse({'error': 'Conversation not found or not a member.'}, status=404)

    if request.method == 'POST':
        member.is_pinned = True
        member.save(update_fields=['is_pinned'])
        return JsonResponse({'status': 'ok', 'is_pinned': True})
    elif request.method == 'DELETE':
        member.is_pinned = False
        member.save(update_fields=['is_pinned'])
        return JsonResponse({'status': 'ok', 'is_pinned': False})
    return JsonResponse({'error': 'Method not allowed.'}, status=405)


@login_required(login_url='login')
def mute_conversation_view(request, conversation_id):
    """Mute or unmute a conversation. POST with duration_minutes to mute, DELETE to unmute."""
    member = _get_active_member(conversation_id, request.user)
    if not member:
        return JsonResponse({'error': 'Conversation not found or not a member.'}, status=404)

    if request.method == 'POST':
        data = _json_body(request)
        duration_minutes = int(data.get('duration_minutes', 60))
        duration_minutes = min(duration_minutes, 10080)  # max 7 days
        member.muted_until = timezone.now() + timezone.timedelta(minutes=duration_minutes)
        member.save(update_fields=['muted_until'])
        return JsonResponse({
            'status': 'ok',
            'muted_until': member.muted_until.isoformat(),
        })
    elif request.method == 'DELETE':
        member.muted_until = None
        member.save(update_fields=['muted_until'])
        return JsonResponse({'status': 'ok', 'muted_until': None})
    return JsonResponse({'error': 'Method not allowed.'}, status=405)


@login_required(login_url='login')
@require_POST
def archive_conversation_view(request, conversation_id):
    """Archive a conversation."""
    member = _get_active_member(conversation_id, request.user)
    if not member:
        return JsonResponse({'error': 'Conversation not found or not a member.'}, status=404)

    member.archived_at = timezone.now()
    member.save(update_fields=['archived_at'])
    return JsonResponse({'status': 'ok', 'archived_at': member.archived_at.isoformat()})


@login_required(login_url='login')
@require_POST
def unarchive_conversation_view(request, conversation_id):
    """Unarchive a conversation."""
    member = _get_active_member(conversation_id, request.user)
    if not member:
        return JsonResponse({'error': 'Conversation not found or not a member.'}, status=404)

    member.archived_at = None
    member.save(update_fields=['archived_at'])
    return JsonResponse({'status': 'ok', 'archived_at': None})


@login_required(login_url='login')
def hide_conversation_view(request, conversation_id):
    """Soft-hide a conversation for the current user only (DELETE)."""
    if request.method != 'DELETE':
        return JsonResponse({'error': 'Method not allowed.'}, status=405)

    member = _get_active_member(conversation_id, request.user)
    if not member:
        return JsonResponse({'error': 'Conversation not found or not a member.'}, status=404)

    member.hidden_at = timezone.now()
    member.save(update_fields=['hidden_at'])
    return JsonResponse({'status': 'ok', 'hidden_at': member.hidden_at.isoformat()})


@login_required(login_url='login')
@require_POST
def clear_conversation_view(request, conversation_id):
    """Clear chat history for the current user (sets cleared_at)."""
    member = _get_active_member(conversation_id, request.user)
    if not member:
        return JsonResponse({'error': 'Conversation not found or not a member.'}, status=404)

    now = timezone.now()
    member.cleared_at = now
    member.unread_count = 0
    member.save(update_fields=['cleared_at', 'unread_count'])
    return JsonResponse({'status': 'ok', 'cleared_at': now.isoformat()})


@login_required(login_url='login')
@require_POST
def read_conversation_view(request, conversation_id):
    """Mark a conversation as read (reset unread_count, update last_read_message_id)."""
    member = _get_active_member(conversation_id, request.user)
    if not member:
        return JsonResponse({'error': 'Conversation not found or not a member.'}, status=404)

    conversation = member.conversation
    member.unread_count = 0
    if conversation.last_message_id:
        member.last_read_message_id = conversation.last_message_id
    member.save(update_fields=['unread_count', 'last_read_message_id'])
    return JsonResponse({
        'status': 'ok',
        'unread_count': 0,
        'last_read_message_id': member.last_read_message_id,
    })


@login_required(login_url='login')
@require_POST
def unread_conversation_view(request, conversation_id):
    """Mark a conversation as unread."""
    member = _get_active_member(conversation_id, request.user)
    if not member:
        return JsonResponse({'error': 'Conversation not found or not a member.'}, status=404)

    data = _json_body(request)
    count = int(data.get('unread_count', 1))
    member.unread_count = max(1, min(count, 99))
    member.save(update_fields=['unread_count'])
    return JsonResponse({'status': 'ok', 'unread_count': member.unread_count})


# ---------------------------------------------------------------------------
# T20: Message operations API
# ---------------------------------------------------------------------------

@login_required(login_url='login')
@require_POST
def forward_message_view(request, conversation_id):
    """Forward an encrypted message to the target conversation.

    The client decrypts the original and re-encrypts for the target.
    The server only stores ciphertext (E2EE preserved).
    Accepts the same payload as a normal send but includes metadata about
    the original message for the UI.
    """
    data = _json_body(request)
    original_message_id = data.get('original_message_id')
    original_conversation_id = data.get('original_conversation_id')

    # Validate sender is member of target conversation
    member = _get_active_member(conversation_id, request.user)
    if not member:
        return JsonResponse({'error': 'Target conversation not found or not a member.'}, status=404)

    # For private chat targets
    conversation = member.conversation
    if conversation.type == Conversation.Type.SINGLE:
        peer_id = data.get('peer_id')
        if not peer_id:
            return JsonResponse({'error': 'peer_id is required for private chat.'}, status=400)

        try:
            EncryptedMessage.objects.create(
                conversation=conversation,
                sender=request.user,
                receiver_id=peer_id,
                message_type=data.get('message_type', EncryptedMessage.MessageType.TEXT),
                ciphertext=data.get('ciphertext', ''),
                nonce=data.get('nonce', ''),
                auth_tag=data.get('auth_tag', ''),
                algorithm=data.get('algorithm', 'AES-256-GCM'),
                sender_key_version=data.get('sender_key_version'),
                receiver_key_version=data.get('receiver_key_version'),
                client_message_id=data.get('client_message_id', ''),
                reply_to_message_id=original_message_id,
            )
            conversation.last_message_id = EncryptedMessage.objects.filter(
                conversation=conversation,
            ).latest('created_at').pk
            conversation.last_message_at = timezone.now()
            conversation.save(update_fields=['last_message_id', 'last_message_at', 'updated_at'])

            # Increment unread count for the peer
            ConversationMember.objects.filter(
                conversation=conversation,
                user_id=peer_id,
                status=ConversationMember.Status.ACTIVE,
            ).update(unread_count=F('unread_count') + 1)

            return JsonResponse({'status': 'ok', 'conversation_id': conversation.id}, status=201)
        except (ValueError, KeyError) as e:
            return JsonResponse({'error': f'Invalid payload: {e}'}, status=400)

    # For group chat targets
    elif conversation.type == Conversation.Type.GROUP:
        group_message = GroupMessage.objects.create(
            conversation=conversation,
            sender=request.user,
            message_type=data.get('message_type', GroupMessage.MessageType.TEXT),
            client_message_id=data.get('client_message_id', ''),
            reply_to_message_id=original_message_id,
        )
        recipients = data.get('recipients', [])
        if recipients:
            recipient_objs = [
                GroupMessageRecipient(
                    group_message=group_message,
                    receiver_id=r['receiver_id'],
                    ciphertext=r.get('ciphertext', ''),
                    nonce=r.get('nonce', ''),
                    auth_tag=r.get('auth_tag', ''),
                    algorithm=r.get('algorithm', 'AES-256-GCM'),
                    sender_key_version=r.get('sender_key_version'),
                    receiver_key_version=r.get('receiver_key_version'),
                )
                for r in recipients
            ]
            GroupMessageRecipient.objects.bulk_create(recipient_objs)

            active_members = ConversationMember.objects.filter(
                conversation=conversation,
                status=ConversationMember.Status.ACTIVE,
            )
            active_members.exclude(user=request.user).update(
                unread_count=F('unread_count') + 1,
            )

        conversation.last_message_id = group_message.pk
        conversation.last_message_at = group_message.created_at
        conversation.save(update_fields=['last_message_id', 'last_message_at', 'updated_at'])

        return JsonResponse({'status': 'ok', 'conversation_id': conversation.id}, status=201)

    return JsonResponse({'error': 'Invalid conversation type.'}, status=400)


@login_required(login_url='login')
def delete_message_view(request, conversation_id, message_id):
    """Per-user soft-delete a message. Only affects the requesting user's view."""
    if request.method != 'DELETE':
        return JsonResponse({'error': 'Method not allowed.'}, status=405)

    member = _get_active_member(conversation_id, request.user)
    if not member:
        return JsonResponse({'error': 'Conversation not found or not a member.'}, status=404)

    conversation = member.conversation
    if conversation.type == Conversation.Type.SINGLE:
        try:
            EncryptedMessage.objects.get(pk=message_id, conversation_id=conversation_id)
        except EncryptedMessage.DoesNotExist:
            return JsonResponse({'error': 'Message not found.'}, status=404)
        message_type = UserMessageDeletion.MessageType.PRIVATE
    else:
        try:
            GroupMessage.objects.get(pk=message_id, conversation_id=conversation_id)
        except GroupMessage.DoesNotExist:
            return JsonResponse({'error': 'Message not found.'}, status=404)
        message_type = UserMessageDeletion.MessageType.GROUP

    _, created = UserMessageDeletion.objects.get_or_create(
        user=request.user,
        message_type=message_type,
        message_id=message_id,
        defaults={'conversation': conversation},
    )

    # Notify the user's own sessions via WebSocket
    channel_layer = get_channel_layer()
    async_to_sync(channel_layer.group_send)(
        f'user_{request.user.pk}',
        {
            'type': 'message.deleted',
            'data': {
                'conversation_id': conversation_id,
                'message_id': message_id,
                'message_type': message_type,
            },
        },
    )

    return JsonResponse({
        'status': 'ok',
        'created': created,
        'message_id': message_id,
    })


RECALL_LIMIT_MINUTES = 30


@login_required(login_url='login')
@require_POST
def recall_message_view(request, conversation_id, message_id):
    """Recall a sent message. Sender only, 30-minute time limit."""
    member = _get_active_member(conversation_id, request.user)
    if not member:
        return JsonResponse({'error': 'Conversation not found or not a member.'}, status=404)

    conversation = member.conversation

    if conversation.type == Conversation.Type.SINGLE:
        try:
            message = EncryptedMessage.objects.get(pk=message_id, conversation_id=conversation_id)
        except EncryptedMessage.DoesNotExist:
            return JsonResponse({'error': 'Message not found.'}, status=404)

        if message.sender_id != request.user.pk:
            return JsonResponse({'error': 'Only the sender can recall this message.'}, status=403)
        if message.status == EncryptedMessage.Status.RECALLED:
            return JsonResponse({'error': 'Message already recalled.'}, status=409)

        elapsed = (timezone.now() - message.created_at).total_seconds()
        if elapsed > RECALL_LIMIT_MINUTES * 60:
            return JsonResponse(
                {'error': f'Recall time limit exceeded ({RECALL_LIMIT_MINUTES} minutes).'},
                status=400,
            )

        message.status = EncryptedMessage.Status.RECALLED
        message.recalled_at = timezone.now()
        message.save(update_fields=['status', 'recalled_at', 'updated_at'])

        # Broadcast recall via WebSocket
        channel_layer = get_channel_layer()
        recall_data = {
            'conversation_type': 'single',
            'message_id': message.pk,
            'conversation_id': conversation_id,
            'sender_id': message.sender_id,
            'other_user_id': message.receiver_id,
            'recalled_at': message.recalled_at.isoformat(),
        }
        for uid in (message.sender_id, message.receiver_id):
            async_to_sync(channel_layer.group_send)(
                f'user_{uid}',
                {'type': 'message.recalled', 'data': recall_data},
            )

        return JsonResponse({
            'status': 'recalled',
            'message_id': message.pk,
            'recalled_at': message.recalled_at.isoformat(),
        })

    else:  # Group
        try:
            group_message = GroupMessage.objects.select_for_update().get(
                pk=message_id, conversation_id=conversation_id,
            )
        except GroupMessage.DoesNotExist:
            return JsonResponse({'error': 'Message not found.'}, status=404)

        if group_message.sender_id != request.user.pk:
            return JsonResponse({'error': 'Only the sender can recall this message.'}, status=403)
        if group_message.status == GroupMessage.Status.RECALLED:
            return JsonResponse({'error': 'Message already recalled.'}, status=409)

        elapsed = (timezone.now() - group_message.created_at).total_seconds()
        if elapsed > RECALL_LIMIT_MINUTES * 60:
            return JsonResponse(
                {'error': f'Recall time limit exceeded ({RECALL_LIMIT_MINUTES} minutes).'},
                status=400,
            )

        group_message.status = GroupMessage.Status.RECALLED
        group_message.recalled_at = timezone.now()
        group_message.save(update_fields=['status', 'recalled_at', 'updated_at'])
        GroupMessageRecipient.objects.filter(
            group_message=group_message,
        ).update(status=GroupMessageRecipient.Status.RECALLED)

        # Broadcast to all active group members
        channel_layer = get_channel_layer()
        member_ids = list(
            ConversationMember.objects.filter(
                conversation_id=conversation_id,
                status=ConversationMember.Status.ACTIVE,
            ).values_list('user_id', flat=True)
        )
        recall_data = {
            'conversation_type': 'group',
            'message_id': group_message.pk,
            'conversation_id': conversation_id,
            'sender_id': group_message.sender_id,
            'recalled_at': group_message.recalled_at.isoformat(),
        }
        for uid in member_ids:
            async_to_sync(channel_layer.group_send)(
                f'user_{uid}',
                {'type': 'message.recalled', 'data': recall_data},
            )

        return JsonResponse({
            'status': 'recalled',
            'message_id': group_message.pk,
            'recalled_at': group_message.recalled_at.isoformat(),
        })


@login_required(login_url='login')
@require_GET
def message_status_view(request, conversation_id, message_id):
    """Query the delivery/read status of a message."""
    member = _get_active_member(conversation_id, request.user)
    if not member:
        return JsonResponse({'error': 'Conversation not found or not a member.'}, status=404)

    conversation = member.conversation

    if conversation.type == Conversation.Type.SINGLE:
        try:
            message = EncryptedMessage.objects.get(pk=message_id, conversation_id=conversation_id)
        except EncryptedMessage.DoesNotExist:
            return JsonResponse({'error': 'Message not found.'}, status=404)

        if request.user.pk not in (message.sender_id, message.receiver_id):
            return JsonResponse({'error': 'Permission denied.'}, status=403)

        return JsonResponse({
            'message_id': message.pk,
            'conversation_type': 'single',
            'status': message.status,
            'sender_id': message.sender_id,
            'receiver_id': message.receiver_id,
            'created_at': message.created_at.isoformat(),
            'updated_at': message.updated_at.isoformat(),
        })
    else:
        try:
            group_message = GroupMessage.objects.get(pk=message_id, conversation_id=conversation_id)
        except GroupMessage.DoesNotExist:
            return JsonResponse({'error': 'Message not found.'}, status=404)

        recipients = GroupMessageRecipient.objects.filter(
            group_message=group_message,
        ).values_list('receiver_id', 'status')

        return JsonResponse({
            'message_id': group_message.pk,
            'conversation_type': 'group',
            'status': group_message.status,
            'sender_id': group_message.sender_id,
            'created_at': group_message.created_at.isoformat(),
            'recipients': [
                {'user_id': uid, 'status': s}
                for uid, s in recipients
            ],
        })


# ---------------------------------------------------------------------------
# Group chat management API
# ---------------------------------------------------------------------------

def _get_member(conversation_id, user):
    """Return the ConversationMember instance or the user_id if user is int."""
    try:
        return ConversationMember.objects.get(
            conversation_id=conversation_id, user=user
        )
    except ConversationMember.DoesNotExist:
        return None


@login_required(login_url='login')
def create_group_view(request):
    """Create a group conversation. Creator becomes owner automatically.
    T32: Accepts optional initial_member_ids list for member pre-selection."""
    if request.method != "POST":
        return JsonResponse({"error": "Method not allowed."}, status=405)

    data = _json_body(request)
    name = data.get("name", "").strip()
    if not name:
        return JsonResponse({"error": "Group name is required."}, status=400)

    with transaction.atomic():
        conversation = Conversation.objects.create(
            type=Conversation.Type.GROUP,
            name=name,
            avatar=data.get("avatar", "").strip(),
            created_by=request.user,
        )
        ConversationMember.objects.create(
            conversation=conversation,
            user=request.user,
            role=ConversationMember.Role.OWNER,
        )

        # T32: Add initial members (contacts only)
        initial_ids = data.get("initial_member_ids", [])
        if isinstance(initial_ids, list):
            # Deduplicate and exclude self
            unique_ids = list(dict.fromkeys(
                uid for uid in initial_ids
                if isinstance(uid, int) and uid != request.user.pk
            ))
            valid_users = User.objects.filter(
                id__in=unique_ids, is_active=True,
            ).values_list('id', flat=True)
            members_to_create = [
                ConversationMember(
                    conversation=conversation,
                    user_id=uid,
                    role=ConversationMember.Role.MEMBER,
                )
                for uid in valid_users
            ]
            if members_to_create:
                ConversationMember.objects.bulk_create(members_to_create)

    return JsonResponse({
        "id": conversation.id,
        "name": conversation.name,
        "type": conversation.type,
        "created_at": conversation.created_at.isoformat(),
        "member_count": ConversationMember.objects.filter(
            conversation=conversation, status=ConversationMember.Status.ACTIVE,
        ).count(),
    }, status=201)


@login_required(login_url='login')
def update_group_view(request, conversation_id):
    """Update group name / avatar. Owner only."""
    if request.method != "PUT":
        return JsonResponse({"error": "Method not allowed."}, status=405)

    member = _get_member(conversation_id, request.user)
    if not member or member.role != ConversationMember.Role.OWNER:
        return JsonResponse({"error": "Only the group owner can update the group."}, status=403)

    try:
        conversation = Conversation.objects.get(
            id=conversation_id, type=Conversation.Type.GROUP
        )
    except Conversation.DoesNotExist:
        return JsonResponse({"error": "Group not found."}, status=404)

    data = _json_body(request)
    if "name" in data:
        conversation.name = data["name"].strip() or conversation.name
    if "avatar" in data:
        conversation.avatar = data["avatar"].strip()
    conversation.save(update_fields=["name", "avatar", "updated_at"])

    return JsonResponse({
        "id": conversation.id,
        "name": conversation.name,
        "avatar": conversation.avatar,
    })


@login_required(login_url='login')
def invite_member_view(request, conversation_id):
    """Invite a user to a group. Owner / admin only."""
    if request.method != "POST":
        return JsonResponse({"error": "Method not allowed."}, status=405)

    actor = _get_member(conversation_id, request.user)
    if not actor or actor.role not in (ConversationMember.Role.OWNER, ConversationMember.Role.ADMIN):
        return JsonResponse({"error": "Permission denied."}, status=403)

    try:
        conversation = Conversation.objects.get(
            id=conversation_id, type=Conversation.Type.GROUP
        )
    except Conversation.DoesNotExist:
        return JsonResponse({"error": "Group not found."}, status=404)

    data = _json_body(request)
    user_id = data.get("user_id")
    if not user_id:
        return JsonResponse({"error": "user_id is required."}, status=400)

    try:
        target = User.objects.get(id=user_id)
    except User.DoesNotExist:
        return JsonResponse({"error": "User not found."}, status=404)

    if ConversationMember.objects.filter(
        conversation=conversation, user=target
    ).exists():
        return JsonResponse({"error": "User is already a member."}, status=409)

    ConversationMember.objects.create(
        conversation=conversation,
        user=target,
        role=ConversationMember.Role.MEMBER,
    )
    conversation.membership_version = F('membership_version') + 1
    conversation.save(update_fields=['membership_version', 'updated_at'])
    conversation.refresh_from_db(fields=['membership_version'])
    _broadcast_member_change(
        group_id=conversation.pk,
        change='member_added',
        actor_id=request.user.pk,
        affected_user_id=target.id,
        membership_version=conversation.membership_version,
    )

    return JsonResponse({"status": "ok", "user_id": target.id}, status=201)


@login_required(login_url='login')
def remove_member_view(request, conversation_id):
    """Remove a member from a group. Owner / admin only. Cannot remove owner."""
    if request.method != "POST":
        return JsonResponse({"error": "Method not allowed."}, status=405)

    actor = _get_member(conversation_id, request.user)
    if not actor or actor.role not in (ConversationMember.Role.OWNER, ConversationMember.Role.ADMIN):
        return JsonResponse({"error": "Permission denied."}, status=403)

    data = _json_body(request)
    user_id = data.get("user_id")
    if not user_id:
        return JsonResponse({"error": "user_id is required."}, status=400)

    target_member = _get_member(conversation_id, user_id)
    if not target_member:
        return JsonResponse({"error": "User is not a member."}, status=404)

    if target_member.role == ConversationMember.Role.OWNER:
        return JsonResponse({"error": "Cannot remove the group owner."}, status=403)

    target_member.status = ConversationMember.Status.REMOVED
    target_member.left_at = timezone.now()
    target_member.save(update_fields=["status", "left_at"])

    try:
        conversation = Conversation.objects.get(
            id=conversation_id, type=Conversation.Type.GROUP
        )
    except Conversation.DoesNotExist:
        return JsonResponse({"error": "Group not found."}, status=404)
    conversation.membership_version = F('membership_version') + 1
    conversation.save(update_fields=['membership_version', 'updated_at'])
    conversation.refresh_from_db(fields=['membership_version'])
    _broadcast_member_change(
        group_id=conversation.pk,
        change='member_removed',
        actor_id=request.user.pk,
        affected_user_id=user_id,
        membership_version=conversation.membership_version,
    )

    return JsonResponse({"status": "ok", "user_id": user_id})


@login_required(login_url='login')
def disband_group_view(request, conversation_id):
    """Disband a group. Owner only."""
    if request.method != "POST":
        return JsonResponse({"error": "Method not allowed."}, status=405)

    member = _get_member(conversation_id, request.user)
    if not member or member.role != ConversationMember.Role.OWNER:
        return JsonResponse({"error": "Only the group owner can disband the group."}, status=403)

    try:
        conversation = Conversation.objects.get(
            id=conversation_id, type=Conversation.Type.GROUP
        )
    except Conversation.DoesNotExist:
        return JsonResponse({"error": "Group not found."}, status=404)

    conversation.status = Conversation.Status.DELETED
    conversation.membership_version = F('membership_version') + 1
    conversation.save(update_fields=["status", "membership_version", "updated_at"])
    conversation.refresh_from_db(fields=['membership_version'])
    _broadcast_member_change(
        group_id=conversation.pk,
        change='group_dissolved',
        actor_id=request.user.pk,
        affected_user_id=None,
        membership_version=conversation.membership_version,
    )

    return JsonResponse({"status": "ok"})


# ---------------------------------------------------------------------------
# Group member detail API
# ---------------------------------------------------------------------------

@login_required(login_url='login')
def group_members_view(request, conversation_id):
    """Return active group members and the current membership_version.

    Only active group members may access this endpoint.
    """
    member = _get_member(conversation_id, request.user)
    if not member or member.status != ConversationMember.Status.ACTIVE:
        return JsonResponse(
            {"error": "You are not a member of this group."},
            status=403,
        )

    try:
        conversation = Conversation.objects.get(
            id=conversation_id, type=Conversation.Type.GROUP
        )
    except Conversation.DoesNotExist:
        return JsonResponse({"error": "Group not found."}, status=404)

    active_members = ConversationMember.objects.filter(
        conversation=conversation,
        status=ConversationMember.Status.ACTIVE,
    ).select_related('user')

    return JsonResponse({
        "group_id": conversation.id,
        "membership_version": conversation.membership_version,
        "members": [
            {"user_id": m.user_id, "role": m.role}
            for m in active_members
        ],
    })


# ---------------------------------------------------------------------------
# Group message history API
# ---------------------------------------------------------------------------

@login_required(login_url='login')
def group_messages_view(request, conversation_id):
    """Return paginated group messages for the current user.

    Only group members may read messages.  Messages created before the
    user joined are excluded so that new members cannot see history
    from before they were added.
    """
    member = _get_member(conversation_id, request.user)
    if not member or member.status != ConversationMember.Status.ACTIVE:
        return JsonResponse(
            {"error": "You are not a member of this group."},
            status=403,
        )

    page_number = request.GET.get("page", 1)
    per_page = min(int(request.GET.get("per_page", 30)), 100)

    # Filter out deleted messages for this user
    deleted_ids = set(
        UserMessageDeletion.objects.filter(
            user=request.user,
            conversation_id=conversation_id,
            message_type=UserMessageDeletion.MessageType.GROUP,
        ).values_list('message_id', flat=True)
    )

    joined_at = member.joined_at
    recipient_queryset = (
        GroupMessageRecipient.objects.filter(
            receiver=request.user,
            group_message__conversation_id=conversation_id,
            group_message__created_at__gte=joined_at,
        )
        .select_related("group_message")
        .order_by("-group_message__created_at")
    )

    # Apply cleared_at filter
    if member.cleared_at:
        recipient_queryset = recipient_queryset.filter(
            group_message__created_at__gte=member.cleared_at,
        )

    # Apply user message deletion filter
    if deleted_ids:
        recipient_queryset = recipient_queryset.exclude(
            group_message_id__in=deleted_ids,
        )

    paginator = Paginator(recipient_queryset, per_page)
    page_obj = paginator.get_page(page_number)

    messages_data = [
        {
            "id": r.group_message.id,
            "sender_id": r.group_message.sender_id,
            "message_type": r.group_message.message_type,
            "ciphertext": r.ciphertext,
            "nonce": r.nonce,
            "auth_tag": r.auth_tag,
            "algorithm": r.algorithm,
            "sender_key_version": r.sender_key_version,
            "receiver_key_version": r.receiver_key_version,
            "reply_to_message_id": r.group_message.reply_to_message_id,
            "membership_version": r.membership_version,
            "status": r.status,
            "recalled_at": r.group_message.recalled_at.isoformat() if r.group_message.recalled_at else None,
            "created_at": r.group_message.created_at.isoformat(),
        }
        for r in page_obj
    ]

    return JsonResponse({
        "conversation_id": conversation_id,
        "page": page_obj.number,
        "total_pages": paginator.num_pages,
        "total_messages": paginator.count,
        "has_next": page_obj.has_next(),
        "has_previous": page_obj.has_previous(),
        "messages": messages_data,
    })


# ---------------------------------------------------------------------------
# Private chat history API
# ---------------------------------------------------------------------------

@login_required(login_url='login')
def conversation_messages_view(request, conversation_id):
    """Return paginated encrypted messages for a conversation.

    Only conversation participants may access the history.
    Messages are ordered newest-first so the frontend can
    load the most recent page by default.
    """
    if not ConversationMember.objects.filter(
        conversation_id=conversation_id,
        user=request.user,
        status=ConversationMember.Status.ACTIVE,
    ).exists():
        return JsonResponse(
            {"error": "You are not a participant of this conversation."},
            status=403,
        )

    # For private chats, enforce contact relationship (T29).
    conversation = Conversation.objects.only("type").get(id=conversation_id)
    member = _get_active_member(conversation_id, request.user)
    if conversation.type == Conversation.Type.SINGLE:
        peer_id = (
            ConversationMember.objects
            .filter(conversation_id=conversation_id, status=ConversationMember.Status.ACTIVE)
            .exclude(user=request.user)
            .values_list("user_id", flat=True)
            .first()
        )
        if peer_id and not _are_contacts(request.user, peer_id):
            return JsonResponse(
                {"error": "Private chats are limited to contacts."},
                status=403,
            )

    page_number = request.GET.get("page", 1)
    per_page = min(int(request.GET.get("per_page", 30)), 100)

    # Filter out deleted messages for this user
    deleted_ids = set(
        UserMessageDeletion.objects.filter(
            user=request.user,
            conversation_id=conversation_id,
            message_type=UserMessageDeletion.MessageType.PRIVATE,
        ).values_list('message_id', flat=True)
    )

    queryset = (
        EncryptedMessage.objects
        .filter(conversation_id=conversation_id)
        .order_by("-created_at")
    )

    # Apply cleared_at filter
    if member and member.cleared_at:
        queryset = queryset.filter(created_at__gte=member.cleared_at)

    # Apply user message deletion filter
    if deleted_ids:
        queryset = queryset.exclude(id__in=deleted_ids)

    paginator = Paginator(queryset, per_page)
    page_obj = paginator.get_page(page_number)

    messages_data = [
        {
            "id": msg.id,
            "sender_id": msg.sender_id,
            "receiver_id": msg.receiver_id,
            "message_type": msg.message_type,
            "ciphertext": msg.ciphertext,
            "nonce": msg.nonce,
            "auth_tag": msg.auth_tag,
            "algorithm": msg.algorithm,
            "sender_key_version": msg.sender_key_version,
            "receiver_key_version": msg.receiver_key_version,
            "reply_to_message_id": msg.reply_to_message_id,
            "status": msg.status,
            "recalled_at": msg.recalled_at.isoformat() if msg.recalled_at else None,
            "created_at": msg.created_at.isoformat(),
        }
        for msg in page_obj
    ]

    return JsonResponse({
        "conversation_id": conversation_id,
        "page": page_obj.number,
        "total_pages": paginator.num_pages,
        "total_messages": paginator.count,
        "has_next": page_obj.has_next(),
        "has_previous": page_obj.has_previous(),
        "messages": messages_data,
    })


# ---------------------------------------------------------------------------
# T22: Presence API
# ---------------------------------------------------------------------------

@login_required(login_url='login')
@require_GET
def user_presence_view(request, user_id):
    """Query another user's presence (respects visibility settings)."""
    try:
        target = User.objects.get(id=user_id, is_active=True)
    except User.DoesNotExist:
        return JsonResponse({'error': 'User not found.'}, status=404)

    try:
        presence = target.presence
    except UserPresence.DoesNotExist:
        return JsonResponse({
            'user_id': target.pk,
            'is_online': False,
            'last_seen': None,
            'status': 'offline',
        })

    # Self-query always returns full data
    if request.user.pk == target.pk:
        return JsonResponse({
            'user_id': target.pk,
            'is_online': presence.is_online,
            'last_seen': presence.last_seen.isoformat() if presence.last_seen else None,
            'status': presence.status,
            'presence_visibility': presence.presence_visibility,
        })

    # Apply visibility rules
    if presence.presence_visibility == UserPresence.Visibility.NOBODY:
        return JsonResponse({
            'user_id': target.pk,
            'is_online': False,
            'last_seen': None,
            'status': 'offline',
        })

    if presence.presence_visibility == UserPresence.Visibility.CONTACTS:
        if not _are_contacts(request.user, target):
            return JsonResponse({
                'user_id': target.pk,
                'is_online': False,
                'last_seen': None,
                'status': 'offline',
            })

    # Everyone or contact: return full data
    return JsonResponse({
        'user_id': target.pk,
        'is_online': presence.is_online,
        'last_seen': presence.last_seen.isoformat() if presence.last_seen else None,
        'status': presence.status,
    })


@login_required(login_url='login')
def update_presence_view(request):
    """Update the current user's presence status and visibility."""
    if request.method != 'PUT':
        return JsonResponse({'error': 'Method not allowed.'}, status=405)

    data = _json_body(request)
    presence, _ = UserPresence.objects.get_or_create(user=request.user)

    status = data.get('status')
    if status and status in UserPresence.Status.values:
        presence.status = status
        if status == UserPresence.Status.OFFLINE:
            presence.is_online = False
            presence.last_seen = timezone.now()
        elif status == UserPresence.Status.ONLINE:
            presence.is_online = True

    visibility = data.get('presence_visibility')
    if visibility and visibility in UserPresence.Visibility.values:
        presence.presence_visibility = visibility

    presence.save(update_fields=[
        f for f in ['status', 'presence_visibility', 'is_online', 'last_seen', 'updated_at']
        if data.get(f) or status or visibility
    ] + ['updated_at'])

    # Broadcast presence change
    channel_layer = get_channel_layer()
    async_to_sync(channel_layer.group_send)(
        f'user_{request.user.pk}',
        {
            'type': 'presence.updated',
            'data': {
                'user_id': request.user.pk,
                'is_online': presence.is_online,
                'status': presence.status,
                'last_seen': presence.last_seen.isoformat() if presence.last_seen else None,
            },
        },
    )

    return JsonResponse({
        'user_id': request.user.pk,
        'is_online': presence.is_online,
        'last_seen': presence.last_seen.isoformat() if presence.last_seen else None,
        'status': presence.status,
        'presence_visibility': presence.presence_visibility,
    })


# ---------------------------------------------------------------------------
# Private message send API (HTTP fallback)
# ---------------------------------------------------------------------------

@login_required(login_url='login')
def send_private_message_view(request, conversation_id):
    """Persist a private encrypted message over HTTP and broadcast it.

    The browser still receives messages over WebSocket, but HTTP gives the send
    path a reliable request/response fallback when the socket is reconnecting.
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'Method not allowed.'}, status=405)

    data = _json_body(request)
    data['conversation_id'] = conversation_id

    try:
        message = async_to_sync(ChatConsumer.create_private_message)(request.user.pk, data)
    except Exception as error:
        code = getattr(error, 'code', 'invalid_payload')
        detail = getattr(error, 'message', str(error))
        status = 404 if code == 'conversation_not_found' else 400
        if code == 'conversation_forbidden':
            status = 403
        return JsonResponse({'error': code, 'detail': detail}, status=status)

    channel_layer = get_channel_layer()
    async_to_sync(channel_layer.group_send)(
        ChatConsumer.user_group(message['receiver_id']),
        {'type': 'message.single.new', 'data': message},
    )

    return JsonResponse(message, status=201)


# ── T27: Auto-delete messages API ───────────────────────────────────

@login_required(login_url='login')
def auto_delete_setting_view(request):
    """GET/PUT global auto-delete default. PUT with {'seconds': N} or {'disabled': true}."""
    if request.method == 'GET':
        conv_default = Conversation.objects.filter(
            created_by=request.user, auto_delete_seconds__isnull=False,
        ).values('auto_delete_seconds').first()
        return JsonResponse({
            'global_auto_delete_seconds': (
                conv_default['auto_delete_seconds'] if conv_default else None
            ),
            'enabled': conv_default is not None and conv_default['auto_delete_seconds'] is not None,
        })
    if request.method == 'PUT':
        data = _json_body(request)
        seconds = None if data.get('disabled') else int(data.get('seconds', 0) or 0)
        seconds = seconds if seconds and seconds > 0 else None
        Conversation.objects.filter(
            type=Conversation.Type.SINGLE,
            created_by=request.user,
        ).update(auto_delete_seconds=seconds)
        return JsonResponse({'global_auto_delete_seconds': seconds, 'status': 'ok'})
    return JsonResponse({'error': 'Method not allowed.'}, status=405)


@login_required(login_url='login')
def conversation_auto_delete_view(request, conversation_id):
    """GET/PUT per-conversation auto-delete override."""
    member = _get_active_member(conversation_id, request.user)
    if not member:
        return JsonResponse({'error': 'Conversation not found or not a member.'}, status=404)
    if request.method == 'GET':
        return JsonResponse({
            'conversation_id': conversation_id,
            'auto_delete_seconds': member.auto_delete_seconds,
            'global_auto_delete_seconds': member.conversation.auto_delete_seconds,
        })
    if request.method == 'PUT':
        data = _json_body(request)
        seconds = None if data.get('disabled') else int(data.get('seconds', 0) or 0)
        seconds = seconds if seconds and seconds > 0 else None
        member.auto_delete_seconds = seconds
        member.save(update_fields=['auto_delete_seconds'])
        return JsonResponse({'status': 'ok', 'auto_delete_seconds': seconds})
    return JsonResponse({'error': 'Method not allowed.'}, status=405)


# ── T33/T34: Unified search API with scope filtering ────────────────

@login_required(login_url='login')
def search_unified_view(request):
    """Unified search across conversations, contacts, and groups with scope filter."""
    query = request.GET.get('q', '').strip()
    scope = request.GET.get('scope', 'all')
    results = {'conversations': [], 'contacts': [], 'groups': [], 'channels': []}

    if not query:
        return JsonResponse({'results': results, 'scope': scope})

    user = request.user

    # Contacts search
    if scope in ('all', 'contacts', 'private_chats'):
        name_matches = User.objects.filter(
            Q(username__icontains=query) | Q(profile__nickname__icontains=query),
        ).exclude(id=user.id).distinct().select_related('profile')[:10]

        for u in name_matches:
            try:
                nickname = u.profile.nickname or ''
            except Exception:
                nickname = ''
            results['contacts'].append({
                'id': u.id, 'username': u.username,
                'nickname': nickname,
                'is_contact': _are_contacts(user, u),
            })

    # Group search
    if scope in ('all', 'group_chats'):
        group_matches = Conversation.objects.filter(
            type=Conversation.Type.GROUP,
            name__icontains=query,
            status=Conversation.Status.ACTIVE,
        )[:10]
        for g in group_matches:
            is_member = ConversationMember.objects.filter(
                conversation=g, user=user, status=ConversationMember.Status.ACTIVE,
            ).exists()
            results['groups'].append({
                'id': g.id, 'name': g.name,
                'is_member': is_member,
                'member_count': ConversationMember.objects.filter(
                    conversation=g, status=ConversationMember.Status.ACTIVE,
                ).count(),
            })

    # Conversation search (private chats)
    if scope in ('all', 'private_chats'):
        conv_matches = ConversationMember.objects.filter(
            user=user, status=ConversationMember.Status.ACTIVE,
            conversation__type=Conversation.Type.SINGLE,
        ).select_related('conversation')
        peer_convs = []
        for m in conv_matches:
            peer = ConversationMember.objects.filter(
                conversation=m.conversation, status=ConversationMember.Status.ACTIVE,
            ).exclude(user=user).select_related('user__profile').first()
            if peer:
                pname = _display_name(peer.user)
                if query.lower() in pname.lower() or query.lower() in peer.user.username.lower():
                    peer_convs.append({
                        'conversation_id': m.conversation_id,
                        'peer_id': peer.user_id,
                        'peer_username': peer.user.username,
                        'peer_display_name': pname,
                    })
        results['conversations'] = peer_convs[:10]

    # Channels (placeholder — T33)
    results['channels'] = []

    return JsonResponse({'results': results, 'scope': scope, 'query': query})


# ── T37: Advanced group management ───────────────────────────────────

def _require_admin(conversation_id, user):
    """Return (member, conversation) if user is admin/owner, else (None, error_response)."""
    member = _get_member(conversation_id, user)
    if not member or member.status != ConversationMember.Status.ACTIVE:
        return None, JsonResponse({'error': 'Not a member.'}, status=403)
    if member.role not in (ConversationMember.Role.OWNER, ConversationMember.Role.ADMIN):
        return None, JsonResponse({'error': 'Admin permission required.'}, status=403)
    try:
        conv = Conversation.objects.get(id=conversation_id, type=Conversation.Type.GROUP)
    except Conversation.DoesNotExist:
        return None, JsonResponse({'error': 'Group not found.'}, status=404)
    return (member, conv), None


@login_required(login_url='login')
def group_promote_view(request, conversation_id, user_id):
    """Promote a member to admin. Owner or admin only."""
    result, err = _require_admin(conversation_id, request.user)
    if err:
        return err
    _, conv = result
    target = _get_member(conversation_id, user_id)
    if not target or target.status != ConversationMember.Status.ACTIVE:
        return JsonResponse({'error': 'Target not a member.'}, status=404)
    if target.role in (ConversationMember.Role.OWNER, ConversationMember.Role.ADMIN):
        return JsonResponse({'error': 'Already an admin or owner.'}, status=409)
    target.role = ConversationMember.Role.ADMIN
    target.save(update_fields=['role'])
    conv.membership_version = F('membership_version') + 1
    conv.save(update_fields=['membership_version', 'updated_at'])
    return JsonResponse({'status': 'ok', 'user_id': user_id, 'role': 'admin'})


@login_required(login_url='login')
def group_demote_view(request, conversation_id, user_id):
    """Demote an admin to member. Owner only."""
    member = _get_member(conversation_id, request.user)
    if not member or member.role != ConversationMember.Role.OWNER:
        return JsonResponse({'error': 'Only the owner can demote admins.'}, status=403)
    target = _get_member(conversation_id, user_id)
    if not target or target.status != ConversationMember.Status.ACTIVE:
        return JsonResponse({'error': 'Target not a member.'}, status=404)
    if target.role != ConversationMember.Role.ADMIN:
        return JsonResponse({'error': 'Target is not an admin.'}, status=409)
    target.role = ConversationMember.Role.MEMBER
    target.save(update_fields=['role'])
    conv = Conversation.objects.get(id=conversation_id)
    conv.membership_version = F('membership_version') + 1
    conv.save(update_fields=['membership_version', 'updated_at'])
    return JsonResponse({'status': 'ok', 'user_id': user_id, 'role': 'member'})


@login_required(login_url='login')
def group_transfer_view(request, conversation_id):
    """Transfer ownership to another member. Owner only."""
    if request.method != 'POST':
        return JsonResponse({'error': 'Method not allowed.'}, status=405)
    member = _get_member(conversation_id, request.user)
    if not member or member.role != ConversationMember.Role.OWNER:
        return JsonResponse({'error': 'Only the owner can transfer ownership.'}, status=403)
    data = _json_body(request)
    new_owner_id = data.get('user_id')
    if not new_owner_id:
        return JsonResponse({'error': 'user_id is required.'}, status=400)
    target = _get_member(conversation_id, new_owner_id)
    if not target or target.status != ConversationMember.Status.ACTIVE:
        return JsonResponse({'error': 'Target not a member.'}, status=404)
    if target.user_id == request.user.pk:
        return JsonResponse({'error': 'You already own this group.'}, status=400)
    member.role = ConversationMember.Role.ADMIN
    member.save(update_fields=['role'])
    target.role = ConversationMember.Role.OWNER
    target.save(update_fields=['role'])
    conv = Conversation.objects.get(id=conversation_id)
    conv.membership_version = F('membership_version') + 1
    conv.save(update_fields=['membership_version', 'updated_at'])
    return JsonResponse({'status': 'ok', 'new_owner_id': new_owner_id})


@login_required(login_url='login')
def group_announcement_view(request, conversation_id):
    """GET: get active announcement. POST: create/replace. DELETE: remove."""
    member = _get_member(conversation_id, request.user)
    if not member or member.status != ConversationMember.Status.ACTIVE:
        return JsonResponse({'error': 'Not a member.'}, status=403)
    if member.role not in (ConversationMember.Role.OWNER, ConversationMember.Role.ADMIN):
        return JsonResponse({'error': 'Admin permission required.'}, status=403)
    try:
        conv = Conversation.objects.get(id=conversation_id, type=Conversation.Type.GROUP)
    except Conversation.DoesNotExist:
        return JsonResponse({'error': 'Group not found.'}, status=404)

    if request.method == 'GET':
        ann = GroupAnnouncement.objects.filter(
            conversation=conv, is_active=True,
        ).select_related('author').first()
        if not ann:
            return JsonResponse({'announcement': None})
        return JsonResponse({
            'announcement': {
                'id': ann.id, 'content': ann.content,
                'author_id': ann.author_id, 'author_username': ann.author.username,
                'created_at': ann.created_at.isoformat(),
            },
        })
    elif request.method == 'POST':
        data = _json_body(request)
        content = data.get('content', '').strip()
        if not content:
            return JsonResponse({'error': 'Content is required.'}, status=400)
        # Deactivate old
        GroupAnnouncement.objects.filter(conversation=conv, is_active=True).update(is_active=False)
        ann = GroupAnnouncement.objects.create(
            conversation=conv, author=request.user, content=content,
        )
        return JsonResponse({
            'announcement': {
                'id': ann.id, 'content': ann.content,
                'author_id': ann.author_id,
                'created_at': ann.created_at.isoformat(),
            },
        }, status=201)
    elif request.method == 'DELETE':
        GroupAnnouncement.objects.filter(conversation=conv, is_active=True).update(is_active=False)
        return JsonResponse({'status': 'ok'})
    return JsonResponse({'error': 'Method not allowed.'}, status=405)


@login_required(login_url='login')
def group_mute_view(request, conversation_id):
    """Mute a group (prevent non-admin sends). Owner/admin only."""
    result, err = _require_admin(conversation_id, request.user)
    if err:
        return err
    _, conv = result
    if request.method == 'POST':
        data = _json_body(request)
        mins = int(data.get('duration_minutes', 60))
        mins = min(mins, 10080)
        conv.muted_until = timezone.now() + timezone.timedelta(minutes=mins)
        conv.save(update_fields=['muted_until'])
        return JsonResponse({'status': 'ok', 'muted_until': conv.muted_until.isoformat()})
    elif request.method == 'DELETE':
        conv.muted_until = None
        conv.save(update_fields=['muted_until'])
        return JsonResponse({'status': 'ok'})
    return JsonResponse({'error': 'Method not allowed.'}, status=405)


@login_required(login_url='login')
def group_members_advanced_view(request, conversation_id):
    """GET active members with roles for the group admin panel."""
    member = _get_member(conversation_id, request.user)
    if not member or member.status != ConversationMember.Status.ACTIVE:
        return JsonResponse({'error': 'Not a member.'}, status=403)
    try:
        conv = Conversation.objects.get(id=conversation_id, type=Conversation.Type.GROUP)
    except Conversation.DoesNotExist:
        return JsonResponse({'error': 'Group not found.'}, status=404)

    members = ConversationMember.objects.filter(
        conversation=conv, status=ConversationMember.Status.ACTIVE,
    ).select_related('user__profile')

    return JsonResponse({
        'group_id': conv.id,
        'name': conv.name,
        'owner_id': conv.created_by_id,
        'membership_version': conv.membership_version,
        'members': [
            {
                'user_id': m.user_id,
                'username': m.user.username,
                'display_name': _display_name(m.user),
                'role': m.role,
                'joined_at': m.joined_at.isoformat(),
                'initials': _initials(_display_name(m.user)),
                'avatar_color': _avatar_color(_display_name(m.user)),
            }
            for m in members
        ],
    })
