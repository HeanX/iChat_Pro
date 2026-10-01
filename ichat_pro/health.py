"""Liveness and readiness endpoints (P4 T12 / NEW-CLD-012).

Public responses carry only a status word; failure details go to the server
log so internal topology is never disclosed on a public endpoint.
"""

import logging
from uuid import uuid4

from django.core.cache import cache
from django.db import connection
from django.http import JsonResponse
from django.views.decorators.http import require_GET

logger = logging.getLogger(__name__)


@require_GET
def health_live(request):
    """Process liveness: the application is running and can serve requests."""
    return JsonResponse({'status': 'ok'})


@require_GET
def health_ready(request):
    """Readiness: database and cache (Redis in production) are reachable."""
    try:
        with connection.cursor() as cursor:
            cursor.execute('SELECT 1')
        probe_key = f'health:ready:{uuid4()}'
        cache.set(probe_key, 1, 10)
        try:
            probe_ok = cache.get(probe_key) == 1
        finally:
            cache.delete(probe_key)
        if not probe_ok:
            raise RuntimeError('cache probe did not round-trip')
    except Exception:
        logger.warning('Readiness check failed', exc_info=True)
        return JsonResponse({'status': 'unavailable'}, status=503)
    return JsonResponse({'status': 'ok'})
