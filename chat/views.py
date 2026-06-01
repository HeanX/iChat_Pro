from django.contrib.auth.decorators import login_required
from django.shortcuts import render


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
