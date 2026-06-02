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
from django.views.decorators.http import require_GET, require_POST, require_http_methods

from .forms import ProfileForm, RegistrationForm
from .models import (
    Contact,
    FriendRequest,
    Group,
    GroupMember,
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
        form = AuthenticationForm(request, data=request.POST)
        username = request.POST.get('username', '')
        password = request.POST.get('password', '')

        if form.is_valid():
            user = authenticate(
                request, username=username, password=password,
            )

            if user is not None:
                login(request, user)
                messages.success(
                    request,
                    f'Welcome back, {user.get_short_name() or username}!',
                )
                return redirect('index')

            # credentials were valid format but authenticate returned None
            try:
                target = User.objects.get(username=username)
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
            except User.DoesNotExist:
                messages.error(
                    request,
                    'No account found with that username. '
                    'Please check and try again.',
                )
        else:
            # Distinguish between missing fields and bad credentials
            if username and password:
                try:
                    target = User.objects.get(username=username)
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
                except User.DoesNotExist:
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
    """Search for users (JSON endpoint for the contact modal)."""
    query = request.GET.get('q', '').strip()
    results = []

    if query:
        users = User.objects.filter(
            username__icontains=query,
        ).exclude(
            id=request.user.id,
        )[:20]

        current_user = request.user
        for user in users:
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
                'is_contact': is_contact,
                'has_pending_out': has_pending_out,
                'has_pending_in': has_pending_in,
            })

    return JsonResponse({'results': results})


@login_required(login_url='login')
@require_http_methods(['POST'])
def friend_request_send(request):
    """Send a friend request to another user."""
    username = request.POST.get('username', '').strip()

    if not username:
        messages.error(request, 'Please provide a username.')
        return redirect('contacts')

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

    friend_request.status = FriendRequest.Status.ACCEPTED
    friend_request.save()

    Contact.objects.get_or_create(
        user=friend_request.sender,
        contact=friend_request.receiver,
    )

    messages.success(
        request,
        f'You are now contacts with {friend_request.sender.username}.',
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


# ── Profile views ──────────────────────────────────────────────────


@login_required(login_url='login')
def profile_edit_view(request):
    """Edit nickname, bio, and avatar."""
    profile, _ = UserProfile.objects.get_or_create(user=request.user)

    if request.method == 'POST':
        form = ProfileForm(request.POST, request.FILES, instance=profile)
        if form.is_valid():
            form.save()
            messages.success(request, 'Profile updated.')
            return redirect('index')
        for field, errors in form.errors.items():
            for error in errors:
                messages.error(request, error)
    else:
        form = ProfileForm(instance=profile)

    return render(request, 'pages/profile_edit.html', {
        'form': form,
        'profile': profile,
    })


# ── Group views ────────────────────────────────────────────────────


@login_required(login_url='login')
def group_list_view(request):
    """Show all groups the user is a member of."""
    memberships = GroupMember.objects.filter(
        user=request.user,
    ).select_related('group')
    groups = [m.group for m in memberships]
    return render(request, 'pages/groups.html', {'groups': groups})


@login_required(login_url='login')
def group_create_view(request):
    """Create a new group."""
    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        description = request.POST.get('description', '').strip()

        if not name:
            messages.error(request, 'Group name is required.')
            return redirect('groups')

        group = Group.objects.create(
            name=name,
            description=description,
            creator=request.user,
        )
        GroupMember.objects.create(
            group=group,
            user=request.user,
            role=GroupMember.Role.ADMIN,
        )
        messages.success(request, f'Group "{name}" created.')
        return redirect('group_detail', group_id=group.id)

    return render(request, 'pages/groups.html', {'show_create': True})


@login_required(login_url='login')
def group_detail_view(request, group_id):
    """Show group details and member list."""
    group = get_object_or_404(Group, id=group_id)
    members = group.members.select_related('user')
    is_member = members.filter(user=request.user).exists()

    if not is_member:
        messages.error(request, 'You are not a member of this group.')
        return redirect('groups')

    return render(request, 'pages/group_detail.html', {
        'group': group,
        'members': members,
        'is_admin': members.filter(
            user=request.user, role=GroupMember.Role.ADMIN,
        ).exists(),
    })


@login_required(login_url='login')
@require_http_methods(['POST'])
def group_add_member_view(request, group_id):
    """Add a contact to a group."""
    group = get_object_or_404(Group, id=group_id)
    username = request.POST.get('username', '').strip()
    user_to_add = get_object_or_404(User, username=username)

    is_contact = Contact.objects.filter(
        (models.Q(user=request.user) & models.Q(contact=user_to_add))
        | (models.Q(user=user_to_add) & models.Q(contact=request.user)),
    ).exists()
    if not is_contact:
        messages.error(request, f'{username} is not in your contacts.')
        return redirect('group_detail', group_id=group.id)

    _, created = GroupMember.objects.get_or_create(
        group=group, user=user_to_add,
        defaults={'role': GroupMember.Role.MEMBER},
    )
    if created:
        messages.success(request, f'{username} added to {group.name}.')
    else:
        messages.info(request, f'{username} is already a member.')
    return redirect('group_detail', group_id=group.id)


@login_required(login_url='login')
@require_http_methods(['POST'])
def group_leave_view(request, group_id):
    """Leave a group."""
    group = get_object_or_404(Group, id=group_id)
    membership = get_object_or_404(
        GroupMember, group=group, user=request.user,
    )
    membership.delete()
    messages.info(request, f'You left "{group.name}".')

    if not group.members.exists():
        group.delete()

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
