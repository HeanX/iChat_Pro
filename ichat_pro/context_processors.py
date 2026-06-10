"""Template context processors for iChat Pro."""

from django.conf import settings


def tailwind(request):
    """Expose TAILWIND_CDN setting to templates.

    When True, base.html uses the Tailwind Play CDN (development).
    When False, base.html loads the pre-built static CSS (production).
    """
    return {
        "use_tailwind_cdn": getattr(settings, "TAILWIND_CDN", settings.DEBUG),
    }


def settings_sidebar(request):
    """Expose real account and sidebar settings data to shared templates."""
    if not getattr(request, "user", None) or not request.user.is_authenticated:
        return {}

    from django.contrib.sessions.models import Session
    from django.utils import timezone

    from accounts.models import BlockedUser
    from chat.models import Conversation, ConversationMember, UserPresence

    user = request.user
    profile = getattr(user, "profile", None)
    display_name = (
        getattr(profile, "nickname", "")
        or user.get_full_name()
        or user.username
    )
    phone_number = getattr(profile, "phone_number", "") if profile else ""
    email = user.email or ""

    active_chat_count = ConversationMember.objects.filter(
        user=user,
        status=ConversationMember.Status.ACTIVE,
        conversation__status=Conversation.Status.ACTIVE,
    ).count()
    group_chat_count = ConversationMember.objects.filter(
        user=user,
        status=ConversationMember.Status.ACTIVE,
        conversation__type=Conversation.Type.GROUP,
        conversation__status=Conversation.Status.ACTIVE,
    ).count()
    unread_chat_count = ConversationMember.objects.filter(
        user=user,
        status=ConversationMember.Status.ACTIVE,
        conversation__status=Conversation.Status.ACTIVE,
        unread_count__gt=0,
    ).count()
    blocked_count = BlockedUser.objects.filter(blocker=user).count()

    active_sessions = []
    # Count total active sessions belonging to this user (scalar query, not per-session decode)
    session_count = 0
    for session in Session.objects.filter(expire_date__gte=timezone.now()):
        try:
            data = session.get_decoded()
        except Exception:
            continue
        if data.get("_auth_user_id") != str(user.pk):
            continue
        session_count += 1
        active_sessions.append({
            # Never expose real session keys to templates.
            # Use an opaque index that is only meaningful alongside the
            # server-side index map stored in the request session.
            "session_index": session_count,
            "short_key": f"session-{session_count}",  # Opaque label — no real key data
            "expires_at": session.expire_date,
            "is_current": session.session_key == request.session.session_key,
        })
    active_sessions.sort(key=lambda item: (not item["is_current"], item["expires_at"]))

    presence = getattr(user, "presence", None)
    if presence is None:
        presence = UserPresence(user=user, is_online=True, status=UserPresence.Status.ONLINE)
    status_label = presence.get_status_display() if presence.pk else "Online"

    avatar_url = ""
    if profile and profile.avatar:
        try:
            avatar_url = profile.avatar.url
        except ValueError:
            avatar_url = ""

    return {
        "settings_display_name": display_name,
        "settings_initials": (display_name[:1] or user.username[:1] or "?").upper(),
        "settings_avatar_url": avatar_url,
        "settings_phone_number": phone_number,
        "settings_phone_display": phone_number or "Not set",
        "settings_email": email,
        "settings_email_display": email or "Not set",
        "settings_username_display": f"@{user.username.lower()}",
        "settings_presence_label": status_label,
        "settings_is_online": bool(getattr(presence, "is_online", True)),
        "settings_chat_count": active_chat_count,
        "settings_chat_count_label": f"{active_chat_count} chat" + ("" if active_chat_count == 1 else "s"),
        "settings_group_chat_count": group_chat_count,
        "settings_group_chat_count_label": f"{group_chat_count} chat" + ("" if group_chat_count == 1 else "s"),
        "settings_unread_chat_count": unread_chat_count,
        "settings_unread_chat_count_label": f"{unread_chat_count} chat" + ("" if unread_chat_count == 1 else "s"),
        "settings_active_sessions": active_sessions,
        "settings_active_sessions_count": len(active_sessions),
        "settings_active_sessions_label": f"{len(active_sessions)} active",
        "settings_active_sessions_title": f"Active Sessions ({len(active_sessions)})",
        "settings_blocked_count": blocked_count,
        "settings_blocked_label": (
            "No users currently blocked"
            if blocked_count == 0
            else f"{blocked_count} blocked user" + ("" if blocked_count == 1 else "s")
        ),
    }
