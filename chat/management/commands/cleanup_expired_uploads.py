"""
Management command to clean up expired file uploads and orphaned chunk
directories.

Run periodically (e.g. via cron) to reclaim disk space::

    python manage.py cleanup_expired_uploads
"""
import os
import shutil
import time
from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from chat.models import EncryptedFile, EncryptedFileChunk


class Command(BaseCommand):
    help = (
        "Remove expired EncryptedFile records and their on-disk files, "
        "and delete orphaned chunk directories older than 24 hours."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be deleted without actually deleting.',
        )
        parser.add_argument(
            '--older-than-hours',
            type=int,
            default=24,
            help='Delete expired files older than N hours (default: 24).',
        )

    def handle(self, **options):
        dry_run = options['dry_run']
        older_than = options['older_than_hours']
        now = timezone.now()
        cutoff = now - timedelta(hours=older_than)

        # ── 1. Expired EncryptedFile records ──────────────────────────
        expired_files = EncryptedFile.objects.filter(
            status__in=[EncryptedFile.Status.AVAILABLE, EncryptedFile.Status.UPLOADING],
            expires_at__lt=now,
        )

        self.stdout.write(
            f'Found {expired_files.count()} expired file(s) '
            f'(older than {older_than}h cutoff).'
        )

        for ef in expired_files:
            self.stdout.write(f'  - EncryptedFile #{ef.id} ({ef.message_kind}) '
                              f'status={ef.status} expires={ef.expires_at}')

            if not dry_run:
                # Delete merged file from disk
                if ef.storage_path:
                    file_path = settings.MEDIA_ROOT / ef.storage_path
                    if file_path.exists():
                        file_path.unlink()
                        self.stdout.write(f'    Deleted: {file_path}')

                # Delete chunk records (their temp files should already be gone)
                EncryptedFileChunk.objects.filter(file=ef).delete()

                ef.status = EncryptedFile.Status.DELETED
                ef.deleted_at = now
                ef.save(update_fields=['status', 'deleted_at'])

        # ── 2. Orphaned chunk directories ─────────────────────────────
        chunks_root = settings.MEDIA_ROOT / 'uploads' / 'chunks'
        if chunks_root.exists():
            for entry in chunks_root.iterdir():
                if not entry.is_dir():
                    continue
                try:
                    mtime = entry.stat().st_mtime
                    age_seconds = time.time() - mtime
                    if age_seconds > older_than * 3600:
                        self.stdout.write(f'  - Orphan chunk dir: {entry.name} '
                                          f'(age: {age_seconds / 3600:.1f}h)')
                        if not dry_run:
                            shutil.rmtree(entry)
                            self.stdout.write(f'    Removed.')
                except OSError as exc:
                    self.stderr.write(f'  Error reading {entry}: {exc}')

        if dry_run:
            self.stdout.write(self.style.WARNING('Dry run — no files deleted.'))
        else:
            self.stdout.write(self.style.SUCCESS('Cleanup complete.'))
