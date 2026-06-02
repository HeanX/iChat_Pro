from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.http import JsonResponse
from django.shortcuts import render

from .models import ConversationMember, EncryptedMessage


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


@login_required(login_url='login')
def conversation_messages_view(request, conversation_id):
    """Return paginated encrypted messages for a conversation.

    Only conversation participants may access the history.
    Messages are ordered newest-first so the frontend can
    load the most recent page by default.
    """
    # Authorization: user must be an active member of this conversation.
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
