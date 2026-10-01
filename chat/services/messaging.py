"""Unified messaging service (P4 T32/T36).

Single transactional implementation for private and group message sends,
used by BOTH the WebSocket consumer and the HTTP fallback views so the two
entry points can never drift apart (R-03/R-04).

Key guarantees:

- Transaction order (fixes R-01/R-02): structural validation -> conversation
  lock -> identity/permission -> *idempotency replay* -> new-message checks
  (mute / membership version / recipients / file) -> insert -> counters.
- Idempotency (fixes R-01/R-02/R-04): ``(sender, client_message_id)`` replays
  return the original message without re-bumping unread/last_message or
  re-broadcasting. A replayed request whose content differs from the stored
  one raises ``idempotency_conflict`` (409) — never another conversation's
  record. Digests are recomputed from stored columns, so no schema change is
  needed and concurrent same-ID inserts are covered via IntegrityError.
- Push happens in the caller AFTER the (committed) service call returns;
  a failed push never un-saves a message (clients recover via replay/sync).
"""

import base64
import binascii
import hashlib
import json
from dataclasses import dataclass

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.db.models import F
from django.utils import timezone

from ..errors import PayloadError
from ..models import (
    Conversation,
    ConversationMember,
    EncryptedFile,
    EncryptedFileKey,
    EncryptedMessage,
    GroupMessage,
    GroupMessageRecipient,
)


@dataclass(frozen=True)
class SendResult:
    """Outcome of a send: the persisted message and whether THIS call created it."""

    created: bool
    message: object


# ── payload validation (moved verbatim from the consumer) ─────────────────

private_message_algorithm = "AES-256-GCM"
group_message_algorithm = "AES-256-GCM"
max_group_active_members = 50


def validate_private_message(data):
    if not isinstance(data, dict):
        raise PayloadError("invalid_payload", "消息数据格式错误")
    if data.get("algorithm") != private_message_algorithm:
        raise PayloadError("unsupported_algorithm", "不支持的私聊加密算法")

    message_type = data.get("message_type", EncryptedMessage.MessageType.TEXT)
    if message_type not in EncryptedMessage.MessageType.values:
        raise PayloadError("invalid_payload", "Invalid message type.")

    require_base64(data, "ciphertext", max_decoded_length=65536)
    require_base64(data, "nonce", decoded_length=12)
    require_base64(data, "auth_tag", decoded_length=16)
    if data.get("sender_ephemeral_public_key") is not None:
        require_base64(data, "sender_ephemeral_public_key", max_decoded_length=256)
    sender_copy = data.get("sender_copy")
    if sender_copy is not None:
        if not isinstance(sender_copy, dict):
            raise PayloadError("invalid_payload", "sender_copy must be an object.")
        require_base64(sender_copy, "ciphertext", max_decoded_length=65536)
        require_base64(sender_copy, "nonce", decoded_length=12)
        require_base64(sender_copy, "auth_tag", decoded_length=16)
        require_base64(sender_copy, "sender_ephemeral_public_key", max_decoded_length=256)
    client_message_id = data.get("client_message_id")
    if not isinstance(client_message_id, str) or not client_message_id or len(client_message_id) > 64:
        raise PayloadError("invalid_payload", "client_message_id is missing or invalid.")

    reply_to = data.get("reply_to_message_id")
    if reply_to is not None and not isinstance(reply_to, int):
        raise PayloadError("invalid_payload", "reply_to_message_id must be an integer.")

    result = {
        "conversation_id": require_positive_integer(data, "conversation_id"),
        "receiver_id": require_positive_integer(data, "receiver_id"),
        "sender_key_version": require_positive_integer(data, "sender_key_version"),
        "receiver_key_version": require_positive_integer(data, "receiver_key_version"),
        "message_type": message_type,
        "ciphertext": data["ciphertext"],
        "nonce": data["nonce"],
        "auth_tag": data["auth_tag"],
        "sender_ephemeral_public_key": data.get("sender_ephemeral_public_key"),
        "sender_copy": sender_copy,
        "algorithm": data["algorithm"],
        "client_message_id": client_message_id,
    }
    if reply_to is not None:
        result["reply_to_message_id"] = reply_to
    file_id = data.get("file_id")
    if file_id is not None:
        result["file_id"] = require_positive_integer({"file_id": file_id}, "file_id")
    return result


