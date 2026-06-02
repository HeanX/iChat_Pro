from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.models import User
from django.db import models
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods

from .forms import RegistrationForm
from .models import Contact, FriendRequest, UserPublicKey


def register_view(request):
    if request.user.is_authenticated:
        return redirect('index')

    if request.method == 'POST':
        form = RegistrationForm(request.POST)
        if form.is_valid():
            user = form.save()
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

    # Accepted contacts — any row where the current user is either party
    contacts = Contact.objects.filter(
        models.Q(user=request.user) | models.Q(contact=request.user),
    ).select_related('user', 'contact')

    # Incoming pending requests
    incoming = FriendRequest.objects.filter(
        receiver=request.user,
        status=FriendRequest.Status.PENDING,
    ).select_related('sender')

    # Outgoing pending requests (so we can show "Requested" status)
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
            # Check relationship status
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

    # Check if already contacts
    already_contacts = Contact.objects.filter(
        (models.Q(user=request.user) & models.Q(contact=receiver))
        | (models.Q(user=receiver) & models.Q(contact=request.user)),
    ).exists()

    if already_contacts:
        messages.info(request, f'{username} is already in your contacts.')
        return redirect('contacts')

    # Check for existing pending request in either direction
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

    # Create bidirectional contact record
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

    # Ensure the requesting user is one of the two parties
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


# ── Public‑key management API ──────────────────────────────────────


@login_required(login_url='login')
@require_http_methods(['POST'])
def upload_public_key(request):
    """Store (or replace) the authenticated userʼs ECDH public key."""
    public_key = request.POST.get('public_key', '').strip()
    fingerprint = request.POST.get('fingerprint', '').strip()

    if not public_key or not fingerprint:
        return JsonResponse(
            {'ok': False, 'error': 'public_key and fingerprint are required.'},
            status=400,
        )

    if len(fingerprint) != 64:
        return JsonResponse(
            {'ok': False, 'error': 'fingerprint must be a 64-char hex string.'},
            status=400,
        )

    UserPublicKey.objects.update_or_create(
        user=request.user,
        defaults={
            'public_key': public_key,
            'fingerprint': fingerprint,
            'algorithm': 'ECDH-P256',
        },
    )

    return JsonResponse({'ok': True})


def get_public_key(request, username):
    """Return the public key for the given username (JSON)."""
    user = get_object_or_404(User, username=username)
    try:
        pk_entry = user.public_key
        return JsonResponse({
            'ok': True,
            'username': user.username,
            'public_key': pk_entry.public_key,
            'fingerprint': pk_entry.fingerprint,
            'algorithm': pk_entry.algorithm,
        })
    except UserPublicKey.DoesNotExist:
        return JsonResponse(
            {'ok': False, 'error': 'No public key found for this user.'},
            status=404,
        )