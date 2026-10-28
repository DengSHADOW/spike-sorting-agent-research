"""Provider for a local vLLM server (OpenAI-compatible /v1/chat/completions). No client on import.

A provider only translates and transports: it turns API-neutral request content
(instructions, text, PNG bytes, JSON schema) into a chat-completions body, sends it, checks the
response and returns the parsed JSON answer. It knows nothing about curation. Accounting is in
tokens and seconds; there are no prices. No fallback, no substitute answers; a truncated
response is retried once with the identical body, anything else stops.
"""
from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
import threading
from time import monotonic
import urllib.request

from src.pipeline.curation_controller_v2 import LimitStop
from src.pipeline.curation_engineering_v1 import digest, write_json

SAMPLING_KEYS = {"temperature", "top_p", "top_k", "min_p", "presence_penalty", "repetition_penalty"}


class ProviderStop(RuntimeError):
    pass


def http_json(base_url, timeout):
    def send(method, path, body=None):
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(base_url.rstrip("/") + path, data=data, method=method,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    return send


class VllmProvider:
    kind = "VLLM_OPENAI_CHAT_NO_FALLBACK"

    def __init__(self, profile, ledger, *, base_url="http://127.0.0.1:8000", timeout=1800,
                 max_total_tokens=None, transport=None):
        if not set(profile["sampling"]) <= SAMPLING_KEYS or type(profile["max_tokens"]) is not int:
            raise ValueError("Unexpected sampling keys or output limit")
        self.profile, self.ledger = profile, Path(ledger)
        self.send = transport or http_json(base_url, timeout)
        self.max_total_tokens = max_total_tokens
        self.calls, self.truncations = 0, 0
        self.tokens = dict(prompt_tokens=0, completion_tokens=0)
        self.seconds = 0.
        self.lock = threading.Lock()  # the controller may call respond() from several threads

    def check(self):
        """The served model must be the profile's; returns what the server reports, for the manifest."""
        served = [m["id"] for m in self.send("GET", "/v1/models")["data"]]
        if served != [self.profile["served_name"]]:
            raise ProviderStop(f"Server does not serve exactly {self.profile['served_name']}")
        return dict(served_models=served, version=self.send("GET", "/version").get("version"))

    def body(self, content):
        images = [{"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(p).decode()}}
                  for p in content["images"]]
        if content.get("images_first"):  # harness v2: question and image list, images, then measurements
            user = [{"type": "text", "text": content["preface"]}, *images, {"type": "text", "text": content["text"]}]
        else:
            user = [{"type": "text", "text": content["text"]}, *images]
        b = dict(model=self.profile["served_name"], max_tokens=self.profile["max_tokens"], n=1, stream=False,
                 messages=[{"role": "system", "content": content["instructions"]}, {"role": "user", "content": user}],
                 response_format={"type": "json_schema", "json_schema": {
                     "name": content["schema_name"], "strict": True, "schema": content["schema"]}},
                 chat_template_kwargs=self.profile["chat_template_kwargs"], **self.profile["sampling"])
        # Deterministic per request so a replayed or repeated run sees the same sample on the same server.
        b["seed"] = int(content_digest(content)[:8], 16)
        return b

    def respond(self, content, *, folder):
        with self.lock:
            if self.max_total_tokens is not None and sum(self.tokens.values()) >= self.max_total_tokens:
                raise LimitStop("Token budget reached")
        body = self.body(content)
        for attempt in (1, 2):
            started = monotonic()
            raw = self.send("POST", "/v1/chat/completions", body)
            seconds = monotonic() - started
            write_json(Path(folder) / f"response_{attempt}.json", raw)
            usage = raw.get("usage") or {}
            with self.lock:
                self.calls += 1
                self.seconds += seconds
                for key in self.tokens:
                    self.tokens[key] += int(usage.get(key) or 0)
            choice = (raw.get("choices") or [{}])[0]
            finish = choice.get("finish_reason")
            self.log(dict(folder=Path(folder).name, attempt=attempt, request_sha256=content_digest(content),
                          seed=body["seed"], finish_reason=finish, seconds=seconds,
                          prompt_tokens=usage.get("prompt_tokens"), completion_tokens=usage.get("completion_tokens")))
            if raw.get("model") != self.profile["served_name"]:
                raise ProviderStop("Unexpected response model")
            if finish == "length" and attempt == 1:
                with self.lock:
                    self.truncations += 1
                continue
            if finish != "stop":
                raise ProviderStop(f"Response not finished cleanly: {finish}")
            try:
                return json.loads((choice.get("message") or {}).get("content") or "")
            except ValueError as exc:
                raise ProviderStop("Answer is not JSON") from exc
        raise ProviderStop("Truncated twice")

    def log(self, row):
        with self.lock, self.ledger.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, sort_keys=True) + "\n")

    def stats(self):
        return dict(calls=self.calls, truncations=self.truncations, seconds=self.seconds, **self.tokens)


def content_digest(content):
    return digest({k: v for k, v in content.items() if k != "images"}
                  | {"images": [hashlib.sha256(p).hexdigest() for p in content["images"]]})