def validate_group_message(data):
    if not isinstance(data, dict):
        raise PayloadError("invalid_payload", "消息数据格式错误")
    if data.get("algorithm") != group_message_algorithm:
        raise PayloadError("unsupported_algorithm", "Unsupported group message algorithm.")

    message_type = data.get("message_type", GroupMessage.MessageType.TEXT)
    if message_type not in GroupMessage.MessageType.values:
        raise PayloadError("invalid_payload", "Invalid message type.")

    recipients = data.get("recipients")
    if not isinstance(recipients, list) or not recipients:
        raise PayloadError("invalid_payload", "recipients must be a non-empty array.")
    seen_receivers = set()
    for r in recipients:
        if not isinstance(r, dict):
            raise PayloadError("invalid_payload", "recipient entries must be objects.")
        receiver_id = require_positive_integer(r, "receiver_id")
        if receiver_id in seen_receivers:
            raise PayloadError("invalid_payload", f"receiver_id {receiver_id} is duplicated.")
        seen_receivers.add(receiver_id)
        require_base64(r, "ciphertext", max_decoded_length=65536)
        require_base64(r, "nonce", decoded_length=12)
        require_base64(r, "auth_tag", decoded_length=16)
        require_positive_integer(r, "receiver_key_version")
        if r.get("sender_ephemeral_public_key") is not None:
            require_base64(r, "sender_ephemeral_public_key", max_decoded_length=256)

    client_message_id = data.get("client_message_id")
    if not isinstance(client_message_id, str) or not client_message_id or len(client_message_id) > 64:
        raise PayloadError("invalid_payload", "client_message_id is missing or invalid.")

    reply_to = data.get("reply_to_message_id")
    if reply_to is not None and not isinstance(reply_to, int):
        raise PayloadError("invalid_payload", "reply_to_message_id must be an integer.")

    sender_copy = data.get("sender_copy")
    if sender_copy is not None:
        if not isinstance(sender_copy, dict):
            raise PayloadError("invalid_payload", "sender_copy must be an object.")
        require_base64(sender_copy, "ciphertext", max_decoded_length=65536)
        require_base64(sender_copy, "nonce", decoded_length=12)
        require_base64(sender_copy, "auth_tag", decoded_length=16)
        require_base64(sender_copy, "sender_ephemeral_public_key", max_decoded_length=256)

    result = {
        "group_id": require_positive_integer(data, "group_id"),
        "membership_version": require_positive_integer(data, "membership_version"),
        "sender_key_version": require_positive_integer(data, "sender_key_version"),
        "message_type": message_type,
        "algorithm": data["algorithm"],
        "client_message_id": client_message_id,
        "recipients": [
            {
                "receiver_id": r["receiver_id"],
                "receiver_key_version": r["receiver_key_version"],
                "ciphertext": r["ciphertext"],
                "nonce": r["nonce"],
                "auth_tag": r["auth_tag"],
                "sender_ephemeral_public_key": r.get("sender_ephemeral_public_key"),
            }
            for r in recipients
        ],
    }
    if sender_copy is not None:
        result["sender_copy"] = sender_copy
    if reply_to is not None:
        result["reply_to_message_id"] = reply_to
    file_id = data.get("file_id")
    if file_id is not None:
        result["file_id"] = require_positive_integer({"file_id": file_id}, "file_id")
    return result


def require_positive_integer(data, field):
    if not isinstance(data, dict):
        raise PayloadError("invalid_payload", "Message payload must be an object.")
    value = data.get(field)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise PayloadError("invalid_payload", f"{field} must be a positive integer.")
    return value


