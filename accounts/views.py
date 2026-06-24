import base64
import binascii
import hashlib
import io
import json
import re

from PIL import Image, UnidentifiedImageError
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.models import User
from django.db import models, transaction
from django.core.files.base import ContentFile
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_GET, require_POST, require_http_methods

from chat.models import Conversation, ConversationMember as ChatMember

from .forms import ProfileForm, RegistrationForm
from .models import (
    Contact,
    FriendRequest,
    KeyVerificationRequest,
    KeyTrust,
    UserChatFolderSettings,
    UserGeneralSettings,
    UserPrivacySettings,
    UserProfile,
    UserPublicKey,
)


MAX_PUBLIC_KEY_BYTES = 512
MAX_AVATAR_UPLOAD_BYTES = 5 * 1024 * 1024
MAX_AVATAR_PIXELS = 4096 * 4096


def _json_body(request):
    try:
        return json.loads(request.body or '{}')
    except json.JSONDecodeError:
        return None


def _serialize_key(public_key):
    return {
        'user_id': public_key.user_id,
        'identity_public_key': public_key.identity_public_key,
        'key_fingerprint': public_key.key_fingerprint,
        'algorithm': public_key.algorithm,
        'key_version': public_key.key_version,
        'is_active': public_key.is_active,
        'created_at': public_key.created_at.isoformat(),
    }


def _active_key(user_id):
    return UserPublicKey.objects.filter(
        user_id=user_id,
        is_active=True,
    ).first()


def _ensure_single_conversation(user, peer):
    """Create or reuse the active one-to-one conversation for two contacts."""
    my_conversation_ids = ChatMember.objects.filter(
        user=user,
        status=ChatMember.Status.ACTIVE,
        conversation__type=Conversation.Type.SINGLE,
        conversation__status=Conversation.Status.ACTIVE,
    ).values_list('conversation_id', flat=True)

    existing = (
        ChatMember.objects
        .filter(
            user=peer,
            status=ChatMember.Status.ACTIVE,
            conversation_id__in=my_conversation_ids,
        )
        .select_related('conversation')
        .first()
    )
    if existing:
        return existing.conversation, False

    conversation = Conversation.objects.create(
        type=Conversation.Type.SINGLE,
        created_by=user,
    )
    ChatMember.objects.bulk_create([
        ChatMember(
            conversation=conversation,
            user=user,
            role=ChatMember.Role.MEMBER,
        ),
        ChatMember(
            conversation=conversation,
            user=peer,
            role=ChatMember.Role.MEMBER,
        ),
    ])
    return conversation, True


def register_view(request):
    if request.user.is_authenticated:
        return redirect('index')

    if request.method == 'POST':
        form = RegistrationForm(request.POST)
        if form.is_valid():
            user = form.save()
            user.email = request.POST.get('email', '').strip()
            user.save(update_fields=['email'])
            login(request, user)
            messages.success(
                request,
                'Registration successful! Welcome to iChat Pro.',
            )
            return redirect('index')

        # Collect field-level errors for display
        for field, errors in form.errors.items():
            for error in errors:
                messages.error(request, error)
    else:
        form = RegistrationForm()

    return render(request, 'pages/register.html', {'form': form})


def _safe_next_url(next_url, fallback='index'):
    """Return next_url if it is a safe same-site path, otherwise fallback."""
    if next_url and url_has_allowed_host_and_scheme(next_url, allowed_hosts=None):
        return next_url
    return fallback


def login_view(request):
    if request.user.is_authenticated:
        return redirect('index')

    if request.method == 'POST':
        post_data = request.POST.copy()
        username = post_data.get('username', '').strip()
        password = request.POST.get('password', '')
        target = (
            User.objects.filter(username=username).first()
            or User.objects.filter(email__iexact=username).first()
        )
        if target is not None:
            post_data['username'] = target.get_username()

        form = AuthenticationForm(request, data=post_data)

        if form.is_valid():
            user = authenticate(
                request, username=post_data.get('username', ''), password=password,
            )

            if user is not None:
                login(request, user)
                messages.success(
                    request,
                    f'Welcome back, {user.get_short_name() or username}!',
                )
                next_url = _safe_next_url(
                    request.POST.get('next') or request.GET.get('next') or '',
                )
                return redirect(next_url)

            # credentials were valid format but authenticate returned None
            if target is not None and not target.is_active:
                messages.error(
                    request,
                    '该账号已被禁用，请联系管理员。',
                )
            else:
                # Use a generic message to prevent account enumeration
                messages.error(
                    request,
                    '用户名或密码错误，请重试。',
                )
        else:
            # Distinguish between missing fields and bad credentials
            if username and password:
                if target is not None and not target.is_active:
                    messages.error(
                        request,
                        '该账号已被禁用，请联系管理员。',
                    )
                else:
                    # Use a generic message to prevent account enumeration
                    messages.error(
                        request,
                        '用户名或密码错误，请重试。',
                    )
            else:
                for field, errors in form.errors.items():
                    for error in errors:
                        messages.error(request, error)
    else:
        form = AuthenticationForm()

    return render(request, 'pages/login.html', {'form': form})


def logout_view(request):
    logout(request)
    next_url = _safe_next_url(request.GET.get('next') or '', fallback='login')
    return redirect(next_url)


# ── Contact & Friend-request views ──────────────────────────────────


