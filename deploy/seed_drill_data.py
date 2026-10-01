"""Seed realistic business data for the restore drill (idempotent).

Run on the server as the service user, from the repo root:
    sudo -u ichat -E env PATH=/opt/ichat/.venv/bin:/usr/bin:/bin \
        /opt/ichat/.venv/bin/python manage.py shell < deploy/seed_drill_data.py

Creates: 3 users (p4_drill_a/b/c), contacts, one private conversation with
two text messages (sender_copy included), one group conversation with one
group message (per-recipient copies), and one encrypted file (real merged
ciphertext bytes on disk + per-holder wrapped keys) attached to a file
message. Server-side opaque ciphertext only — no plaintext anywhere.
"""

import base64
import hashlib
import os
import uuid
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

from accounts.models import Contact
from chat.models import (
    Conversation,
    ConversationMember,
    EncryptedFile,
    EncryptedFileChunk,
    EncryptedFileKey,
    EncryptedMessage,
    GroupMessage,
    GroupMessageRecipient,
)

DRILL_USERS = ["p4_drill_a", "p4_drill_b", "p4_drill_c"]
GROUP_NAME = "p4-drill-group"
FILE_CLIENT_ID = "p4drill-file-001"
FILE_NAME = "p4drill.enc"


def b64(n):
    return base64.b64encode(os.urandom(n)).decode()


with transaction.atomic():
    # --- idempotent cleanup of previous drill data ---
    prev_users = get_user_model().objects.filter(username__in=DRILL_USERS)
    prev_conv_ids = list(
        ConversationMember.objects.filter(user__in=prev_users)
        .values_list("conversation_id", flat=True)
    )
    Conversation.objects.filter(id__in=prev_conv_ids).delete()
    prev_users.delete()
    legacy_path = os.path.join(settings.MEDIA_ROOT, "uploads", "files", FILE_NAME)
    if os.path.exists(legacy_path):
        os.remove(legacy_path)

    # --- users & contacts ---
    pw = b64(18)
    users = {
        name: get_user_model().objects.create_user(
            name, email=f"{name}@drill.example.invalid", password=pw
        )
        for name in DRILL_USERS
    }
    a, b, c = users["p4_drill_a"], users["p4_drill_b"], users["p4_drill_c"]
    Contact.objects.create(user=a, contact=b)
    Contact.objects.create(user=b, contact=c)

    # --- private conversation: a <-> b ---
    private = Conversation.objects.create(type=Conversation.Type.SINGLE, created_by=a)
    ConversationMember.objects.create(conversation=private, user=a, role=ConversationMember.Role.MEMBER)
    ConversationMember.objects.create(
        conversation=private, user=b, role=ConversationMember.Role.MEMBER, unread_count=2
    )
    m1 = EncryptedMessage.objects.create(
        conversation=private, sender=a, receiver=b,
        message_type=EncryptedMessage.MessageType.TEXT,
        ciphertext=b64(64), nonce=b64(12), auth_tag=b64(16),
        sender_copy_ciphertext=b64(64), sender_copy_nonce=b64(12), sender_copy_auth_tag=b64(16),
        algorithm="AES-256-GCM", sender_key_version=1, receiver_key_version=1,
        client_message_id="p4drill-p001", status=EncryptedMessage.Status.READ,
    )
    EncryptedMessage.objects.create(
        conversation=private, sender=b, receiver=a,
        message_type=EncryptedMessage.MessageType.TEXT,
        ciphertext=b64(48), nonce=b64(12), auth_tag=b64(16),
        algorithm="AES-256-GCM", sender_key_version=1, receiver_key_version=1,
        client_message_id="p4drill-p002",
    )

    # --- encrypted file: real merged ciphertext on disk + wrapped keys ---
    file_bytes = os.urandom(4096)
    rel_path = os.path.join("uploads", "files", FILE_NAME)
    abs_path = os.path.join(settings.MEDIA_ROOT, rel_path)
    os.makedirs(os.path.dirname(abs_path), exist_ok=True)
    with open(abs_path, "wb") as fh:
        fh.write(file_bytes)
    enc_file = EncryptedFile.objects.create(
        upload_id=str(uuid.uuid4()), client_file_id=FILE_CLIENT_ID,
        owner=a, conversation=private,
        message_kind=EncryptedFile.MessageKind.FILE,
        status=EncryptedFile.Status.AVAILABLE,
        storage_path=rel_path,
        total_size_bytes=len(file_bytes), chunk_count=1,
        ciphertext_sha256=hashlib.sha256(file_bytes).hexdigest(),
        encrypted_metadata=b64(96), metadata_nonce=b64(12), metadata_auth_tag=b64(16),
    )
    EncryptedFileChunk.objects.create(
        file=enc_file, chunk_index=0, size_bytes=len(file_bytes), offset_bytes=0,
        nonce=b64(12), auth_tag=b64(16),
    )
    for holder in (a, b):
        EncryptedFileKey.objects.create(
            file=enc_file, holder=holder, sender=a,
            encrypted_file_key=b64(48), nonce=b64(12), auth_tag=b64(16),
            algorithm="AES-256-GCM", sender_key_version=1, receiver_key_version=1,
        )
    file_message = EncryptedMessage.objects.create(
        conversation=private, sender=a, receiver=b,
        message_type=EncryptedMessage.MessageType.FILE,
        ciphertext=b64(64), nonce=b64(12), auth_tag=b64(16),
        algorithm="AES-256-GCM", sender_key_version=1, receiver_key_version=1,
        client_message_id="p4drill-p003", file_id=enc_file,
    )

    # --- group conversation: a(owner) + b + c ---
    group = Conversation.objects.create(
        type=Conversation.Type.GROUP, name=GROUP_NAME, created_by=a,
        membership_version=1,
    )
    ConversationMember.objects.create(conversation=group, user=a, role=ConversationMember.Role.OWNER)
    ConversationMember.objects.create(conversation=group, user=b, role=ConversationMember.Role.MEMBER, unread_count=1)
    ConversationMember.objects.create(conversation=group, user=c, role=ConversationMember.Role.MEMBER, unread_count=1)
    gmsg = GroupMessage.objects.create(
        conversation=group, sender=a,
        message_type=GroupMessage.MessageType.TEXT,
        client_message_id="p4drill-g001",
        sender_copy_ciphertext=b64(32), sender_copy_nonce=b64(12), sender_copy_auth_tag=b64(16),
    )
    for receiver in (a, b, c):
        GroupMessageRecipient.objects.create(
            group_message=gmsg, receiver=receiver,
            ciphertext=b64(32), nonce=b64(12), auth_tag=b64(16),
            algorithm="AES-256-GCM", sender_key_version=1, receiver_key_version=1,
            membership_version=1,
        )

    now = timezone.now()
    for conv in (private, group):
        conv.last_message_at = now - timedelta(minutes=1)
        conv.save(update_fields=["last_message_at", "updated_at"])

    print(
        "SEEDED users=3 contacts=2 private_conv=1 private_msgs=3 "
        "group_conv=1 group_msgs=1 recipients=3 files=1 file_keys=2 "
        f"file_bytes={len(file_bytes)} sha256={hashlib.sha256(file_bytes).hexdigest()[:16]}"
    )