def require_base64(data, field, *, decoded_length=None, max_decoded_length=None):
    value = data.get(field)
    if not isinstance(value, str) or not value:
        raise PayloadError("invalid_payload", f"{field} must be Base64 text.")
    try:
        decoded = base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error) as error:
        raise PayloadError("invalid_payload", f"{field} must be valid Base64 text.") from error
    if decoded_length is not None and len(decoded) != decoded_length:
        raise PayloadError("invalid_payload", f"{field} has invalid length.")
    if max_decoded_length is not None and len(decoded) > max_decoded_length:
        raise PayloadError("invalid_payload", f"{field} exceeds maximum length.")


def validate_attached_file(*, file_id, sender_id, conversation, message_type,
                           required_holder_ids, enforce_conversation=True,
                           pending_keys=None):
    try:
        attached_file = EncryptedFile.objects.get(pk=file_id)
    except EncryptedFile.DoesNotExist as error:
        raise PayloadError("file_not_found", "Attached file not found.") from error

    # The sender must be the file owner OR a key holder (forwarding lets a
    # recipient re-share a file they can decrypt into their own conversations).
    holds_key = EncryptedFileKey.objects.filter(file=attached_file, holder_id=sender_id).exists()
    if attached_file.owner_id != sender_id and not holds_key:
        raise PayloadError("file_forbidden", "Attached file is not owned by the sender.")
    if attached_file.status != EncryptedFile.Status.AVAILABLE:
        raise PayloadError("file_unavailable", "Attached file is not available.")
    # Direct sends require the file to live in the message's conversation;
    # forwards intentionally carry a file from another conversation (the
    # re-wrapped per-member keys below remain mandatory either way).
    if enforce_conversation and attached_file.conversation_id != conversation.pk:
        raise PayloadError("file_forbidden", "Attached file does not belong to this conversation.")
    if attached_file.message_kind != message_type:
        raise PayloadError("file_type_mismatch", "Attached file type does not match message type.")

    holder_ids = set(
        EncryptedFileKey.objects.filter(file=attached_file).values_list("holder_id", flat=True)
    )
    pending = pending_keys or []
    # Keys supplied with THIS request count towards coverage; they are only
    # persisted after the message is actually created.
    holder_ids |= {k["holder_id"] for k in pending}
    if set(required_holder_ids) - holder_ids:
        raise PayloadError("file_forbidden", "Attached file keys do not cover all active members.")
    if {k["holder_id"] for k in pending} - set(required_holder_ids):
        raise PayloadError("file_forbidden", "File keys may only target active members of the conversation.")


# ── idempotency digests ───────────────────────────────────────────────────
#
# The digest covers every field that defines the LOGICAL request (everything
# except the transport-level request_id and the client_message_id itself,
# which is the idempotency key). It is recomputed from stored columns on
# replay, so a retried request that differs in ANY content field conflicts.


def _canonical_digest(payload):
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _normalized_sender_copy(sender_copy):
    """Keep only the persisted fields so extra client keys (e.g. an echoed
    ``algorithm``) cannot change the request digest (P2 review fix)."""
    if not sender_copy:
        return None
    return {
        "ciphertext": sender_copy.get("ciphertext"),
        "nonce": sender_copy.get("nonce"),
        "auth_tag": sender_copy.get("auth_tag"),
        "sender_ephemeral_public_key": sender_copy.get("sender_ephemeral_public_key"),
    }


def _private_digest_payload(data):
    return {
        "kind": "private",
        "conversation_id": data.get("conversation_id"),
        "receiver_id": data.get("receiver_id"),
        "message_type": data.get("message_type"),
        "algorithm": data.get("algorithm"),
        "sender_key_version": data.get("sender_key_version"),
        "receiver_key_version": data.get("receiver_key_version"),
        "ciphertext": data.get("ciphertext"),
        "nonce": data.get("nonce"),
        "auth_tag": data.get("auth_tag"),
        "sender_ephemeral_public_key": data.get("sender_ephemeral_public_key"),
        "sender_copy": _normalized_sender_copy(data.get("sender_copy")),
        "reply_to_message_id": data.get("reply_to_message_id"),
        "file_id": data.get("file_id"),
    }


