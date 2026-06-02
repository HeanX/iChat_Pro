import base64
import binascii
import hashlib
import json

from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm
from django.contrib.auth.decorators import login_required
from django.contrib.auth import get_user_model
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from .models import UserPublicKey


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
        form = UserCreationForm(request.POST)
        if form.is_valid():
            user = form.save()
            user.email = request.POST.get('email', '').strip()
            user.save(update_fields=['email'])
            login(request, user)
            messages.success(request, 'Registration successful! Welcome to iChat Pro.')
            return redirect('index')

        for field, errors in form.errors.items():
            for error in errors:
                messages.error(request, f'{field.capitalize()}: {error}')
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
                messages.success(request, f'Welcome back, {username}!')
                return redirect('index')

        messages.error(request, 'Invalid username or password.')
    else:
        form = AuthenticationForm()

    return render(request, 'pages/login.html', {'form': form})


def logout_view(request):
    logout(request)
    messages.info(request, 'You have been logged out.')
    return redirect('login')


@login_required
@require_POST
def upload_public_key_view(request):
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
