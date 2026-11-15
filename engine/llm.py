"""
llm.py

Sends a question to an AI model and gives back the answer.
Two connectors, same simple shape: client.chat(system, user) -> LLMReply

  - OllamaClient        : local model through Ollama (the default)
  - OpenAICompatClient  : online APIs that use the common "OpenAI style"
                          (for example Groq or OpenRouter). Only the web
                          address, the key and the model name change.

The rest of the app only uses create_client(settings), so it never needs to
know which one is behind it.

Needs one library:   pip install httpx

To test it on its own (from the project folder, with the .venv active):
    python engine/llm.py                 (uses qwen2.5:7b)
    python engine/llm.py phi3            (uses another installed model)
"""

import sys
import time
from dataclasses import dataclass

import httpx


# ---------------------------------------------------------------------------
# Errors, settings and replies
# ---------------------------------------------------------------------------

class LLMError(Exception):
    """A problem with the model, written so a normal person can understand it."""

    def __init__(self, message, status_code=None):
        super().__init__(message)
        self.status_code = status_code


@dataclass
class LLMSettings:
    provider: str = "ollama"                  # "ollama" (local) or "api" (online)
    base_url: str = "http://localhost:11434"  # for "api", example: https://api.groq.com/openai/v1
    model: str = "qwen2.5:7b"
    api_key: str = ""                         # only for "api" (never write a real key in code)
    temperature: float = 0.2                  # low = more careful, less creative
    num_ctx: int = 4096                       # memory window of the local model (Ollama only)
    timeout_seconds: float = 300.0            # the first call also has to load the model
    keep_alive: str = "10m"                   # how long Ollama keeps the model loaded


@dataclass
class LLMReply:
    text: str
    seconds: float                            # total time of the call
    load_seconds: float | None = None         # time to load the model (Ollama only)
    prompt_tokens: int | None = None
    output_tokens: int | None = None
    tokens_per_second: float | None = None
    prompt_tokens_per_second: float | None = None   # how fast the model reads the question


# ---------------------------------------------------------------------------
# Talking to a web service (shared by both connectors)
# ---------------------------------------------------------------------------

def _error_text(service, response):
    try:
        body = response.json()
        detail = body.get("error") if isinstance(body, dict) else None
        if isinstance(detail, dict):
            detail = detail.get("message")
        detail = detail or response.text[:200]
    except ValueError:
        detail = response.text[:200]
    return f"{service} replied with an error (HTTP {response.status_code}): {detail}"


def _http_request(method, url, service, timeout, **kwargs):
    """Send one request and return the answer as a dict.
    Every problem becomes an LLMError with a friendly message."""
    try:
        with httpx.Client(timeout=httpx.Timeout(timeout, connect=10.0)) as client:
            response = client.request(method, url, **kwargs)
    except httpx.ConnectError:
        raise LLMError(
            f"Cannot reach {service} at {url}. Is it running? "
            f"(For Ollama: start the Ollama app and try again.)"
        ) from None
    except httpx.TimeoutException:
        raise LLMError(
            f"{service} did not answer within {int(timeout)} seconds. "
            f"The model may be too slow for this request."
        ) from None
    except httpx.HTTPError as error:
        raise LLMError(f"Network problem while talking to {service}: {error}") from None

    if response.status_code >= 400:
        raise LLMError(_error_text(service, response), status_code=response.status_code)

    try:
        return response.json()
    except ValueError:
        raise LLMError(f"{service} sent an answer that is not valid JSON.") from None


def _messages(system, user):
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": user})
    return messages


# ---------------------------------------------------------------------------
# The base shape
# ---------------------------------------------------------------------------

class LLMClient:
    def __init__(self, settings):
        self.settings = settings

    def check(self):
        """Return a short success message, or raise LLMError if not usable."""
        raise NotImplementedError

    def chat(self, system, user, json_mode=False):
        """Send a question, return an LLMReply."""
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Connector 1: local Ollama
# ---------------------------------------------------------------------------

def _model_installed(model, installed_names):
    wanted = model if ":" in model else model + ":latest"
    return wanted in installed_names


