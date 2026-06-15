import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'ichat_pro.settings')
django.setup()

from accounts.models import UserProfile
for profile in UserProfile.objects.all():
    print(f"User: {profile.user.username}, ID: {profile.user.id}")
    print(f"  Nickname: {profile.nickname}")
    print(f"  Avatar name: {profile.avatar.name if profile.avatar else 'None'}")
    if profile.avatar:
        try:
            print(f"  Avatar URL: {profile.avatar.url}")
            print(f"  File exists: {profile.avatar.storage.exists(profile.avatar.name)}")
            print(f"  Full path: {profile.avatar.path}")
        except Exception as e:
            print(f"  Error: {e}")
