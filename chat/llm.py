import os
import json
import logging
import ipaddress
import socket
import urllib.request
import urllib.error
from urllib.parse import urlparse, urlunparse
from concurrent.futures import ThreadPoolExecutor

logger = logging.getLogger(__name__)
executor = ThreadPoolExecutor(max_workers=4)

DEFAULT_ALLOWED_LLM_HOSTS = {
    "api.anthropic.com",
    "api.openai.com",
    "openrouter.ai",
    "api.openrouter.ai",
    "4router.net",
    "dashscope.aliyuncs.com",
    "api.deepseek.com",
    "generativelanguage.googleapis.com",
}


def _allowed_llm_hosts():
    configured = {
        host.strip().lower()
        for host in os.environ.get("LLM_ALLOWED_HOSTS", "").split(",")
        if host.strip()
    }
    return DEFAULT_ALLOWED_LLM_HOSTS | configured


def _host_matches_allowed(host, allowed_host):
    return host == allowed_host or host.endswith(f".{allowed_host}")


def validate_llm_endpoint(endpoint: str):
    parsed = urlparse(endpoint or "")
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("Model request endpoint must be an HTTPS URL.")

    host = parsed.hostname.lower().rstrip(".")
    allowed = _allowed_llm_hosts()
    if not any(_host_matches_allowed(host, allowed_host) for allowed_host in allowed):
        raise ValueError("Model request endpoint host is not allowed.")

    try:
        addr_infos = socket.getaddrinfo(host, parsed.port or 443, type=socket.SOCK_STREAM)
    except socket.gaierror as error:
        raise ValueError("Model request endpoint host could not be resolved.") from error

    for addr_info in addr_infos:
        ip = ipaddress.ip_address(addr_info[4][0])
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        ):
            raise ValueError("Model request endpoint resolved to a blocked network address.")

def normalize_chat_completions_endpoint(endpoint: str) -> str:
    endpoint = (endpoint or "").strip()
    parsed = urlparse(endpoint)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("Invalid model request endpoint.")

    normalized = urlunparse(parsed._replace(params="", query="", fragment=""))
    validate_llm_endpoint(normalized)
    return normalized

def normalize_anthropic_messages_endpoint(endpoint: str) -> str:
    endpoint = (endpoint or "").strip()
    parsed = urlparse(endpoint)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("Invalid Anthropic request endpoint.")

    normalized = urlunparse(parsed._replace(params="", query="", fragment=""))
    validate_llm_endpoint(normalized)
    return normalized

def is_anthropic_messages_endpoint(endpoint: str) -> bool:
    parsed = urlparse(endpoint or "")
    host = parsed.netloc.lower()
    path = parsed.path.rstrip("/")
    return "anthropic.com" in host or path.endswith("/v1/messages")


def _content_to_text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text") or item.get("content")
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts)
    if isinstance(content, dict):
        text = content.get("text") or content.get("content")
        return text if isinstance(text, str) else ""
    return ""


def _openai_choice_text(choice):
    if not isinstance(choice, dict):
        return ""
    delta = choice.get("delta") or {}
    message = choice.get("message") or {}
    return (
        _content_to_text(delta.get("content"))
        or _content_to_text(delta.get("text"))
        or _content_to_text(message.get("content"))
        or _content_to_text(choice.get("text"))
    )


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

        base_payload = {
            "model": self.model,
            "messages": api_messages,
            "temperature": 0.7,
        }
        stream_payload = {**base_payload, "stream": True}
        yielded = False
        try:
            for data in self._stream_request(stream_payload):
                choices = data.get("choices") or []
                if not choices:
                    continue
                content = _openai_choice_text(choices[0])
                if content:
                    yielded = True
                    yield content
        except Exception:
            if yielded:
                raise
            fallback = self._request(base_payload)
            if fallback:
                yield fallback
            return
        if not yielded:
            fallback = self._request(base_payload)
            if fallback:
                yield fallback

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
                choices = data.get("choices") or []
                if choices:
                    content = _openai_choice_text(choices[0])
                    if content:
                        return content
                content = _content_to_text(data.get("content"))
                if content:
                    return content
                raise KeyError("choices[0].message.content")
        except urllib.error.HTTPError as e:
            error_body = e.read().decode("utf-8", errors="replace")
            logger.error(f"OpenAI-compatible API HTTP Error {e.code}: {error_body}")
            raise RuntimeError(f"Model API returned error {e.code}.")
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
            raise RuntimeError(f"Model API returned error {e.code}.")
        except urllib.error.URLError as e:
            logger.error(f"OpenAI-compatible stream connection failed: {e.reason}")
            raise RuntimeError(f"Failed to connect to model API endpoint: {self.endpoint}")

class AnthropicMessagesProvider(LlmProvider):
    def __init__(self, api_key, endpoint="https://api.anthropic.com/v1/messages", model="claude-sonnet-4-6"):
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
                content = _content_to_text(data.get("content"))
                if content:
                    return content
                raise KeyError("content[0].text")
        except urllib.error.HTTPError as e:
            error_body = e.read().decode("utf-8", errors="replace")
            logger.error(f"Anthropic API HTTP Error {e.code}: {error_body}")
            raise RuntimeError(f"Anthropic API returned error {e.code}.")
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

        yielded = False
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
                            yielded = True
                            yield text
                    elif data.get("type") == "message_stop":
                        break
            if not yielded:
                fallback = self.complete(messages=messages, system=system)
                if fallback:
                    yield fallback
        except urllib.error.HTTPError as e:
            error_body = e.read().decode("utf-8", errors="replace")
            logger.error(f"Anthropic stream HTTP Error {e.code}: {error_body}")
            if yielded:
                raise RuntimeError(f"Anthropic API returned error {e.code}.")
            fallback = self.complete(messages=messages, system=system)
            if fallback:
                yield fallback
        except urllib.error.URLError as e:
            logger.error(f"Anthropic stream connection failed: {e.reason}")
            if yielded:
                raise RuntimeError(f"Failed to connect to Anthropic API endpoint: {self.url}")
            fallback = self.complete(messages=messages, system=system)
            if fallback:
                yield fallback

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

    # Fallback: detect provider from environment variables.
    # Priority: ANTHROPIC_API_KEY > OPENAI_API_KEY (with OPENAI_API_BASE).
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY")
    if anthropic_key:
        return AnthropicMessagesProvider(api_key=anthropic_key)

    openai_key = os.environ.get("OPENAI_API_KEY")
    if openai_key:
        openai_base = os.environ.get(
            "OPENAI_API_BASE",
            "https://api.openai.com/v1/chat/completions",
        )
        openai_model = os.environ.get("OPENAI_MODEL", "gpt-5.4")
        return OpenAICompatibleProvider(
            endpoint=openai_base,
            api_key=openai_key,
            model=openai_model,
        )

    return MockProvider()
