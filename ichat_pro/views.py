from django.shortcuts import render, redirect
from django.contrib.auth import login, logout, authenticate
from django.contrib.auth.models import User
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm
from django.http import JsonResponse

def register_view(request):
    if request.user.is_authenticated:
        return redirect('index')
    
    if request.method == 'POST':
        form = UserCreationForm(request.POST)
        if form.is_valid():
            user = form.save()
            # Log the user in after successful registration
            login(request, user)
            messages.success(request, "Registration successful! Welcome to iChat Pro.")
            return redirect('index')
        else:
            for field, errors in form.errors.items():
                for error in errors:
                    messages.error(request, f"{field.capitalize()}: {error}")
    else:
        form = UserCreationForm()
    
    return render(request, 'pages/register.html', {'form': form})

def login_view(request):
    if request.user.is_authenticated:
        return redirect('index')
        
    if request.method == 'POST':
        form = AuthenticationForm(request, data=request.POST)
        if form.is_valid():
            username = form.cleaned_data.get('username')
            password = form.cleaned_data.get('password')
            user = authenticate(username=username, password=password)
            if user is not None:
                login(request, user)
                messages.success(request, f"Welcome back, {username}!")
                return redirect('index')
            else:
                messages.error(request, "Invalid username or password.")
        else:
            messages.error(request, "Invalid username or password.")
    else:
        form = AuthenticationForm()
        
    return render(request, 'pages/login.html', {'form': form})

def logout_view(request):
    logout(request)
    messages.info(request, "You have been logged out.")
    return redirect('login')

@login_required(login_url='login')
def index_view(request):
    # Mock data for chats/conversations list
    chats = [
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
        }
    ]
    
    # Mock message logs for the active chat (Alice Vance)
    active_chat_messages = [
        {
            'sender': 'Alice Vance',
            'is_self': False,
            'text': "Hello! Welcome to our secure chat room. Under the E2EE protocol, all messages are encrypted on my device and decrypted on yours.",
            'time': '18:05',
        },
        {
            'sender': 'Alice Vance',
            'is_self': False,
            'text': "Here is my public key fingerprint:\n9F8D 7E6A 5B4C 3D2E 1F0A 9B8C 7D6E 5F4A",
            'time': '18:06',
        },
        {
            'sender': 'You',
            'is_self': True,
            'text': "Hi Alice! That looks correct. I've verified your key and my browser has automatically generated my session AES-GCM key.",
            'time': '18:10',
            'status': 'read', # read, sent
        },
        {
            'sender': 'Alice Vance',
            'is_self': False,
            'text': "Hey, did you generate your E2EE key pairs?",
            'time': '18:12',
        }
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
    # Renders the main chat window but with settings sidebar overlay active
    # This matches the Telegram Desktop UI pattern
    chats = [
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
        }
    ]
    
    context = {
        'chats': chats,
        'active_chat': chats[0],
        'messages': [],
        'open_settings': True,
    }
    return render(request, 'pages/chat.html', context)