def _sidebar_conversations_context(request):
    user = request.user
    status_filter = models.Q(
        user=user,
        status=ChatMember.Status.ACTIVE,
        conversation__status=Conversation.Status.ACTIVE,
        archived_at__isnull=True,
        hidden_at__isnull=True,
    )
    memberships = (
        ChatMember.objects
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
        
        last_message_preview = ''
        last_msg_at = conversation.last_message_at
        if last_msg_at:
            last_message_preview = 'Encrypted message'
        
        if conversation.type == Conversation.Type.SINGLE:
            peer_member = (
                ChatMember.objects
                .filter(conversation=conversation, status=ChatMember.Status.ACTIVE)
                .exclude(user=user)
                .select_related('user__profile')
                .first()
            )
            if not peer_member:
                name = 'Unknown User'
                initials = '??'
                avatar_color = '#5c6bc0'
                avatar_url = ''
                peer_id = None
                is_secure = False
            else:
                peer = peer_member.user
                from chat.views import _display_name, _initials, _avatar_color, _avatar_url
                name = _display_name(peer)
                initials = _initials(name)
                avatar_color = _avatar_color(name)
                avatar_url = _avatar_url(request, peer)
                is_secure = peer.public_keys.filter(is_active=True).exists()
                peer_id = peer.id
            
            conversations.append({
                'id': conversation.id,
                'type': 'single',
                'name': name,
                'initials': initials,
                'avatar_color': avatar_color,
                'avatar_url': avatar_url,
                'peer_id': peer_id,
                'is_secure': is_secure,
                'unread': membership.unread_count,
                'is_pinned': membership.is_pinned,
                'is_muted': is_muted,
                'last_message_preview': last_message_preview,
                'last_message_at': last_msg_at,
            })
        else:
            name = conversation.name or f'Group #{conversation.id}'
            from chat.views import _initials, _avatar_color
            initials = _initials(name)
            avatar_color = _avatar_color(name)
            if not last_message_preview:
                last_message_preview = 'Open group chat'
            
            conversations.append({
                'id': conversation.id,
                'type': 'group',
                'name': name,
                'initials': initials,
                'avatar_color': avatar_color,
                'avatar_url': '',
                'is_secure': True,
                'unread': membership.unread_count,
                'is_pinned': membership.is_pinned,
                'is_muted': is_muted,
                'last_message_preview': last_message_preview,
                'last_message_at': last_msg_at,
            })
            
    return {
        'sidebar_conversations': conversations,
    }


@login_required(login_url='login')
def contact_list_view(request):
    """Show the user's contacts and pending incoming friend requests."""

    contacts = Contact.objects.filter(
        models.Q(user=request.user) | models.Q(contact=request.user),
    ).select_related('user__profile', 'contact__profile')

    incoming = FriendRequest.objects.filter(
        receiver=request.user,
        status=FriendRequest.Status.PENDING,
    ).select_related('sender__profile')

    outgoing = FriendRequest.objects.filter(
        sender=request.user,
        status=FriendRequest.Status.PENDING,
    ).select_related('receiver__profile')

    context = {
        'open_settings': False,
        'open_contacts': True,
        'contacts': contacts,
        'incoming_requests': incoming,
        'outgoing_requests': outgoing,
    }
    return render(request, 'pages/chat.html', context)


def _avatar_url(request, user):
    """Return absolute avatar image URL for a user, or empty string if none set."""
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


@login_required(login_url='login')
def search_users(request):
    """Search for users by username, nickname, or user ID (JSON endpoint)."""
    query = request.GET.get('q', '').strip()
    results = []

    if query:
        current_user = request.user

        # Match by exact user ID
        id_matches = User.objects.none()
        if query.isdigit():
            id_matches = User.objects.filter(id=int(query))

        # Match by username or nickname
        name_matches = User.objects.filter(
            models.Q(username__icontains=query)
            | models.Q(profile__nickname__icontains=query),
        )

        # Combine, exclude self, deduplicate, limit
        users = (
            (id_matches | name_matches)
            .exclude(id=current_user.id)
            .distinct()
            .select_related('profile')
        )[:20]

        for user in users:
            # Resolve nickname (UserProfile may not exist yet)
            try:
                nickname = user.profile.nickname or ''
            except UserProfile.DoesNotExist:
                nickname = ''

            is_contact = Contact.objects.filter(
                (models.Q(user=current_user) & models.Q(contact=user))
                | (models.Q(user=user) & models.Q(contact=current_user)),
            ).exists()

            has_pending_out = FriendRequest.objects.filter(
                sender=current_user,
                receiver=user,
                status=FriendRequest.Status.PENDING,
            ).exists()

            has_pending_in = FriendRequest.objects.filter(
                sender=user,
                receiver=current_user,
                status=FriendRequest.Status.PENDING,
            ).exists()

            try:
                user_type = user.profile.user_type
            except UserProfile.DoesNotExist:
                user_type = 'user'

            results.append({
                'id': user.id,
                'username': user.username,
                'nickname': nickname,
                'user_type': user_type,
                'is_contact': is_contact,
                'has_pending_out': has_pending_out,
                'has_pending_in': has_pending_in,
                'avatar_url': _avatar_url(request, user),
            })

    return JsonResponse({'results': results})


@login_required(login_url='login')
@require_http_methods(['POST'])
def friend_request_send(request):
    """Send a friend request to another user (by username or user ID)."""
    username = request.POST.get('username', '').strip()
    user_id = request.POST.get('user_id', '').strip()

    if not username and not user_id:
        messages.error(request, 'Please provide a username or user ID.')
        return redirect('contacts')

    if user_id and user_id.isdigit():
        receiver = get_object_or_404(User, id=int(user_id))
    else:
        receiver = get_object_or_404(User, username=username)

    if receiver == request.user:
        messages.error(request, 'You cannot add yourself as a contact.')
        return redirect('contacts')

    already_contacts = Contact.objects.filter(
        (models.Q(user=request.user) & models.Q(contact=receiver))
        | (models.Q(user=receiver) & models.Q(contact=request.user)),
    ).exists()

    if already_contacts:
        messages.info(request, f'{username} is already in your contacts.')
        return redirect('contacts')

    existing = FriendRequest.objects.filter(
        (
            models.Q(sender=request.user, receiver=receiver)
            | models.Q(sender=receiver, receiver=request.user)
        ),
        status=FriendRequest.Status.PENDING,
    ).first()

    if existing:
        if existing.sender == request.user:
            messages.info(request, 'You already sent a request to this user.')
        else:
            messages.info(
                request,
                f'{username} has already sent you a request. '
                'Accept it instead.',
            )
        return redirect('contacts')

    FriendRequest.objects.create(sender=request.user, receiver=receiver)
    messages.success(request, f'Friend request sent to {username}.')
    return redirect('contacts')


@login_required(login_url='login')
@require_http_methods(['POST'])
def friend_request_accept(request, request_id):
    """Accept an incoming friend request and create a Contact."""
    friend_request = get_object_or_404(
        FriendRequest,
        id=request_id,
        receiver=request.user,
        status=FriendRequest.Status.PENDING,
    )

    with transaction.atomic():
        friend_request.status = FriendRequest.Status.ACCEPTED
        friend_request.save()

        Contact.objects.get_or_create(
            user=friend_request.sender,
            contact=friend_request.receiver,
        )
        _ensure_single_conversation(
            friend_request.sender,
            friend_request.receiver,
        )

    messages.success(
        request,
        f'You are now contacts with {friend_request.sender.username}. '
        'A private conversation is ready.',
    )
    return redirect('contacts')


@login_required(login_url='login')
@require_http_methods(['POST'])
def friend_request_reject(request, request_id):
    """Reject an incoming friend request."""
    friend_request = get_object_or_404(
        FriendRequest,
        id=request_id,
        receiver=request.user,
        status=FriendRequest.Status.PENDING,
    )

    friend_request.status = FriendRequest.Status.REJECTED
    friend_request.save()

    messages.info(
        request,
        f'Friend request from {friend_request.sender.username} rejected.',
    )
    return redirect('contacts')


@login_required(login_url='login')
@require_http_methods(['POST'])
def friend_request_cancel(request, user_id):
    """Cancel an outgoing friend request to the given user. JSON API for T17."""
    friend_request = get_object_or_404(
        FriendRequest,
        sender=request.user,
        receiver_id=user_id,
        status=FriendRequest.Status.PENDING,
    )
    friend_request.status = FriendRequest.Status.REJECTED
    friend_request.save()
    return JsonResponse({'status': 'ok'})


@login_required(login_url='login')
@require_http_methods(['POST'])
def friend_request_accept_by_user(request, user_id):
    """Accept an incoming friend request from the given user. JSON API for T17."""
    friend_request = get_object_or_404(
        FriendRequest,
        sender_id=user_id,
        receiver=request.user,
        status=FriendRequest.Status.PENDING,
    )

    with transaction.atomic():
        friend_request.status = FriendRequest.Status.ACCEPTED
        friend_request.save()
        Contact.objects.get_or_create(
            user=friend_request.sender,
            contact=friend_request.receiver,
        )
        _ensure_single_conversation(
            friend_request.sender,
            friend_request.receiver,
        )

    return JsonResponse({'status': 'ok', 'username': friend_request.sender.username})


@login_required(login_url='login')
@require_http_methods(['POST'])
def friend_request_reject_by_user(request, user_id):
    """Reject an incoming friend request from the given user. JSON API for T17."""
    friend_request = get_object_or_404(
        FriendRequest,
        sender_id=user_id,
        receiver=request.user,
        status=FriendRequest.Status.PENDING,
    )
    friend_request.status = FriendRequest.Status.REJECTED
    friend_request.save()
    return JsonResponse({'status': 'ok'})


@login_required(login_url='login')
@require_http_methods(['POST'])
def contact_delete(request, contact_id):
    """Remove a contact (friendship)."""
    contact = get_object_or_404(
        Contact,
        id=contact_id,
    )

    if request.user not in (contact.user, contact.contact):
        messages.error(request, 'You are not part of this contact.')
        return redirect('contacts')

    other = contact.contact if contact.user == request.user else contact.user
    contact.delete()
    messages.info(
        request,
        f'{other.username} has been removed from your contacts.',
    )
    return redirect('contacts')


@login_required(login_url='login')
def contact_chat_view(request, contact_id):
    """Open or create a private chat with an existing contact."""
    contact = get_object_or_404(Contact, id=contact_id)

    if request.user not in (contact.user, contact.contact):
        messages.error(request, 'You are not part of this contact.')
        return redirect('contacts')

    peer = contact.contact if contact.user == request.user else contact.user
    conversation, _ = _ensure_single_conversation(request.user, peer)
    return redirect(f'{reverse("index")}?conversation={conversation.id}')


# ── Profile views ──────────────────────────────────────────────────


@login_required(login_url='login')
def avatar_editor_view(request):
    """Render the avatar crop/edit page."""
    return render(request, 'pages/avatar_editor.html')


@login_required(login_url='login')
@require_POST
def avatar_crop_save_view(request):
    """Accept a base64 cropped image, save it to the user's profile."""
    cropped_data = request.POST.get('cropped_data', '').strip()
    if not cropped_data:
        return JsonResponse({'error': 'no_data'}, status=400)

    # Strip the data URL prefix (data:image/jpeg;base64,...)
    match = re.match(r'^data:image/(?P<fmt>\w+);base64,(?P<data>.+)$', cropped_data)
    if not match:
        return JsonResponse({'error': 'invalid_data'}, status=400)

    fmt = match.group('fmt').lower()
    if fmt not in ('jpeg', 'jpg', 'png', 'webp'):
        fmt = 'jpeg'
    ext = 'jpg' if fmt in ('jpeg', 'jpg') else fmt

    try:
        raw = base64.b64decode(match.group('data'), validate=True)
    except Exception:
        return JsonResponse({'error': 'decode_error'}, status=400)
    if len(raw) > MAX_AVATAR_UPLOAD_BYTES:
        return JsonResponse({'error': 'image_too_large'}, status=400)

    try:
        with Image.open(io.BytesIO(raw)) as image:
            image.verify()
        with Image.open(io.BytesIO(raw)) as image:
            if image.width * image.height > MAX_AVATAR_PIXELS:
                return JsonResponse({'error': 'image_too_large'}, status=400)
            image = image.convert('RGBA' if ext == 'png' else 'RGB')
            normalized = io.BytesIO()
            save_format = 'JPEG' if ext == 'jpg' else ext.upper()
            if save_format == 'WEBP':
                image.save(normalized, format=save_format, quality=90)
            elif save_format == 'PNG':
                image.save(normalized, format=save_format, optimize=True)
            else:
                image.save(normalized, format=save_format, quality=90, optimize=True)
            raw = normalized.getvalue()
    except (UnidentifiedImageError, OSError, ValueError):
        return JsonResponse({'error': 'invalid_image'}, status=400)

    profile, _ = UserProfile.objects.get_or_create(user=request.user)

    filename = f'avatar_{request.user.id}.{ext}'
    profile.avatar.save(filename, ContentFile(raw), save=True)

    avatar_url = ''
    try:
        timestamp = int(profile.updated_at.timestamp())
        avatar_url = request.build_absolute_uri(f"{profile.avatar.url}?t={timestamp}")
    except Exception:
        try:
            avatar_url = request.build_absolute_uri(profile.avatar.url)
        except Exception:
            pass

    # Find contacts and active conversation members to notify of profile change
    peer_ids = set()
    try:
        from django.db.models import Q
        from chat.models import ConversationMember
        from .models import Contact

        contacts = Contact.objects.filter(Q(user=request.user) | Q(contact=request.user))
        for c in contacts:
            peer_ids.add(c.contact_id if c.user_id == request.user.id else c.user_id)

        my_convs = ConversationMember.objects.filter(
            user=request.user,
            status=ConversationMember.Status.ACTIVE
        ).values_list('conversation_id', flat=True)
        
        other_members = ConversationMember.objects.filter(
            conversation_id__in=my_convs,
            status=ConversationMember.Status.ACTIVE
        ).exclude(user=request.user).values_list('user_id', flat=True)
        
        peer_ids.update(other_members)
    except Exception:
        pass

    # Broadcast event via Channels
    try:
        from channels.layers import get_channel_layer
        from asgiref.sync import async_to_sync
        channel_layer = get_channel_layer()
        if channel_layer:
            display_name = profile.nickname or request.user.get_full_name() or request.user.username
            for pid in peer_ids:
                async_to_sync(channel_layer.group_send)(
                    f"user_{pid}",
                    {
                        "type": "profile.updated",
                        "data": {
                            "user_id": request.user.id,
                            "username": request.user.username,
                            "display_name": display_name,
                            "avatar_url": avatar_url,
                        }
                    }
                )
    except Exception:
        pass

    return JsonResponse({
        'ok': True,
        'avatar': avatar_url,
        'profile': _profile_payload(request, profile),
    })


@login_required(login_url='login')
def profile_edit_view(request):
    """Edit nickname, bio, avatar, username, and name fields."""
    profile, _ = UserProfile.objects.get_or_create(user=request.user)

    is_sidebar = request.POST.get('_sidebar') if request.method == 'POST' else False

    if request.method == 'POST':
        form = ProfileForm(
            request.POST, request.FILES, instance=profile, user=request.user,
        )
        if form.is_valid():
            form.save()
            # Save User model fields
            user = request.user
            user.first_name = form.cleaned_data.get('first_name', '').strip()
            user.last_name = form.cleaned_data.get('last_name', '').strip()
            new_username = form.cleaned_data.get('username', '').strip().lower()
            if new_username and new_username != user.username:
                user.username = new_username
            user.save(update_fields=['first_name', 'last_name', 'username'])
            messages.success(request, 'Profile updated.')
            return redirect('index')
        for field, errors in form.errors.items():
            for error in errors:
                messages.error(request, error)
    else:
        form = ProfileForm(instance=profile, user=request.user, initial={
            'first_name': request.user.first_name,
            'last_name': request.user.last_name,
            'username': request.user.username,
        })

    template = 'pages/profile_edit_sidebar.html' if is_sidebar else 'pages/profile_edit.html'
    return render(request, template, {
        'form': form,
        'profile': profile,
    })


# ── Group views (consolidated to chat.Conversation — T22) ────────────


def _profile_payload(request, profile):
    avatar_url = ''
    if profile.avatar:
        try:
            timestamp = int(profile.updated_at.timestamp())
            avatar_url = request.build_absolute_uri(f"{profile.avatar.url}?t={timestamp}")
        except Exception:
            try:
                avatar_url = request.build_absolute_uri(profile.avatar.url)
            except ValueError:
                avatar_url = ''

    display_name = profile.nickname or request.user.get_full_name() or request.user.username
    from chat.views import _avatar_color
    return {
        'user_id': request.user.id,
        'username': request.user.username,
        'first_name': request.user.first_name,
        'last_name': request.user.last_name,
        'nickname': profile.nickname,
        'display_name': display_name,
        'initials': (display_name[:1] or request.user.username[:1] or '?').upper(),
        'bio': profile.bio,
        'avatar': avatar_url,
        'avatar_color': _avatar_color(display_name),
        'phone_number': profile.phone_number,
        'location': profile.location,
        'birthday': profile.birthday.isoformat() if profile.birthday else '',
    }


def _save_profile_form(request, form):
    profile = form.save()
    user = request.user
    user.first_name = form.cleaned_data.get('first_name', '').strip()
    user.last_name = form.cleaned_data.get('last_name', '').strip()
    new_username = form.cleaned_data.get('username', '').strip().lower()
    if new_username and new_username != user.username:
        user.username = new_username
    user.save(update_fields=['first_name', 'last_name', 'username'])
    return profile


@login_required(login_url='login')
@require_http_methods(['GET', 'POST', 'PUT'])
def profile_me_api_view(request):
    """Return or update the current user's profile for the Telegram-style sidebar."""
    profile, _ = UserProfile.objects.get_or_create(user=request.user)

    if request.method == 'GET':
        return JsonResponse({'profile': _profile_payload(request, profile)})

    if request.method == 'PUT':
        payload = _json_body(request)
        if payload is None:
            return JsonResponse({'error': 'invalid_json'}, status=400)
        data = {
            'username': payload.get('username', request.user.username),
            'first_name': payload.get('first_name', request.user.first_name),
            'last_name': payload.get('last_name', request.user.last_name),
            'nickname': payload.get('nickname', profile.nickname),
            'bio': payload.get('bio', profile.bio),
            'phone_number': payload.get('phone_number', profile.phone_number),
            'location': payload.get('location', profile.location),
            'birthday': payload.get('birthday', profile.birthday.isoformat() if profile.birthday else ''),
        }
        form = ProfileForm(data, instance=profile, user=request.user)
    else:
        post_data = request.POST.copy()
        post_data.setdefault('username', request.user.username)
        post_data.setdefault('nickname', profile.nickname)
        post_data.setdefault('bio', profile.bio)
        form = ProfileForm(post_data, request.FILES, instance=profile, user=request.user)

    if not form.is_valid():
        return JsonResponse({'errors': form.errors}, status=400)

    profile = _save_profile_form(request, form)
    return JsonResponse({'profile': _profile_payload(request, profile)})


@login_required(login_url='login')
def group_list_view(request):
    """Show all groups the user is a member of."""
    memberships = ChatMember.objects.filter(
        user=request.user,
        conversation__type=Conversation.Type.GROUP,
        status=ChatMember.Status.ACTIVE,
    ).select_related('conversation')
    group_list = [m.conversation for m in memberships]
    return render(request, 'pages/chat.html', {
        'open_settings': False,
        'open_groups': True,
        'groups': group_list,
    })


@login_required(login_url='login')
def group_create_view(request):
    """Create a new group."""
    if request.method == 'POST':
        name = request.POST.get('name', '').strip()

        if not name:
            messages.error(request, 'Group name is required.')
            return redirect('groups')

        conversation = Conversation.objects.create(
            type=Conversation.Type.GROUP,
            name=name,
            created_by=request.user,
        )
        ChatMember.objects.create(
            conversation=conversation,
            user=request.user,
            role=ChatMember.Role.OWNER,
        )
        messages.success(request, f'Group "{name}" created.')
        return redirect('group_detail', group_id=conversation.id)

    memberships = ChatMember.objects.filter(
        user=request.user,
        conversation__type=Conversation.Type.GROUP,
        status=ChatMember.Status.ACTIVE,
    ).select_related('conversation')
    group_list = [m.conversation for m in memberships]
    return render(request, 'pages/chat.html', {
        'open_settings': False,
        'open_groups': True,
        'show_create': True,
        'groups': group_list,
    })


@login_required(login_url='login')
def group_detail_view(request, group_id):
    """Show group details and member list."""
    conversation = get_object_or_404(
        Conversation, id=group_id, type=Conversation.Type.GROUP,
    )
    members = conversation.members.select_related('user')
    current = members.filter(user=request.user).first()

    if not current or current.status != ChatMember.Status.ACTIVE:
        messages.error(request, 'You are not a member of this group.')
        return redirect('groups')

    return render(request, 'pages/chat.html', {
        'open_settings': False,
        'open_group_detail': True,
        'group': conversation,
        'members': members,
        'is_admin': current.role in (ChatMember.Role.OWNER, ChatMember.Role.ADMIN),
    })


@login_required(login_url='login')
@require_http_methods(['POST'])
def group_add_member_view(request, group_id):
    """Add a contact to a group. Requires owner/admin role (T23)."""
    conversation = get_object_or_404(
        Conversation, id=group_id, type=Conversation.Type.GROUP,
    )

    current = ChatMember.objects.filter(
        conversation=conversation,
        user=request.user,
        status=ChatMember.Status.ACTIVE,
    ).first()
    if not current or current.role not in (ChatMember.Role.OWNER, ChatMember.Role.ADMIN):
        messages.error(request, 'Only group admins can add members.')
        return redirect('group_detail', group_id=conversation.id)

    username = request.POST.get('username', '').strip()
    user_to_add = get_object_or_404(User, username=username)

    is_contact = Contact.objects.filter(
        (models.Q(user=request.user) & models.Q(contact=user_to_add))
        | (models.Q(user=user_to_add) & models.Q(contact=request.user)),
    ).exists()
    if not is_contact:
        messages.error(request, f'{username} is not in your contacts.')
        return redirect('group_detail', group_id=conversation.id)

    _, created = ChatMember.objects.get_or_create(
        conversation=conversation,
        user=user_to_add,
        defaults={'role': ChatMember.Role.MEMBER},
    )
    if created:
        messages.success(request, f'{username} added to {conversation.name}.')
    else:
        messages.info(request, f'{username} is already a member.')
    return redirect('group_detail', group_id=conversation.id)


@login_required(login_url='login')
@require_http_methods(['POST'])
def group_leave_view(request, group_id):
    """Leave a group."""
    conversation = get_object_or_404(
        Conversation, id=group_id, type=Conversation.Type.GROUP,
    )
    membership = get_object_or_404(
        ChatMember, conversation=conversation, user=request.user,
    )

    if membership.role == ChatMember.Role.OWNER:
        other_members = conversation.members.filter(
            status=ChatMember.Status.ACTIVE,
        ).exclude(user=request.user).exists()
        if not other_members:
            conversation.status = Conversation.Status.DELETED
            conversation.save(update_fields=['status', 'updated_at'])
        else:
            messages.error(
                request,
                'You are the owner. Transfer ownership or delete the group before leaving.',
            )
            return redirect('group_detail', group_id=conversation.id)

    membership.status = ChatMember.Status.LEFT
    membership.left_at = timezone.now()
    membership.save(update_fields=['status', 'left_at'])
    messages.info(request, f'You left "{conversation.name}".')
    return redirect('groups')


# ── Public-key management API (multi-version E2EE) ─────────────────


@login_required
@require_POST
def upload_public_key_view(request):
    """Upload a new public key, rotating the active version atomically."""
    payload = _json_body(request)
    if payload is None:
        return JsonResponse({'error': 'invalid_json'}, status=400)

    forbidden_fields = {'private_key', 'session_key', 'file_key'}
    if forbidden_fields.intersection(payload):
        return JsonResponse({'error': 'private_key_material_not_allowed'}, status=400)

    identity_public_key = payload.get('identity_public_key', '')
    algorithm = payload.get('algorithm', UserPublicKey.ALGORITHM_ECDH_P256)
    if algorithm != UserPublicKey.ALGORITHM_ECDH_P256:
        return JsonResponse({'error': 'unsupported_algorithm'}, status=400)

    try:
        decoded_key = base64.b64decode(identity_public_key, validate=True)
    except (ValueError, binascii.Error):
        return JsonResponse({'error': 'invalid_public_key'}, status=400)
    if not decoded_key or len(decoded_key) > MAX_PUBLIC_KEY_BYTES:
        return JsonResponse({'error': 'invalid_public_key'}, status=400)

    fingerprint = hashlib.sha256(decoded_key).hexdigest().upper()
    supplied_fingerprint = payload.get('key_fingerprint', '').replace(':', '').upper()
    if supplied_fingerprint and supplied_fingerprint != fingerprint:
        return JsonResponse({'error': 'fingerprint_mismatch'}, status=400)

    with transaction.atomic():
        existing = _active_key(request.user.pk)
        if existing and existing.identity_public_key == identity_public_key:
            return JsonResponse({'key': _serialize_key(existing)})

        latest = UserPublicKey.objects.filter(user=request.user).first()
        next_version = latest.key_version + 1 if latest else 1
        UserPublicKey.objects.filter(user=request.user, is_active=True).update(is_active=False)
        public_key = UserPublicKey.objects.create(
            user=request.user,
            identity_public_key=identity_public_key,
            key_fingerprint=fingerprint,
            algorithm=algorithm,
            key_version=next_version,
        )

    return JsonResponse({'key': _serialize_key(public_key)}, status=201)


@login_required
@require_GET
def public_key_view(request, user_id):
    public_key = _active_key(user_id)
    if public_key is None:
        return JsonResponse({'error': 'public_key_not_found'}, status=404)
    return JsonResponse({'key': _serialize_key(public_key)})


@login_required
@require_GET
def public_key_version_view(request, user_id, key_version):
    public_key = UserPublicKey.objects.filter(
        user_id=user_id,
        key_version=key_version,
    ).first()
    if public_key is None:
        return JsonResponse({'error': 'public_key_not_found'}, status=404)
    return JsonResponse({'key': _serialize_key(public_key)})


@login_required
@require_POST
def batch_public_keys_view(request):
    payload = _json_body(request)
    if payload is None or not isinstance(payload.get('user_ids'), list):
        return JsonResponse({'error': 'user_ids_must_be_a_list'}, status=400)

    user_ids = list(dict.fromkeys(payload['user_ids']))
    if len(user_ids) > 100 or any(not isinstance(user_id, int) for user_id in user_ids):
        return JsonResponse({'error': 'invalid_user_ids'}, status=400)

    public_keys = UserPublicKey.objects.filter(user_id__in=user_ids, is_active=True)
    return JsonResponse({'keys': [_serialize_key(public_key) for public_key in public_keys]})


@login_required
@require_GET
def public_key_fingerprint_view(request, user_id):
    public_key = _active_key(user_id)
    if public_key is None:
        return JsonResponse({'error': 'public_key_not_found'}, status=404)
    return JsonResponse({
        'user_id': public_key.user_id,
        'key_fingerprint': public_key.key_fingerprint,
        'key_version': public_key.key_version,
    })


# ── P2 T38: Key trust and fingerprint management ──────────────────


@login_required
@require_GET
def my_fingerprints_view(request):
    """Return all public keys (active + historical) for the current user."""
    keys = UserPublicKey.objects.filter(user=request.user).order_by('-key_version')
    trust_counts = (
        KeyTrust.objects
        .filter(contact=request.user, trust_status=KeyTrust.TrustStatus.TRUSTED)
        .count()
    )
    return JsonResponse({
        'user_id': request.user.pk,
        'keys': [_serialize_key(k) for k in keys],
        'active_key_count': keys.filter(is_active=True).count(),
        'trusted_by_count': trust_counts,
    })


@login_required
@require_GET
def contact_fingerprints_view(request, user_id):
    """Return public key fingerprints for a given contact (T38)."""
    try:
        from django.contrib.auth import get_user_model
        User = get_user_model()
        target = User.objects.get(id=user_id, is_active=True)
    except Exception:
        return JsonResponse({'error': 'User not found.'}, status=404)

    keys = UserPublicKey.objects.filter(user=target).order_by('-key_version')
    active_key = keys.filter(is_active=True).first()

    # Check if current user has verified any of the contact's keys
    trust_records = KeyTrust.objects.filter(
        user=request.user,
        contact=target,
    )
    trust_map = {t.key_fingerprint: t.trust_status for t in trust_records}

    key_data = []
    for k in keys:
        info = _serialize_key(k)
        info['trust_status'] = trust_map.get(k.key_fingerprint, 'untrusted')
        key_data.append(info)

    is_contact = Contact.objects.filter(
        (models.Q(user=request.user) & models.Q(contact=target))
        | (models.Q(user=target) & models.Q(contact=request.user)),
    ).exists()

    return JsonResponse({
        'user_id': target.pk,
        'username': target.username,
        'is_contact': is_contact,
        'active_key': _serialize_key(active_key) if active_key else None,
        'keys': key_data,
    })


@login_required
@require_http_methods(['POST', 'DELETE'])
def key_trust_view(request, user_id):
    """Trust or untrust a contact's active key (T38)."""
    from django.contrib.auth import get_user_model
    User = get_user_model()

    try:
        target = User.objects.get(id=user_id, is_active=True)
    except User.DoesNotExist:
        return JsonResponse({'error': 'User not found.'}, status=404)

    if target == request.user:
        return JsonResponse({'error': 'Cannot trust your own key.'}, status=400)

    active_key = _active_key(user_id)
    if active_key is None:
        return JsonResponse({'error': 'Contact has no public key.'}, status=404)

    if request.method == 'POST':
        trust_status = _json_body(request).get('trust_status', 'trusted')
        if trust_status not in KeyTrust.TrustStatus.values:
            return JsonResponse({'error': 'Invalid trust status.'}, status=400)

        key_trust, created = KeyTrust.objects.update_or_create(
            user=request.user,
            contact=target,
            key_fingerprint=active_key.key_fingerprint,
            defaults={
                'key_version': active_key.key_version,
                'trust_status': trust_status,
                'verified_at': timezone.now(),
            },
        )
        return JsonResponse({
            'status': 'ok',
            'created': created,
            'trust_status': key_trust.trust_status,
            'key_fingerprint': key_trust.key_fingerprint,
        })

    elif request.method == 'DELETE':
        deleted, _ = KeyTrust.objects.filter(
            user=request.user,
            contact=target,
            key_fingerprint=active_key.key_fingerprint,
        ).delete()
        return JsonResponse({
            'status': 'ok',
            'deleted': deleted > 0,
        })

    return JsonResponse({'error': 'Method not allowed.'}, status=405)


@login_required
@require_GET
def key_trust_list_view(request):
    """List key trust status for all contacts (T38)."""
    trust_records = KeyTrust.objects.filter(
        user=request.user,
    ).select_related('contact').order_by('-updated_at')

    results = []
    for t in trust_records:
        # Check if the contact has rotated their key
        active_key = _active_key(t.contact_id)
        key_changed = (
            active_key is not None
            and active_key.key_fingerprint != t.key_fingerprint
        )
        results.append({
            'contact_id': t.contact_id,
            'contact_username': t.contact.username,
            'key_fingerprint': t.key_fingerprint,
            'key_version': t.key_version,
            'trust_status': t.trust_status,
            'verified_at': t.verified_at.isoformat() if t.verified_at else None,
            'key_changed': key_changed,
            'active_key_fingerprint': active_key.key_fingerprint if active_key else None,
        })

    return JsonResponse({'trusts': results})


def _key_verification_allowed(user, target):
    if user == target:
        return False
    if Contact.objects.filter(
        (models.Q(user=user) & models.Q(contact=target))
        | (models.Q(user=target) & models.Q(contact=user)),
    ).exists():
        return True
    return Conversation.objects.filter(
        type=Conversation.Type.SINGLE,
        status=Conversation.Status.ACTIVE,
        members__user=user,
        members__status=ChatMember.Status.ACTIVE,
    ).filter(
        members__user=target,
        members__status=ChatMember.Status.ACTIVE,
    ).exists()


def _serialize_key_verification_request(verification, viewer=None):
    return {
        'id': verification.pk,
        'requester_id': verification.requester_id,
        'requester_username': verification.requester.username,
        'responder_id': verification.responder_id,
        'responder_username': verification.responder.username,
        'requester_key_version': verification.requester_key_version,
        'requester_key_fingerprint': verification.requester_key_fingerprint,
        'responder_key_version': verification.responder_key_version,
        'responder_key_fingerprint': verification.responder_key_fingerprint,
        'status': verification.status,
        'direction': (
            'incoming'
            if viewer is not None and verification.responder_id == viewer.pk
            else 'outgoing'
        ),
        'created_at': verification.created_at.isoformat(),
        'responded_at': verification.responded_at.isoformat() if verification.responded_at else None,
    }


def _push_key_verification_event(verification, event):
    channel_layer = get_channel_layer()
    if channel_layer is None:
        return
    for user in (verification.requester, verification.responder):
        async_to_sync(channel_layer.group_send)(
            f'user_{user.pk}',
            {
                'type': 'key.verification.new',
                'data': {
                    'event': event,
                    'verification': _serialize_key_verification_request(verification, viewer=user),
                },
            },
        )


def _upsert_verified_key_trust(user, contact, key):
    return KeyTrust.objects.update_or_create(
        user=user,
        contact=contact,
        key_fingerprint=key.key_fingerprint,
        defaults={
            'key_version': key.key_version,
            'trust_status': KeyTrust.TrustStatus.VERIFIED,
            'verified_at': timezone.now(),
        },
    )


@login_required
@require_http_methods(['GET', 'POST'])
def key_verification_requests_view(request):
    from django.contrib.auth import get_user_model
    User = get_user_model()

    if request.method == 'GET':
        pending = KeyVerificationRequest.objects.filter(
            models.Q(requester=request.user) | models.Q(responder=request.user),
            status=KeyVerificationRequest.Status.PENDING,
        ).select_related('requester', 'responder').order_by('-created_at')
        return JsonResponse({
            'requests': [
                _serialize_key_verification_request(item, viewer=request.user)
                for item in pending
            ],
        })

    payload = _json_body(request)
    user_id = payload.get('user_id')
    try:
        target = User.objects.get(id=user_id, is_active=True)
    except (TypeError, ValueError, User.DoesNotExist):
        return JsonResponse({'error': 'User not found.'}, status=404)

    if not _key_verification_allowed(request.user, target):
        return JsonResponse({'error': 'Only contacts or active private-chat members can verify keys.'}, status=403)

    requester_key = _active_key(request.user.pk)
    responder_key = _active_key(target.pk)
    if requester_key is None or responder_key is None:
        return JsonResponse({'error': 'Both users must have active encryption keys.'}, status=404)

    existing = KeyVerificationRequest.objects.filter(
        requester=request.user,
        responder=target,
        status=KeyVerificationRequest.Status.PENDING,
    ).select_related('requester', 'responder').first()
    if existing:
        return JsonResponse({
            'status': 'ok',
            'request': _serialize_key_verification_request(existing, viewer=request.user),
        })

    verification = KeyVerificationRequest.objects.create(
        requester=request.user,
        responder=target,
        requester_key_fingerprint=requester_key.key_fingerprint,
        requester_key_version=requester_key.key_version,
        responder_key_fingerprint=responder_key.key_fingerprint,
        responder_key_version=responder_key.key_version,
    )
    _push_key_verification_event(verification, 'requested')
    return JsonResponse({
        'status': 'ok',
        'request': _serialize_key_verification_request(verification, viewer=request.user),
    }, status=201)


@login_required
@require_POST
def key_verification_request_respond_view(request, request_id):
    payload = _json_body(request)
    action = payload.get('action')
    if action not in {'accept', 'decline', 'cancel'}:
        return JsonResponse({'error': 'Invalid action.'}, status=400)

    try:
        verification = KeyVerificationRequest.objects.select_related('requester', 'responder').get(
            pk=request_id,
            status=KeyVerificationRequest.Status.PENDING,
        )
    except KeyVerificationRequest.DoesNotExist:
        return JsonResponse({'error': 'Verification request not found.'}, status=404)

    if action == 'cancel':
        if verification.requester_id != request.user.pk:
            return JsonResponse({'error': 'Only the requester can cancel this request.'}, status=403)
        verification.status = KeyVerificationRequest.Status.CANCELLED
        verification.responded_at = timezone.now()
        verification.save(update_fields=['status', 'responded_at', 'updated_at'])
        _push_key_verification_event(verification, 'cancelled')
        return JsonResponse({'status': 'ok', 'request': _serialize_key_verification_request(verification, viewer=request.user)})

    if verification.responder_id != request.user.pk:
        return JsonResponse({'error': 'Only the invited contact can respond.'}, status=403)

    if action == 'decline':
        verification.status = KeyVerificationRequest.Status.DECLINED
        verification.responded_at = timezone.now()
        verification.save(update_fields=['status', 'responded_at', 'updated_at'])
        _push_key_verification_event(verification, 'declined')
        return JsonResponse({'status': 'ok', 'request': _serialize_key_verification_request(verification, viewer=request.user)})

    requester_key = _active_key(verification.requester_id)
    responder_key = _active_key(verification.responder_id)
    keys_match_request = (
        requester_key is not None
        and responder_key is not None
        and requester_key.key_fingerprint == verification.requester_key_fingerprint
        and requester_key.key_version == verification.requester_key_version
        and responder_key.key_fingerprint == verification.responder_key_fingerprint
        and responder_key.key_version == verification.responder_key_version
    )
    if not keys_match_request:
        verification.status = KeyVerificationRequest.Status.EXPIRED
        verification.responded_at = timezone.now()
        verification.save(update_fields=['status', 'responded_at', 'updated_at'])
        _push_key_verification_event(verification, 'expired')
        return JsonResponse({'error': 'Keys changed. Start a new verification request.'}, status=409)

    with transaction.atomic():
        _upsert_verified_key_trust(verification.requester, verification.responder, responder_key)
        _upsert_verified_key_trust(verification.responder, verification.requester, requester_key)
        verification.status = KeyVerificationRequest.Status.ACCEPTED
        verification.responded_at = timezone.now()
        verification.save(update_fields=['status', 'responded_at', 'updated_at'])

    _push_key_verification_event(verification, 'accepted')
    return JsonResponse({'status': 'ok', 'request': _serialize_key_verification_request(verification, viewer=request.user)})


# ── Notification settings API (P2 T23) ────────────────────────────


_GENERAL_DEFAULT_SETTINGS = {
    'theme': 'system',
    'accent_color': 'blue',
    'wallpaper': 'default',
    'message_font_size': '16',
    'time_format': '24h',
    'power_saving': False,
    'reduce_motion': False,
}

_GENERAL_CHOICES = {
    'theme': {'light', 'dark', 'system'},
    'accent_color': {'blue', 'green', 'purple', 'red', 'orange'},
    'wallpaper': {'default', 'blue', 'green', 'pink', 'purple', 'orange', 'cyan', 'yellow'},
    'time_format': {'12h', '24h'},
}

_FOLDER_DEFAULT_SETTINGS = {
    'folders': [],
    'folders_view': 'above',
}


def _merge_dict(defaults, values):
    merged = dict(defaults)
    if isinstance(values, dict):
        merged.update(values)
    return merged


def _sanitize_general_settings(raw_settings):
    current = _merge_dict(_GENERAL_DEFAULT_SETTINGS, raw_settings)
    sanitized = dict(_GENERAL_DEFAULT_SETTINGS)

    for field, choices in _GENERAL_CHOICES.items():
        value = current.get(field)
        if value in choices:
            sanitized[field] = value

    try:
        font_size = int(current.get('message_font_size', 16))
    except (TypeError, ValueError):
        font_size = 16
    sanitized['message_font_size'] = str(max(12, min(24, font_size)))

    for field in ('power_saving', 'reduce_motion'):
        raw = current.get(field)
        if isinstance(raw, bool):
            sanitized[field] = raw
        elif isinstance(raw, int):
            sanitized[field] = bool(raw)
        elif isinstance(raw, str):
            sanitized[field] = raw.lower() in ('true', '1', 'on', 'yes')

    return sanitized


def _sanitize_chat_folder_settings(raw_settings):
    current = _merge_dict(_FOLDER_DEFAULT_SETTINGS, raw_settings)
    folders = current.get('folders') if isinstance(current.get('folders'), list) else []
    sanitized_folders = []

    for index, folder in enumerate(folders[:50]):
        if not isinstance(folder, dict):
            continue
        name = str(folder.get('name', '')).strip()[:80]
        if not name:
            continue
        folder_id = str(folder.get('id') or f'folder-{index + 1}')[:80]
        try:
            chat_count = max(0, int(folder.get('chat_count', 0) or 0))
        except (TypeError, ValueError):
            chat_count = 0
        sanitized_folders.append({
            'id': folder_id,
            'name': name,
            'chat_count': chat_count,
            'created_at': str(folder.get('created_at', ''))[:40],
        })

    folders_view = current.get('folders_view')
    if folders_view is True:
        folders_view = 'above'
    elif folders_view is False:
        folders_view = 'sidebar'
    if folders_view not in ('above', 'sidebar'):
        folders_view = _FOLDER_DEFAULT_SETTINGS['folders_view']

    return {
        'folders': sanitized_folders,
        'folders_view': folders_view,
    }


@login_required
@require_http_methods(['GET', 'POST'])
def general_settings_view(request):
    settings_obj, _ = UserGeneralSettings.objects.get_or_create(user=request.user)

    if request.method == 'GET':
        return JsonResponse({
            'settings': _sanitize_general_settings(settings_obj.settings_json),
        })

    payload = _json_body(request)
    if payload is None:
        return JsonResponse({'error': 'invalid_json'}, status=400)

    current = _sanitize_general_settings(settings_obj.settings_json)
    settings_obj.settings_json = _sanitize_general_settings(_merge_dict(current, payload))
    settings_obj.save(update_fields=['settings_json', 'updated_at'])
    return JsonResponse({'status': 'ok', 'settings': settings_obj.settings_json})


@login_required
@require_http_methods(['GET', 'POST'])
def chat_folder_settings_view(request):
    settings_obj, _ = UserChatFolderSettings.objects.get_or_create(user=request.user)

    if request.method == 'GET':
        return JsonResponse({
            'settings': _sanitize_chat_folder_settings(settings_obj.settings_json),
        })

    payload = _json_body(request)
    if payload is None:
        return JsonResponse({'error': 'invalid_json'}, status=400)

    current = _sanitize_chat_folder_settings(settings_obj.settings_json)
    settings_obj.settings_json = _sanitize_chat_folder_settings(_merge_dict(current, payload))
    settings_obj.save(update_fields=['settings_json', 'updated_at'])
    return JsonResponse({'status': 'ok', 'settings': settings_obj.settings_json})


_NOTIFICATION_FIELDS = [
    'display_notifications',
    'offline_notifications',
    'all_accounts_notifications',
    'notification_sound',
    'volume',
    'message_sent_sound',
    'private_chat_notifications',
    'group_chat_notifications',
    'channel_notifications',
    'message_preview_private',
    'message_preview_group',
    'message_preview_channel',
    'contact_join_notifications',
]


@login_required
@require_GET
def notification_settings_view(request):
    """Return the current user's notification settings."""
    from .models import UserNotificationSettings  # avoid top-level circular
    settings_obj, _ = UserNotificationSettings.objects.get_or_create(
        user=request.user,
    )
    data = {
        'user_id': request.user.id,
        **{f: getattr(settings_obj, f) for f in _NOTIFICATION_FIELDS},
    }
    return JsonResponse(data)


@login_required
@require_http_methods(['PUT'])
def notification_settings_update_view(request):
    """Update the current user's notification settings."""
    from .models import UserNotificationSettings
    settings_obj, _ = UserNotificationSettings.objects.get_or_create(
        user=request.user,
    )
    payload = _json_body(request)
    if payload is None:
        return JsonResponse({'error': 'invalid_json'}, status=400)

    updated = False
    if 'volume' in payload:
        try:
            payload['volume'] = max(0, min(100, int(payload['volume'])))
        except (TypeError, ValueError):
            return JsonResponse({'error': 'invalid_volume'}, status=400)

    for sound_field in ('notification_sound', 'message_sent_sound'):
        if sound_field in payload and payload[sound_field] not in ('default', 'off'):
            return JsonResponse({'error': f'invalid_{sound_field}'}, status=400)

    for field in _NOTIFICATION_FIELDS:
        if field in payload:
            setattr(settings_obj, field, payload[field])
            updated = True
    if updated:
        settings_obj.save(update_fields=[f for f in _NOTIFICATION_FIELDS if f in payload] + ['updated_at'])

    return JsonResponse({
        'user_id': request.user.id,
        **{f: getattr(settings_obj, f) for f in _NOTIFICATION_FIELDS},
    })


# Storage, privacy, and blocked-user APIs have been consolidated into
# chat/views.py (ketter1024's P2 T05/T06/T19-T40 views — see PR #109).


# ── QR Code card API (P2 T30) ─────────────────────────────────────


@login_required
@require_GET
def qr_card_view(request):
    """Return the current user's public card data for QR code sharing."""
    profile, _ = UserProfile.objects.get_or_create(user=request.user)
    privacy, _ = UserPrivacySettings.objects.get_or_create(user=request.user)
    avatar_url = ''
    if profile.avatar and privacy.profile_photo_visibility != 'nobody':
        try:
            timestamp = int(profile.updated_at.timestamp())
            avatar_url = request.build_absolute_uri(f"{profile.avatar.url}?t={timestamp}")
        except Exception:
            avatar_url = request.build_absolute_uri(profile.avatar.url)
            
    return JsonResponse({
        'user_id': request.user.id,
        'username': request.user.username,
        'nickname': profile.nickname or request.user.username,
        'avatar': avatar_url,
        'bio': profile.bio if privacy.bio_visibility != 'nobody' else '',
        'phone_number': profile.phone_number if privacy.phone_number_visibility == 'everyone' else '',
    })


# ── Multi-account context API (P2 T35) ────────────────────────────


@login_required
@require_GET
def multi_account_view(request):
    from .models import MultiAccountContext
    ctx, _ = MultiAccountContext.objects.get_or_create(user=request.user)
    return JsonResponse({'user_id': request.user.id, 'context_json': ctx.context_json})


@login_required
@require_http_methods(['PUT'])
def multi_account_update_view(request):
    from .models import MultiAccountContext
    payload = _json_body(request)
    if payload is None:
        return JsonResponse({'error': 'invalid_json'}, status=400)
    ctx, _ = MultiAccountContext.objects.get_or_create(user=request.user)
    if 'context_json' in payload:
        ctx.context_json = payload['context_json']
        ctx.save(update_fields=['context_json', 'updated_at'])
    return JsonResponse({'user_id': request.user.id, 'context_json': ctx.context_json})


# ── Session management API (P2 T36) ───────────────────────────────


@login_required
@require_GET
def session_list_view(request):
    """List active sessions for the current user.

    Returns opaque session IDs (index-based) rather than real session keys.
    """
    from django.contrib.sessions.models import Session
    sessions = Session.objects.filter(expire_date__gte=timezone.now())
    results = []
    # Build a temporary index → session_key mapping, stored server-side in
    # the request session so terminate_view can resolve the real key later.
    index_map = {}
    idx = 0
    for s in sessions.order_by('-expire_date'):
        try:
            data = s.get_decoded()
        except Exception:
            continue
        if data.get('_auth_user_id') != str(request.user.id):
            continue
        idx += 1
        opaque_id = f'sid_{idx}'
        index_map[opaque_id] = s.session_key
        results.append({
            'session_id': opaque_id,
            'created': s.expire_date.strftime('%Y-%m-%d %H:%M'),
            'is_current': s.session_key == request.session.session_key,
        })
    # Stash the mapping in the current session so terminate_view can resolve
    request.session['_session_index_map'] = index_map
    return JsonResponse({'sessions': results})


@login_required
@require_http_methods(['POST'])
def session_terminate_view(request):
    """Terminate a specific session. Verifies session ownership before deleting."""
    from django.contrib.sessions.models import Session
    payload = _json_body(request) or {}
    session_id = payload.get('session_id', '')

    if not session_id:
        return JsonResponse({'error': 'session_id required'}, status=400)

    # Resolve opaque session_id → real session_key via server-side index map
    index_map = request.session.get('_session_index_map', {})
    real_key = index_map.get(session_id)

    if not real_key:
        return JsonResponse({'error': 'Session not found or not yours.'}, status=404)

    # Don't allow terminating current session via this endpoint
    current_key = request.session.session_key or ''
    if real_key == current_key:
        return JsonResponse({'error': 'Use logout to end current session'}, status=400)

    # Verify ownership: decode the session and check _auth_user_id
    try:
        target_session = Session.objects.get(session_key=real_key)
    except Session.DoesNotExist:
        return JsonResponse({'error': 'Session not found.'}, status=404)

    try:
        data = target_session.get_decoded()
    except Exception:
        # Corrupt session data — delete it anyway
        target_session.delete()
        return JsonResponse({'terminated': True})

    if data.get('_auth_user_id') != str(request.user.id):
        return JsonResponse({'error': 'Session not found or not yours.'}, status=404)

    target_session.delete()
    # Clean up the index map from current session
    index_map.pop(session_id, None)
    request.session['_session_index_map'] = index_map
    return JsonResponse({'terminated': True})


# ── Profile sync events API (P2 T39) ──────────────────────────────


@login_required
@require_GET
def profile_updates_view(request):
    """Return recent profile update events for contacts."""
    from .models import UserProfileUpdateLog
    since = request.GET.get('since')
    contact_ids = set(
        Contact.objects.filter(models.Q(user=request.user) | models.Q(contact=request.user))
        .values_list('user_id', 'contact_id')
    )
    visible_user_ids = {request.user.id}
    for user_id, contact_id in contact_ids:
        visible_user_ids.add(contact_id if user_id == request.user.id else user_id)

    qs = UserProfileUpdateLog.objects.select_related('user').filter(
        user_id__in=visible_user_ids,
    ).order_by('-created_at')
    if since:
        qs = qs.filter(created_at__gt=since)
    qs = qs[:50]
    results = [{'id': e.id, 'user_id': e.user_id, 'username': e.user.username,
                'created_at': e.created_at.isoformat()} for e in qs]
    return JsonResponse({'updates': results})
