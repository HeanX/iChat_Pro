import copy
import hashlib
import json
import logging
import os
import shutil
import uuid as uuid_lib
from datetime import timedelta

from asgiref.sync import async_to_sync, sync_to_async
from channels.layers import get_channel_layer
from django.conf import settings as django_settings
from django.contrib.auth import get_user_model, logout
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.db.models import F, Q
from django.http import FileResponse, HttpResponse, JsonResponse
from django.http.multipartparser import MultiPartParser, MultiPartParserError
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_POST, require_GET

from accounts.models import BlockedUser, Contact, FriendRequest, UserPrivacySettings, UserStorageSettings
from .consumers import ChatConsumer, ClientPayloadError
from .models import (
    Conversation,
    ConversationMember,
    ChatReport,
    EncryptedFile,
    EncryptedFileChunk,
    EncryptedFileKey,
    EncryptedMessage,
    GroupInvitation,
    GroupAnnouncement,
    GroupMessage,
    GroupMessageRecipient,
    UserMessageDeletion,
    UserPresence,
    UserLLMConfig,
)

User = get_user_model()
logger = logging.getLogger(__name__)

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


def _privacy_settings_for(user):
    settings_obj, _ = UserPrivacySettings.objects.get_or_create(user=user)
    return settings_obj


def _can_view_privacy_field(viewer, target, field_name):
    if viewer == target:
        return True
    privacy = _privacy_settings_for(target)
    visibility = getattr(privacy, field_name, 'everyone')
    return _visibility_allows(viewer, target, visibility)


def _avatar_url(request, user):
    """Return absolute avatar image URL for a user, respecting profile-photo privacy."""
    viewer = getattr(request, 'user', None)
    if viewer and getattr(viewer, 'is_authenticated', False):
        if not _can_view_privacy_field(viewer, user, 'profile_photo_visibility'):
            return ''
    try:
        if user.profile and user.profile.avatar:
            timestamp = int(user.profile.updated_at.timestamp())
            return request.build_absolute_uri(f"{user.profile.avatar.url}?t={timestamp}")
    except Exception:
        try:
            if user.profile and user.profile.avatar:
                return request.build_absolute_uri(user.profile.avatar.url)
        except Exception:
            pass
    return ''


def _are_contacts(user, peer):
    return Contact.objects.filter(
        (Q(user=user) & Q(contact=peer))
        | (Q(user=peer) & Q(contact=user)),
    ).exists()


def _visibility_allows(viewer, target, visibility):
    if viewer == target:
        return True
    if visibility == 'everyone':
        return True
    if visibility == 'contacts':
        return _are_contacts(viewer, target)
    return False


def _visible_peer_profile_payload(request, peer):
    """Return profile fields for chat details, respecting per-field privacy."""
    try:
        profile = peer.profile
    except Exception:
        profile = None

    can_view_bio = _can_view_privacy_field(request.user, peer, 'bio_visibility')
    can_view_phone = _can_view_privacy_field(request.user, peer, 'phone_number_visibility')
    can_view_birthday = _can_view_privacy_field(request.user, peer, 'birthday_visibility')
    can_view_photo = _can_view_privacy_field(request.user, peer, 'profile_photo_visibility')
    is_contact = _are_contacts(request.user, peer)

    return {
        'peer_email': peer.email if (request.user == peer or is_contact) else '',
        'peer_first_name': peer.first_name,
        'peer_last_name': peer.last_name,
        'peer_bio': (profile.bio if profile and can_view_bio else ''),
        'peer_phone_number': (profile.phone_number if profile and can_view_phone else ''),
        'peer_location': (profile.location if profile else ''),
        'peer_birthday': profile.birthday.isoformat() if profile and profile.birthday and can_view_birthday else '',
        'peer_can_view_bio': can_view_bio,
        'peer_can_view_phone_number': can_view_phone,
        'peer_can_view_birthday': can_view_birthday,
        'peer_can_view_profile_photo': can_view_photo,
        'peer_is_contact': is_contact,
    }


def _is_blocked_by(blocker, target):
    """Return True if *blocker* has blocked *target*."""
    return BlockedUser.objects.filter(
        blocker=blocker,
        blocked=target,
    ).exists()


def _user_type_for(user):
    try:
        return user.profile.user_type
    except Exception:
        return 'user'


def _is_automated_account(user):
    return _user_type_for(user) in {'agent', 'bot'}


def _can_initiate_conversation(sender, receiver):
    """Check whether *sender* is allowed to start a private chat with *receiver*.

    Returns (allowed: bool, reason: str | None).
    """
    # Blocked users cannot chat at all
    if _is_blocked_by(receiver, sender):
        return False, 'You have been blocked by this user.'
    if _is_blocked_by(sender, receiver):
        return False, 'You have blocked this user. Unblock them first.'

    # Contacts can always chat with each other
    if _are_contacts(sender, receiver):
        return True, None

    # Bots/agents are intentionally discoverable interaction targets. They do
    # not require a mutual contact edge before a user can start a chat.
    if _is_automated_account(receiver):
        return True, None

    # Non-contacts: check receiver's privacy settings
    try:
        ps = UserPrivacySettings.objects.get(user=receiver)
    except UserPrivacySettings.DoesNotExist:
        return False, 'Private chats are limited to contacts.'

    if ps.who_can_send_messages == 'everyone':
        return True, None

    return False, 'Private chats are limited to contacts.'


def _can_send_private_message(sender, receiver):
    """Return whether sender may send a private message to receiver right now."""
    if sender == receiver:
        return False, 'Cannot send messages to yourself.'
    if _is_blocked_by(receiver, sender):
        return False, 'You have been blocked by this user.'
    if _is_blocked_by(sender, receiver):
        return False, 'You have blocked this user. Unblock them first.'
    if _are_contacts(sender, receiver):
        return True, None
    privacy = _privacy_settings_for(receiver)
    if privacy.who_can_send_messages == 'everyone':
        return True, None
    return False, 'This user only accepts messages from contacts.'


