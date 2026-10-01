"""Sync cursor signing/verification for the catch-up API (P4 T34).

The cursor is an opaque server-signed token binding the conversation, the
requesting user, the last fully-applied sequence and the high-water
snapshot. Clients must treat it as opaque; tampering or cross-conversation
reuse fails verification with ``sync_cursor_invalid``. Tokens expire after
COURSOR_TTL_DAYS (``sync_cursor_expired``), signalling that the client must
re-snapshot.
"""

import base64
import hashlib
import hmac
import json
import time

from django.conf import settings

from ..errors import PayloadError

CURSOR_TTL_SECONDS = 7 * 24 * 3600
_CURSOR_VERSION = 1


def _sign(body: bytes) -> str:
    key = settings.SECRET_KEY.encode("utf-8")
    return hmac.new(key, body, hashlib.sha256).hexdigest()


def make_sync_cursor(*, conversation_id, user_id, last_sequence, high_water=None):
    """``high_water=None`` releases the snapshot: the next call re-snapshots
    and returns the delta since ``last_sequence`` (used when a walk has
    completed and the client is caught up)."""
    payload = {
        "v": _CURSOR_VERSION,
        "c": conversation_id,
        "u": user_id,
        "s": last_sequence,
        "hw": high_water,
        "t": int(time.time()),
    }
    body = base64.urlsafe_b64encode(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).rstrip(b"=")
    return f"{body.decode('ascii')}.{_sign(body)}"


def verify_sync_cursor(cursor, *, conversation_id, user_id):
    """Return (last_sequence, high_water or None) or raise a cursor
    PayloadError."""
    try:
        body_str, _, signature = cursor.partition(".")
        if not body_str or not signature:
            raise ValueError("malformed")
        body = body_str.encode("ascii")
        if not hmac.compare_digest(_sign(body), signature):
            raise ValueError("bad signature")
        padding = b"=" * (-len(body) % 4)
        payload = json.loads(base64.urlsafe_b64decode(body + padding))
    except (ValueError, UnicodeDecodeError, TypeError, json.JSONDecodeError):
        # TypeError covers hmac.compare_digest on non-ASCII signatures.
        raise PayloadError("sync_cursor_invalid", "同步游标不合法") from None

    if (
        not isinstance(payload, dict)
        or payload.get("v") != _CURSOR_VERSION
        or payload.get("c") != conversation_id
        or payload.get("u") != user_id
    ):
        raise PayloadError("sync_cursor_invalid", "同步游标不合法")
    if not isinstance(payload.get("s"), int):
        raise PayloadError("sync_cursor_invalid", "同步游标不合法")
    if payload.get("hw") is not None and not isinstance(payload.get("hw"), int):
        raise PayloadError("sync_cursor_invalid", "同步游标不合法")
    if time.time() - payload.get("t", 0) > CURSOR_TTL_SECONDS:
        raise PayloadError("sync_cursor_expired", "同步游标已过期，请重新快照")
    return payload["s"], payload["hw"]