class OllamaClient(LLMClient):
    def _url(self, path):
        return self.settings.base_url.rstrip("/") + path

    def check(self):
        data = _http_request(
            "GET", self._url("/api/tags"), "Ollama", self.settings.timeout_seconds
        )
        names = [m.get("name", "") for m in data.get("models", [])]
        if not _model_installed(self.settings.model, names):
            installed = ", ".join(names) or "none"
            raise LLMError(
                f"Ollama is running, but the model '{self.settings.model}' is not installed. "
                f"Installed models: {installed}. "
                f"To install it, run:  ollama pull {self.settings.model}"
            )
        return f"Ollama is running and the model '{self.settings.model}' is installed."

    def chat(self, system, user, json_mode=False):
        s = self.settings
        payload = {
            "model": s.model,
            "messages": _messages(system, user),
            "stream": False,
            "options": {"temperature": s.temperature, "num_ctx": s.num_ctx},
            "keep_alive": s.keep_alive,
        }
        if json_mode:
            payload["format"] = "json"

        started = time.perf_counter()
        try:
            data = _http_request(
                "POST", self._url("/api/chat"), "Ollama", s.timeout_seconds, json=payload
            )
        except LLMError as error:
            if error.status_code == 404:
                raise LLMError(
                    f"The model '{s.model}' is not installed in Ollama. "
                    f"To install it, run:  ollama pull {s.model}",
                    status_code=404,
                ) from None
            raise
        seconds = time.perf_counter() - started

        text = (data.get("message") or {}).get("content", "")
        if not text.strip():
            raise LLMError("The model sent back an empty answer.")

        output_tokens = data.get("eval_count")
        eval_duration = data.get("eval_duration")       # in nanoseconds
        load_duration = data.get("load_duration")       # in nanoseconds

        speed = None
        if output_tokens and eval_duration:
            speed = output_tokens / (eval_duration / 1e9)

        prompt_tokens = data.get("prompt_eval_count")
        prompt_duration = data.get("prompt_eval_duration")   # in nanoseconds
        prompt_speed = None
        if prompt_tokens and prompt_duration:
            prompt_speed = prompt_tokens / (prompt_duration / 1e9)

        return LLMReply(
            text=text.strip(),
            seconds=seconds,
            load_seconds=(load_duration / 1e9) if load_duration else None,
            prompt_tokens=prompt_tokens,
            output_tokens=output_tokens,
            tokens_per_second=speed,
            prompt_tokens_per_second=prompt_speed,
        )


# ---------------------------------------------------------------------------
# Connector 2: online APIs in the "OpenAI style"
# ---------------------------------------------------------------------------

class OpenAICompatClient(LLMClient):
    def _url(self, path):
        return self.settings.base_url.rstrip("/") + path

    def _headers(self):
        headers = {"Content-Type": "application/json"}
        if self.settings.api_key:
            headers["Authorization"] = f"Bearer {self.settings.api_key}"
        return headers

    def check(self):
        if not self.settings.api_key:
            raise LLMError("No API key is set. API mode needs a key from your provider.")
        try:
            _http_request(
                "GET", self._url("/models"), "The API",
                self.settings.timeout_seconds, headers=self._headers(),
            )
        except LLMError as error:
            if error.status_code in (401, 403):
                raise LLMError("The API key was rejected. Please check the key.") from None
            if error.status_code == 404:
                # Some providers have no model list. We can only say it is reachable.
                return "The API is reachable (it has no model list to check)."
            raise
        return f"The API is reachable and the key was accepted. Model to use: '{self.settings.model}'."

    def chat(self, system, user, json_mode=False):
        s = self.settings
        payload = {
            "model": s.model,
            "messages": _messages(system, user),
            "temperature": s.temperature,
            "stream": False,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        started = time.perf_counter()
        data = _http_request(
            "POST", self._url("/chat/completions"), "The API",
            s.timeout_seconds, headers=self._headers(), json=payload,
        )
        seconds = time.perf_counter() - started

        try:
            text = data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError):
            raise LLMError("The API sent an answer in a format we do not understand.") from None
        if not text.strip():
            raise LLMError("The model sent back an empty answer.")

        usage = data.get("usage") or {}
        return LLMReply(
            text=text.strip(),
            seconds=seconds,
            prompt_tokens=usage.get("prompt_tokens"),
            output_tokens=usage.get("completion_tokens"),
        )


# ---------------------------------------------------------------------------
# The one function the rest of the app uses
# ---------------------------------------------------------------------------

def create_client(settings):
    if settings.provider == "ollama":
        return OllamaClient(settings)
    if settings.provider == "api":
        return OpenAICompatClient(settings)
    raise LLMError(f"Unknown provider '{settings.provider}'. Use 'ollama' or 'api'.")


# ---------------------------------------------------------------------------
# Test from the terminal
# ---------------------------------------------------------------------------

def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")

    settings = LLMSettings()
    if len(sys.argv) > 1:
        settings.model = sys.argv[1]

    try:
        client = create_client(settings)

        print(f"Provider: {settings.provider}   Model: {settings.model}")
        print("Checking the connection...")
        print(client.check())
        print()

        print("Asking the model one question.")
        print("(The first call can take a while, because the model must load into memory.)")
        reply = client.chat(
            system="You are a helpful assistant. Answer in 2 short sentences, in simple words.",
            user="What is a Software Requirements Specification (SRS) document?",
        )
    except LLMError as error:
        print(f"ERROR: {error}")
        sys.exit(1)

    print()
    print("Answer:")
    print(reply.text)
    print()
    print(f"Time in total : {reply.seconds:.1f} s")
    if reply.load_seconds is not None:
        print(f"Model loading : {reply.load_seconds:.1f} s")
    if reply.prompt_tokens is not None:
        print(f"Question size : {reply.prompt_tokens} tokens")
    if reply.prompt_tokens_per_second is not None:
        print(f"Question speed: {reply.prompt_tokens_per_second:.1f} tokens per second")
    if reply.output_tokens is not None:
        print(f"Answer size   : {reply.output_tokens} tokens")
    if reply.tokens_per_second is not None:
        print(f"Speed         : {reply.tokens_per_second:.1f} tokens per second")


if __name__ == "__main__":
    main()