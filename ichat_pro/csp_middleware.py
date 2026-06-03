"""
CSP (Content-Security-Policy) middleware for iChat Pro.

Adds a baseline CSP header that:
- Restricts script sources to self + pinned CDN origins
- Allows inline scripts/styles (Phase 1 pragmatism — see threat model for
  the plan to eliminate 'unsafe-inline' in Phase 2)
- Restricts connect-src for WebSocket (wss:) connections
- Blocks object-src, frame-ancestors (anti-clickjacking)

The policy is deliberately permissive for inline scripts because the current
codebase has many inline event handlers (onclick, onsubmit) and inline
<script> blocks (Tailwind Play CDN config, theme init). These will be
migrated to external files with nonce-based CSP in a future phase.

Reference: docs/iChat Pro 浏览器端安全威胁模型.md
           docs/iChat Pro 部署安全说明.md
"""

from django.conf import settings


class CSPMiddleware:
    """
    Adds Content-Security-Policy header to all responses.

    The policy tightens in production (DEBUG=False) and is more lenient in
    development to allow browser dev tools and hot-reload.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)

        # Build policy directives
        directives = self._build_directives(request)

        # CSP header value
        policy_value = "; ".join(
            f"{key} {value}".strip()
            for key, value in directives.items()
        )

        response["Content-Security-Policy"] = policy_value

        # Also set Report-Only in DEBUG mode so violations are logged but not blocked
        if settings.DEBUG:
            response["Content-Security-Policy-Report-Only"] = response[
                "Content-Security-Policy"
            ]
            del response["Content-Security-Policy"]

        return response

    def _build_directives(self, request):
        """Build the CSP directive dict."""

        # Determine WebSocket origin for connect-src
        host = request.get_host()

        # In production (TAILWIND_CDN=False) drop the Tailwind CDN from CSP whitelist
        use_tailwind_cdn = getattr(settings, "TAILWIND_CDN", settings.DEBUG)
        tailwind_cdn = "https://cdn.tailwindcss.com " if use_tailwind_cdn else ""

        directives = {
            "default-src": "'self'",
            # Script: allow self, CDN deps, and inline (Phase 1 baseline)
            "script-src": (
                "'self' "
                "'unsafe-inline' "  # Phase 2: replace with nonce + strict-dynamic
                + tailwind_cdn +
                "https://unpkg.com"
            ),
            # Style: allow self + inline; Tailwind CDN only needed in dev
            "style-src": (
                "'self' "
                "'unsafe-inline' "
                + tailwind_cdn.rstrip()
            ),
            # Connect: allow self + WebSocket (ws/wss)
            "connect-src": (
                "'self' "
                f"ws://{host} "
                f"wss://{host}"
            ),
            # Images: allow self + data URIs (favicons, emoji)
            "img-src": "'self' data:",
            # Fonts: self-hosted only
            "font-src": "'self'",
            # Object: block plugins (Flash, Java, etc.)
            "object-src": "'none'",
            # Base URI: prevent <base> tag injection
            "base-uri": "'self'",
            # Form targets: restrict to same origin
            "form-action": "'self'",
            # Frame ancestors: anti-clickjacking (belt-and-suspenders with X-Frame-Options)
            "frame-ancestors": "'none'",
            # Manifest: allow self for PWA
            "manifest-src": "'self'",
        }

        # In production, optionally add upgrade-insecure-requests
        if not settings.DEBUG:
            directives["upgrade-insecure-requests"] = ""

        return directives
