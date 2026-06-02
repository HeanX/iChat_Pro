import json

from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import F
from django.http import JsonResponse
from django.shortcuts import render

from .models import (
    Conversation,
    ConversationMember,
    EncryptedMessage,
    GroupMessage,
    GroupMessageRecipient,
)


def _mock_chats():
    return [
        {
            'id': 1,
            'name': 'Alice Vance',
            'initials': 'AV',
            'avatar_color': '#5c6bc0',
            'last_message': 'Hey, did you generate your E2EE key pairs?',
            'time': '18:12',
            'unread': 2,
            'is_online': True,
            'is_secure': True,
        },
        {
            'id': 2,
            'name': 'Dev Team Group',
            'initials': 'DT',
            'avatar_color': '#26a69a',
            'last_message': 'Bob: I pushed the updated Electron config today.',
            'time': '16:45',
            'unread': 0,
            'is_online': False,
            'is_secure': False,
        },
        {
            'id': 3,
            'name': 'Telegram Bot',
            'initials': 'TB',
            'avatar_color': '#42a5f5',
            'last_message': 'Welcome to iChat Pro! End-to-end encryption is enabled for private chats.',
            'time': 'Yesterday',
            'unread': 0,
            'is_online': True,
            'is_secure': False,
        },
        {
            'id': 4,
            'name': 'Charlie Brown',
            'initials': 'CB',
            'avatar_color': '#ffa726',
            'last_message': 'My safety fingerprint matches yours.',
            'time': 'May 26',
            'unread': 1,
            'is_online': False,
            'is_secure': True,
        },
    ]


@login_required(login_url='login')
def index_view(request):
    chats = _mock_chats()
    active_chat_messages = [
        {
            'sender': 'Alice Vance',
            'is_self': False,
            'text': 'Hello! Welcome to our secure chat room. Under the E2EE protocol, all messages are encrypted on my device and decrypted on yours.',
            'time': '18:05',
        },
        {
            'sender': 'Alice Vance',
            'is_self': False,
            'text': 'Here is my public key fingerprint:\n9F8D 7E6A 5B4C 3D2E 1F0A 9B8C 7D6E 5F4A',
            'time': '18:06',
        },
        {
            'sender': 'You',
            'is_self': True,
            'text': "Hi Alice! That looks correct. I've verified your key and my browser has automatically generated my session AES-GCM key.",
            'time': '18:10',
            'status': 'read',
        },
        {
            'sender': 'Alice Vance',
            'is_self': False,
            'text': 'Hey, did you generate your E2EE key pairs?',
            'time': '18:12',
        },
    ]

    context = {
        'chats': chats,
        'active_chat': chats[0],
        'messages': active_chat_messages,
        'open_settings': False,
    }

    return render(request, 'pages/chat.html', context)


@login_required(login_url='login')
def settings_view(request):
    chats = _mock_chats()[:2]
    context = {
        'chats': chats,
        'active_chat': chats[0],
        'messages': [],
        'open_settings': True,
    }
    return render(request, 'pages/chat.html', context)


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