def _private_stored_payload(message):
    sender_copy = None
    if message.sender_copy_ciphertext and message.sender_copy_nonce and message.sender_copy_auth_tag:
        sender_copy = {
            "ciphertext": message.sender_copy_ciphertext,
            "nonce": message.sender_copy_nonce,
            "auth_tag": message.sender_copy_auth_tag,
            "sender_ephemeral_public_key": message.sender_copy_ephemeral_public_key,
        }
    return {
        "kind": "private",
        "conversation_id": message.conversation_id,
        "receiver_id": message.receiver_id,
        "message_type": message.message_type,
        "algorithm": message.algorithm,
        "sender_key_version": message.sender_key_version,
        "receiver_key_version": message.receiver_key_version,
        "ciphertext": message.ciphertext,
        "nonce": message.nonce,
        "auth_tag": message.auth_tag,
        "sender_ephemeral_public_key": message.sender_ephemeral_public_key,
        "sender_copy": sender_copy,
        "reply_to_message_id": message.reply_to_message_id,
        "file_id": message.file_id_id,
    }


def _group_digest_payload(data):
    return {
        "kind": "group",
        "group_id": data.get("group_id"),
        "membership_version": data.get("membership_version"),
        "sender_key_version": data.get("sender_key_version"),
        "message_type": data.get("message_type"),
        "algorithm": data.get("algorithm"),
        "reply_to_message_id": data.get("reply_to_message_id"),
        "file_id": data.get("file_id"),
        "sender_copy": _normalized_sender_copy(data.get("sender_copy")),
        "recipients": sorted(data.get("recipients") or [], key=lambda r: r["receiver_id"]),
    }


def _group_stored_payload(group_message):
    recipients = sorted(group_message.recipients.all(), key=lambda r: r.receiver_id)
    first = recipients[0] if recipients else None
    return {
        "kind": "group",
        "group_id": group_message.conversation_id,
        "membership_version": first.membership_version if first else None,
        "sender_key_version": first.sender_key_version if first else None,
        "message_type": group_message.message_type,
        "algorithm": first.algorithm if first else None,
        "reply_to_message_id": group_message.reply_to_message_id,
        "file_id": group_message.file_id_id,
        "sender_copy": _normalized_sender_copy(
            {
                "ciphertext": group_message.sender_copy_ciphertext,
                "nonce": group_message.sender_copy_nonce,
                "auth_tag": group_message.sender_copy_auth_tag,
                "sender_ephemeral_public_key": group_message.sender_copy_ephemeral_public_key,
            }
        ) if group_message.sender_copy_ciphertext else None,
        "recipients": [
            {
                "receiver_id": r.receiver_id,
                "receiver_key_version": r.receiver_key_version,
                "ciphertext": r.ciphertext,
                "nonce": r.nonce,
                "auth_tag": r.auth_tag,
                "sender_ephemeral_public_key": r.sender_ephemeral_public_key,
            }
            for r in recipients
        ],
    }


def _positive_int_or_none(value, field):
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise PayloadError("invalid_file_metadata", f"{field} must be a positive integer.")
    return value


