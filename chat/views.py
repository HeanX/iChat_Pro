import hashlib
import json

from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import F, Q
from django.http import JsonResponse
from django.shortcuts import render
from django.utils import timezone

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


def _avatar_color(name: str) -> str:
    """Return a deterministic colour for a display name."""
    digest = hashlib.md5(name.encode()).hexdigest()
    return AVATAR_COLORS[int(digest, 16) % len(AVATAR_COLORS)]


def _initials(name: str) -> str:
    """Derive 1-2 uppercase initials from a display name."""
    parts = name.strip().split()
    if len(parts) >= 2:
        return (parts[0][0] + parts[-1][0]).upper()
    return (name.strip()[:2] or '?').upper()


def _display_name(user_or_profile, username: str) -> str:
    """Return the best human-readable name for a user."""
    if hasattr(user_or_profile, 'profile'):
        nick = user_or_profile.profile.nickname
        if nick:
            return nick
    return username


def _json_body(request):
    """Parse and return the JSON body of a request, or empty dict on error."""
    try:
        return json.loads(request.body.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return {}


# ---------------------------------------------------------------------------
# Page views
# ---------------------------------------------------------------------------

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
    """Return the authenticated user's active conversations for the sidebar.

    GET /api/conversations/
    """
    memberships = (
        ConversationMember.objects
        .filter(
            user=request.user,
            status=ConversationMember.Status.ACTIVE,
            conversation__status=Conversation.Status.ACTIVE,
        )
        .select_related('conversation')
        .order_by('-conversation__last_message_at')
    )

    conversations = []
    for m in memberships:
        conv = m.conversation
        item = {
            'id': conv.id,
            'type': conv.type,
            'unread': m.unread_count,
            'last_message_at': (
                conv.last_message_at.isoformat() if conv.last_message_at else None
            ),
        }

        if conv.type == Conversation.Type.SINGLE:
            # Find the peer (the other member)
            peer_member = (
                ConversationMember.objects
                .filter(
                    conversation=conv,
                    status=ConversationMember.Status.ACTIVE,
                )
                .exclude(user=request.user)
                .select_related('user__profile')
                .first()
            )
            if peer_member:
                peer = peer_member.user
                item['peer_id'] = peer.id
                item['peer_username'] = peer.username
                item['name'] = _display_name(peer, peer.username)
                item['initials'] = _initials(item['name'])
                item['avatar_color'] = _avatar_color(item['name'])
                # Check if peer has an active public key
                item['is_secure'] = (
                    peer.public_keys.filter(is_active=True).exists()
                )
            else:
                item['name'] = 'Unknown User'
                item['initials'] = '??'
                item['avatar_color'] = AVATAR_COLORS[0]
                item['peer_id'] = None
                item['peer_username'] = None
                item['is_secure'] = False

            item['last_message_preview'] = 'Encrypted message'

        else:  # GROUP
            member_count = ConversationMember.objects.filter(
                conversation=conv,
                status=ConversationMember.Status.ACTIVE,
            ).count()
            item['name'] = conv.name or f'Group #{conv.id}'
            item['initials'] = _initials(item['name'])
            item['avatar_color'] = _avatar_color(item['name'])
            item['member_count'] = member_count
            item['membership_version'] = conv.membership_version
            item['last_message_preview'] = 'Encrypted message'
            # Group is secure if creator has uploaded keys (rough heuristic)
            item['is_secure'] = (
                conv.created_by
                and conv.created_by.public_keys.filter(is_active=True).exists()
            )

        conversations.append(item)

    return JsonResponse({'conversations': conversations})


@login_required(login_url='login')
def get_or_create_single_conversation_view(request):
    """Find an existing private conversation or create one.

    POST /api/conversations/
    Body: {"peer_id": <int>}

    Returns 200 with the conversation id if it already exists,
    201 if a new conversation was created.
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'Method not allowed.'}, status=405)

    data = _json_body(request)
    peer_id = data.get('peer_id')
    if not peer_id:
        return JsonResponse({'error': 'peer_id is required.'}, status=400)

    try:
        peer = User.objects.get(id=peer_id)
    except User.DoesNotExist:
        return JsonResponse({'error': 'User not found.'}, status=404)

    if peer == request.user:
        return JsonResponse({'error': 'Cannot chat with yourself.'}, status=400)

    # Search for an existing SINGLE conversation where both users are members
    my_convs = ConversationMember.objects.filter(
        user=request.user,
        status=ConversationMember.Status.ACTIVE,
        conversation__type=Conversation.Type.SINGLE,
        conversation__status=Conversation.Status.ACTIVE,
    ).values_list('conversation_id', flat=True)

    existing = (
        ConversationMember.objects.filter(
            user=peer,
            status=ConversationMember.Status.ACTIVE,
            conversation_id__in=my_convs,
        )
        .select_related('conversation')
        .first()
    )

    if existing:
        return JsonResponse({
            'conversation_id': existing.conversation_id,
            'created': False,
        })

    # Create new conversation
    with transaction.atomic():
        conv = Conversation.objects.create(
            type=Conversation.Type.SINGLE,
            created_by=request.user,
        )
        ConversationMember.objects.create(
            conversation=conv,
            user=request.user,
            role=ConversationMember.Role.MEMBER,
        )
        ConversationMember.objects.create(
            conversation=conv,
            user=peer,
            role=ConversationMember.Role.MEMBER,
        )

    return JsonResponse({
        'conversation_id': conv.id,
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

    # Only return messages sent on or after the user joined.
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
