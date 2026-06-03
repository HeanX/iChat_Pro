"""一键创建 iChat Pro 一期演示账号并建立联系关系。

用法:
    python demo_setup.py

演示账号:
    alice / demo1234
    bob   / demo1234
    carol / demo1234
"""
import os
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ichat_pro.settings")
django.setup()

from django.contrib.auth import get_user_model
from accounts.models import Contact

User = get_user_model()

users = {}
for username in ("alice", "bob", "carol"):
    user, created = User.objects.get_or_create(username=username)
    user.set_password("demo1234")
    user.save()
    users[username] = user
    action = "Created" if created else "Updated"
    print(f"  {action} {username}")

# 建立双向联系人 (alice <-> bob, alice <-> carol, bob <-> carol)
pairs = [("alice", "bob"), ("alice", "carol"), ("bob", "carol")]
for u1, u2 in pairs:
    for a_name, b_name in [(u1, u2), (u2, u1)]:
        Contact.objects.get_or_create(
            user=users[a_name], contact=users[b_name],
        )

print("\nDemo accounts ready:")
print("  alice / demo1234")
print("  bob   / demo1234")
print("  carol / demo1234")
print("\nAll three are mutual contacts.")