def normalize_pending_file_keys(raw):
    """Validate client-supplied wrapped keys strictly.

    Missing or malformed key material is rejected (review round 3: the
    lenient rewrite used to accept holder-only entries and then OVERWRITE
    existing wrapped keys via update_or_create). Member targeting and
    coverage are enforced on the CREATED path only, so an ACK-lost retry
    after a membership change can still replay.
    """
    if not raw:
        return []
    if not isinstance(raw, list):
        raise PayloadError("invalid_file_metadata", "file_keys must be a list.")
    out, seen = [], set()
    for fk in raw:
        if not isinstance(fk, dict):
            raise PayloadError("invalid_file_metadata", "Each file_key must be an object.")
        holder_id = fk.get("holder_id")
        if isinstance(holder_id, bool) or not isinstance(holder_id, int) or holder_id <= 0:
            raise PayloadError("invalid_file_metadata", "Each file_key must have a valid holder_id.")
        if holder_id in seen:
            continue
        seen.add(holder_id)

        encrypted_file_key = fk.get("encrypted_file_key")
        if not isinstance(encrypted_file_key, str) or not encrypted_file_key:
            raise PayloadError("invalid_file_metadata", "encrypted_file_key is required.")
        require_base64(fk, "nonce", decoded_length=12)
        require_base64(fk, "auth_tag", decoded_length=16)
        algorithm = fk.get("algorithm", "AES-256-GCM")
        if algorithm != "AES-256-GCM":
            raise PayloadError("unsupported_algorithm", "Unsupported file key algorithm.")
        sender_ephemeral_public_key = fk.get("sender_ephemeral_public_key")
        if sender_ephemeral_public_key is not None:
            require_base64(fk, "sender_ephemeral_public_key", max_decoded_length=256)

        out.append({
            "holder_id": holder_id,
            "encrypted_file_key": encrypted_file_key,
            "nonce": fk["nonce"],
            "auth_tag": fk["auth_tag"],
            "algorithm": algorithm,
            "sender_key_version": _positive_int_or_none(fk.get("sender_key_version"), "sender_key_version"),
            "receiver_key_version": _positive_int_or_none(fk.get("receiver_key_version"), "receiver_key_version"),
            "membership_version": _positive_int_or_none(fk.get("membership_version"), "membership_version"),
            "sender_ephemeral_public_key": sender_ephemeral_public_key,
        })
    return out


def _write_pending_file_keys(file, pending, sender_id):
    for fk in pending:
        EncryptedFileKey.objects.update_or_create(
            file=file,
            holder_id=fk["holder_id"],
            defaults={
                "sender_id": sender_id,
                "encrypted_file_key": fk["encrypted_file_key"],
                "nonce": fk["nonce"],
                "auth_tag": fk["auth_tag"],
                "algorithm": fk["algorithm"],
                "sender_key_version": fk["sender_key_version"],
                "receiver_key_version": fk["receiver_key_version"],
                "membership_version": fk["membership_version"],
                "sender_ephemeral_public_key": fk["sender_ephemeral_public_key"],
            },
        )


def _replay_or_conflict(existing, incoming_digest, stored_payload):
    """Return a replay result, or raise 409 when the request content differs."""
    if _canonical_digest(stored_payload) != incoming_digest:
        raise PayloadError(
            "idempotency_conflict",
            "client_message_id 已被不同内容使用",
        )
    return SendResult(created=False, message=existing)


# ── private send ──────────────────────────────────────────────────────────


