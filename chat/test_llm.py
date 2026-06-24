import json
import socket
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase

from .llm import OpenAICompatibleProvider, _content_to_text, normalize_chat_completions_endpoint
from .models import UserLLMConfig


PUBLIC_ADDR_INFO = [
    (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443)),
]


class LlmEndpointNormalizationTests(SimpleTestCase):
    @patch("chat.llm.socket.getaddrinfo", return_value=PUBLIC_ADDR_INFO)
    def test_4router_preset_endpoint_is_allowed(self, _mock_getaddrinfo):
        endpoint = "https://4router.net/v1/chat/completions"

        self.assertEqual(normalize_chat_completions_endpoint(endpoint), endpoint)

    @patch("chat.llm.socket.getaddrinfo", return_value=PUBLIC_ADDR_INFO)
    def test_openai_compatible_endpoint_path_is_preserved(self, _mock_getaddrinfo):
        endpoint = normalize_chat_completions_endpoint(
            "https://dashscope.aliyuncs.com/compatible-mode/v1"
        )

        self.assertEqual(
            endpoint,
            "https://dashscope.aliyuncs.com/compatible-mode/v1",
        )

    def test_stream_falls_back_when_provider_returns_no_content_deltas(self):
        provider = OpenAICompatibleProvider.__new__(OpenAICompatibleProvider)
        provider.model = "claude-test"
        provider.api_key = "sk-test"
        provider.endpoint = "https://4router.net/v1/chat/completions"
        provider._stream_request = lambda _payload: iter([
            {"choices": [{"delta": {"role": "assistant"}}]},
            {"choices": [{"finish_reason": "stop"}]},
        ])
        provider._request = lambda _payload: "fallback response"

        chunks = list(provider.stream(messages=[{"role": "user", "content": "hi"}]))

        self.assertEqual(chunks, ["fallback response"])

    def test_content_block_list_is_extracted_as_text(self):
        self.assertEqual(
            _content_to_text([
                {"type": "text", "text": "hello"},
                {"type": "text", "text": " world"},
            ]),
            "hello world",
        )


class AiConfigViewTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="llm-user",
            password="test-pass",
        )
        self.client.login(username="llm-user", password="test-pass")

    @patch("chat.llm.socket.getaddrinfo", return_value=PUBLIC_ADDR_INFO)
    def test_save_config_stores_endpoint_without_changing_path(self, _mock_getaddrinfo):
        response = self.client.post(
            "/api/ai/config/",
            data=json.dumps({
                "endpoint": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                "api_key": "sk-test",
            }),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(
            payload["endpoint"],
            "https://dashscope.aliyuncs.com/compatible-mode/v1",
        )
        self.assertTrue(payload["has_api_key"])

        config = UserLLMConfig.objects.get(user=self.user)
        self.assertEqual(config.api_url, payload["endpoint"])
        self.assertEqual(config.assistant_id, "ai-assistant")
        self.assertEqual(config.get_api_key(), "sk-test")

    @patch("chat.llm.socket.getaddrinfo", return_value=PUBLIC_ADDR_INFO)
    def test_configs_are_scoped_per_assistant(self, _mock_getaddrinfo):
        first = self.client.post(
            "/api/ai/config/",
            data=json.dumps({
                "assistant_id": "ai-assistant",
                "endpoint": "https://api.openai.com/v1/chat/completions",
                "api_key": "sk-first",
            }),
            content_type="application/json",
        )
        second = self.client.post(
            "/api/ai/config/",
            data=json.dumps({
                "assistant_id": "ai-assistant-2",
                "endpoint": "https://4router.net/v1/messages",
                "api_key": "sk-second",
            }),
            content_type="application/json",
        )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(UserLLMConfig.objects.filter(user=self.user).count(), 2)

        first_config = UserLLMConfig.objects.get(
            user=self.user,
            assistant_id="ai-assistant",
        )
        second_config = UserLLMConfig.objects.get(
            user=self.user,
            assistant_id="ai-assistant-2",
        )
        self.assertEqual(first_config.api_url, "https://api.openai.com/v1/chat/completions")
        self.assertEqual(first_config.get_api_key(), "sk-first")
        self.assertEqual(second_config.api_url, "https://4router.net/v1/messages")
        self.assertEqual(second_config.get_api_key(), "sk-second")

        clear_second = self.client.post(
            "/api/ai/config/",
            data=json.dumps({
                "assistant_id": "ai-assistant-2",
                "endpoint": "",
                "api_key": "",
            }),
            content_type="application/json",
        )

        self.assertEqual(clear_second.status_code, 200)
        self.assertTrue(
            UserLLMConfig.objects.filter(
                user=self.user,
                assistant_id="ai-assistant",
            ).exists()
        )
        self.assertFalse(
            UserLLMConfig.objects.filter(
                user=self.user,
                assistant_id="ai-assistant-2",
            ).exists()
        )

    def test_save_config_rejects_non_https_endpoint(self):
        response = self.client.post(
            "/api/ai/config/",
            data=json.dumps({
                "endpoint": "http://api.openai.com/v1",
                "api_key": "sk-test",
            }),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(UserLLMConfig.objects.filter(user=self.user).exists())
