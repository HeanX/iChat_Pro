import copy
import json

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.contrib.auth import get_user_model, logout
from django.contrib.auth.decorators import login_required
from django.utils import timezone
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import F, Q
from django.http import JsonResponse
from django.shortcuts import render

from accounts.models import BlockedUser, Contact, FriendRequest, UserPrivacySettings
from .consumers import ChatConsumer
from .models import (
    Conversation,
    ConversationMember,
    EncryptedMessage,
    GroupMessage,
    GroupMessageRecipient,
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


def _is_blocked_by(blocker, target):
    """Return True if *blocker* has blocked *target*."""
    return BlockedUser.objects.filter(
        blocker=blocker,
        blocked=target,
    ).exists()


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

    # Non-contacts: check receiver's privacy settings
    try:
        ps = UserPrivacySettings.objects.get(user=receiver)
    except UserPrivacySettings.DoesNotExist:
        return False, 'Private chats are limited to contacts.'

    if ps.who_can_send_messages == 'everyone':
        return True, None

    return False, 'Private chats are limited to contacts.'


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
        ).select_related('user', 'contact'),
        'incoming_requests': FriendRequest.objects.filter(
            receiver=user,
            status=FriendRequest.Status.PENDING,
        ).select_related('sender'),
        'outgoing_requests': FriendRequest.objects.filter(
            sender=user,
            status=FriendRequest.Status.PENDING,
        ).select_related('receiver'),
    }


# ---------------------------------------------------------------------------
# Conversation list & creation API
# ---------------------------------------------------------------------------

@login_required(login_url='login')
def conversations_list_view(request):
    """Return active conversations for the authenticated user's sidebar."""
    memberships = (
        ConversationMember.objects
        .filter(
            user=request.user,
            status=ConversationMember.Status.ACTIVE,
            conversation__status=Conversation.Status.ACTIVE,
        )
        .select_related('conversation', 'conversation__created_by')
        .order_by('-conversation__last_message_at', '-conversation__updated_at')
    )

    conversations = []
    for membership in memberships:
        conversation = membership.conversation
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


