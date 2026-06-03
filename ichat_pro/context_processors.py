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
