import os
import json
import logging
import urllib.request
import urllib.error
from urllib.parse import urlparse, urlunparse
from concurrent.futures import ThreadPoolExecutor

logger = logging.getLogger(__name__)
executor = ThreadPoolExecutor(max_workers=4)

def normalize_chat_completions_endpoint(endpoint: str) -> str:
    endpoint = (endpoint or "").strip()
    parsed = urlparse(endpoint)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("Invalid model request endpoint.")

    path = parsed.path.rstrip("/")
    if not path:
        path = "/v1/chat/completions"
    elif path.endswith("/v1"):
        path = f"{path}/chat/completions"
    elif path.endswith("/api/v1"):
        path = f"{path}/chat/completions"

    return urlunparse(parsed._replace(path=path, params="", query="", fragment=""))

def normalize_anthropic_messages_endpoint(endpoint: str) -> str:
    endpoint = (endpoint or "").strip()
    parsed = urlparse(endpoint)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("Invalid Anthropic request endpoint.")

    path = parsed.path.rstrip("/")
    if not path:
        path = "/v1/messages"
    elif path.endswith("/v1"):
        path = f"{path}/messages"

    return urlunparse(parsed._replace(path=path, params="", query="", fragment=""))

def is_anthropic_messages_endpoint(endpoint: str) -> bool:
    parsed = urlparse(endpoint or "")
    host = parsed.netloc.lower()
    path = parsed.path.rstrip("/")
    return "anthropic.com" in host or path.endswith("/v1/messages")

class LlmProvider:
    def complete(self, *, messages, system=None) -> str:
        raise NotImplementedError

    def stream(self, *, messages, system=None):
        yield self.complete(messages=messages, system=system)

class MockProvider(LlmProvider):
    def complete(self, *, messages, system=None) -> str:
        user_input = messages[-1]["content"] if messages else ""
        return f"【Mock LLM】Received: '{user_input}'. Since no model API key is configured, this is a simulated local response."

    def stream(self, *, messages, system=None):
        text = self.complete(messages=messages, system=system)
        for i in range(0, len(text), 12):
            yield text[i:i + 12]

class OpenAICompatibleProvider(LlmProvider):
    def __init__(self, *, endpoint, api_key, model):
        self.endpoint = normalize_chat_completions_endpoint(endpoint)
        self.api_key = api_key
        self.model = model

    def complete(self, *, messages, system=None) -> str:
        api_messages = []
        if system:
            api_messages.append({"role": "system", "content": system})
        api_messages.extend(messages)

        payload = {
            "model": self.model,
            "messages": api_messages,
            "temperature": 0.7,
        }
        return self._request(payload)

    def stream(self, *, messages, system=None):
        api_messages = []
        if system:
            api_messages.append({"role": "system", "content": system})
        api_messages.extend(messages)

        payload = {
            "model": self.model,
            "messages": api_messages,
            "temperature": 0.7,
            "stream": True,
        }
        for data in self._stream_request(payload):
            choices = data.get("choices") or []
            if not choices:
                continue
            delta = choices[0].get("delta") or {}
            content = delta.get("content")
            if content:
                yield content

    def _request(self, payload):
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }
        req = urllib.request.Request(
            self.endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST"
        )

        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                res_body = response.read().decode("utf-8")
                data = json.loads(res_body)
                return data["choices"][0]["message"]["content"]
        except urllib.error.HTTPError as e:
            error_body = e.read().decode("utf-8", errors="replace")
            logger.error(f"OpenAI-compatible API HTTP Error {e.code}: {error_body}")
            detail = error_body[:500] if error_body else ""
            raise RuntimeError(f"Model API returned error {e.code}. {detail}")
        except urllib.error.URLError as e:
            logger.error(f"OpenAI-compatible API connection failed: {e.reason}")
            raise RuntimeError(f"Failed to connect to model API endpoint: {self.endpoint}")
        except (KeyError, IndexError, TypeError) as e:
            logger.error(f"Unexpected OpenAI-compatible response shape: {e}")
            raise RuntimeError("Model API returned an unsupported response format")
        except Exception as e:
            logger.error(f"Unexpected error calling model API: {e}")
            raise RuntimeError("Internal error calling model API")

    def _stream_request(self, payload):
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "text/event-stream",
        }
        req = urllib.request.Request(
            self.endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST"
        )

        try:
            with urllib.request.urlopen(req, timeout=60) as response:
                for raw_line in response:
                    line = raw_line.decode("utf-8", errors="replace").strip()
                    if not line or line.startswith(":"):
                        continue
                    if not line.startswith("data:"):
                        continue
                    data_text = line[5:].strip()
                    if data_text == "[DONE]":
                        break
                    try:
                        yield json.loads(data_text)
                    except json.JSONDecodeError:
                        logger.warning("Skipping malformed stream chunk: %s", data_text[:120])
        except urllib.error.HTTPError as e:
            error_body = e.read().decode("utf-8", errors="replace")
            logger.error(f"OpenAI-compatible stream HTTP Error {e.code}: {error_body}")
            detail = error_body[:500] if error_body else ""
            raise RuntimeError(f"Model API returned error {e.code}. {detail}")
        except urllib.error.URLError as e:
            logger.error(f"OpenAI-compatible stream connection failed: {e.reason}")
            raise RuntimeError(f"Failed to connect to model API endpoint: {self.endpoint}")

