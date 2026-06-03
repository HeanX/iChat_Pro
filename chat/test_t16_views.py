import json

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from accounts.models import Contact
from chat.models import Conversation, ConversationMember


class ConversationApiTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.alice = User.objects.create_user(username='alice', password='pw')
        self.bob = User.objects.create_user(username='bob', password='pw')
        self.mallory = User.objects.create_user(username='mallory', password='pw')
        self.client.force_login(self.alice)

    def test_private_conversation_requires_contact(self):
        response = self.client.post(
            reverse('api_conversation_create'),
            json.dumps({'peer_id': self.mallory.pk}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(Conversation.objects.count(), 0)

    def test_private_conversation_create_or_get_for_contact(self):
        Contact.objects.create(user=self.alice, contact=self.bob)

        first = self.client.post(
            reverse('api_conversation_create'),
            json.dumps({'peer_id': self.bob.pk}),
            content_type='application/json',
        )
        second = self.client.post(
            reverse('api_conversation_create'),
            json.dumps({'peer_id': self.bob.pk}),
            content_type='application/json',
        )

        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(
            first.json()['conversation_id'],
            second.json()['conversation_id'],
        )
        self.assertEqual(Conversation.objects.count(), 1)
        self.assertEqual(ConversationMember.objects.count(), 2)

    def test_conversation_list_is_limited_to_memberships(self):
        Contact.objects.create(user=self.alice, contact=self.bob)
        visible = Conversation.objects.create(
            type=Conversation.Type.SINGLE,
            created_by=self.alice,
        )
        ConversationMember.objects.create(conversation=visible, user=self.alice)
        ConversationMember.objects.create(conversation=visible, user=self.bob)
        hidden = Conversation.objects.create(
            type=Conversation.Type.SINGLE,
            created_by=self.bob,
        )
        ConversationMember.objects.create(conversation=hidden, user=self.bob)
        ConversationMember.objects.create(conversation=hidden, user=self.mallory)

        response = self.client.get(reverse('api_conversations'))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(
            [conversation['id'] for conversation in payload['conversations']],
            [visible.pk],
        )
