import importlib.util
from pathlib import Path
from types import SimpleNamespace

from src.agent import api


SCRIPT_PATH = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "test"
    / "eval_unit_actions_from_dataset.py"
)
SPEC = importlib.util.spec_from_file_location("eval_unit_actions_from_dataset", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_action_only_prompt_replaces_legacy_reasoned_contract() -> None:
    prompt = (
        "Inspect the images and metrics.\n\n"
        'Return only final answer JSON (no hidden reasoning): '
        '{"action":"KEEP|DISCARD|SPLIT","rationale":"brief rationale"}'
    )

    result = MODULE._action_only_prompt(prompt, "split", ["KEEP", "DISCARD", "SPLIT"])

    assert "brief rationale" not in result
    assert "action-only-json-v2" in result
    assert '{"action":"ACTION"}' in result
    assert "additional keys" in result


def test_action_only_schema_is_strict_and_default() -> None:
    assert MODULE.SPLIT_ACTION_ONLY_SCHEMA["additionalProperties"] is False
    assert MODULE.MERGE_ACTION_ONLY_SCHEMA["additionalProperties"] is False
    args = MODULE._build_parser().parse_args(["--model", "example"])
    assert args.use_response_schema is True
    legacy_args = MODULE._build_parser().parse_args(
        ["--model", "example", "--no-response-schema"]
    )
    assert legacy_args.use_response_schema is False


def test_vllm_uses_standard_json_schema_response_format(monkeypatch) -> None:
    captured = {}

    class Completions:
        def create(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(
                model="example",
                choices=[SimpleNamespace(message=SimpleNamespace(content='{"action":"SPLIT"}'))],
                usage=None,
            )

    client = SimpleNamespace(chat=SimpleNamespace(completions=Completions()))
    monkeypatch.setattr(api, "_record_call_meta", lambda metadata: None)

    response = api._call_vision_model(
        client=client,
        prompt="Choose an action.",
        images=[],
        model="Qwen/Qwen3.5-4B",
        max_tokens=32,
        temperature=0.0,
        provider_name="vllm",
        response_schema=MODULE.SPLIT_ACTION_ONLY_SCHEMA,
        extra_body={"chat_template_kwargs": {"enable_thinking": False}},
    )

    assert response == '{"action":"SPLIT"}'
    assert captured["response_format"]["type"] == "json_schema"
    assert captured["response_format"]["json_schema"]["strict"] is True
    assert captured["response_format"]["json_schema"]["schema"]["additionalProperties"] is False
    assert "guided_json" not in captured["extra_body"]