def send_private_message(sender_id, data, *, enforce_file_conversation=True,
                         pending_file_keys=None):
    """Persist one private encrypted message. Returns SendResult."""
    data = validate_private_message(data)
    client_message_id = data["client_message_id"]
    pending_keys = normalize_pending_file_keys(pending_file_keys)
    incoming_digest = _canonical_digest(_private_digest_payload(data))

    with transaction.atomic():
        # 1. conversation lock (identity of the target conversation)
        try:
            conversation = Conversation.objects.select_for_update().get(
                pk=data["conversation_id"],
                type=Conversation.Type.SINGLE,
                status=Conversation.Status.ACTIVE,
            )
        except Conversation.DoesNotExist as error:
            raise PayloadError("conversation_not_found", "Private conversation not found or unavailable.") from error

        # 2. identity / permission (R-01: BEFORE any idempotency hit)
        active_members = ConversationMember.objects.filter(
            conversation=conversation,
            status=ConversationMember.Status.ACTIVE,
        )
        if (
            sender_id == data["receiver_id"]
            or active_members.count() != 2
            or not active_members.filter(user_id=sender_id).exists()
            or not active_members.filter(user_id=data["receiver_id"]).exists()
        ):
            raise PayloadError("conversation_forbidden", "Cannot send in this private conversation.")

        from accounts.models import BlockedUser

        blocked = (
            BlockedUser.objects.filter(blocker=data["receiver_id"], blocked=sender_id).exists()
            or BlockedUser.objects.filter(blocker=sender_id, blocked=data["receiver_id"]).exists()
        )
        if blocked:
            raise PayloadError("conversation_forbidden", "Blocked users cannot send messages.")

        # 2b. the receiver account must still exist and be active (R-06 fix
        # parity: HTTP used to check this, WS did not — single source now)
        if not get_user_model().objects.filter(pk=data["receiver_id"], is_active=True).exists():
            raise PayloadError("receiver_not_found", "Receiver not found or inactive.")

        # 3. idempotency replay with content digest (R-01/R-04)
        if client_message_id:
            existing = (
                EncryptedMessage.objects.filter(
                    sender_id=sender_id, client_message_id=client_message_id
                ).first()
            )
            if existing is not None:
                return _replay_or_conflict(
                    existing, incoming_digest, _private_stored_payload(existing)
                )

        # 4. new-message-only checks and insert
        if data.get("file_id"):
            validate_attached_file(
                file_id=data["file_id"],
                sender_id=sender_id,
                conversation=conversation,
                message_type=data["message_type"],
                required_holder_ids=set(active_members.values_list("user_id", flat=True)),
                enforce_conversation=enforce_file_conversation,
                pending_keys=pending_keys,
            )

        sender_copy = data.get("sender_copy") or {}
        try:
            # Nested atomic = savepoint: an IntegrityError only rolls back the
            # INSERT, leaving the outer transaction usable for the recovery
            # query below (PostgreSQL aborts the whole transaction otherwise).
            with transaction.atomic():
                message = EncryptedMessage.objects.create(
                    conversation=conversation,
                sender_id=sender_id,
                    receiver_id=data["receiver_id"],
                    message_type=data["message_type"],
                    ciphertext=data["ciphertext"],
                    nonce=data["nonce"],
                    auth_tag=data["auth_tag"],
                    sender_ephemeral_public_key=data.get("sender_ephemeral_public_key"),
                    sender_copy_ciphertext=sender_copy.get("ciphertext"),
                    sender_copy_nonce=sender_copy.get("nonce"),
                    sender_copy_auth_tag=sender_copy.get("auth_tag"),
                    sender_copy_ephemeral_public_key=sender_copy.get("sender_ephemeral_public_key"),
                    algorithm=data["algorithm"],
                    sender_key_version=data["sender_key_version"],
                    receiver_key_version=data["receiver_key_version"],
                    client_message_id=client_message_id,
                    reply_to_message_id=data.get("reply_to_message_id"),
                    file_id_id=data.get("file_id"),
                )
        except IntegrityError:
            # Concurrent same-ID insert: re-apply the digest rules.
            if not client_message_id:
                raise
            existing = EncryptedMessage.objects.filter(
                sender_id=sender_id, client_message_id=client_message_id
            ).first()
            if existing is None:
                raise
            return _replay_or_conflict(
                existing, incoming_digest, _private_stored_payload(existing)
            )

        # 5. wrapped keys + counters (created path only — replay never
        # rewrites key material or re-bumps)
        if data.get("file_id") and pending_keys:
            _write_pending_file_keys(message.file_id, pending_keys, sender_id)
        conversation.last_message_id = message.pk
        conversation.last_message_at = message.created_at
        conversation.save(update_fields=["last_message_id", "last_message_at", "updated_at"])
        active_members.filter(user_id=data["receiver_id"]).update(
            unread_count=F("unread_count") + 1
        )

    return SendResult(created=True, message=message)


# ── group send ────────────────────────────────────────────────────────────