def _json_body(request):
    """Parse and return the JSON body of a request, or empty dict on error."""
    try:
        return json.loads(request.body.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return {}


@login_required(login_url='login')
def create_group_view(request):
    """Create a group conversation. Creator becomes owner automatically."""
    if request.method != "POST":
        return JsonResponse({"error": "Method not allowed."}, status=405)

    data = _json_body(request)
    name = data.get("name", "").strip()
    if not name:
        return JsonResponse({"error": "Group name is required."}, status=400)

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

    return JsonResponse({
        "id": conversation.id,
        "name": conversation.name,
        "type": conversation.type,
        "created_at": conversation.created_at.isoformat(),
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

    from django.contrib.auth import get_user_model
    User = get_user_model()
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

    from django.utils import timezone
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

    page_number = request.GET.get("page", 1)
    per_page = min(int(request.GET.get("per_page", 30)), 100)

    # Only return messages sent on or after the user joined.
    joined_at = member.joined_at
    recipient_queryset = (
        GroupMessageRecipient.objects.filter(
            receiver=request.user,
            group_message__conversation_id=conversation_id,
            group_message__created_at__gte=joined_at,
        )
        .select_related("group_message", "group_message__sender__profile")
        .order_by("-group_message__created_at")
    )
    paginator = Paginator(recipient_queryset, per_page)
    page_obj = paginator.get_page(page_number)

    messages_data = [
        {
            "id": r.group_message.id,
            "sender_id": r.group_message.sender_id,
            "sender_username": r.group_message.sender.username,
            "sender_name": _display_name(r.group_message.sender),
            "sender_initials": _initials(_display_name(r.group_message.sender)),
            "sender_avatar_color": _avatar_color(_display_name(r.group_message.sender)),
            "message_type": r.group_message.message_type,
            "ciphertext": r.ciphertext,
            "nonce": r.nonce,
            "auth_tag": r.auth_tag,
            "algorithm": r.algorithm,
            "sender_key_version": r.sender_key_version,
            "receiver_key_version": r.receiver_key_version,
            "membership_version": r.membership_version,
            "status": r.status,
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

    queryset = (
        EncryptedMessage.objects
        .filter(conversation_id=conversation_id)
        .order_by("-created_at")
    )
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
            "status": msg.status,
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

    # Check block status before attempting to send
    receiver_id = data.get('receiver_id')
    if receiver_id:
        try:
            receiver = User.objects.get(id=receiver_id, is_active=True)
        except User.DoesNotExist:
            return JsonResponse({'error': 'receiver_not_found', 'detail': 'Receiver not found.'}, status=404)

        if _is_blocked_by(receiver, request.user):
            return JsonResponse({'error': 'conversation_forbidden', 'detail': 'You have been blocked by this user.'}, status=403)
        if _is_blocked_by(request.user, receiver):
            return JsonResponse({'error': 'conversation_forbidden', 'detail': 'You have blocked this user.'}, status=403)

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


# ---------------------------------------------------------------------------
# P2 T05: Data & Storage API
# ---------------------------------------------------------------------------

@login_required(login_url='login')
def storage_stats_view(request):
    """Return storage usage statistics for the current user.

    Provides estimated sizes for each category (images, video, stickers,
    other, video stream chunks) based on database record counts and
    approximate per-item sizes.  A future T24 backend will replace these
    estimates with real file-system measurements.
    """
    user = request.user

    # --- Private messages by type ---
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

    # --- Group messages received by this user ---
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

    # Rough size estimates (bytes per item, conservative)
    EST_IMAGE = 200 * 1024       # 200 KB per image
    EST_FILE = 500 * 1024        # 500 KB per file
    EST_STICKER = 30 * 1024      # 30 KB per sticker
    EST_OTHER = 2 * 1024         # 2 KB per text/system message (ciphertext overhead)

    images_bytes = (private_images + group_images) * EST_IMAGE
    videos_bytes = 0  # No video message type yet — placeholder for T24
    stickers_bytes = (private_stickers + group_stickers) * EST_STICKER
    other_bytes = (private_other + group_other) * EST_OTHER
    # Video stream chunks are not persisted yet (T24)
    video_stream_bytes = 0

    total_bytes = images_bytes + videos_bytes + stickers_bytes + other_bytes + video_stream_bytes
    quota_bytes = 50 * 1024 * 1024  # 50 MB default quota

    return JsonResponse({
        'categories': {
            'images': {
                'size_bytes': images_bytes,
                'count': private_images + group_images,
                'label': 'Images',
            },
            'videos': {
                'size_bytes': videos_bytes,
                'count': 0,
                'label': 'Video files',
            },
            'stickers': {
                'size_bytes': stickers_bytes,
                'count': private_stickers + group_stickers,
                'label': 'Stickers and emojis',
            },
            'other': {
                'size_bytes': other_bytes,
                'count': private_other + group_other,
                'label': 'Other',
            },
            'video_stream_chunks': {
                'size_bytes': video_stream_bytes,
                'count': 0,
                'label': 'Cached video stream chunks',
            },
        },
        'total_bytes': total_bytes,
        'quota_bytes': quota_bytes,
        'usage_percent': round((total_bytes / quota_bytes) * 100, 1) if quota_bytes else 0,
    })


@login_required(login_url='login')
def storage_clear_view(request):
    """Clear cached data for specific categories.

    Accepts: {"categories": ["images", "videos", "stickers", "other",
    "video_stream_chunks"]} or "all".
    Currently a stub — real file-system cleanup depends on T24.
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'Method not allowed.'}, status=405)

    data = _json_body(request)
    categories = data.get('categories', [])

    if not categories:
        return JsonResponse({'error': 'categories list is required.'}, status=400)

    cleared = []
    skipped = []

    for cat in categories:
        if cat in ('images', 'videos', 'stickers', 'other', 'video_stream_chunks', 'all'):
            cleared.append(cat)
        else:
            skipped.append(cat)

    # TODO (T24): perform real file-system / blob cleanup for each category.

    return JsonResponse({
        'status': 'ok',
        'cleared': cleared,
        'skipped': skipped,
        'message': 'Server-side cache cleanup is not yet implemented (T24). Local browser cache should be cleared client-side.',
    })


@login_required(login_url='login')
def storage_settings_view(request):
    """Get or update server-side storage settings for the user.

    GET  — return current settings.
    POST — merge the provided settings keys.
    Currently backed by the session as a lightweight placeholder.
    A future T24 will persist these in a StorageSettings model.
    """
    if request.method == 'GET':
        defaults = {
            'auto_download': {
                'mobile_data': {'photos': False, 'videos': False, 'files': False},
                'wifi': {'photos': True, 'videos': True, 'files': True},
                'roaming': {'photos': False, 'videos': False, 'files': False},
            },
            'file_size_limit_mb': {
                'photos': 10,
                'videos': 50,
                'files': 25,
            },
            'cache_retention_days': 30,
            'cache_max_size_mb': 500,
        }
        stored = request.session.get('ichat_storage_settings', {})
        # Deep-merge stored into defaults
        merged = _deep_merge(defaults, stored)
        return JsonResponse({'settings': merged})

    if request.method == 'POST':
        data = _json_body(request)
        current = request.session.get('ichat_storage_settings', {})
        updated = _deep_merge(current, data)
        request.session['ichat_storage_settings'] = updated
        request.session.modified = True
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
}

# Permission fields only accept 'everyone' or 'contacts' — 'nobody' is NOT valid here
_PRIVACY_PERMISSION_FIELDS = {
    'who_can_send_messages',
    'who_can_voice_video_call',
}

_PRIVACY_BOOL_FIELDS = {
    'sensitive_content_filter',
    'passcode_lock_enabled',
    'two_step_verification_enabled',
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
        'who_can_send_messages': ps.who_can_send_messages,
        'who_can_voice_video_call': ps.who_can_voice_video_call,
        'auto_delete_messages_days': ps.auto_delete_messages_days,
        'sensitive_content_filter': ps.sensitive_content_filter,
        'passcode_lock_enabled': ps.passcode_lock_enabled,
        'two_step_verification_enabled': ps.two_step_verification_enabled,
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

        # Permission fields only accept 'everyone' or 'contacts' — NOT 'nobody'
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
