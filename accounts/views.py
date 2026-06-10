import base64
import binascii
import hashlib
import json

from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.models import User
from django.db import models, transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST, require_http_methods

from chat.models import Conversation, ConversationMember as ChatMember

from .forms import ProfileForm, RegistrationForm
from .models import (
    Contact,
    FriendRequest,
    KeyTrust,
    UserProfile,
    UserPublicKey,
)


MAX_PUBLIC_KEY_BYTES = 512


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
                return redirect('index')

            # credentials were valid format but authenticate returned None
            if target is not None:
                if not target.is_active:
                    messages.error(
                        request,
                        'This account has been disabled. '
                        'Please contact an administrator.',
                    )
                else:
                    messages.error(
                        request,
                        'Invalid password. Please try again.',
                    )
            else:
                messages.error(
                    request,
                    'No account found with that username. '
                    'Please check and try again.',
                )
        else:
            # Distinguish between missing fields and bad credentials
            if username and password:
                if target is not None:
                    if not target.is_active:
                        messages.error(
                            request,
                            'This account has been disabled. '
                            'Please contact an administrator.',
                        )
                    else:
                        messages.error(
                            request,
                            'Invalid password. Please try again.',
                        )
                else:
                    messages.error(
                        request,
                        'No account found with that username. '
                        'Please check and try again.',
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
    messages.info(request, 'You have been logged out.')
    return redirect('login')


# ── Contact & Friend-request views ──────────────────────────────────


@login_required(login_url='login')
def contact_list_view(request):
    """Show the user's contacts and pending incoming friend requests."""

    contacts = Contact.objects.filter(
        models.Q(user=request.user) | models.Q(contact=request.user),
    ).select_related('user', 'contact')

    incoming = FriendRequest.objects.filter(
        receiver=request.user,
        status=FriendRequest.Status.PENDING,
    ).select_related('sender')

    outgoing = FriendRequest.objects.filter(
        sender=request.user,
        status=FriendRequest.Status.PENDING,
    ).select_related('receiver')

    context = {
        'contacts': contacts,
        'incoming_requests': incoming,
        'outgoing_requests': outgoing,
    }
    return render(request, 'pages/contacts.html', context)


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

            results.append({
                'id': user.id,
                'username': user.username,
                'nickname': nickname,
                'is_contact': is_contact,
                'has_pending_out': has_pending_out,
                'has_pending_in': has_pending_in,
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


@login_required(login_url='login')
def group_list_view(request):
    """Show all groups the user is a member of."""
    memberships = ChatMember.objects.filter(
        user=request.user,
        conversation__type=Conversation.Type.GROUP,
        status=ChatMember.Status.ACTIVE,
    ).select_related('conversation')
    conversations = [m.conversation for m in memberships]
    return render(request, 'pages/groups.html', {'groups': conversations})


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

    return render(request, 'pages/groups.html', {'show_create': True})


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

    return render(request, 'pages/group_detail.html', {
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


# ── Notification settings API (P2 T23) ────────────────────────────


_NOTIFICATION_FIELDS = [
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


# ── Storage settings API (P2 T24) ─────────────────────────────────


@login_required
@require_GET
def storage_settings_view(request):
    """Return the current user's storage & auto-download settings."""
    from .models import UserStorageSettings
    settings_obj, _ = UserStorageSettings.objects.get_or_create(
        user=request.user,
    )
    return JsonResponse({
        'user_id': request.user.id,
        'settings_json': settings_obj.settings_json,
    })


@login_required
@require_http_methods(['PUT'])
def storage_settings_update_view(request):
    """Update the current user's storage settings (JSON blob)."""
    from .models import UserStorageSettings
    payload = _json_body(request)
    if payload is None:
        return JsonResponse({'error': 'invalid_json'}, status=400)

    settings_obj, _ = UserStorageSettings.objects.get_or_create(
        user=request.user,
    )
    if 'settings_json' in payload:
        settings_obj.settings_json = payload['settings_json']
        settings_obj.save(update_fields=['settings_json', 'updated_at'])

    return JsonResponse({
        'user_id': request.user.id,
        'settings_json': settings_obj.settings_json,
    })


# ── Privacy settings API (P2 T25) ─────────────────────────────────

_PRIVACY_FIELDS = [
    'last_seen_visibility',
    'profile_photo_visibility',
    'phone_number_visibility',
    'bio_visibility',
    'forward_link_visibility',
    'who_can_send_messages',
    'who_can_voice_video_call',
    'auto_delete_messages_days',
    'sensitive_content_filter',
    'passcode_lock_enabled',
    'two_step_verification_enabled',
    'login_email',
]


@login_required
@require_GET
def privacy_settings_view(request):
    from .models import UserPrivacySettings
    settings_obj, _ = UserPrivacySettings.objects.get_or_create(
        user=request.user,
    )
    return JsonResponse({
        'user_id': request.user.id,
        **{f: getattr(settings_obj, f) for f in _PRIVACY_FIELDS},
    })


@login_required
@require_http_methods(['PUT'])
def privacy_settings_update_view(request):
    from .models import UserPrivacySettings
    settings_obj, _ = UserPrivacySettings.objects.get_or_create(
        user=request.user,
    )
    payload = _json_body(request)
    if payload is None:
        return JsonResponse({'error': 'invalid_json'}, status=400)

    updated = False
    for field in _PRIVACY_FIELDS:
        if field in payload:
            setattr(settings_obj, field, payload[field])
            updated = True
    if updated:
        settings_obj.save(update_fields=[f for f in _PRIVACY_FIELDS if f in payload] + ['updated_at'])

    return JsonResponse({
        'user_id': request.user.id,
        **{f: getattr(settings_obj, f) for f in _PRIVACY_FIELDS},
    })


# ── Blocked users API (P2 T26) ────────────────────────────────────


@login_required
@require_GET
def blocked_users_list_view(request):
    """List users blocked by the current user."""
    from .models import BlockedUser
    blocked_qs = BlockedUser.objects.filter(
        blocker=request.user,
    ).select_related('blocked').order_by('-created_at')
    results = [
        {'user_id': b.blocked.id, 'username': b.blocked.username,
         'blocked_at': b.created_at.isoformat()}
        for b in blocked_qs
    ]
    return JsonResponse({'blocked_users': results})


@login_required
@require_http_methods(['POST'])
def block_user_view(request):
    """Block a user."""
    from .models import BlockedUser
    payload = _json_body(request)
    if not payload or not payload.get('user_id'):
        return JsonResponse({'error': 'user_id is required.'}, status=400)
    target = get_object_or_404(User, id=payload['user_id'])
    if target == request.user:
        return JsonResponse({'error': 'Cannot block yourself.'}, status=400)
    _, created = BlockedUser.objects.get_or_create(
        blocker=request.user, blocked=target)
    return JsonResponse({
        'blocked': True, 'user_id': target.id, 'created': created,
    }, status=201 if created else 200)


@login_required
@require_http_methods(['POST'])
def unblock_user_view(request):
    """Unblock a user."""
    from .models import BlockedUser
    payload = _json_body(request)
    if not payload or not payload.get('user_id'):
        return JsonResponse({'error': 'user_id is required.'}, status=400)
    deleted, _ = BlockedUser.objects.filter(
        blocker=request.user, blocked_id=payload['user_id']).delete()
    return JsonResponse({'unblocked': deleted > 0})