def send_group_message(sender_id, data, *, enforce_file_conversation=True,
                       pending_file_keys=None):
    """Persist one group encrypted message with per-recipient copies."""
    data = validate_group_message(data)
    client_message_id = data["client_message_id"]
    pending_keys = normalize_pending_file_keys(pending_file_keys)
    incoming_digest = _canonical_digest(_group_digest_payload(data))

    with transaction.atomic():
        try:
            conversation = Conversation.objects.select_for_update().get(
                pk=data["group_id"],
                type=Conversation.Type.GROUP,
                status=Conversation.Status.ACTIVE,
            )
        except Conversation.DoesNotExist as error:
            raise PayloadError("conversation_not_found", "Group conversation not found or unavailable.") from error

        active_members = ConversationMember.objects.filter(
            conversation=conversation,
            status=ConversationMember.Status.ACTIVE,
        )
        active_member_ids = set(active_members.values_list("user_id", flat=True))
        if len(active_member_ids) > max_group_active_members:
            raise PayloadError(
                "group_too_large", f"Active group members exceed limit {max_group_active_members}."
            )
        if sender_id not in active_member_ids:
            raise PayloadError("conversation_forbidden", "Cannot send in this group conversation.")

        # R-02: idempotency replay BEFORE membership_version/recipients checks
        # so an ACK-lost retry still finds its original confirmation even when
        # the membership changed in between. The digest guarantees the replay
        # carries the same logical content; the sender must currently be a
        # member (checked above) to touch the conversation at all.
        if client_message_id:
            existing = (
                GroupMessage.objects.filter(sender_id=sender_id, client_message_id=client_message_id)
                .prefetch_related("recipients")
                .first()
            )
            if existing is not None:
                return _replay_or_conflict(
                    existing, incoming_digest, _group_stored_payload(existing)
                )

        if conversation.muted_until and conversation.muted_until > timezone.now():
            sender_role = active_members.filter(user_id=sender_id).values_list("role", flat=True).first()
            if sender_role not in (ConversationMember.Role.OWNER, ConversationMember.Role.ADMIN):
                raise PayloadError(
                    "group_muted", "This group is muted. Only owners and admins can send messages."
                )

        if data["membership_version"] != conversation.membership_version:
            raise PayloadError(
                "membership_conflict", "Group membership version changed. Refresh member list."
            )

        recipient_user_ids = {r["receiver_id"] for r in data["recipients"]}
        if recipient_user_ids != active_member_ids:
            raise PayloadError("recipients_mismatch", "Recipient list does not match active members.")

        if data.get("file_id"):
            validate_attached_file(
                file_id=data["file_id"],
                sender_id=sender_id,
                conversation=conversation,
                message_type=data["message_type"],
                required_holder_ids=active_member_ids,
                enforce_conversation=enforce_file_conversation,
                pending_keys=pending_keys,
            )

        sender_copy = data.get("sender_copy") or {}
        try:
            # Savepoint, same rationale as the private path.
            with transaction.atomic():
                group_message = GroupMessage.objects.create(
                    conversation=conversation,
                    sender_id=sender_id,
                    message_type=data["message_type"],
                    client_message_id=client_message_id,
                    reply_to_message_id=data.get("reply_to_message_id"),
                    file_id_id=data.get("file_id"),
                    sender_copy_ciphertext=sender_copy.get("ciphertext"),
                    sender_copy_nonce=sender_copy.get("nonce"),
                    sender_copy_auth_tag=sender_copy.get("auth_tag"),
                    sender_copy_ephemeral_public_key=sender_copy.get("sender_ephemeral_public_key"),
                )
        except IntegrityError:
            if not client_message_id:
                raise
            existing = (
                GroupMessage.objects.filter(sender_id=sender_id, client_message_id=client_message_id)
                .prefetch_related("recipients")
                .first()
            )
            if existing is None:
                raise
            return _replay_or_conflict(
                existing, incoming_digest, _group_stored_payload(existing)
            )

        GroupMessageRecipient.objects.bulk_create(
            [
                GroupMessageRecipient(
                    group_message=group_message,
                    receiver_id=r["receiver_id"],
                    ciphertext=r["ciphertext"],
                    nonce=r["nonce"],
                    auth_tag=r["auth_tag"],
                    algorithm=data["algorithm"],
                    sender_key_version=data["sender_key_version"],
                    receiver_key_version=r["receiver_key_version"],
                    sender_ephemeral_public_key=r.get("sender_ephemeral_public_key"),
                    membership_version=data["membership_version"],
                )
                for r in data["recipients"]
            ]
        )

        if data.get("file_id") and pending_keys:
            _write_pending_file_keys(group_message.file_id, pending_keys, sender_id)

        conversation.last_message_id = group_message.pk
        conversation.last_message_at = group_message.created_at
        conversation.save(update_fields=["last_message_id", "last_message_at", "updated_at"])
        active_members.exclude(user_id=sender_id).update(unread_count=F("unread_count") + 1)

    return SendResult(created=True, message=group_message)
