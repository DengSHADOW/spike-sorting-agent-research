"""Explicit hosted Chat Completions profile; credentials are read only from an environment variable.

No fallback, model discovery, implicit retries or import-time network calls. The call cap includes
failed requests and is shared by parallel workers. Token/cost fields are provider-reported, not estimates.
"""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import threading
from time import monotonic
import urllib.error
import urllib.request

from src.agent.vllm_provider import ProviderStop, content_digest
from src.pipeline.curation_controller_v2 import LimitStop
from src.pipeline.curation_engineering_v1 import write_json


class HostedProvider:
    kind = "HOSTED_OPENAI_CHAT_NO_FALLBACK"

    def __init__(self, profile, ledger, *, max_calls, transport=None):
        required = {"base_url", "model", "api_key_env", "max_tokens", "temperature", "response_format"}
        if not required <= set(profile) or set(profile) - required - {"extra_body", "timeout"}:
            raise ValueError("Unexpected hosted profile fields")
        if (type(max_calls) is not int or max_calls < 1 or type(profile["max_tokens"]) is not int
                or profile["max_tokens"] < 1 or profile["response_format"] not in ("json_schema", "json_object")
                or not profile["base_url"].startswith("https://")):
            raise ValueError("Invalid hosted profile or call cap")
        if set(profile.get("extra_body", {})) & {"model", "messages", "response_format", "stream", "n", "max_tokens"}:
            raise ValueError("extra_body cannot override the request contract")
        self.profile, self.ledger, self.max_calls = profile, Path(ledger), max_calls
        self.send = transport or self._send
        self.lock, self.calls, self.rows = threading.Lock(), 0, []

    def describe(self):
        return dict(kind=self.kind, profile=self.profile, max_calls=self.max_calls)

    def _send(self, body):
        key = os.environ.get(self.profile["api_key_env"])
        if not key:
            raise ProviderStop(f"Missing environment variable {self.profile['api_key_env']}")
        req = urllib.request.Request(self.profile["base_url"].rstrip("/") + "/chat/completions",
            data=json.dumps(body).encode(), headers={"Content-Type": "application/json",
                                                     "Authorization": "Bearer " + key})
        try:
            with urllib.request.urlopen(req, timeout=self.profile.get("timeout", 600)) as response:
                return json.loads(response.read())
        except urllib.error.HTTPError as exc:
            raise ProviderStop(f"Hosted HTTP status {exc.code}; no automatic retry") from None
        except urllib.error.URLError:
            raise ProviderStop("Hosted connection failed; no automatic retry") from None

    def body(self, content):
        images = [{"type": "image_url", "image_url": {"url": "data:image/png;base64," +
                    base64.b64encode(p).decode()}} for p in content["images"]]
        user = [{"type": "text", "text": content.get("preface", "")}, *images,
                {"type": "text", "text": content["text"]}]
        schema = content["schema"]
        response_format = {"type": "json_object"}
        if self.profile["response_format"] == "json_schema":
            response_format = {"type": "json_schema", "json_schema": dict(name=content["schema_name"],
                                                                          strict=True, schema=schema)}
        return dict(model=self.profile["model"], max_tokens=self.profile["max_tokens"],
                    temperature=self.profile["temperature"], stream=False, n=1,
                    messages=[{"role": "system", "content": content["instructions"]},
                              {"role": "user", "content": user}], response_format=response_format,
                    **self.profile.get("extra_body", {}))

    def respond(self, content, *, folder):
        folder = Path(folder)
        body = self.body(content)
        with self.lock:
            if self.calls >= self.max_calls:
                raise LimitStop("Hosted request cap reached")
            self.calls += 1
        started = monotonic()
        row = dict(request_sha256=content_digest(content), requested_model=self.profile["model"])
        try:
            raw = self.send(body)
            write_json(folder / "response_1.json", raw)
            row.update(actual_model=raw.get("model"), provider=raw.get("provider"), usage=raw.get("usage"))
            choice = (raw.get("choices") or [{}])[0]
            row["finish_reason"] = choice.get("finish_reason")
            if choice.get("finish_reason") != "stop":
                raise ProviderStop("Hosted response did not finish cleanly")
            try:
                return json.loads(choice["message"]["content"])
            except (KeyError, TypeError, ValueError):
                raise ProviderStop("Hosted answer is not JSON") from None
        except Exception as exc:
            row["error_type"] = type(exc).__name__
            raise
        finally:
            row["seconds"] = monotonic() - started
            with self.lock:
                self.rows.append(row)
                with self.ledger.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(row, sort_keys=True) + "\n")

    def stats(self):
        with self.lock:
            return dict(calls=self.calls, failed_calls=sum("error_type" in r for r in self.rows),
                        usage=[r.get("usage") for r in self.rows],
                        seconds=sum(r["seconds"] for r in self.rows))