class AnthropicMessagesProvider(LlmProvider):
    def __init__(self, api_key, endpoint="https://api.anthropic.com/v1/messages", model="claude-3-5-sonnet-20241022"):
        self.api_key = api_key
        self.url = normalize_anthropic_messages_endpoint(endpoint)
        self.model = model

    def complete(self, *, messages, system=None) -> str:
        headers = {
            "Content-Type": "application/json",
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
        }
        
        # Build payload according to Anthropic Messages API specification
        payload = {
            "model": self.model,
            "max_tokens": 1024,
            "messages": messages,
        }
        if system:
            payload["system"] = system

        req = urllib.request.Request(
            self.url,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST"
        )
        
        try:
            # Set a 20-second timeout to prevent blocking thread pool indefinitely
            with urllib.request.urlopen(req, timeout=20) as response:
                res_body = response.read().decode("utf-8")
                data = json.loads(res_body)
                return data["content"][0]["text"]
        except urllib.error.HTTPError as e:
            error_body = e.read().decode("utf-8", errors="replace")
            logger.error(f"Anthropic API HTTP Error {e.code}: {error_body}")
            detail = error_body[:500] if error_body else ""
            raise RuntimeError(f"Anthropic API returned error {e.code}. {detail}")
        except urllib.error.URLError as e:
            logger.error(f"Anthropic connection failed: {e.reason}")
            raise RuntimeError(f"Failed to connect to Anthropic API endpoint: {self.url}")
        except Exception as e:
            logger.error(f"Unexpected error calling Anthropic API: {e}")
            raise RuntimeError("Internal error calling LLM API")

    def stream(self, *, messages, system=None):
        headers = {
            "Content-Type": "application/json",
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "Accept": "text/event-stream",
        }
        payload = {
            "model": self.model,
            "max_tokens": 1024,
            "messages": messages,
            "stream": True,
        }
        if system:
            payload["system"] = system

        req = urllib.request.Request(
            self.url,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST"
        )

        try:
            with urllib.request.urlopen(req, timeout=60) as response:
                for raw_line in response:
                    line = raw_line.decode("utf-8", errors="replace").strip()
                    if not line or line.startswith(":") or not line.startswith("data:"):
                        continue
                    data_text = line[5:].strip()
                    try:
                        data = json.loads(data_text)
                    except json.JSONDecodeError:
                        continue
                    if data.get("type") == "content_block_delta":
                        delta = data.get("delta") or {}
                        text = delta.get("text")
                        if text:
                            yield text
                    elif data.get("type") == "message_stop":
                        break
        except urllib.error.HTTPError as e:
            error_body = e.read().decode("utf-8", errors="replace")
            logger.error(f"Anthropic stream HTTP Error {e.code}: {error_body}")
            detail = error_body[:500] if error_body else ""
            raise RuntimeError(f"Anthropic API returned error {e.code}. {detail}")
        except urllib.error.URLError as e:
            logger.error(f"Anthropic stream connection failed: {e.reason}")
            raise RuntimeError(f"Failed to connect to Anthropic API endpoint: {self.url}")

def get_llm_provider(model_config=None):
    model_config = model_config or {}
    configured_api_key = model_config.get("api_key")
    configured_endpoint = model_config.get("endpoint")
    configured_model = model_config.get("model")
    if configured_api_key and configured_endpoint and configured_model:
        if is_anthropic_messages_endpoint(configured_endpoint):
            return AnthropicMessagesProvider(
                api_key=configured_api_key,
                endpoint=configured_endpoint,
                model=configured_model,
            )
        return OpenAICompatibleProvider(
            endpoint=configured_endpoint,
            api_key=configured_api_key,
            model=configured_model,
        )

    # Automatically switch between Anthropic and Mock based on API key presence
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return MockProvider()
    return AnthropicMessagesProvider(api_key=api_key)