def _json_body(request):
    """Parse and return the JSON body of a request, or empty dict on error."""
    try:
        return json.loads(request.body.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return {}


def _rate_limit(request, bucket, *, limit, window_seconds):
    user_id = request.user.pk if request.user.is_authenticated else 'anon'
    key = f'rl:{bucket}:{user_id}'
    count = cache.get(key, 0)
    if count >= limit:
        return JsonResponse({'error': 'rate_limited', 'retry_after_seconds': window_seconds}, status=429)
    if count == 0:
        cache.set(key, 1, window_seconds)
    else:
        try:
            cache.incr(key)
        except ValueError:
            cache.set(key, 1, window_seconds)
    return None


def _parse_int(value, default=0, min_value=None, max_value=None):
    """Safely parse an integer from user input.

    Returns *default* on ValueError/TypeError instead of raising 500.
    Optionally clamps to [min_value, max_value].
    """
    try:
        result = int(value)
    except (ValueError, TypeError):
        return default
    if min_value is not None:
        result = max(min_value, result)
    if max_value is not None:
        result = min(max_value, result)
    return result


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


def _effective_auto_delete_seconds(member):
    if not member:
        return None
    if member.auto_delete_seconds is not None:
        return member.auto_delete_seconds or None
    if member.conversation.auto_delete_seconds is not None:
        return member.conversation.auto_delete_seconds or None
    days = _privacy_settings_for(member.user).auto_delete_messages_days
    return days * 86400 if days else None


def _auto_delete_cutoff(member):
    seconds = _effective_auto_delete_seconds(member)
    if not seconds:
        return None
    return timezone.now() - timezone.timedelta(seconds=seconds)


def _deleted_message_ids(user, conversation_id, message_type):
    return set(
        UserMessageDeletion.objects.filter(
            user=user,
            conversation_id=conversation_id,
            message_type=message_type,
        ).values_list('message_id', flat=True)
    )


def _apply_auto_delete_for_member(member):
    """Hide messages older than the member's effective auto-delete window."""
    cutoff = _auto_delete_cutoff(member)
    if not cutoff:
        return

    if member.conversation.type == Conversation.Type.SINGLE:
        message_ids = list(
            EncryptedMessage.objects.filter(
                conversation=member.conversation,
                created_at__lt=cutoff,
            ).values_list('id', flat=True)[:1000]
        )
        message_type = UserMessageDeletion.MessageType.PRIVATE
    else:
        message_ids = list(
            GroupMessageRecipient.objects.filter(
                receiver=member.user,
                group_message__conversation=member.conversation,
                group_message__created_at__lt=cutoff,
            ).values_list('group_message_id', flat=True)[:1000]
        )
        message_type = UserMessageDeletion.MessageType.GROUP

    if not message_ids:
        return

    UserMessageDeletion.objects.bulk_create(
        [
            UserMessageDeletion(
                user=member.user,
                conversation=member.conversation,
                message_type=message_type,
                message_id=message_id,
            )
            for message_id in message_ids
        ],
        ignore_conflicts=True,
    )


def _visible_private_messages_queryset(member):
    queryset = EncryptedMessage.objects.filter(conversation=member.conversation)
    if member.cleared_at:
        queryset = queryset.filter(created_at__gte=member.cleared_at)
    cutoff = _auto_delete_cutoff(member)
    if cutoff:
        queryset = queryset.filter(created_at__gte=cutoff)
    deleted_ids = _deleted_message_ids(
        member.user,
        member.conversation_id,
        UserMessageDeletion.MessageType.PRIVATE,
    )
    if deleted_ids:
        queryset = queryset.exclude(id__in=deleted_ids)
    return queryset


def _visible_group_recipients_queryset(member):
    queryset = GroupMessageRecipient.objects.filter(
        receiver=member.user,
        group_message__conversation=member.conversation,
        group_message__created_at__gte=member.joined_at,
    )
    if member.cleared_at:
        queryset = queryset.filter(group_message__created_at__gte=member.cleared_at)
    cutoff = _auto_delete_cutoff(member)
    if cutoff:
        queryset = queryset.filter(group_message__created_at__gte=cutoff)
    deleted_ids = _deleted_message_ids(
        member.user,
        member.conversation_id,
        UserMessageDeletion.MessageType.GROUP,
    )
    if deleted_ids:
        queryset = queryset.exclude(group_message_id__in=deleted_ids)
    return queryset


@login_required(login_url='login')
def index_view(request):
    return render(request, 'pages/chat.html', {
        'open_settings': False,
        **_sidebar_contacts_context(request.user),
    })


@login_required(login_url='login')
def settings_view(request):
    return render(request, 'pages/chat.html', {
        'open_settings': True,
        **_sidebar_contacts_context(request.user),
    })


def _sidebar_contacts_context(user):
    return {
        'contacts': Contact.objects.filter(
            Q(user=user) | Q(contact=user),
        ).select_related('user__profile', 'contact__profile'),
        'incoming_requests': FriendRequest.objects.filter(
            receiver=user,
            status=FriendRequest.Status.PENDING,
        ).select_related('sender__profile'),
        'outgoing_requests': FriendRequest.objects.filter(
            sender=user,
            status=FriendRequest.Status.PENDING,
        ).select_related('receiver__profile'),
    }


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
        _apply_auto_delete_for_member(membership)
        conversation = membership.conversation
        is_muted = (
            membership.muted_until is not None
            and membership.muted_until > timezone.now()
        )

        last_message_data = None
        if conversation.type == Conversation.Type.SINGLE:
            last_msg = _visible_private_messages_queryset(membership).order_by('-created_at').first()
            if last_msg:
                last_message_data = _private_message_payload_for_viewer(last_msg, request.user.pk)
        else:
            last_msg = (
                _visible_group_recipients_queryset(membership)
                .select_related('group_message')
                .order_by('-group_message__created_at')
                .first()
            )
            if last_msg:
                gm = last_msg.group_message
                is_sender = gm.sender_id == request.user.pk
                has_sender_copy = gm.sender_copy_ciphertext and gm.sender_copy_nonce and gm.sender_copy_auth_tag
                last_message_data = {
                    'id': gm.id,
                    'group_id': conversation.id,
                    'ciphertext': gm.sender_copy_ciphertext if is_sender and has_sender_copy else last_msg.ciphertext,
                    'nonce': gm.sender_copy_nonce if is_sender and has_sender_copy else last_msg.nonce,
                    'auth_tag': gm.sender_copy_auth_tag if is_sender and has_sender_copy else last_msg.auth_tag,
                    'algorithm': last_msg.algorithm,
                    'sender_id': gm.sender_id,
                    'receiver_id': request.user.id,
                    'message_type': gm.message_type,
                    'file_id': gm.file_id_id,
                    'sender_key_version': last_msg.sender_key_version or 0,
                    'receiver_key_version': last_msg.receiver_key_version or 0,
                    'sender_ephemeral_public_key': (
                        gm.sender_copy_ephemeral_public_key if is_sender and has_sender_copy
                        else last_msg.sender_ephemeral_public_key
                    ),
                    'membership_version': last_msg.membership_version or 0,
                }

        item = {
            'id': conversation.id,
            'type': conversation.type,
            'unread': membership.unread_count,
            'last_message_at': (
                last_msg.group_message.created_at.isoformat()
                if conversation.type != Conversation.Type.SINGLE and last_message_data
                else last_msg.created_at.isoformat()
                if conversation.type == Conversation.Type.SINGLE and last_message_data
                else None
            ),
            'last_message_preview': 'Encrypted message' if last_message_data else '',
            'last_message_data': last_message_data,
            'last_message_id': last_message_data['id'] if last_message_data else None,
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
                try:
                    peer_user_type = peer.profile.user_type
                except Exception:
                    peer_user_type = 'user'
                item.update({
                    'peer_id': peer.id,
                    'peer_username': peer.username,
                    'peer_user_type': peer_user_type,
                    'name': name,
                    'initials': _initials(name),
                    'avatar_color': _avatar_color(name),
                    'avatar_url': _avatar_url(request, peer),
                    'is_secure': peer.public_keys.filter(is_active=True).exists(),
                })
                item.update(_visible_peer_profile_payload(request, peer))
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

    allowed, reason = _can_initiate_conversation(request.user, peer)
    if not allowed:
        return JsonResponse({'error': reason or 'Cannot start this conversation.'}, status=403)

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
        duration_minutes = _parse_int(data.get('duration_minutes'), 60, min_value=1, max_value=10080)
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
    count = _parse_int(data.get('unread_count'), 1, min_value=1, max_value=99)
    member.unread_count = count
    member.save(update_fields=['unread_count'])
    return JsonResponse({'status': 'ok', 'unread_count': member.unread_count})


# ---------------------------------------------------------------------------
# T20: Message operations API
# ---------------------------------------------------------------------------

def _client_payload_error_response(error, status=400):
    return JsonResponse({'error': error.code, 'detail': error.message}, status=status)


def _normalize_forward_file_keys(file_keys_data, allowed_holder_ids):
    if not isinstance(file_keys_data, list) or not file_keys_data:
        return None, JsonResponse(
            {'error': 'invalid_file_metadata', 'detail': 'file_keys is required for file forwarding.'},
            status=400,
        )

    normalized = []
    holder_ids = set()
    for fk in file_keys_data:
        if not isinstance(fk, dict):
            return None, JsonResponse(
                {'error': 'invalid_file_metadata', 'detail': 'Each file_key must be an object.'},
                status=400,
            )
        try:
            holder_id = int(fk.get('holder_id', 0))
        except (TypeError, ValueError):
            return None, JsonResponse(
                {'error': 'invalid_file_metadata', 'detail': 'Each file_key must have a valid holder_id.'},
                status=400,
            )
        if holder_id in holder_ids:
            continue
        holder_ids.add(holder_id)
        if holder_id not in allowed_holder_ids:
            return None, JsonResponse(
                {'error': 'invalid_file_metadata', 'detail': 'file_keys may only target active members of the target conversation.'},
                status=403,
            )

        encrypted_file_key = str(fk.get('encrypted_file_key', ''))
        nonce = str(fk.get('nonce', ''))
        auth_tag = str(fk.get('auth_tag', ''))
        algorithm = str(fk.get('algorithm', 'AES-256-GCM'))
        if not encrypted_file_key or not nonce or not auth_tag:
            return None, JsonResponse(
                {'error': 'invalid_file_metadata', 'detail': 'Each file_key requires encrypted_file_key, nonce, and auth_tag.'},
                status=400,
            )
        if algorithm != 'AES-256-GCM':
            return None, JsonResponse(
                {'error': 'invalid_file_metadata', 'detail': f'Unsupported file key algorithm: {algorithm}.'},
                status=400,
            )

        try:
            sender_key_version = int(fk.get('sender_key_version', 0)) or None
            receiver_key_version = int(fk.get('receiver_key_version', 0)) or None
            membership_version = int(fk.get('membership_version', 0)) or None
        except (TypeError, ValueError):
            return None, JsonResponse(
                {'error': 'invalid_file_metadata', 'detail': 'file_key key versions must be integers.'},
                status=400,
            )

        normalized.append({
            'holder_id': holder_id,
            'encrypted_file_key': encrypted_file_key,
            'nonce': nonce,
            'auth_tag': auth_tag,
            'algorithm': algorithm,
            'sender_key_version': sender_key_version,
            'receiver_key_version': receiver_key_version,
            'membership_version': membership_version,
            'sender_ephemeral_public_key': str(fk.get('sender_ephemeral_public_key', '') or '') or None,
        })

    if holder_ids != allowed_holder_ids:
        return None, JsonResponse(
            {'error': 'invalid_file_metadata', 'detail': 'file_keys must cover every active member of the target conversation.'},
            status=400,
        )

    return normalized, None


def _save_forward_file_keys(forward_file, file_keys, sender):
    for fk in file_keys:
        EncryptedFileKey.objects.update_or_create(
            file=forward_file,
            holder_id=fk['holder_id'],
            defaults={
                'sender': sender,
                'encrypted_file_key': fk['encrypted_file_key'],
                'nonce': fk['nonce'],
                'auth_tag': fk['auth_tag'],
                'algorithm': fk['algorithm'],
                'sender_key_version': fk['sender_key_version'],
                'receiver_key_version': fk['receiver_key_version'],
                'membership_version': fk['membership_version'],
                'sender_ephemeral_public_key': fk.get('sender_ephemeral_public_key'),
            },
        )


@login_required(login_url='login')
@require_POST
def forward_message_view(request, conversation_id):
    """Forward an encrypted message using the same validation as WebSocket sends."""
    data = _json_body(request)
    original_message_id = data.get('original_message_id')
    original_conversation_id = data.get('original_conversation_id')
    if original_message_id not in (None, ''):
        try:
            original_message_id = int(original_message_id)
        except (TypeError, ValueError):
            return JsonResponse({'error': 'invalid_payload', 'detail': 'original_message_id must be an integer.'}, status=400)

    forward_file = None
    file_id = data.get('file_id')
    if file_id:
        try:
            forward_file, file_error = _get_encrypted_file_or_error(int(file_id), request.user)
        except (TypeError, ValueError):
            return JsonResponse({'error': 'invalid_file_metadata', 'detail': 'file_id must be an integer.'}, status=400)
        if file_error:
            return file_error
        if forward_file.status != EncryptedFile.Status.AVAILABLE:
            return JsonResponse({'error': 'file_unavailable'}, status=410)

    if original_message_id and original_conversation_id:
        source_member = _get_active_member(original_conversation_id, request.user)
        if not source_member:
            return JsonResponse({'error': 'Original conversation not found or not a member.'}, status=403)
        if source_member.conversation.type == Conversation.Type.SINGLE:
            source_exists = EncryptedMessage.objects.filter(
                pk=original_message_id,
                conversation_id=original_conversation_id,
            ).exists()
        else:
            source_exists = GroupMessageRecipient.objects.filter(
                group_message_id=original_message_id,
                group_message__conversation_id=original_conversation_id,
                receiver=request.user,
            ).exists()
        if not source_exists:
            return JsonResponse({'error': 'Original message not found or not accessible.'}, status=404)

    member = _get_active_member(conversation_id, request.user)
    if not member:
        return JsonResponse({'error': 'Target conversation not found or not a member.'}, status=404)

    conversation = member.conversation
    if conversation.status != Conversation.Status.ACTIVE:
        return JsonResponse({'error': 'Target conversation is not active.'}, status=400)
    forwarded_reply_to_id = (
        original_message_id
        if original_message_id and str(original_conversation_id) != str(conversation.id)
        else None
    )

    if conversation.type == Conversation.Type.SINGLE:
        peer_id = data.get('peer_id')
        if not peer_id:
            return JsonResponse({'error': 'peer_id is required for private chat.'}, status=400)
        try:
            peer_id = int(peer_id)
        except (TypeError, ValueError):
            return JsonResponse({'error': 'peer_id must be an integer.'}, status=400)

        active_members = ConversationMember.objects.filter(
            conversation=conversation,
            status=ConversationMember.Status.ACTIVE,
        )
        if (
            active_members.count() != 2
            or not active_members.filter(user_id=request.user.id).exists()
            or not active_members.filter(user_id=peer_id).exists()
        ):
            return JsonResponse({'error': 'Peer is not a member of this conversation.'}, status=403)

        peer = User.objects.filter(id=peer_id, is_active=True).first()
        if not peer:
            return JsonResponse({'error': 'Peer not found.'}, status=404)
        if _is_blocked_by(peer, request.user):
            return JsonResponse({'error': 'You have been blocked by this user.'}, status=403)
        if _is_blocked_by(request.user, peer):
            return JsonResponse({'error': 'You have blocked this user. Unblock them first.'}, status=403)

        payload = dict(data)
        payload['conversation_id'] = conversation.id
        payload['receiver_id'] = peer_id
        if forwarded_reply_to_id:
            payload['reply_to_message_id'] = forwarded_reply_to_id
        try:
            validated = ChatConsumer.validate_private_message(payload)
        except ClientPayloadError as error:
            return _client_payload_error_response(error)

        file_keys = []
        if forward_file:
            file_keys, file_error = _normalize_forward_file_keys(
                data.get('file_keys', []),
                set(active_members.values_list('user_id', flat=True)),
            )
            if file_error:
                return file_error

        try:
            with transaction.atomic():
                existing = EncryptedMessage.objects.filter(
                    sender=request.user,
                    client_message_id=validated['client_message_id'],
                ).first()
                if existing:
                    return JsonResponse({
                        'status': 'ok',
                        'conversation_id': existing.conversation_id,
                        'message_id': existing.pk,
                    }, status=200)

                if forward_file:
                    _save_forward_file_keys(forward_file, file_keys, request.user)

                try:
                    sender_copy = validated.get('sender_copy') or {}
                    message = EncryptedMessage.objects.create(
                        conversation=conversation,
                        sender=request.user,
                        receiver_id=validated['receiver_id'],
                        message_type=validated['message_type'],
                        ciphertext=validated['ciphertext'],
                        nonce=validated['nonce'],
                        auth_tag=validated['auth_tag'],
                        sender_ephemeral_public_key=validated.get('sender_ephemeral_public_key'),
                        sender_copy_ciphertext=sender_copy.get('ciphertext'),
                        sender_copy_nonce=sender_copy.get('nonce'),
                        sender_copy_auth_tag=sender_copy.get('auth_tag'),
                        sender_copy_ephemeral_public_key=sender_copy.get('sender_ephemeral_public_key'),
                        algorithm=validated['algorithm'],
                        sender_key_version=validated['sender_key_version'],
                        receiver_key_version=validated['receiver_key_version'],
                        client_message_id=validated['client_message_id'],
                        reply_to_message_id=forwarded_reply_to_id,
                        file_id=forward_file,
                    )
                except IntegrityError:
                    existing = EncryptedMessage.objects.get(
                        sender=request.user,
                        client_message_id=validated['client_message_id'],
                    )
                    return JsonResponse({
                        'status': 'ok',
                        'conversation_id': existing.conversation_id,
                        'message_id': existing.pk,
                    }, status=200)

                conversation.last_message_id = message.pk
                conversation.last_message_at = message.created_at
                conversation.save(update_fields=['last_message_id', 'last_message_at', 'updated_at'])

                ConversationMember.objects.filter(
                    conversation=conversation,
                    user_id=peer_id,
                    status=ConversationMember.Status.ACTIVE,
                ).update(unread_count=F('unread_count') + 1)
        except (ValueError, KeyError) as e:
            return JsonResponse({'error': f'Invalid payload: {e}'}, status=400)

        channel_layer = get_channel_layer()
        async_to_sync(channel_layer.group_send)(
            f'user_{peer_id}',
            {
                'type': 'message.single.new',
                'data': (
                    _serialize_file_private_message(message, _build_file_sub_object(forward_file, peer_id), viewer_id=peer_id)
                    if forward_file else ChatConsumer.serialize_private_message(message, viewer_id=peer_id)
                ),
            },
        )
        async_to_sync(channel_layer.group_send)(
            f'user_{request.user.pk}',
            {
                'type': 'message.single.new',
                'data': (
                    _serialize_file_private_message(message, _build_file_sub_object(forward_file, request.user.pk), viewer_id=request.user.pk)
                    if forward_file else ChatConsumer.serialize_private_message(message, viewer_id=request.user.pk)
                ),
            },
        )

        return JsonResponse({
            'status': 'ok',
            'conversation_id': conversation.id,
            'message_id': message.pk,
        }, status=201)

    if conversation.type == Conversation.Type.GROUP:
        if conversation.muted_until and conversation.muted_until > timezone.now():
            if member.role not in (ConversationMember.Role.OWNER, ConversationMember.Role.ADMIN):
                return JsonResponse({'error': 'This group is muted.'}, status=403)

        active_member_ids = set(
            ConversationMember.objects.filter(
                conversation=conversation,
                status=ConversationMember.Status.ACTIVE,
            ).values_list('user_id', flat=True)
        )

        payload = dict(data)
        payload['group_id'] = conversation.id
        if forwarded_reply_to_id:
            payload['reply_to_message_id'] = forwarded_reply_to_id
        try:
            validated = ChatConsumer.validate_group_message(payload)
        except ClientPayloadError as error:
            return _client_payload_error_response(error)

        recipient_user_ids = {r['receiver_id'] for r in validated['recipients']}
        if recipient_user_ids != active_member_ids:
            return JsonResponse({'error': 'Recipients must match current active members.'}, status=400)

        client_membership_version = validated['membership_version']
        if client_membership_version != conversation.membership_version:
            return JsonResponse({'error': 'Membership version mismatch. Please refresh.'}, status=409)

        file_keys = []
        if forward_file:
            file_keys, file_error = _normalize_forward_file_keys(
                data.get('file_keys', []),
                active_member_ids,
            )
            if file_error:
                return file_error

        with transaction.atomic():
            existing = GroupMessage.objects.filter(
                sender=request.user,
                client_message_id=validated['client_message_id'],
            ).first()
            if existing:
                return JsonResponse({
                    'status': 'ok',
                    'conversation_id': existing.conversation_id,
                    'message_id': existing.pk,
                }, status=200)

            if forward_file:
                _save_forward_file_keys(forward_file, file_keys, request.user)

            sender_copy = validated.get('sender_copy') or {}
            try:
                group_message = GroupMessage.objects.create(
                    conversation=conversation,
                    sender=request.user,
                    message_type=validated['message_type'],
                    client_message_id=validated['client_message_id'],
                    reply_to_message_id=forwarded_reply_to_id,
                    file_id=forward_file,
                    sender_copy_ciphertext=sender_copy.get('ciphertext'),
                    sender_copy_nonce=sender_copy.get('nonce'),
                    sender_copy_auth_tag=sender_copy.get('auth_tag'),
                    sender_copy_ephemeral_public_key=sender_copy.get('sender_ephemeral_public_key'),
                )
            except IntegrityError:
                existing = GroupMessage.objects.get(
                    sender=request.user,
                    client_message_id=validated['client_message_id'],
                )
                return JsonResponse({
                    'status': 'ok',
                    'conversation_id': existing.conversation_id,
                    'message_id': existing.pk,
                }, status=200)

            recipient_objs = [
                GroupMessageRecipient(
                    group_message=group_message,
                    receiver_id=r['receiver_id'],
                    ciphertext=r['ciphertext'],
                    nonce=r['nonce'],
                    auth_tag=r['auth_tag'],
                    algorithm=validated['algorithm'],
                    sender_key_version=validated['sender_key_version'],
                    receiver_key_version=r['receiver_key_version'],
                    sender_ephemeral_public_key=r.get('sender_ephemeral_public_key'),
                    membership_version=client_membership_version,
                )
                for r in validated['recipients']
            ]
            GroupMessageRecipient.objects.bulk_create(recipient_objs)

            ConversationMember.objects.filter(
                conversation=conversation,
                status=ConversationMember.Status.ACTIVE,
            ).exclude(user=request.user).update(
                unread_count=F('unread_count') + 1,
            )

            conversation.last_message_id = group_message.pk
            conversation.last_message_at = group_message.created_at
            conversation.save(update_fields=['last_message_id', 'last_message_at', 'updated_at'])

        channel_layer = get_channel_layer()
        for recipient_data in ChatConsumer._build_recipients_payload(group_message, conversation):
            if forward_file:
                recipient_data = _serialize_file_group_recipient(
                    group_message,
                    GroupMessageRecipient.objects.get(
                        group_message=group_message,
                        receiver_id=recipient_data['receiver_id'],
                    ),
                    _build_file_sub_object(forward_file, recipient_data['receiver_id']),
                )
            if recipient_data['receiver_id'] == request.user.pk:
                continue
            async_to_sync(channel_layer.group_send)(
                f"user_{recipient_data['receiver_id']}",
                {'type': 'message.group.new', 'data': recipient_data},
            )

        return JsonResponse({
            'status': 'ok',
            'conversation_id': conversation.id,
            'message_id': group_message.pk,
        }, status=201)

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
        with transaction.atomic():
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

    import re
    import base64
    import uuid
    from django.core.files.base import ContentFile
    from django.core.files.storage import default_storage

    avatar_data = data.get("avatar", "").strip()
    avatar_url = ""
    if avatar_data.startswith("data:image/"):
        match = re.match(r'^data:image/(?P<fmt>\w+);base64,(?P<data>.+)$', avatar_data)
        if match:
            fmt = match.group('fmt').lower()
            ext = 'jpg' if fmt in ('jpeg', 'jpg') else fmt
            try:
                raw = base64.b64decode(match.group('data'))
                filename = f'group_avatars/group_{uuid.uuid4().hex}.{ext}'
                saved_path = default_storage.save(filename, ContentFile(raw))
                avatar_url = default_storage.url(saved_path)
            except Exception:
                pass
    else:
        avatar_url = avatar_data

    with transaction.atomic():
        conversation = Conversation.objects.create(
            type=Conversation.Type.GROUP,
            name=name,
            avatar=avatar_url,
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
            valid_users = []
            for target in User.objects.filter(id__in=unique_ids, is_active=True):
                privacy = _privacy_settings_for(target)
                invite_policy = privacy.who_can_add_me_to_groups
                if invite_policy == 'nobody':
                    continue
                if invite_policy == 'contacts' and not _are_contacts(target, request.user):
                    continue
                valid_users.append(target.id)
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
    if not member or member.status != ConversationMember.Status.ACTIVE:
        return JsonResponse({"error": "Not an active member of this group."}, status=403)
    if member.role != ConversationMember.Role.OWNER:
        return JsonResponse({"error": "Only the group owner can update the group."}, status=403)

    try:
        conversation = Conversation.objects.get(
            id=conversation_id, type=Conversation.Type.GROUP, status=Conversation.Status.ACTIVE,
        )
    except Conversation.DoesNotExist:
        return JsonResponse({"error": "Group not found or not active."}, status=404)

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
    """Create a two-step group invitation.

    Any active group member can invite. Owner/admin invitations skip admin review,
    but the invited user still has to accept before membership is created.
    """
    if request.method != "POST":
        return JsonResponse({"error": "Method not allowed."}, status=405)

    actor = _get_member(conversation_id, request.user)
    if not actor or actor.status != ConversationMember.Status.ACTIVE:
        return JsonResponse({"error": "Not an active member of this group."}, status=403)

    try:
        conversation = Conversation.objects.get(
            id=conversation_id, type=Conversation.Type.GROUP, status=Conversation.Status.ACTIVE,
        )
    except Conversation.DoesNotExist:
        return JsonResponse({"error": "Group not found or not active."}, status=404)

    data = _json_body(request)
    user_id = data.get("user_id")
    if not user_id:
        return JsonResponse({"error": "user_id is required."}, status=400)

    try:
        target = User.objects.get(id=user_id)
    except User.DoesNotExist:
        return JsonResponse({"error": "User not found."}, status=404)

    privacy, _ = UserPrivacySettings.objects.get_or_create(user=target)
    invite_policy = privacy.who_can_add_me_to_groups
    if invite_policy == 'nobody':
        return JsonResponse({"error": "This user does not allow group invitations."}, status=403)
    if invite_policy == 'contacts' and not _are_contacts(target, request.user):
        return JsonResponse({"error": "This user only allows contacts to invite them."}, status=403)

    if ConversationMember.objects.filter(
        conversation=conversation, user=target, status=ConversationMember.Status.ACTIVE,
    ).exists():
        return JsonResponse({"error": "User is already a member."}, status=409)

    initial_status = (
        GroupInvitation.Status.PENDING_INVITEE
        if actor.role in (ConversationMember.Role.OWNER, ConversationMember.Role.ADMIN)
        else GroupInvitation.Status.PENDING_ADMIN
    )
    try:
        invitation, created = GroupInvitation.objects.get_or_create(
            conversation=conversation,
            invitee=target,
            status__in=[
                GroupInvitation.Status.PENDING_ADMIN,
                GroupInvitation.Status.PENDING_INVITEE,
            ],
            defaults={
                "inviter": request.user,
                "status": initial_status,
                "reviewed_by": request.user if initial_status == GroupInvitation.Status.PENDING_INVITEE else None,
                "reviewed_at": timezone.now() if initial_status == GroupInvitation.Status.PENDING_INVITEE else None,
            },
        )
    except TypeError:
        # get_or_create cannot express __in lookup as creation kwargs on older Django paths.
        invitation = GroupInvitation.objects.filter(
            conversation=conversation,
            invitee=target,
            status__in=[
                GroupInvitation.Status.PENDING_ADMIN,
                GroupInvitation.Status.PENDING_INVITEE,
            ],
        ).first()
        if invitation:
            created = False
        else:
            invitation = GroupInvitation.objects.create(
                conversation=conversation,
                inviter=request.user,
                invitee=target,
                status=initial_status,
                reviewed_by=request.user if initial_status == GroupInvitation.Status.PENDING_INVITEE else None,
                reviewed_at=timezone.now() if initial_status == GroupInvitation.Status.PENDING_INVITEE else None,
            )
            created = True
    except IntegrityError:
        invitation = GroupInvitation.objects.filter(
            conversation=conversation,
            invitee=target,
            status__in=[
                GroupInvitation.Status.PENDING_ADMIN,
                GroupInvitation.Status.PENDING_INVITEE,
            ],
        ).first()
        created = False

    if not invitation:
        return JsonResponse({"error": "Invitation already exists."}, status=409)

    # Send WebSocket push notification
    channel_layer = get_channel_layer()
    if invitation.status == GroupInvitation.Status.PENDING_INVITEE:
        async_to_sync(ChatConsumer.broadcast_group_invitation)(
            channel_layer, _group_invitation_push_payload(invitation), target.id,
        )
    elif invitation.status == GroupInvitation.Status.PENDING_ADMIN:
        async_to_sync(ChatConsumer.broadcast_group_invitation_to_admins)(
            channel_layer, conversation.id,
        )

    return JsonResponse({
        "status": invitation.status,
        "invitation_id": invitation.id,
        "user_id": target.id,
        "requires_admin_approval": invitation.status == GroupInvitation.Status.PENDING_ADMIN,
        "requires_invitee_approval": invitation.status == GroupInvitation.Status.PENDING_INVITEE,
    }, status=201 if created else 200)


@login_required(login_url='login')
@require_GET
def group_invite_candidates_view(request, conversation_id):
    """Return group invite candidates with availability reasons."""
    actor = _get_member(conversation_id, request.user)
    if not actor or actor.status != ConversationMember.Status.ACTIVE:
        return JsonResponse({"error": "Not an active member of this group."}, status=403)

    try:
        conversation = Conversation.objects.get(
            id=conversation_id,
            type=Conversation.Type.GROUP,
            status=Conversation.Status.ACTIVE,
        )
    except Conversation.DoesNotExist:
        return JsonResponse({"error": "Group not found or not active."}, status=404)

    membership_by_user_id = {
        membership.user_id: membership
        for membership in ConversationMember.objects.filter(
            conversation=conversation,
        ).select_related("user__profile")
    }
    pending_by_user_id = {
        invitation.invitee_id: invitation
        for invitation in GroupInvitation.objects.filter(
            conversation=conversation,
            status__in=[
                GroupInvitation.Status.PENDING_ADMIN,
                GroupInvitation.Status.PENDING_INVITEE,
            ],
        ).select_related("invitee__profile")
    }

    candidates_by_id = {}

    def add_candidate(user, source):
        if not user:
            return
        existing = candidates_by_id.get(user.id, {})
        sources = set(existing.get("sources", []))
        sources.add(source)
        membership = membership_by_user_id.get(user.id)
        pending = pending_by_user_id.get(user.id)
        can_invite = True
        reason_code = "available"
        reason = "可邀请"

        if user.id == request.user.id:
            can_invite = False
            reason_code = "self"
            reason = "当前用户自己"
        elif membership and membership.status == ConversationMember.Status.ACTIVE:
            can_invite = False
            reason_code = "already_member"
            reason = "已经在群里的成员"
        elif pending:
            can_invite = False
            reason_code = "pending_invitation"
            reason = "已经有待处理邀请的人"
        else:
            privacy, _ = UserPrivacySettings.objects.get_or_create(user=user)
            if privacy.who_can_add_me_to_groups == "nobody":
                can_invite = False
                reason_code = "not_allowed"
                reason = "不允许被邀请进群的人"
            elif privacy.who_can_add_me_to_groups == "contacts" and not _are_contacts(user, request.user):
                can_invite = False
                reason_code = "not_allowed"
                reason = "不允许被邀请进群的人"
            elif membership and membership.status in (
                ConversationMember.Status.LEFT,
                ConversationMember.Status.REMOVED,
            ):
                reason_code = "former_member"
                reason = "曾经退群，可重新邀请"

        display_name = _display_name(user)
        candidates_by_id[user.id] = {
            "user_id": user.id,
            "username": user.username,
            "display_name": display_name,
            "initials": _initials(display_name),
            "avatar_color": _avatar_color(display_name),
            "avatar_url": _avatar_url(request, user),
            "user_type": _user_type_for(user),
            "can_invite": can_invite,
            "reason_code": reason_code,
            "reason": reason,
            "membership_status": membership.status if membership else None,
            "invitation_status": pending.status if pending else None,
            "sources": sorted(sources),
        }

    add_candidate(request.user, "self")

    contacts = Contact.objects.filter(
        Q(user=request.user) | Q(contact=request.user),
    ).select_related("user__profile", "contact__profile")
    for entry in contacts:
        peer = entry.contact if entry.user_id == request.user.id else entry.user
        add_candidate(peer, "contact")

    for membership in membership_by_user_id.values():
        add_candidate(membership.user, "group_member")

    for invitation in pending_by_user_id.values():
        add_candidate(invitation.invitee, "pending_invitation")

    candidates = sorted(
        candidates_by_id.values(),
        key=lambda item: (
            0 if item["can_invite"] else 1,
            item["reason_code"],
            (item["display_name"] or item["username"]).lower(),
        ),
    )
    return JsonResponse({"candidates": candidates})


@login_required(login_url='login')
@require_GET
def pending_group_invitations_view(request):
    """Return group invitations waiting for the current user's approval."""
    invitations = (
        GroupInvitation.objects
        .filter(invitee=request.user, status=GroupInvitation.Status.PENDING_INVITEE)
        .select_related("conversation", "inviter__profile")
        .order_by("-created_at")
    )
    return JsonResponse({
        "invitations": [
            {
                "id": inv.id,
                "status": inv.status,
                "group_id": inv.conversation_id,
                "group_name": inv.conversation.name or f"Group #{inv.conversation_id}",
                "group_avatar": inv.conversation.avatar,
                "group_initials": _initials(inv.conversation.name or "G"),
                "group_avatar_color": _avatar_color(inv.conversation.name or "G"),
                "inviter_id": inv.inviter_id,
                "inviter_username": inv.inviter.username,
                "inviter_display_name": _display_name(inv.inviter),
                "created_at": inv.created_at.isoformat(),
            }
            for inv in invitations
        ]
    })


def _activate_group_invitation(invitation, actor):
    conversation = invitation.conversation
    member, _ = ConversationMember.objects.update_or_create(
        conversation=conversation,
        user=invitation.invitee,
        defaults={
            "role": ConversationMember.Role.MEMBER,
            "status": ConversationMember.Status.ACTIVE,
            "left_at": None,
        },
    )
    invitation.status = GroupInvitation.Status.ACCEPTED
    invitation.responded_at = timezone.now()
    invitation.save(update_fields=["status", "responded_at", "updated_at"])
    conversation.membership_version = F('membership_version') + 1
    conversation.save(update_fields=['membership_version', 'updated_at'])
    conversation.refresh_from_db(fields=['membership_version'])
    _broadcast_member_change(
        group_id=conversation.pk,
        change='member_added',
        actor_id=actor.pk,
        affected_user_id=member.user_id,
        membership_version=conversation.membership_version,
    )
    return member


def _group_invitation_push_payload(invitation):
    conversation = invitation.conversation
    inviter = invitation.inviter
    return {
        'invitation_id': invitation.id,
        'group_id': invitation.conversation_id,
        'group_name': conversation.name or f'Group #{invitation.conversation_id}',
        'group_avatar': conversation.avatar,
        'group_initials': _initials(conversation.name or "G"),
        'group_avatar_color': _avatar_color(conversation.name or "G"),
        'inviter_id': invitation.inviter_id,
        'inviter_username': inviter.username,
        'inviter_display_name': _display_name(inviter),
        'status': invitation.status,
        'created_at': invitation.created_at.isoformat(),
    }


@login_required(login_url='login')
@require_POST
def group_invitation_approve_view(request, invitation_id):
    invitation = GroupInvitation.objects.select_related(
        "conversation", "invitee", "inviter",
    ).filter(pk=invitation_id).first()
    if not invitation:
        return JsonResponse({"error": "Invitation not found."}, status=404)
    actor = _get_member(invitation.conversation_id, request.user)
    if not actor or actor.status != ConversationMember.Status.ACTIVE:
        return JsonResponse({"error": "Not an active member of this group."}, status=403)
    if actor.role not in (ConversationMember.Role.OWNER, ConversationMember.Role.ADMIN):
        return JsonResponse({"error": "Admin permission required."}, status=403)
    if invitation.status != GroupInvitation.Status.PENDING_ADMIN:
        return JsonResponse({"error": "Invitation is not waiting for admin approval."}, status=409)
    invitation.status = GroupInvitation.Status.PENDING_INVITEE
    invitation.reviewed_by = request.user
    invitation.reviewed_at = timezone.now()
    invitation.save(update_fields=["status", "reviewed_by", "reviewed_at", "updated_at"])

    # Notify the invitee via WebSocket
    channel_layer = get_channel_layer()
    async_to_sync(ChatConsumer.broadcast_group_invitation)(
        channel_layer, _group_invitation_push_payload(invitation), invitation.invitee_id,
    )

    return JsonResponse({"status": invitation.status, "invitation_id": invitation.id})


@login_required(login_url='login')
@require_POST
def group_invitation_reject_view(request, invitation_id):
    invitation = GroupInvitation.objects.select_related("conversation").filter(pk=invitation_id).first()
    if not invitation:
        return JsonResponse({"error": "Invitation not found."}, status=404)
    actor = _get_member(invitation.conversation_id, request.user)
    is_admin = actor and actor.status == ConversationMember.Status.ACTIVE and actor.role in (
        ConversationMember.Role.OWNER,
        ConversationMember.Role.ADMIN,
    )
    is_invitee = invitation.invitee_id == request.user.pk
    if not (is_admin or is_invitee):
        return JsonResponse({"error": "Permission denied."}, status=403)
    if invitation.status not in (GroupInvitation.Status.PENDING_ADMIN, GroupInvitation.Status.PENDING_INVITEE):
        return JsonResponse({"error": "Invitation is no longer pending."}, status=409)
    previous_status = invitation.status
    invitation.status = GroupInvitation.Status.REJECTED
    invitation.responded_at = timezone.now()
    if is_admin and previous_status == GroupInvitation.Status.PENDING_ADMIN:
        invitation.reviewed_by = request.user
        invitation.reviewed_at = timezone.now()
    invitation.save(update_fields=["status", "responded_at", "reviewed_by", "reviewed_at", "updated_at"])
    return JsonResponse({"status": invitation.status, "invitation_id": invitation.id})


@login_required(login_url='login')
@require_POST
def group_invitation_accept_view(request, invitation_id):
    invitation = GroupInvitation.objects.select_related("conversation", "invitee").filter(pk=invitation_id).first()
    if not invitation:
        return JsonResponse({"error": "Invitation not found."}, status=404)
    if invitation.invitee_id != request.user.pk:
        return JsonResponse({"error": "Only the invited user can accept this invitation."}, status=403)
    if invitation.status != GroupInvitation.Status.PENDING_INVITEE:
        return JsonResponse({"error": "Invitation is not ready for invitee approval."}, status=409)
    member = _activate_group_invitation(invitation, request.user)
    return JsonResponse({
        "status": invitation.status,
        "invitation_id": invitation.id,
        "group_id": invitation.conversation_id,
        "user_id": member.user_id,
    })


@login_required(login_url='login')
def remove_member_view(request, conversation_id):
    """Remove a member from a group. Owner / admin only.
    Only the owner can remove admins; admins can only remove regular members."""
    if request.method != "POST":
        return JsonResponse({"error": "Method not allowed."}, status=405)

    actor = _get_member(conversation_id, request.user)
    if not actor or actor.status != ConversationMember.Status.ACTIVE:
        return JsonResponse({"error": "You are not an active member of this group."}, status=403)
    if actor.role not in (ConversationMember.Role.OWNER, ConversationMember.Role.ADMIN):
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

    # Admins can only remove regular members; only the owner can remove admins
    if target_member.role == ConversationMember.Role.ADMIN and actor.role != ConversationMember.Role.OWNER:
        return JsonResponse({"error": "Only the group owner can remove admins."}, status=403)

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
    if not member or member.status != ConversationMember.Status.ACTIVE:
        return JsonResponse({"error": "Not an active member of this group."}, status=403)
    if member.role != ConversationMember.Role.OWNER:
        return JsonResponse({"error": "Only the group owner can disband the group."}, status=403)

    try:
        conversation = Conversation.objects.get(
            id=conversation_id, type=Conversation.Type.GROUP, status=Conversation.Status.ACTIVE,
        )
    except Conversation.DoesNotExist:
        return JsonResponse({"error": "Group not found or not active."}, status=404)

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


@login_required(login_url='login')
def leave_group_view(request, conversation_id):
    """Leave a group. Any active member can leave."""
    if request.method != "POST":
        return JsonResponse({"error": "Method not allowed."}, status=405)

    member = _get_member(conversation_id, request.user)
    if not member or member.status != ConversationMember.Status.ACTIVE:
        return JsonResponse({"error": "You are not a member of this group."}, status=403)

    try:
        conversation = Conversation.objects.get(
            id=conversation_id, type=Conversation.Type.GROUP
        )
    except Conversation.DoesNotExist:
        return JsonResponse({"error": "Group not found."}, status=404)

    # If owner and has other members, require transfer first
    if member.role == ConversationMember.Role.OWNER:
        other_active = ConversationMember.objects.filter(
            conversation=conversation,
            status=ConversationMember.Status.ACTIVE,
        ).exclude(user=request.user).exists()
        if other_active:
            return JsonResponse(
                {"error": "You are the owner. Transfer ownership before leaving."},
                status=403,
            )
        # Owner is the only member - disband instead
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
        return JsonResponse({"status": "ok", "group_dissolved": True})

    member.status = ConversationMember.Status.LEFT
    member.left_at = timezone.now()
    member.save(update_fields=["status", "left_at"])

    conversation.membership_version = F('membership_version') + 1
    conversation.save(update_fields=['membership_version', 'updated_at'])
    conversation.refresh_from_db(fields=['membership_version'])
    _broadcast_member_change(
        group_id=conversation.pk,
        change='member_left',
        actor_id=request.user.pk,
        affected_user_id=request.user.pk,
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
    ).select_related('user__profile')

    return JsonResponse({
        "group_id": conversation.id,
        "membership_version": conversation.membership_version,
        "members": [
            {
                "user_id": m.user_id,
                "username": m.user.username,
                "display_name": _display_name(m.user),
                "initials": _initials(_display_name(m.user)),
                "avatar_color": _avatar_color(_display_name(m.user)),
                "avatar_url": _avatar_url(request, m.user),
                "role": m.role,
                "is_secure": m.user.public_keys.filter(is_active=True).exists(),
            }
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
    _apply_auto_delete_for_member(member)

    page_number = request.GET.get("page", 1)
    per_page = _parse_int(request.GET.get("per_page"), 30, min_value=1, max_value=100)

    recipient_queryset = (
        _visible_group_recipients_queryset(member)
        .select_related("group_message", "group_message__sender__profile")
        .order_by("-group_message__created_at")
    )

    paginator = Paginator(recipient_queryset, per_page)
    page_obj = paginator.get_page(page_number)

    messages_data = [
        {
            "id": r.group_message.id,
            "group_id": conversation_id,
            "sender_id": r.group_message.sender_id,
            "receiver_id": r.receiver_id,
            "sender_username": r.group_message.sender.username,
            "sender_name": _display_name(r.group_message.sender),
            "sender_initials": _initials(_display_name(r.group_message.sender)),
            "sender_avatar_color": _avatar_color(_display_name(r.group_message.sender)),
            "sender_avatar_url": _avatar_url(request, r.group_message.sender),
            "message_type": r.group_message.message_type,
            "ciphertext": (
                r.group_message.sender_copy_ciphertext
                if r.group_message.sender_id == request.user.pk and r.group_message.sender_copy_ciphertext
                else r.ciphertext
            ),
            "nonce": (
                r.group_message.sender_copy_nonce
                if r.group_message.sender_id == request.user.pk and r.group_message.sender_copy_nonce
                else r.nonce
            ),
            "auth_tag": (
                r.group_message.sender_copy_auth_tag
                if r.group_message.sender_id == request.user.pk and r.group_message.sender_copy_auth_tag
                else r.auth_tag
            ),
            "algorithm": r.algorithm,
            "sender_key_version": r.sender_key_version or 0,
            "receiver_key_version": r.receiver_key_version or 0,
            "sender_ephemeral_public_key": (
                r.group_message.sender_copy_ephemeral_public_key
                if r.group_message.sender_id == request.user.pk and r.group_message.sender_copy_ephemeral_public_key
                else r.sender_ephemeral_public_key
            ),
            "reply_to_message_id": r.group_message.reply_to_message_id,
            "membership_version": r.membership_version or 0,
            "file_id": r.group_message.file_id_id,
            "file": (
                _build_file_sub_object(r.group_message.file_id, request.user.pk)
                if r.group_message.file_id_id else None
            ),
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

    member = _get_active_member(conversation_id, request.user)
    _apply_auto_delete_for_member(member)
    conversation = member.conversation
    if conversation.type == Conversation.Type.SINGLE:
        peer = (
            ConversationMember.objects
            .filter(conversation_id=conversation_id, status=ConversationMember.Status.ACTIVE)
            .exclude(user=request.user)
            .select_related("user")
            .first()
        )
        if peer and (_is_blocked_by(request.user, peer.user) or _is_blocked_by(peer.user, request.user)):
            return JsonResponse(
                {"error": "This conversation is blocked."},
                status=403,
            )

    page_number = request.GET.get("page", 1)
    per_page = _parse_int(request.GET.get("per_page"), 30, min_value=1, max_value=100)

    queryset = (
        _visible_private_messages_queryset(member)
        .select_related('sender', 'sender__profile', 'file_id')
        .order_by("-created_at")
    )

    paginator = Paginator(queryset, per_page)
    page_obj = paginator.get_page(page_number)

    messages_data = [
        _private_message_payload_for_viewer(msg, request.user.pk, request=request)
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

    # Apply the Privacy & Security setting first; fall back to the older
    # presence-specific visibility for clients that still use it.
    last_seen_visibility = _privacy_settings_for(target).last_seen_visibility
    if last_seen_visibility == 'nobody' or presence.presence_visibility == UserPresence.Visibility.NOBODY:
        return JsonResponse({
            'user_id': target.pk,
            'is_online': False,
            'last_seen': None,
            'status': 'offline',
        })

    if (
        last_seen_visibility == 'contacts'
        or presence.presence_visibility == UserPresence.Visibility.CONTACTS
    ) and not _are_contacts(request.user, target):
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

    # Check block status before attempting to send. If a private conversation
    # already exists and both users are active members, contact/privacy rules
    # should not block normal in-conversation sends.
    receiver_id = data.get('receiver_id')
    if receiver_id:
        try:
            receiver = User.objects.get(id=receiver_id, is_active=True)
        except User.DoesNotExist:
            return JsonResponse({'error': 'receiver_not_found', 'detail': 'Receiver not found.'}, status=404)

        if receiver == request.user:
            return JsonResponse({'error': 'conversation_forbidden', 'detail': 'Cannot send messages to yourself.'}, status=403)
        if _is_blocked_by(receiver, request.user):
            return JsonResponse({'error': 'conversation_forbidden', 'detail': 'You have been blocked by this user.'}, status=403)
        if _is_blocked_by(request.user, receiver):
            return JsonResponse({'error': 'conversation_forbidden', 'detail': 'You have blocked this user. Unblock them first.'}, status=403)

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

    sender_message = ChatConsumer.serialize_private_message_by_id(
        message['message_id'],
        viewer_id=request.user.pk,
    )
    return JsonResponse(sender_message, status=201)


@login_required(login_url='login')
def send_group_message_view(request, conversation_id):
    """Persist a group encrypted message over HTTP and broadcast it.

    This is the HTTP fallback when the WebSocket is unavailable.
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'Method not allowed.'}, status=405)

    data = _json_body(request)
    data['group_id'] = conversation_id

    try:
        accepted, recipients = async_to_sync(ChatConsumer.create_group_message)(
            request.user.pk, data,
        )
    except Exception as error:
        code = getattr(error, 'code', 'invalid_payload')
        detail = getattr(error, 'message', str(error))
        status = 404 if code == 'conversation_not_found' else 400
        if code == 'conversation_forbidden':
            status = 403
        return JsonResponse({'error': code, 'detail': detail}, status=status)

    channel_layer = get_channel_layer()
    for recipient_data in recipients:
        if recipient_data['receiver_id'] == request.user.pk:
            continue
        async_to_sync(channel_layer.group_send)(
            ChatConsumer.user_group(recipient_data['receiver_id']),
            {'type': 'message.group.new', 'data': recipient_data},
        )

    return JsonResponse(accepted, status=201)


# ---------------------------------------------------------------------------
# P2 T05: Data & Storage API
# ---------------------------------------------------------------------------

_STORAGE_DEFAULT_SETTINGS = {
    'auto_download_enabled': True,
    'auto_download': {
        'mobile_data': {'photos': True, 'videos': True, 'files': 3},
        'wifi': {'photos': True, 'videos': True, 'files': 3},
        'roaming': {'photos': True, 'videos': True, 'files': 3},
    },
    'file_size_limit_mb': {
        'photos': 10,
        'videos': 50,
        'files': 3,
    },
    'cache_retention_days': 7,
    'cache_max_size_mb': 0,
    'cleared_cache_baseline': {},
}


def _storage_settings_obj(user):
    settings_obj, _ = UserStorageSettings.objects.get_or_create(user=user)
    return settings_obj


def _merged_storage_settings(settings_obj):
    return _deep_merge(_STORAGE_DEFAULT_SETTINGS, settings_obj.settings_json or {})


def _storage_estimates_for_user(user):
    private_qs = EncryptedMessage.objects.filter(
        Q(sender=user) | Q(receiver=user),
    )
    private_images = private_qs.filter(message_type=EncryptedMessage.MessageType.IMAGE).count()
    private_files = private_qs.filter(message_type=EncryptedMessage.MessageType.FILE).count()
    private_stickers = private_qs.filter(message_type=EncryptedMessage.MessageType.STICKER).count()
    private_other = private_qs.filter(
        message_type__in=[
            EncryptedMessage.MessageType.TEXT,
            EncryptedMessage.MessageType.SYSTEM,
        ],
    ).count()

    group_images = GroupMessageRecipient.objects.filter(
        receiver=user,
        group_message__message_type=GroupMessage.MessageType.IMAGE,
    ).count()
    group_files = GroupMessageRecipient.objects.filter(
        receiver=user,
        group_message__message_type=GroupMessage.MessageType.FILE,
    ).count()
    group_stickers = GroupMessageRecipient.objects.filter(
        receiver=user,
        group_message__message_type=GroupMessage.MessageType.STICKER,
    ).count()
    group_other = GroupMessageRecipient.objects.filter(
        receiver=user,
        group_message__message_type__in=[
            GroupMessage.MessageType.TEXT,
            GroupMessage.MessageType.SYSTEM,
        ],
    ).count()

    est_image = 200 * 1024
    est_video = 1200 * 1024
    est_sticker = 30 * 1024
    est_other = 2 * 1024

    return {
        'images': {
            'size_bytes': (private_images + group_images) * est_image,
            'count': private_images + group_images,
            'label': 'Images',
        },
        'videos': {
            'size_bytes': (private_files + group_files) * est_video,
            'count': private_files + group_files,
            'label': 'Video files',
        },
        'stickers': {
            'size_bytes': (private_stickers + group_stickers) * est_sticker,
            'count': private_stickers + group_stickers,
            'label': 'Stickers and emojis',
        },
        'other': {
            'size_bytes': (private_other + group_other) * est_other,
            'count': private_other + group_other,
            'label': 'Other',
        },
        'video_stream_chunks': {
            'size_bytes': 0,
            'count': 0,
            'label': 'Cached video stream chunks',
        },
    }


def _storage_stats_payload(user):
    settings_obj = _storage_settings_obj(user)
    settings_data = _merged_storage_settings(settings_obj)
    estimates = _storage_estimates_for_user(user)
    baselines = settings_data.get('cleared_cache_baseline') or {}
    categories = {}
    for key, value in estimates.items():
        baseline = int(baselines.get(key, 0) or 0)
        size_bytes = max(0, int(value.get('size_bytes', 0)) - baseline)
        categories[key] = {
            **value,
            'size_bytes': size_bytes,
            'cleared_baseline_bytes': baseline,
        }

    total_bytes = sum(item['size_bytes'] for item in categories.values())
    cache_max_size_mb = int(settings_data.get('cache_max_size_mb') or 0)
    quota_bytes = (cache_max_size_mb or 50) * 1024 * 1024
    return {
        'categories': categories,
        'total_bytes': total_bytes,
        'quota_bytes': quota_bytes,
        'usage_percent': round((total_bytes / quota_bytes) * 100, 1) if quota_bytes else 0,
        'settings': settings_data,
    }


@login_required(login_url='login')
def storage_stats_view(request):
    """Return storage usage statistics for the current user.

    Provides estimated sizes for each category (images, video, stickers,
    other, video stream chunks) based on database record counts and
    approximate per-item sizes.  A future T24 backend will replace these
    estimates with real file-system measurements.
    """
    return JsonResponse(_storage_stats_payload(request.user))


@login_required(login_url='login')
def storage_clear_view(request):
    """Clear cached data for specific categories.

    Accepts: {"categories": ["images", "videos", "stickers", "other",
    "video_stream_chunks"]} or "all".
    Currently a stub - real file-system cleanup depends on T24.
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'Method not allowed.'}, status=405)

    data = _json_body(request)
    categories = data.get('categories', [])

    if not categories:
        return JsonResponse({'error': 'categories list is required.'}, status=400)

    valid_categories = {'images', 'videos', 'stickers', 'other', 'video_stream_chunks', 'all'}
    estimates = _storage_estimates_for_user(request.user)
    settings_obj = _storage_settings_obj(request.user)
    settings_data = _merged_storage_settings(settings_obj)
    baselines = settings_data.get('cleared_cache_baseline') or {}
    cleared = []
    skipped = []

    for cat in categories:
        if cat not in valid_categories:
            skipped.append(cat)
            continue
        if cat == 'all':
            for key, value in estimates.items():
                baselines[key] = int(value.get('size_bytes', 0) or 0)
            cleared = sorted(k for k in valid_categories if k != 'all')
            break
        baselines[cat] = int(estimates.get(cat, {}).get('size_bytes', 0) or 0)
        cleared.append(cat)

    settings_data['cleared_cache_baseline'] = baselines
    settings_obj.settings_json = settings_data
    settings_obj.save(update_fields=['settings_json', 'updated_at'])

    return JsonResponse({
        'status': 'ok',
        'cleared': cleared,
        'skipped': skipped,
        'stats': _storage_stats_payload(request.user),
    })


@login_required(login_url='login')
def storage_settings_view(request):
    """Get or update server-side storage settings for the user.

    GET  - return current settings.
    POST - merge the provided settings keys.
    Persisted in UserStorageSettings (DB-backed, survives session expiry).
    """
    ss = _storage_settings_obj(request.user)

    if request.method == 'GET':
        return JsonResponse({'settings': _merged_storage_settings(ss)})

    if request.method == 'POST':
        data = _json_body(request)
        current = _merged_storage_settings(ss)
        updated = _deep_merge(current, data)
        updated['auto_download_enabled'] = bool(updated.get('auto_download_enabled'))
        updated['cache_retention_days'] = _parse_int(
            updated.get('cache_retention_days'), 7, min_value=0, max_value=365,
        )
        updated['cache_max_size_mb'] = _parse_int(
            updated.get('cache_max_size_mb'), 0, min_value=0, max_value=2048,
        )
        file_limits = updated.get('file_size_limit_mb') or {}
        updated['file_size_limit_mb'] = {
            'photos': _parse_int(file_limits.get('photos'), 10, min_value=0, max_value=1024),
            'videos': _parse_int(file_limits.get('videos'), 50, min_value=0, max_value=2048),
            'files': _parse_int(file_limits.get('files'), 3, min_value=0, max_value=2048),
        }
        ss.settings_json = updated
        ss.save(update_fields=['settings_json', 'updated_at'])
        return JsonResponse({'status': 'ok', 'settings': updated})

    return JsonResponse({'error': 'Method not allowed.'}, status=405)


def _deep_merge(base, override):
    """Recursively merge *override* into *base*. Returns a new dict."""
    result = copy.deepcopy(base)
    if not isinstance(override, dict):
        return result
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


# ---------------------------------------------------------------------------
# P2 T06: Privacy & Security API
# ---------------------------------------------------------------------------

_PRIVACY_STRING_FIELDS = {
    'last_seen_visibility',
    'profile_photo_visibility',
    'phone_number_visibility',
    'bio_visibility',
    'forward_link_visibility',
    'birthday_visibility',
    'gifts_visibility',
    'saved_music_visibility',
    'who_can_add_me_to_groups',
}

# Permission fields only accept 'everyone' or 'contacts' - 'nobody' is NOT valid here
_PRIVACY_PERMISSION_FIELDS = {
    'who_can_send_messages',
    'who_can_voice_video_call',
}

_PRIVACY_BOOL_FIELDS = {
    'sensitive_content_filter',
    'passcode_lock_enabled',
    'two_step_verification_enabled',
    'passkey_enabled',
}

_PRIVACY_INT_FIELDS = {
    'auto_delete_messages_days',
}

_PRIVACY_EMAIL_FIELDS = {
    'login_email',
}

_ALLOWED_PRIVACY_FIELDS = (
    _PRIVACY_STRING_FIELDS
    | _PRIVACY_PERMISSION_FIELDS
    | _PRIVACY_BOOL_FIELDS
    | _PRIVACY_INT_FIELDS
    | _PRIVACY_EMAIL_FIELDS
)


def _serialize_privacy_settings(ps):
    """Convert a UserPrivacySettings instance to a JSON-safe dict."""
    return {
        'last_seen_visibility': ps.last_seen_visibility,
        'profile_photo_visibility': ps.profile_photo_visibility,
        'phone_number_visibility': ps.phone_number_visibility,
        'bio_visibility': ps.bio_visibility,
        'forward_link_visibility': ps.forward_link_visibility,
        'birthday_visibility': ps.birthday_visibility,
        'gifts_visibility': ps.gifts_visibility,
        'saved_music_visibility': ps.saved_music_visibility,
        'who_can_add_me_to_groups': ps.who_can_add_me_to_groups,
        'who_can_send_messages': ps.who_can_send_messages,
        'who_can_voice_video_call': ps.who_can_voice_video_call,
        'auto_delete_messages_days': ps.auto_delete_messages_days,
        'sensitive_content_filter': ps.sensitive_content_filter,
        'passcode_lock_enabled': ps.passcode_lock_enabled,
        'two_step_verification_enabled': ps.two_step_verification_enabled,
        'passkey_enabled': ps.passkey_enabled,
        'login_email': ps.login_email,
    }


@login_required(login_url='login')
def privacy_settings_view(request):
    """Get or update privacy settings for the current user."""
    ps, _ = UserPrivacySettings.objects.get_or_create(user=request.user)

    if request.method == 'GET':
        return JsonResponse({'settings': _serialize_privacy_settings(ps)})

    if request.method == 'POST':
        data = _json_body(request)
        if not data:
            return JsonResponse({'error': 'Invalid JSON.'}, status=400)

        updated_fields = []

        for field in _PRIVACY_STRING_FIELDS:
            if field in data and data[field] in ('everyone', 'contacts', 'nobody'):
                setattr(ps, field, data[field])
                updated_fields.append(field)

        # Permission fields only accept 'everyone' or 'contacts' - NOT 'nobody'
        for field in _PRIVACY_PERMISSION_FIELDS:
            if field in data and data[field] in ('everyone', 'contacts'):
                setattr(ps, field, data[field])
                updated_fields.append(field)

        for field in _PRIVACY_BOOL_FIELDS:
            if field in data:
                raw = data[field]
                # Accept JSON booleans, 0/1 ints, or string "true"/"false"
                if isinstance(raw, bool):
                    setattr(ps, field, raw)
                elif isinstance(raw, int):
                    setattr(ps, field, bool(raw))
                elif isinstance(raw, str):
                    setattr(ps, field, raw.lower() in ('true', '1', 'on', 'yes'))
                else:
                    continue
                updated_fields.append(field)

        for field in _PRIVACY_INT_FIELDS:
            if field in data:
                try:
                    val = int(data[field])
                    if val >= 0 and val <= 365:
                        setattr(ps, field, val)
                        updated_fields.append(field)
                except (ValueError, TypeError):
                    pass

        for field in _PRIVACY_EMAIL_FIELDS:
            if field in data:
                value = str(data[field]).strip()
                # Allow clearing the email (empty string)
                if value == '':
                    setattr(ps, field, '')
                    updated_fields.append(field)
                elif '@' in value and len(value) <= 254:
                    setattr(ps, field, value)
                    updated_fields.append(field)
                # Invalid emails are silently skipped (not saved)

        if updated_fields:
            ps.save(update_fields=updated_fields + ['updated_at'])

        return JsonResponse({
            'status': 'ok',
            'settings': _serialize_privacy_settings(ps),
            'updated_fields': updated_fields,
        })

# ──── T27: Auto-delete messages API ──────────────────────────────────────────────────────────────────────

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
        seconds = None if data.get('disabled') else _parse_int(data.get('seconds', 0) or 0, 0, min_value=0)
        seconds = seconds if seconds and seconds > 0 else None
        Conversation.objects.filter(
            type=Conversation.Type.SINGLE,
            created_by=request.user,
        ).update(auto_delete_seconds=seconds)
        return JsonResponse({'global_auto_delete_seconds': seconds, 'status': 'ok'})
    return JsonResponse({'error': 'Method not allowed.'}, status=405)


@login_required(login_url='login')
def blocked_users_list_view(request):
    """Return the list of users blocked by the current user."""
    blocked_qs = BlockedUser.objects.filter(
        blocker=request.user,
    ).select_related('blocked__profile')

    blocked_list = []
    for entry in blocked_qs:
        blocked_user = entry.blocked
        try:
            nickname = blocked_user.profile.nickname or ''
        except Exception:
            nickname = ''
        blocked_list.append({
            'id': blocked_user.id,
            'username': blocked_user.username,
            'nickname': nickname,
            'blocked_at': entry.created_at.isoformat(),
        })

    return JsonResponse({'blocked_users': blocked_list})


@login_required(login_url='login')
def block_user_view(request):
    """Block a user. Also removes any existing Contact relationship."""
    if request.method != 'POST':
        return JsonResponse({'error': 'Method not allowed.'}, status=405)

    data = _json_body(request)
    user_id = data.get('user_id')
    if not user_id:
        return JsonResponse({'error': 'user_id is required.'}, status=400)

    try:
        target = User.objects.get(id=user_id, is_active=True)
    except User.DoesNotExist:
        return JsonResponse({'error': 'User not found.'}, status=404)

    if target == request.user:
        return JsonResponse({'error': 'Cannot block yourself.'}, status=400)

    # Create block if not exists
    block_entry, created = BlockedUser.objects.get_or_create(
        blocker=request.user,
        blocked=target,
    )

    # Remove any existing Contact in either direction
    Contact.objects.filter(
        (Q(user=request.user) & Q(contact=target))
        | (Q(user=target) & Q(contact=request.user)),
    ).delete()

    # Only remove the blocked user from shared groups where the current
    # user is an OWNER or ADMIN.  Cannot remove a group OWNER.  Regular
    # members blocking each other does NOT affect group membership.
    my_admin_groups = ConversationMember.objects.filter(
        user=request.user,
        status=ConversationMember.Status.ACTIVE,
        conversation__type=Conversation.Type.GROUP,
        role__in=(ConversationMember.Role.OWNER, ConversationMember.Role.ADMIN),
    ).values_list('conversation_id', flat=True)

    if my_admin_groups:
        shared_memberships = ConversationMember.objects.filter(
            user=target,
            conversation_id__in=my_admin_groups,
            status=ConversationMember.Status.ACTIVE,
        ).exclude(role=ConversationMember.Role.OWNER)

        for membership in shared_memberships:
            membership.status = ConversationMember.Status.REMOVED
            membership.left_at = timezone.now()
            membership.save(update_fields=['status', 'left_at'])

    return JsonResponse({
        'status': 'ok',
        'blocked_user_id': target.id,
        'created': created,
    })


@login_required(login_url='login')
def unblock_user_view(request):
    """Unblock a previously blocked user."""
    if request.method != 'POST':
        return JsonResponse({'error': 'Method not allowed.'}, status=405)

    data = _json_body(request)
    user_id = data.get('user_id')
    if not user_id:
        return JsonResponse({'error': 'user_id is required.'}, status=400)

    deleted_count, _ = BlockedUser.objects.filter(
        blocker=request.user,
        blocked_id=user_id,
    ).delete()

    if deleted_count == 0:
        return JsonResponse({'error': 'User is not blocked.'}, status=404)

    return JsonResponse({'status': 'ok', 'unblocked_user_id': user_id})


@login_required(login_url='login')
@require_POST
def report_conversation_view(request):
    """Create a report for a conversation the current user belongs to."""
    data = _json_body(request)
    conversation_id = data.get('conversation_id')
    if not conversation_id:
        return JsonResponse({'error': 'conversation_id is required.'}, status=400)

    member = _get_active_member(conversation_id, request.user)
    if not member:
        return JsonResponse({'error': 'Conversation not found or not a member.'}, status=404)

    reason = data.get('reason') or ChatReport.Reason.OTHER
    valid_reasons = {choice[0] for choice in ChatReport.Reason.choices}
    if reason not in valid_reasons:
        reason = ChatReport.Reason.OTHER

    report = ChatReport.objects.create(
        reporter=request.user,
        conversation=member.conversation,
        reason=reason,
        details=(data.get('details') or '')[:2000],
    )
    return JsonResponse({
        'status': 'ok',
        'report_id': report.id,
        'reason': report.reason,
    }, status=201)


@login_required(login_url='login')
def delete_synced_contacts_view(request):
    """Delete all synced contacts for the current user."""
    if request.method != 'POST':
        return JsonResponse({'error': 'Method not allowed.'}, status=405)

    deleted_count, _ = Contact.objects.filter(
        Q(user=request.user) | Q(contact=request.user),
    ).delete()

    return JsonResponse({
        'status': 'ok',
        'deleted_count': deleted_count,
        'message': f'{deleted_count} contacts removed.',
    })


@login_required(login_url='login')
def delete_account_view(request):
    """Permanently deactivate the current user's account."""
    if request.method != 'POST':
        return JsonResponse({'error': 'Method not allowed.'}, status=405)

    user = request.user

    # Anonymize personal data before deactivation
    timestamp = str(int(timezone.now().timestamp()))
    user.username = f'deleted_{timestamp}'
    user.email = ''
    user.first_name = ''
    user.last_name = ''
    user.is_active = False
    user.save()

    # Remove contacts and blocks
    Contact.objects.filter(
        Q(user=user) | Q(contact=user),
    ).delete()
    BlockedUser.objects.filter(
        Q(blocker=user) | Q(blocked=user),
    ).delete()

    # Log the user out
    logout(request)

    return JsonResponse({
        'status': 'ok',
        'message': 'Account deleted successfully.',
    })


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
        seconds = 0 if data.get('disabled') else _parse_int(data.get('seconds', 0) or 0, 0, min_value=0)
        seconds = seconds if seconds and seconds >= 0 else None
        member.auto_delete_seconds = seconds
        member.save(update_fields=['auto_delete_seconds'])
        return JsonResponse({'status': 'ok', 'auto_delete_seconds': seconds})
    return JsonResponse({'error': 'Method not allowed.'}, status=405)


# ──── T33/T34: Unified search API with scope filtering ────────────────────────────────

@login_required(login_url='login')
def search_unified_view(request):
    """Unified search across conversations, contacts, and groups with scope filter."""
    query = request.GET.get('q', '').strip()
    scope = request.GET.get('scope', 'all')
    results = {'conversations': [], 'contacts': [], 'groups': [], 'channels': []}

    if not query:
        return JsonResponse({'results': results, 'scope': scope})

    user = request.user
    contact_peer_ids = set()
    for user_id, contact_id in Contact.objects.filter(
        Q(user=user) | Q(contact=user),
    ).values_list('user_id', 'contact_id'):
        contact_peer_ids.add(contact_id if user_id == user.id else user_id)

    # Contacts search
    if scope in ('all', 'contacts', 'private_chats'):
        name_matches = User.objects.filter(
            Q(username__icontains=query) | Q(profile__nickname__icontains=query),
        ).exclude(id=user.id).filter(
            Q(id__in=contact_peer_ids) | Q(profile__user_type__in=['agent', 'bot'])
        ).distinct().select_related('profile')[:10]

        for u in name_matches:
            try:
                nickname = u.profile.nickname or ''
                user_type = u.profile.user_type
            except Exception:
                nickname = ''
                user_type = 'user'
            results['contacts'].append({
                'id': u.id, 'username': u.username,
                'nickname': nickname,
                'user_type': user_type,
                'is_contact': _are_contacts(user, u),
                'avatar_url': _avatar_url(request, u),
            })

    # Group search
    if scope in ('all', 'group_chats'):
        group_matches = Conversation.objects.filter(
            type=Conversation.Type.GROUP,
            name__icontains=query,
            status=Conversation.Status.ACTIVE,
            members__user=user,
            members__status=ConversationMember.Status.ACTIVE,
        )[:10]
        for g in group_matches:
            results['groups'].append({
                'id': g.id, 'name': g.name,
                'is_member': True,
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
                        'avatar_url': _avatar_url(request, peer.user),
                    })
        results['conversations'] = peer_convs[:10]

    # Channels (placeholder - T33)
    results['channels'] = []

    return JsonResponse({'results': results, 'scope': scope, 'query': query})


# ──── T37: Advanced group management ──────────────────────────────────────────────────────────────────────

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
@require_POST
def group_promote_view(request, conversation_id, user_id):
    """Promote a member to admin. Owner only."""
    member = _get_member(conversation_id, request.user)
    if not member or member.status != ConversationMember.Status.ACTIVE:
        return JsonResponse({'error': 'Not an active member of this group.'}, status=403)
    if member.role != ConversationMember.Role.OWNER:
        return JsonResponse({'error': 'Only the group owner can set admins.'}, status=403)
    try:
        conv = Conversation.objects.get(
            id=conversation_id, type=Conversation.Type.GROUP, status=Conversation.Status.ACTIVE,
        )
    except Conversation.DoesNotExist:
        return JsonResponse({'error': 'Group not found or not active.'}, status=404)
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
@require_POST
def group_demote_view(request, conversation_id, user_id):
    """Demote an admin to member. Owner only."""
    member = _get_member(conversation_id, request.user)
    if not member or member.status != ConversationMember.Status.ACTIVE:
        return JsonResponse({'error': 'Not an active member of this group.'}, status=403)
    if member.role != ConversationMember.Role.OWNER:
        return JsonResponse({'error': 'Only the owner can demote admins.'}, status=403)
    try:
        conv = Conversation.objects.get(
            id=conversation_id, type=Conversation.Type.GROUP, status=Conversation.Status.ACTIVE,
        )
    except Conversation.DoesNotExist:
        return JsonResponse({'error': 'Group not found or not active.'}, status=404)
    target = _get_member(conversation_id, user_id)
    if not target or target.status != ConversationMember.Status.ACTIVE:
        return JsonResponse({'error': 'Target not a member.'}, status=404)
    if target.role != ConversationMember.Role.ADMIN:
        return JsonResponse({'error': 'Target is not an admin.'}, status=409)
    target.role = ConversationMember.Role.MEMBER
    target.save(update_fields=['role'])
    conv.membership_version = F('membership_version') + 1
    conv.save(update_fields=['membership_version', 'updated_at'])
    return JsonResponse({'status': 'ok', 'user_id': user_id, 'role': 'member'})


@login_required(login_url='login')
def group_transfer_view(request, conversation_id):
    """Transfer ownership to another member. Owner only."""
    if request.method != 'POST':
        return JsonResponse({'error': 'Method not allowed.'}, status=405)
    member = _get_member(conversation_id, request.user)
    if not member or member.status != ConversationMember.Status.ACTIVE:
        return JsonResponse({'error': 'Not an active member of this group.'}, status=403)
    if member.role != ConversationMember.Role.OWNER:
        return JsonResponse({'error': 'Only the owner can transfer ownership.'}, status=403)
    try:
        conv = Conversation.objects.get(
            id=conversation_id, type=Conversation.Type.GROUP, status=Conversation.Status.ACTIVE,
        )
    except Conversation.DoesNotExist:
        return JsonResponse({'error': 'Group not found or not active.'}, status=404)
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
    conv.membership_version = F('membership_version') + 1
    conv.save(update_fields=['membership_version', 'updated_at'])
    return JsonResponse({'status': 'ok', 'new_owner_id': new_owner_id})


@login_required(login_url='login')
def group_announcement_view(request, conversation_id):
    """GET: get active announcement (all members).
    POST: create/replace (admin+). DELETE: remove (admin+)."""
    member = _get_member(conversation_id, request.user)
    if not member or member.status != ConversationMember.Status.ACTIVE:
        return JsonResponse({'error': 'Not a member.'}, status=403)

    try:
        conv = Conversation.objects.get(id=conversation_id, type=Conversation.Type.GROUP)
    except Conversation.DoesNotExist:
        return JsonResponse({'error': 'Group not found.'}, status=404)

    # GET allowed for any active member; mutation methods require admin+
    if request.method in ('POST', 'DELETE'):
        if member.role not in (ConversationMember.Role.OWNER, ConversationMember.Role.ADMIN):
            return JsonResponse({'error': 'Admin permission required.'}, status=403)

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
        mins = _parse_int(data.get('duration_minutes'), 60, min_value=1, max_value=10080)
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
    pending_invitations = []
    if member.role in (ConversationMember.Role.OWNER, ConversationMember.Role.ADMIN):
        pending_invitations = [
            {
                'id': inv.id,
                'status': inv.status,
                'inviter_id': inv.inviter_id,
                'inviter_username': inv.inviter.username,
                'invitee_id': inv.invitee_id,
                'invitee_username': inv.invitee.username,
                'invitee_display_name': _display_name(inv.invitee),
                'invitee_initials': _initials(_display_name(inv.invitee)),
                'invitee_avatar_color': _avatar_color(_display_name(inv.invitee)),
                'invitee_avatar_url': _avatar_url(request, inv.invitee),
                'created_at': inv.created_at.isoformat(),
            }
            for inv in GroupInvitation.objects.filter(
                conversation=conv,
                status=GroupInvitation.Status.PENDING_ADMIN,
            ).select_related('inviter', 'invitee__profile')
        ]

    return JsonResponse({
        'group_id': conv.id,
        'name': conv.name,
        'owner_id': conv.created_by_id,
        'membership_version': conv.membership_version,
        'pending_invitations': pending_invitations,
        'members': [
            {
                'user_id': m.user_id,
                'username': m.user.username,
                'display_name': _display_name(m.user),
                'role': m.role,
                'joined_at': m.joined_at.isoformat(),
                'initials': _initials(_display_name(m.user)),
                'avatar_color': _avatar_color(_display_name(m.user)),
                'avatar_url': _avatar_url(request, m.user),
            }
            for m in members
        ],
    })


# ═══════════════════════════════════════════════════════════════════════════
# Encrypted File Transfer API
# ═══════════════════════════════════════════════════════════════════════════

# ── Limits ────────────────────────────────────────────────────────────────

_MAX_FILE_SIZE_BYTES = 100 * 1024 * 1024       # 100 MiB
_MAX_IMAGE_SIZE_BYTES = 20 * 1024 * 1024        # 20 MiB
_MAX_STICKER_SIZE_BYTES = 2 * 1024 * 1024       # 2 MiB
_MAX_CHUNK_SIZE_BYTES = 1 * 1024 * 1024         # 1 MiB
_UPLOAD_EXPIRY_HOURS = 24
_MAX_CONCURRENT_UPLOADS = 5

_SIZE_LIMITS = {
    'image': _MAX_IMAGE_SIZE_BYTES,
    'file': _MAX_FILE_SIZE_BYTES,
    'sticker': _MAX_STICKER_SIZE_BYTES,
}


def _file_chunk_dir(upload_id):
    """Absolute path to temporary chunk directory for an upload session."""
    p = django_settings.MEDIA_ROOT / 'uploads' / 'chunks' / str(upload_id)
    p.mkdir(parents=True, exist_ok=True)
    return p


def _file_storage_dir():
    """Absolute path to merged encrypted file storage."""
    p = django_settings.MEDIA_ROOT / 'uploads' / 'files'
    p.mkdir(parents=True, exist_ok=True)
    return p


def _multipart_payload(request):
    """Return (post, files) for POST and multipart PUT/PATCH requests."""
    if request.method == 'POST':
        return request.POST, request.FILES
    try:
        parser = MultiPartParser(
            request.META,
            request,
            request.upload_handlers,
            request.encoding,
        )
        return parser.parse()
    except MultiPartParserError:
        return None, None


def _get_encrypted_file_or_error(file_id, user):
    """Fetch EncryptedFile and verify the user owns it or has its file key."""
    try:
        ef = EncryptedFile.objects.select_related('conversation').get(pk=file_id)
    except EncryptedFile.DoesNotExist:
        return None, JsonResponse({'error': 'file_not_found'}, status=404)

    if ef.status == EncryptedFile.Status.DELETED:
        return None, JsonResponse({'error': 'file_unavailable', 'detail': 'File has been deleted.'}, status=410)

    # Owner always has access
    if ef.owner_id == user.pk:
        return ef, None

    # Others must have a file-key record. Forwarded files retain their original
    # EncryptedFile.conversation, so access cannot depend on that conversation.
    if EncryptedFileKey.objects.filter(file=ef, holder=user).exists():
        return ef, None

    return None, JsonResponse({'error': 'file_forbidden'}, status=403)


# ── 5.1  Create upload session ────────────────────────────────────────────

@login_required(login_url='login')
def create_upload_session_view(request):
    if request.method != 'POST':
        return JsonResponse({'error': 'Method not allowed.'}, status=405)

    data = _json_body(request)
    if not data:
        return JsonResponse({'error': 'invalid_json'}, status=400)

    # ── Required fields ──
    client_file_id = str(data.get('client_file_id', '')).strip()
    if not client_file_id or len(client_file_id) > 64:
        return JsonResponse({'error': 'invalid_file_metadata', 'detail': 'client_file_id is required (max 64 chars).'}, status=400)

    try:
        conversation_id = int(data.get('conversation_id', 0))
    except (TypeError, ValueError):
        return JsonResponse({'error': 'invalid_file_metadata', 'detail': 'conversation_id is required.'}, status=400)

    conversation_type = str(data.get('conversation_type', '')).strip()
    if conversation_type not in ('single', 'group'):
        return JsonResponse({'error': 'invalid_file_metadata', 'detail': 'conversation_type must be single or group.'}, status=400)

    message_kind = str(data.get('message_kind', '')).strip()
    if message_kind not in ('image', 'file', 'sticker'):
        return JsonResponse({'error': 'invalid_file_metadata', 'detail': 'message_kind must be image, file, or sticker.'}, status=400)

    try:
        total_size_bytes = int(data.get('total_size_bytes', 0))
    except (TypeError, ValueError):
        return JsonResponse({'error': 'invalid_file_metadata', 'detail': 'total_size_bytes is required.'}, status=400)

    try:
        chunk_count = int(data.get('chunk_count', 0))
    except (TypeError, ValueError):
        return JsonResponse({'error': 'invalid_file_metadata', 'detail': 'chunk_count is required.'}, status=400)

    try:
        chunk_size_bytes = int(data.get('chunk_size_bytes', _MAX_CHUNK_SIZE_BYTES))
    except (TypeError, ValueError):
        chunk_size_bytes = _MAX_CHUNK_SIZE_BYTES

    algorithm = str(data.get('algorithm', 'AES-256-GCM')).strip()

    # ── Validate size limits ──
    size_limit = _SIZE_LIMITS.get(message_kind, _MAX_FILE_SIZE_BYTES)
    if total_size_bytes <= 0 or total_size_bytes > size_limit:
        return JsonResponse({
            'error': 'file_too_large',
            'detail': f'File size must be between 1 and {size_limit} bytes for {message_kind}.',
        }, status=400)

    if chunk_count < 1:
        return JsonResponse({'error': 'invalid_file_metadata', 'detail': 'chunk_count must be at least 1.'}, status=400)

    if chunk_size_bytes < 1 or chunk_size_bytes > _MAX_CHUNK_SIZE_BYTES:
        return JsonResponse({
            'error': 'invalid_file_metadata',
            'detail': f'chunk_size_bytes must be between 1 and {_MAX_CHUNK_SIZE_BYTES}.',
        }, status=400)

    # ── Verify conversation membership ──
    member = _get_active_member(conversation_id, request.user)
    if not member:
        return JsonResponse({'error': 'conversation_forbidden'}, status=403)

    try:
        conversation = Conversation.objects.get(pk=conversation_id)
    except Conversation.DoesNotExist:
        return JsonResponse({'error': 'conversation_forbidden'}, status=403)

    if conversation.type != conversation_type:
        return JsonResponse({'error': 'invalid_file_metadata', 'detail': 'conversation_type does not match conversation.'}, status=400)

    # Private chat: check block / contact permissions
    if conversation_type == 'single':
        other_member = ConversationMember.objects.filter(
            conversation=conversation, status=ConversationMember.Status.ACTIVE,
        ).exclude(user=request.user).first()
        if other_member:
            if other_member.user == request.user:
                return JsonResponse({'error': 'conversation_forbidden', 'detail': 'Cannot send to yourself.'}, status=403)
            if _is_blocked_by(other_member.user, request.user):
                return JsonResponse({'error': 'conversation_forbidden', 'detail': 'You have been blocked by this user.'}, status=403)
            if _is_blocked_by(request.user, other_member.user):
                return JsonResponse({'error': 'conversation_forbidden', 'detail': 'You have blocked this user. Unblock them first.'}, status=403)

    # ── Check concurrent upload limit ──
    active_uploads = EncryptedFile.objects.filter(
        owner=request.user, status=EncryptedFile.Status.UPLOADING,
    ).count()
    if active_uploads >= _MAX_CONCURRENT_UPLOADS:
        return JsonResponse({
            'error': 'upload_rate_limited',
            'detail': f'Too many active upload sessions. Limit: {_MAX_CONCURRENT_UPLOADS}.',
        }, status=429)

    # ── Idempotency check ──
    existing = EncryptedFile.objects.filter(
        owner=request.user, client_file_id=client_file_id,
    ).first()
    if existing:
        if existing.status == EncryptedFile.Status.UPLOADING:
            uploaded_chunks = list(
                EncryptedFileChunk.objects.filter(file=existing)
                .values_list('chunk_index', flat=True)
                .order_by('chunk_index')
            )
            return JsonResponse({
                'upload_id': existing.upload_id,
                'file_id': existing.id,
                'client_file_id': existing.client_file_id,
                'status': 'uploading',
                'chunk_size_bytes': existing.chunk_size_bytes,
                'expires_at': existing.expires_at.isoformat() if existing.expires_at else None,
                'uploaded_chunks': uploaded_chunks,
            }, status=200)
        elif existing.status == EncryptedFile.Status.AVAILABLE:
            return JsonResponse({
                'upload_id': existing.upload_id,
                'file_id': existing.id,
                'client_file_id': existing.client_file_id,
                'status': 'available',
            }, status=200)
        else:
            # failed or deleted
            return JsonResponse({
                'error': 'client_file_id_conflict',
                'detail': 'This client_file_id was already used and cannot be reused.',
            }, status=409)

    # ── Create the file record ──
    upload_id = str(uuid_lib.uuid4())
    expires_at = timezone.now() + timedelta(hours=_UPLOAD_EXPIRY_HOURS)

    ef = EncryptedFile.objects.create(
        upload_id=upload_id,
        client_file_id=client_file_id,
        owner=request.user,
        conversation=conversation,
        message_kind=message_kind,
        total_size_bytes=total_size_bytes,
        chunk_size_bytes=chunk_size_bytes,
        chunk_count=chunk_count,
        algorithm=algorithm,
        encrypted_metadata=str(data.get('encrypted_metadata', '')),
        metadata_nonce=str(data.get('metadata_nonce', '')),
        metadata_auth_tag=str(data.get('metadata_auth_tag', '')),
        expires_at=expires_at,
    )

    # Ensure chunk directory exists
    _file_chunk_dir(upload_id)

    return JsonResponse({
        'upload_id': ef.upload_id,
        'file_id': ef.id,
        'client_file_id': ef.client_file_id,
        'status': ef.status,
        'chunk_size_bytes': ef.chunk_size_bytes,
        'expires_at': ef.expires_at.isoformat() if ef.expires_at else None,
        'uploaded_chunks': [],
    }, status=201)


# ── 5.2  Upload chunk ─────────────────────────────────────────────────────

@login_required(login_url='login')
def upload_chunk_view(request, upload_id, chunk_index):
    if request.method != 'PUT':
        return JsonResponse({'error': 'Method not allowed.'}, status=405)

    # ── Fetch and validate upload session ──
    try:
        ef = EncryptedFile.objects.select_related('conversation').get(upload_id=upload_id)
    except EncryptedFile.DoesNotExist:
        return JsonResponse({'error': 'upload_forbidden'}, status=403)

    if ef.owner_id != request.user.pk:
        return JsonResponse({'error': 'upload_forbidden'}, status=403)

    if ef.status != EncryptedFile.Status.UPLOADING:
        return JsonResponse({'error': 'upload_forbidden', 'detail': 'Upload session is not in uploading state.'}, status=403)

    if ef.expires_at and ef.expires_at < timezone.now():
        return JsonResponse({'error': 'upload_expired'}, status=410)

    if chunk_index < 0 or chunk_index >= ef.chunk_count:
        return JsonResponse({
            'error': 'invalid_chunk_index',
            'detail': f'chunk_index must be between 0 and {ef.chunk_count - 1}.',
        }, status=400)

    # ── Read multipart form data. Django does not populate request.FILES
    # for PUT automatically, so parse multipart payloads explicitly.
    post_data, files_data = _multipart_payload(request)
    if post_data is None or files_data is None:
        return JsonResponse({'error': 'invalid_file_metadata', 'detail': 'Invalid multipart upload.'}, status=400)

    chunk_file = files_data.get('chunk')
    if not chunk_file:
        return JsonResponse({'error': 'invalid_file_metadata', 'detail': 'Missing chunk file.'}, status=400)

    nonce = post_data.get('nonce', '')
    auth_tag = post_data.get('auth_tag', '')
    ciphertext_sha256 = post_data.get('ciphertext_sha256', '').strip().lower()
    try:
        size_bytes = int(post_data.get('size_bytes', 0))
    except (TypeError, ValueError):
        size_bytes = chunk_file.size

    if not nonce or not auth_tag:
        return JsonResponse({'error': 'invalid_file_metadata', 'detail': 'nonce and auth_tag are required.'}, status=400)

    if size_bytes > ef.chunk_size_bytes:
        return JsonResponse({'error': 'invalid_chunk_index', 'detail': 'Chunk exceeds chunk_size_bytes.'}, status=400)

    # ── Idempotency: check for existing chunk ──
    existing_chunk = EncryptedFileChunk.objects.filter(
        file=ef, chunk_index=chunk_index,
    ).first()
    if existing_chunk:
        if existing_chunk.ciphertext_sha256 and ciphertext_sha256 and existing_chunk.ciphertext_sha256 != ciphertext_sha256:
            return JsonResponse({'error': 'chunk_conflict', 'detail': 'Chunk already uploaded with different hash.'}, status=409)
        return JsonResponse({
            'upload_id': ef.upload_id,
            'file_id': ef.id,
            'chunk_index': chunk_index,
            'status': 'stored',
        })

    # ── Save chunk to disk ──
    chunk_dir = _file_chunk_dir(upload_id)
    chunk_path = chunk_dir / str(chunk_index)
    sha256 = hashlib.sha256()
    actual_size = 0
    with open(chunk_path, 'wb') as dst:
        for part in chunk_file.chunks():
            dst.write(part)
            sha256.update(part)
            actual_size += len(part)

    if actual_size > ef.chunk_size_bytes:
        if chunk_path.exists():
            chunk_path.unlink()
        return JsonResponse({'error': 'invalid_chunk_index', 'detail': 'Chunk exceeds chunk_size_bytes.'}, status=400)

    actual_hash = sha256.hexdigest()
    if ciphertext_sha256 and ciphertext_sha256 != actual_hash:
        if chunk_path.exists():
            chunk_path.unlink()
        return JsonResponse({'error': 'invalid_chunk_hash', 'detail': 'Chunk SHA-256 does not match.'}, status=400)

    # ── Compute offset ──
    offset_bytes = chunk_index * ef.chunk_size_bytes

    EncryptedFileChunk.objects.create(
        file=ef,
        chunk_index=chunk_index,
        size_bytes=actual_size,
        offset_bytes=offset_bytes,
        nonce=nonce,
        auth_tag=auth_tag,
        ciphertext_sha256=actual_hash,
        storage_path=str(chunk_path.relative_to(django_settings.MEDIA_ROOT)),
    )

    uploaded_count = EncryptedFileChunk.objects.filter(file=ef).count()

    return JsonResponse({
        'upload_id': ef.upload_id,
        'file_id': ef.id,
        'chunk_index': chunk_index,
        'status': 'stored',
        'uploaded_count': uploaded_count,
        'total_chunks': ef.chunk_count,
    })


# ── 5.3  Query upload status ──────────────────────────────────────────────

@login_required(login_url='login')
def query_upload_view(request, upload_id):
    if request.method != 'GET':
        return JsonResponse({'error': 'Method not allowed.'}, status=405)

    try:
        ef = EncryptedFile.objects.get(upload_id=upload_id)
    except EncryptedFile.DoesNotExist:
        return JsonResponse({'error': 'upload_forbidden'}, status=403)

    if ef.owner_id != request.user.pk:
        return JsonResponse({'error': 'upload_forbidden'}, status=403)

    uploaded = list(
        EncryptedFileChunk.objects.filter(file=ef)
        .values_list('chunk_index', flat=True)
        .order_by('chunk_index')
    )
    missing = [i for i in range(ef.chunk_count) if i not in uploaded]

    return JsonResponse({
        'upload_id': ef.upload_id,
        'file_id': ef.id,
        'client_file_id': ef.client_file_id,
        'status': ef.status,
        'chunk_count': ef.chunk_count,
        'uploaded_chunks': uploaded,
        'missing_chunks': missing,
        'expires_at': ef.expires_at.isoformat() if ef.expires_at else None,
    })


# ── 5.4  Complete upload ──────────────────────────────────────────────────

@login_required(login_url='login')
def complete_upload_view(request, upload_id):
    if request.method != 'POST':
        return JsonResponse({'error': 'Method not allowed.'}, status=405)

    try:
        ef = EncryptedFile.objects.select_related('conversation').get(upload_id=upload_id)
    except EncryptedFile.DoesNotExist:
        return JsonResponse({'error': 'upload_forbidden'}, status=403)

    if ef.owner_id != request.user.pk:
        return JsonResponse({'error': 'upload_forbidden'}, status=403)

    if ef.status != EncryptedFile.Status.UPLOADING:
        return JsonResponse({'error': 'upload_forbidden', 'detail': 'Not in uploading state.'}, status=403)

    # ── Check all chunks present ──
    uploaded_indices = set(
        EncryptedFileChunk.objects.filter(file=ef)
        .values_list('chunk_index', flat=True)
    )
    missing = [i for i in range(ef.chunk_count) if i not in uploaded_indices]
    if missing:
        return JsonResponse({
            'error': 'upload_incomplete',
            'detail': f'Missing chunks: {",".join(str(i) for i in missing[:10])}',
        }, status=409)

    # ── Merge chunks in order ──
    _file_storage_dir()
    merged_path = django_settings.MEDIA_ROOT / 'uploads' / 'files' / f'{ef.id}.enc'
    sha256 = hashlib.sha256()
    chunks_qs = EncryptedFileChunk.objects.filter(file=ef).order_by('chunk_index')

    with open(merged_path, 'wb') as dst:
        for chunk in chunks_qs:
            if chunk.storage_path:
                chunk_path = django_settings.MEDIA_ROOT / chunk.storage_path
                if chunk_path.exists():
                    with open(chunk_path, 'rb') as src:
                        data = src.read()
                        dst.write(data)
                        sha256.update(data)
                else:
                    return JsonResponse({
                        'error': 'upload_incomplete',
                        'detail': f'Chunk file missing for index {chunk.chunk_index}.',
                    }, status=409)

    computed_hash = sha256.hexdigest()

    # ── Optional: verify full ciphertext hash ──
    data = _json_body(request)
    client_hash = str(data.get('ciphertext_sha256', '')).strip()
    if client_hash and client_hash != computed_hash:
        # Clean up the merged file on hash mismatch
        if merged_path.exists():
            merged_path.unlink()
        return JsonResponse({
            'error': 'invalid_chunk_hash',
            'detail': 'Complete ciphertext SHA-256 does not match.',
        }, status=400)

    # ── Clean up temp chunks ──
    chunk_dir = _file_chunk_dir(upload_id)
    for chunk in chunks_qs:
        if chunk.storage_path:
            cp = django_settings.MEDIA_ROOT / chunk.storage_path
            if cp.exists():
                cp.unlink()
        chunk.storage_path = None
        chunk.save(update_fields=['storage_path'])

    # Remove the (now empty) chunk directory
    try:
        shutil.rmtree(chunk_dir)
    except OSError:
        pass

    # ── Update file record ──
    ef.storage_path = f'uploads/files/{ef.id}.enc'
    ef.ciphertext_sha256 = computed_hash
    ef.status = EncryptedFile.Status.AVAILABLE
    ef.expires_at = timezone.now() + timedelta(hours=_UPLOAD_EXPIRY_HOURS)
    ef.save(update_fields=['storage_path', 'ciphertext_sha256', 'status', 'expires_at'])

    # ── Broadcast to uploader via WebSocket ──
    channel_layer = get_channel_layer()
    async_to_sync(channel_layer.group_send)(
        ChatConsumer.user_group(request.user.pk),
        {
            'type': 'file.upload.completed',
            'data': {
                'file_id': ef.id,
                'conversation_id': ef.conversation_id,
                'message_kind': ef.message_kind,
                'status': 'available',
            },
        },
    )

    return JsonResponse({
        'file_id': ef.id,
        'client_file_id': ef.client_file_id,
        'status': 'available',
        'created_at': ef.created_at.isoformat(),
    })


# ── 5.5  Send file message ────────────────────────────────────────────────

@login_required(login_url='login')
def send_file_message_view(request, file_id):
    if request.method != 'POST':
        return JsonResponse({'error': 'Method not allowed.'}, status=405)

    data = _json_body(request)
    if not data:
        return JsonResponse({'error': 'invalid_json'}, status=400)

    # ── Fetch and validate file ──
    try:
        ef = EncryptedFile.objects.select_related('conversation').get(pk=file_id)
    except EncryptedFile.DoesNotExist:
        return JsonResponse({'error': 'file_not_found'}, status=404)

    if ef.owner_id != request.user.pk:
        return JsonResponse({'error': 'file_forbidden'}, status=403)

    if ef.status != EncryptedFile.Status.AVAILABLE:
        return JsonResponse({'error': 'file_unavailable', 'detail': 'File is not available for sending.'}, status=410)

    # ── Validate message_type matches message_kind ──
    message_type = str(data.get('message_type', '')).strip()
    if message_type != ef.message_kind:
        return JsonResponse({
            'error': 'message_type_mismatch',
            'detail': f'message_type {message_type} does not match upload message_kind {ef.message_kind}.',
        }, status=400)

    conversation_type = str(data.get('conversation_type', '')).strip()
    if conversation_type not in ('single', 'group'):
        return JsonResponse({'error': 'invalid_file_metadata', 'detail': 'conversation_type is required.'}, status=400)

    try:
        conversation_id = int(data.get('conversation_id', 0))
    except (TypeError, ValueError):
        return JsonResponse({'error': 'invalid_file_metadata', 'detail': 'conversation_id is required.'}, status=400)

    if conversation_id != ef.conversation_id:
        return JsonResponse({'error': 'invalid_file_metadata', 'detail': 'conversation_id does not match file conversation.'}, status=400)

    member = _get_active_member(conversation_id, request.user)
    if not member:
        return JsonResponse({'error': 'conversation_forbidden', 'detail': 'Not an active member of this conversation.'}, status=403)

    conversation = member.conversation
    if conversation.status != Conversation.Status.ACTIVE:
        return JsonResponse({'error': 'conversation_forbidden', 'detail': 'Conversation is not active.'}, status=403)
    if conversation.type != conversation_type:
        return JsonResponse({'error': 'invalid_file_metadata', 'detail': 'conversation_type does not match conversation.'}, status=400)

    active_members = ConversationMember.objects.filter(
        conversation=conversation,
        status=ConversationMember.Status.ACTIVE,
    )
    active_member_ids = set(active_members.values_list('user_id', flat=True))

    client_message_id = str(data.get('client_message_id', '')).strip()
    if not client_message_id or len(client_message_id) > 64:
        return JsonResponse({'error': 'invalid_file_metadata', 'detail': 'client_message_id is required (max 64 chars).'}, status=400)
    reply_to_message_id = data.get('reply_to_message_id')
    if reply_to_message_id in ('', None):
        reply_to_message_id = None
    else:
        try:
            reply_to_message_id = int(reply_to_message_id)
        except (TypeError, ValueError):
            return JsonResponse({'error': 'invalid_file_metadata', 'detail': 'reply_to_message_id must be an integer.'}, status=400)

    # ── Save EncryptedFileKey records ──
    file_keys_data = data.get('file_keys', [])
    if not isinstance(file_keys_data, list) or not file_keys_data:
        return JsonResponse({'error': 'invalid_file_metadata', 'detail': 'file_keys is required.'}, status=400)

    normalized_file_keys, file_keys_error = _normalize_forward_file_keys(
        file_keys_data,
        active_member_ids,
    )
    if file_keys_error:
        return file_keys_error
    file_keys_data = normalized_file_keys

    holder_ids = set()
    for fk in file_keys_data:
        try:
            holder_id = int(fk.get('holder_id', 0))
        except (TypeError, ValueError):
            return JsonResponse({'error': 'invalid_file_metadata', 'detail': 'Each file_key must have a valid holder_id.'}, status=400)

        if holder_id in holder_ids:
            continue
        holder_ids.add(holder_id)

        EncryptedFileKey.objects.update_or_create(
            file=ef,
            holder_id=holder_id,
            defaults={
                'sender': request.user,
                'encrypted_file_key': str(fk.get('encrypted_file_key', '')),
                'nonce': str(fk.get('nonce', '')),
                'auth_tag': str(fk.get('auth_tag', '')),
                'algorithm': str(fk.get('algorithm', 'AES-256-GCM')),
                'sender_key_version': fk.get('sender_key_version'),
                'receiver_key_version': fk.get('receiver_key_version'),
                'membership_version': fk.get('membership_version'),
                'sender_ephemeral_public_key': fk.get('sender_ephemeral_public_key'),
            },
        )

    # ── Create message ──
    if conversation_type == 'single':
        ciphertext = str(data.get('ciphertext', ''))
        nonce = str(data.get('nonce', ''))
        auth_tag = str(data.get('auth_tag', ''))
        algorithm = str(data.get('algorithm', 'AES-256-GCM'))
        sender_key_version = int(data.get('sender_key_version', 0)) or None
        receiver_key_version = int(data.get('receiver_key_version', 0)) or None

        try:
            receiver_id = int(data.get('receiver_id', 0))
        except (TypeError, ValueError):
            return JsonResponse({'error': 'invalid_file_metadata', 'detail': 'receiver_id is required for single chat.'}, status=400)

        if (
            len(active_member_ids) != 2
            or request.user.pk not in active_member_ids
            or receiver_id not in active_member_ids
            or receiver_id == request.user.pk
        ):
            return JsonResponse({'error': 'conversation_forbidden', 'detail': 'Receiver is not the private-chat peer.'}, status=403)

        receiver = User.objects.filter(pk=receiver_id, is_active=True).first()
        if not receiver:
            return JsonResponse({'error': 'conversation_forbidden', 'detail': 'Receiver is not active.'}, status=403)
        if _is_blocked_by(receiver, request.user) or _is_blocked_by(request.user, receiver):
            return JsonResponse({'error': 'conversation_forbidden', 'detail': 'Blocked users cannot exchange file messages.'}, status=403)

        payload = dict(data)
        payload['conversation_id'] = conversation.id
        payload['receiver_id'] = receiver_id
        payload['file_id'] = ef.pk
        try:
            validated_payload = ChatConsumer.validate_private_message(payload)
        except ClientPayloadError as error:
            return _client_payload_error_response(error)

        message = EncryptedMessage.objects.create(
            conversation=ef.conversation,
            sender=request.user,
            receiver_id=receiver_id,
            message_type=message_type,
            ciphertext=validated_payload['ciphertext'],
            nonce=validated_payload['nonce'],
            auth_tag=validated_payload['auth_tag'],
            sender_ephemeral_public_key=validated_payload.get('sender_ephemeral_public_key'),
            sender_copy_ciphertext=(validated_payload.get('sender_copy') or {}).get('ciphertext'),
            sender_copy_nonce=(validated_payload.get('sender_copy') or {}).get('nonce'),
            sender_copy_auth_tag=(validated_payload.get('sender_copy') or {}).get('auth_tag'),
            sender_copy_ephemeral_public_key=(validated_payload.get('sender_copy') or {}).get('sender_ephemeral_public_key'),
            algorithm=validated_payload['algorithm'],
            sender_key_version=validated_payload['sender_key_version'],
            receiver_key_version=validated_payload['receiver_key_version'],
            client_message_id=client_message_id or None,
            reply_to_message_id=reply_to_message_id,
            file_id=ef,
        )
        ef.conversation.last_message_at = message.created_at
        ef.conversation.last_message_id = message.id
        ef.conversation.save(update_fields=['last_message_at', 'last_message_id'])

        receiver_serialized = _serialize_file_private_message(
            message,
            _build_file_sub_object(ef, receiver_id),
            viewer_id=receiver_id,
        )
        sender_serialized = _serialize_file_private_message(
            message,
            _build_file_sub_object(ef, request.user.pk),
            viewer_id=request.user.pk,
        )

        # Broadcast to receiver
        channel_layer = get_channel_layer()
        async_to_sync(channel_layer.group_send)(
            ChatConsumer.user_group(receiver_id),
            {'type': 'message.single.new', 'data': receiver_serialized},
        )
        # Also notify sender (for multi-device sync)
        async_to_sync(channel_layer.group_send)(
            ChatConsumer.user_group(request.user.pk),
            {'type': 'message.single.new', 'data': sender_serialized},
        )

        return JsonResponse({
            'file_id': ef.id,
            'message_id': message.id,
            'status': 'sent',
            'created_at': message.created_at.isoformat(),
        }, status=201)

    else:
        # ── Group chat ──
        if conversation.muted_until and conversation.muted_until > timezone.now():
            if member.role not in (ConversationMember.Role.OWNER, ConversationMember.Role.ADMIN):
                return JsonResponse({'error': 'group_muted', 'detail': 'This group is muted.'}, status=403)

        try:
            membership_version = int(data.get('membership_version', 0))
        except (TypeError, ValueError):
            return JsonResponse({'error': 'invalid_file_metadata', 'detail': 'membership_version is required for group chat.'}, status=400)
        if membership_version <= 0:
            return JsonResponse({'error': 'invalid_file_metadata', 'detail': 'membership_version is required for group chat.'}, status=400)

        # Check membership version
        if membership_version != ef.conversation.membership_version:
            return JsonResponse({
                'error': 'membership_version_conflict',
                'detail': f'Current membership version is {ef.conversation.membership_version}.',
                'membership_version': ef.conversation.membership_version,
            }, status=409)

        recipients_data = data.get('recipients', [])
        if not isinstance(recipients_data, list) or not recipients_data:
            return JsonResponse({'error': 'invalid_file_metadata', 'detail': 'recipients is required for group chat.'}, status=400)

        payload = dict(data)
        payload['group_id'] = conversation.id
        payload['file_id'] = ef.pk
        try:
            validated_payload = ChatConsumer.validate_group_message(payload)
        except ClientPayloadError as error:
            return _client_payload_error_response(error)

        recipient_user_ids = set()
        for r in recipients_data:
            try:
                recipient_user_ids.add(int(r.get('receiver_id', 0)))
            except (TypeError, ValueError):
                return JsonResponse({'error': 'invalid_file_metadata', 'detail': 'recipient receiver_id must be an integer.'}, status=400)
        if recipient_user_ids != active_member_ids:
            return JsonResponse({'error': 'recipients_mismatch', 'detail': 'Recipients must match current active members.'}, status=400)

        sender_copy = data.get('sender_copy') or {}
        group_msg = GroupMessage.objects.create(
            conversation=ef.conversation,
            sender=request.user,
            message_type=message_type,
            client_message_id=client_message_id or None,
            reply_to_message_id=reply_to_message_id,
            file_id=ef,
            sender_copy_ciphertext=sender_copy.get('ciphertext'),
            sender_copy_nonce=sender_copy.get('nonce'),
            sender_copy_auth_tag=sender_copy.get('auth_tag'),
            sender_copy_ephemeral_public_key=sender_copy.get('sender_ephemeral_public_key'),
        )
        ef.conversation.last_message_at = group_msg.created_at
        ef.conversation.last_message_id = group_msg.id
        ef.conversation.save(update_fields=['last_message_at', 'last_message_id'])

        channel_layer = get_channel_layer()
        for r in validated_payload['recipients']:
            try:
                recv_id = int(r.get('receiver_id', 0))
            except (TypeError, ValueError):
                continue

            recipient = GroupMessageRecipient.objects.create(
                group_message=group_msg,
                receiver_id=recv_id,
                ciphertext=str(r.get('ciphertext', '')),
                nonce=str(r.get('nonce', '')),
                auth_tag=str(r.get('auth_tag', '')),
                algorithm=validated_payload['algorithm'],
                sender_key_version=validated_payload['sender_key_version'],
                receiver_key_version=int(r.get('receiver_key_version', 0)) or None,
                sender_ephemeral_public_key=str(r.get('sender_ephemeral_public_key') or '') or None,
                membership_version=membership_version,
            )

            file_obj = _build_file_sub_object(ef, recv_id)
            recipient_data = _serialize_file_group_recipient(group_msg, recipient, file_obj)

            async_to_sync(channel_layer.group_send)(
                ChatConsumer.user_group(recv_id),
                {'type': 'message.group.new', 'data': recipient_data},
            )

        return JsonResponse({
            'file_id': ef.id,
            'message_id': group_msg.id,
            'status': 'sent',
            'created_at': group_msg.created_at.isoformat(),
        }, status=201)


def _build_file_sub_object(ef, holder_id):
    """Build the ``file`` sub-object for WebSocket / API responses."""
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
            'sender_id': fk.sender_id or ef.owner_id,
            'sender_key_version': fk.sender_key_version,
            'receiver_key_version': fk.receiver_key_version,
            'membership_version': fk.membership_version,
            'sender_ephemeral_public_key': fk.sender_ephemeral_public_key,
        }
    return file_obj


def _private_message_payload_for_viewer(message, viewer_id, request=None, file_obj_marker=None):
    """Serialize a private message using the copy decryptable by viewer_id."""
    use_sender_copy = (
        int(viewer_id) == message.sender_id
        and message.sender_copy_ciphertext
        and message.sender_copy_nonce
        and message.sender_copy_auth_tag
    )
    file_obj = None
    if message.file_id_id:
        if file_obj_marker is not None:
            file_obj = file_obj_marker
        else:
            file_obj = _build_file_sub_object(message.file_id, viewer_id)
    payload = {
        "id": message.id,
        "message_id": message.id,
        "conversation_id": message.conversation_id,
        "sender_id": message.sender_id,
        "receiver_id": message.receiver_id,
        "message_type": message.message_type,
        "ciphertext": message.sender_copy_ciphertext if use_sender_copy else message.ciphertext,
        "nonce": message.sender_copy_nonce if use_sender_copy else message.nonce,
        "auth_tag": message.sender_copy_auth_tag if use_sender_copy else message.auth_tag,
        "algorithm": message.algorithm,
        "sender_key_version": message.sender_key_version,
        "receiver_key_version": message.receiver_key_version,
        "sender_ephemeral_public_key": (
            message.sender_copy_ephemeral_public_key
            if use_sender_copy
            else message.sender_ephemeral_public_key
        ),
        "reply_to_message_id": message.reply_to_message_id,
        "file_id": message.file_id_id,
        "file": file_obj,
        "status": message.status,
        "recalled_at": message.recalled_at.isoformat() if message.recalled_at else None,
        "created_at": message.created_at.isoformat(),
    }
    if request is not None:
        payload.update({
            "sender_initials": _initials(_display_name(message.sender)),
            "sender_avatar_color": _avatar_color(_display_name(message.sender)),
            "sender_avatar_url": _avatar_url(request, message.sender),
        })
    return payload


def _serialize_file_private_message(message, file_obj, viewer_id=None):
    """Serialize a private file message for WebSocket broadcast."""
    viewer_id = viewer_id or message.receiver_id
    payload = _private_message_payload_for_viewer(message, viewer_id, file_obj_marker=file_obj)
    payload['client_message_id'] = message.client_message_id or ''
    return payload


def _serialize_file_group_recipient(group_msg, recipient, file_obj):
    """Serialize a group file message recipient for WebSocket broadcast."""
    sender = group_msg.sender
    return {
        'message_id': group_msg.id,
        'group_id': group_msg.conversation_id,
        'conversation_id': group_msg.conversation_id,
        'sender_id': sender.pk,
        'sender_username': sender.username,
        'sender_name': _display_name(sender),
        'sender_initials': _initials(_display_name(sender)),
        'sender_avatar_color': _avatar_color(_display_name(sender)),
        'sender_avatar_url': '',  # populated by consumer
        'receiver_id': recipient.receiver_id,
        'message_type': group_msg.message_type,
        'ciphertext': recipient.ciphertext or '',
        'nonce': recipient.nonce or '',
        'auth_tag': recipient.auth_tag or '',
        'algorithm': recipient.algorithm,
        'sender_key_version': recipient.sender_key_version,
        'receiver_key_version': recipient.receiver_key_version,
        'sender_ephemeral_public_key': recipient.sender_ephemeral_public_key,
        'membership_version': recipient.membership_version,
        'reply_to_message_id': group_msg.reply_to_message_id,
        'file': file_obj if group_msg.file_id else None,
        'status': recipient.status,
        'created_at': recipient.created_at.isoformat(),
    }


# ── 5.6  Get file metadata ────────────────────────────────────────────────

@login_required(login_url='login')
def get_file_metadata_view(request, file_id):
    if request.method != 'GET':
        return JsonResponse({'error': 'Method not allowed.'}, status=405)

    ef, error = _get_encrypted_file_or_error(file_id, request.user)
    if error:
        return error

    if ef.status != EncryptedFile.Status.AVAILABLE:
        return JsonResponse({'error': 'file_unavailable'}, status=410)

    chunks = EncryptedFileChunk.objects.filter(file=ef).order_by('chunk_index')
    file_key = EncryptedFileKey.objects.filter(file=ef, holder=request.user).first()
    if not file_key:
        return JsonResponse({'error': 'file_forbidden', 'detail': 'No file key for this user.'}, status=403)

    return JsonResponse({
        'file_id': ef.id,
        'client_file_id': ef.client_file_id,
        'conversation_id': ef.conversation_id,
        'message_kind': ef.message_kind,
        'status': ef.status,
        'total_size_bytes': ef.total_size_bytes,
        'chunk_size_bytes': ef.chunk_size_bytes,
        'chunk_count': ef.chunk_count,
        'algorithm': ef.algorithm,
        'encrypted_metadata': ef.encrypted_metadata,
        'metadata_nonce': ef.metadata_nonce,
        'metadata_auth_tag': ef.metadata_auth_tag,
        'encrypted_file_key': {
            'encrypted_file_key': file_key.encrypted_file_key,
            'nonce': file_key.nonce,
            'auth_tag': file_key.auth_tag,
            'algorithm': file_key.algorithm,
            'sender_id': file_key.sender_id or ef.owner_id,
            'sender_key_version': file_key.sender_key_version,
            'receiver_key_version': file_key.receiver_key_version,
            'membership_version': file_key.membership_version,
            'sender_ephemeral_public_key': file_key.sender_ephemeral_public_key,
        },
        'chunks': [
            {
                'chunk_index': c.chunk_index,
                'size_bytes': c.size_bytes,
                'offset_bytes': c.offset_bytes,
                'nonce': c.nonce,
                'auth_tag': c.auth_tag,
                'ciphertext_sha256': c.ciphertext_sha256,
            }
            for c in chunks
        ],
        'download_url': f'/api/files/{ef.id}/download/',
    })


# ── 5.7  Download complete ciphertext file ────────────────────────────────

@login_required(login_url='login')
def download_file_view(request, file_id):
    if request.method != 'GET':
        return JsonResponse({'error': 'Method not allowed.'}, status=405)

    ef, error = _get_encrypted_file_or_error(file_id, request.user)
    if error:
        return error

    if ef.status != EncryptedFile.Status.AVAILABLE:
        return JsonResponse({'error': 'file_unavailable'}, status=410)

    file_path = django_settings.MEDIA_ROOT / ef.storage_path
    if not file_path.exists():
        return JsonResponse({'error': 'file_not_found', 'detail': 'File missing from storage.'}, status=404)

    response = FileResponse(
        open(file_path, 'rb'),
        content_type='application/octet-stream',
        as_attachment=False,
    )
    response['Content-Length'] = file_path.stat().st_size
    response['Accept-Ranges'] = 'bytes'
    response['X-iChat-File-SHA256'] = ef.ciphertext_sha256 or ''
    return response


# ── 5.8  Download single chunk (compatibility path) ───────────────────────

@login_required(login_url='login')
def download_chunk_view(request, file_id, chunk_index):
    if request.method != 'GET':
        return JsonResponse({'error': 'Method not allowed.'}, status=405)

    ef, error = _get_encrypted_file_or_error(file_id, request.user)
    if error:
        return error

    if ef.status != EncryptedFile.Status.AVAILABLE:
        return JsonResponse({'error': 'file_unavailable'}, status=410)

    chunk = EncryptedFileChunk.objects.filter(file=ef, chunk_index=chunk_index).first()
    if not chunk:
        return JsonResponse({'error': 'invalid_chunk_index', 'detail': 'Chunk not found.'}, status=400)

    file_path = django_settings.MEDIA_ROOT / ef.storage_path
    if not file_path.exists():
        return JsonResponse({'error': 'file_not_found', 'detail': 'File missing from storage.'}, status=404)

    with open(file_path, 'rb') as f:
        f.seek(chunk.offset_bytes)
        data = f.read(chunk.size_bytes)

    response = HttpResponse(data, content_type='application/octet-stream')
    response['Content-Length'] = len(data)
    response['X-iChat-Chunk-SHA256'] = chunk.ciphertext_sha256 or ''
    return response


# ── 5.9  Cancel upload ────────────────────────────────────────────────────

@login_required(login_url='login')
def cancel_upload_view(request, upload_id):
    if request.method != 'DELETE':
        return JsonResponse({'error': 'Method not allowed.'}, status=405)

    try:
        ef = EncryptedFile.objects.get(upload_id=upload_id)
    except EncryptedFile.DoesNotExist:
        return JsonResponse({'error': 'upload_forbidden'}, status=403)

    if ef.owner_id != request.user.pk:
        return JsonResponse({'error': 'upload_forbidden'}, status=403)

    if ef.status != EncryptedFile.Status.UPLOADING:
        return JsonResponse({'error': 'upload_forbidden', 'detail': 'Not in uploading state.'}, status=403)

    # Clean up chunk files
    chunk_dir = _file_chunk_dir(upload_id)
    try:
        shutil.rmtree(chunk_dir)
    except OSError:
        pass

    # Delete chunk records
    EncryptedFileChunk.objects.filter(file=ef).delete()

    ef.status = EncryptedFile.Status.DELETED
    ef.deleted_at = timezone.now()
    ef.save(update_fields=['status', 'deleted_at'])

    return JsonResponse({
        'upload_id': ef.upload_id,
        'status': 'cancelled',
    })


# ── Phase 3: AI Assistant Chat API ──────────────────────────────────────────

AI_SUPPORTED_MODES = {
    'chat': {
        'label': 'Chat',
        'prompt': (
            "Handle the user's message as a normal assistant conversation. "
            "Answer directly and keep the response useful, concise, and grounded in the text the user provided."
        ),
    },
    'summarize': {
        'label': 'Summarize',
        'prompt': (
            "Summarize the text the user provides. Preserve key facts, decisions, names, dates, and action items. "
            "If the input is too short to summarize, say so briefly and answer as a helpful assistant."
        ),
    },
    'draft_reply': {
        'label': 'Draft Reply',
        'prompt': (
            "Draft a polished reply based only on the user's supplied text. "
            "Keep the tone natural, clear, and ready to send. Do not invent private context."
        ),
    },
}
AI_DEFAULT_MODE = 'chat'


def _normalize_ai_model_config(raw_model_config):
    if not isinstance(raw_model_config, dict):
        return {}
    return {
        'endpoint': str(raw_model_config.get('endpoint') or '').strip(),
        'model': str(raw_model_config.get('model') or '').strip(),
        'assistant_id': _normalize_ai_assistant_id(raw_model_config.get('assistant_id')),
    }


def _normalize_ai_assistant_id(value):
    assistant_id = str(value or 'ai-assistant').strip()
    if not assistant_id:
        return 'ai-assistant'
    return assistant_id[:80]


def _is_placeholder_api_key(value):
    value = str(value or '').strip()
    if not value:
        return False
    if not value.isascii():
        return True
    return len(value) >= 6 and set(value) <= {'*', '.', '-'}


def _normalize_ai_api_key(value):
    api_key = str(value or '').strip()
    if _is_placeholder_api_key(api_key):
        return ''
    return api_key


def _configured_ai_model_config(model_config):
    return bool(
        model_config.get('endpoint')
        and model_config.get('model')
    )


def _trusted_ai_model_config(user, client_model_config):
    """Build model config from server-side credentials only.

    Browser-provided API keys are intentionally ignored. A per-assistant
    user config may provide the endpoint and encrypted API key; the model id
    remains a non-secret client preference.
    """
    assistant_id = client_model_config.get('assistant_id') or 'ai-assistant'
    try:
        user_config = UserLLMConfig.objects.get(user=user, assistant_id=assistant_id)
    except UserLLMConfig.DoesNotExist:
        user_config = None

    model = client_model_config.get('model') or 'qwen-plus'
    api_key = _normalize_ai_api_key(user_config.get_api_key() if user_config else '')
    if user_config and (user_config.api_url or '').strip() and api_key:
        return {
            'endpoint': (user_config.api_url or '').strip(),
            'api_key': api_key,
            'model': model,
        }

    return {}


def _server_ai_config_status(user, assistant_id='ai-assistant'):
    try:
        user_config = UserLLMConfig.objects.get(
            user=user,
            assistant_id=_normalize_ai_assistant_id(assistant_id),
        )
    except UserLLMConfig.DoesNotExist:
        user_config = None

    user_has_endpoint = bool(user_config and (user_config.api_url or '').strip())
    user_has_api_key = bool(user_config and _normalize_ai_api_key(user_config.get_api_key()))
    env_has_api_key = bool(os.environ.get("ANTHROPIC_API_KEY"))

    return {
        'user_endpoint_configured': user_has_endpoint,
        'user_api_key_configured': user_has_api_key,
        'env_api_key_configured': env_has_api_key,
        'server_configured': env_has_api_key,
    }


def _ai_system_prompt(mode, configured_model):
    mode_config = AI_SUPPORTED_MODES.get(mode) or AI_SUPPORTED_MODES[AI_DEFAULT_MODE]
    return (
        "You are AI Assistant inside iChat Pro.\n"
        f"The configured model id for this session is: {configured_model}.\n"
        "If the user asks what model you are, answer with this configured model id. "
        "Do not claim to be another product, IDE assistant, application, or model identity.\n"
        "Only process text that the user explicitly sends inside this AI Assistant chat. "
        "Do not claim to have read encrypted private chats, contacts, files, or external pages unless that content was provided in the prompt.\n"
        f"Mode: {mode}.\n"
        f"{mode_config['prompt']}"
    )


@login_required(login_url='login')
def ai_config_view(request):
    """Save or retrieve the user's LLM provider configuration (endpoint + encrypted API key)."""
    import json as _json
    from .llm import (
        is_anthropic_messages_endpoint,
        normalize_anthropic_messages_endpoint,
        normalize_chat_completions_endpoint,
    )

    limited = _rate_limit(request, 'ai_config', limit=30, window_seconds=60)
    if limited:
        return limited

    if request.method == 'GET':
        assistant_id = _normalize_ai_assistant_id(request.GET.get('assistant_id'))
        try:
            user_config = UserLLMConfig.objects.get(
                user=request.user,
                assistant_id=assistant_id,
            )
        except UserLLMConfig.DoesNotExist:
            user_config = None

        return JsonResponse({
            'configured': bool(user_config and user_config.api_url and _normalize_ai_api_key(user_config.get_api_key())),
            'assistant_id': assistant_id,
            'endpoint': (user_config.api_url or '').strip() if user_config else '',
            'has_api_key': bool(user_config and _normalize_ai_api_key(user_config.get_api_key())),
        })

    if request.method != 'POST':
        return JsonResponse({'error': 'Method not allowed.'}, status=405)

    try:
        data = _json.loads(request.body)
    except Exception:
        return JsonResponse({'error': 'Invalid request body.'}, status=400)

    raw_endpoint = str(data.get('endpoint') or '').strip()
    raw_api_key = _normalize_ai_api_key(data.get('api_key'))
    assistant_id = _normalize_ai_assistant_id(data.get('assistant_id'))

    # Allow clearing the config by sending empty endpoint and empty api_key
    if not raw_endpoint and not raw_api_key:
        UserLLMConfig.objects.filter(
            user=request.user,
            assistant_id=assistant_id,
        ).delete()
        return JsonResponse({
            'configured': False,
            'assistant_id': assistant_id,
            'endpoint': '',
            'has_api_key': False,
        })

    if not raw_endpoint:
        return JsonResponse({'error': 'endpoint is required.'}, status=400)

    try:
        normalized_endpoint = (
            normalize_anthropic_messages_endpoint(raw_endpoint)
            if is_anthropic_messages_endpoint(raw_endpoint)
            else normalize_chat_completions_endpoint(raw_endpoint)
        )
    except ValueError as e:
        logger.warning("AI config rejected unsafe model endpoint: %s", e)
        return JsonResponse({'error': str(e)}, status=400)

    user_config, _created = UserLLMConfig.objects.update_or_create(
        user=request.user,
        assistant_id=assistant_id,
        defaults={'api_url': normalized_endpoint},
    )
    if raw_api_key:
        user_config.set_api_key(raw_api_key)
        user_config.save(update_fields=['api_key', 'updated_at'])
    elif _created:
        # New record without an API key: still save the endpoint-only row.
        pass

    return JsonResponse({
        'configured': bool(user_config.api_url and _normalize_ai_api_key(user_config.get_api_key())),
        'assistant_id': assistant_id,
        'endpoint': (user_config.api_url or '').strip(),
        'has_api_key': bool(_normalize_ai_api_key(user_config.get_api_key())),
    })


@login_required(login_url='login')
def ai_status_view(request):
    """Return AI assistant availability, configuration state, and supported modes."""
    if request.method not in ('GET', 'POST'):
        return JsonResponse({'error': 'Method not allowed.'}, status=405)

    limited = _rate_limit(request, 'ai_status', limit=60, window_seconds=60)
    if limited:
        return limited

    payload = _json_body(request) if request.method == 'POST' else {}
    client_model_config = _normalize_ai_model_config((payload or {}).get('model_config') or {})
    trusted_model_config = _trusted_ai_model_config(request.user, client_model_config)
    client_configured = _configured_ai_model_config(trusted_model_config)
    assistant_id = client_model_config.get('assistant_id') or 'ai-assistant'
    server_status = _server_ai_config_status(request.user, assistant_id)
    configured = client_configured or server_status['server_configured']

    return JsonResponse({
        'available': True,
        'configured': configured,
        'mock_mode': not configured,
        'supported_modes': [
            {'id': mode_id, 'label': mode_config['label']}
            for mode_id, mode_config in AI_SUPPORTED_MODES.items()
        ],
        'default_mode': AI_DEFAULT_MODE,
        'active_model': trusted_model_config.get('model') or client_model_config.get('model') or (
            'anthropic-env-default' if server_status['env_api_key_configured'] else 'local-mock-llm'
        ),
        'configuration': {
            'client_model_configured': client_configured,
            'server_configured': server_status['server_configured'],
            'user_endpoint_configured': server_status['user_endpoint_configured'],
            'user_api_key_configured': server_status['user_api_key_configured'],
            'env_api_key_configured': server_status['env_api_key_configured'],
            'requires': ['server_side_api_key', 'endpoint', 'model'],
        },
    })


@login_required(login_url='login')
def ai_chat_view(request):
    import json
    import logging
    from django.http import JsonResponse, StreamingHttpResponse
    from .llm import get_llm_provider

    logger = logging.getLogger(__name__)

    if request.method != 'POST':
        return JsonResponse({'error': 'Method not allowed.'}, status=405)

    limited = _rate_limit(request, 'ai_chat', limit=20, window_seconds=60)
    if limited:
        return limited

    try:
        data = json.loads(request.body)
        user_message = data.get('message', '').strip()
        history = data.get('history', [])
        raw_model_config = data.get('model_config') or {}
        raw_mode = str(data.get('mode') or AI_DEFAULT_MODE).strip()
        stream_requested = bool(data.get('stream'))
    except Exception:
        return JsonResponse({'error': 'Invalid request body.'}, status=400)

    if not user_message:
        return JsonResponse({'error': 'Message content cannot be empty.'}, status=400)
    if len(user_message) > 8000:
        return JsonResponse({'error': 'Message content is too long.'}, status=400)

    client_model_config = _normalize_ai_model_config(raw_model_config)
    model_config = _trusted_ai_model_config(request.user, client_model_config)
    mode = raw_mode if raw_mode in AI_SUPPORTED_MODES else AI_DEFAULT_MODE
    configured_model = model_config.get('model') or client_model_config.get('model') or 'local-mock-llm'
    system_prompt = _ai_system_prompt(mode, configured_model)

    # Format history (role: user/assistant, content: text)
    formatted_messages = []
    for turn in history[-10:]:
        role = turn.get('role')
        content = turn.get('content')
        if role in ('user', 'assistant') and content:
            formatted_messages.append({
                "role": role,
                "content": content
            })

    formatted_messages.append({
        "role": "user",
        "content": user_message
    })

    try:
        provider = get_llm_provider(model_config=model_config)
        if stream_requested:
            stream_end = object()

            def next_stream_chunk(iterator):
                try:
                    return next(iterator)
                except StopIteration:
                    return stream_end

            async def event_stream():
                iterator = provider.stream(
                    messages=formatted_messages,
                    system=system_prompt
                )
                try:
                    while True:
                        chunk = await sync_to_async(next_stream_chunk, thread_sensitive=False)(iterator)
                        if chunk is stream_end:
                            break
                        if not chunk:
                            continue
                        yield f"data: {json.dumps({'delta': chunk}, ensure_ascii=False)}\n\n"
                    yield f"data: {json.dumps({'done': True}, ensure_ascii=False)}\n\n"
                except ValueError as e:
                    logger.warning("AI assistant rejected unsafe model endpoint: %s", e)
                    yield f"data: {json.dumps({'error': 'Invalid model endpoint.'}, ensure_ascii=False)}\n\n"
                except Exception as e:
                    logger.exception("AI assistant streaming generation failed:")
                    yield f"data: {json.dumps({'error': 'AI assistant service failed.'}, ensure_ascii=False)}\n\n"

            response = StreamingHttpResponse(event_stream(), content_type='text/event-stream; charset=utf-8')
            response['Cache-Control'] = 'no-cache'
            response['X-Accel-Buffering'] = 'no'
            return response

        response_text = provider.complete(
            messages=formatted_messages,
            system=system_prompt
        )
        return JsonResponse({'response': response_text})
    except ValueError as e:
        logger.warning("AI assistant rejected unsafe model endpoint: %s", e)
        return JsonResponse({'error': 'Invalid model endpoint.'}, status=400)
    except Exception as e:
        logger.exception("AI assistant generation failed:")
        return JsonResponse({'error': 'AI assistant service failed.'}, status=500)

