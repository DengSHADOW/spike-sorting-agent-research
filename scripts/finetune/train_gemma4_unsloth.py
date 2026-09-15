"""
Vision SFT for Gemma-4-E4B with Unsloth.

Defaults are aligned with the real-data adaptation plan:
- action-only JSON is the first target
- train/eval are split by recording block; the CH3/CH31 block remains test-only
- multimodal messages use fixed image/text ordering
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List, Sequence

from PIL import Image


SPLIT_IMAGE_KEYS: Sequence[str] = (
    "waveform_overlay",
    "isi_histogram",
    "amplitude_distribution",
    "aggregation_tree",
)
MERGE_IMAGE_KEYS: Sequence[str] = (
    "small_waveform_overlay",
    "large_waveform_overlay",
    "merged_isi_histogram",
)


def _read_jsonl(path: Path, max_rows: int = 0) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    with open(path, "r") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
            if max_rows > 0 and len(rows) >= max_rows:
                break
    return rows


def _parse_values(raw: str) -> List[str]:
    return [item.strip() for item in raw.split(",") if item.strip()]


def _resolve_image_path(dataset_root: Path, rel_or_abs: str) -> Path:
    p = Path(rel_or_abs)
    if p.is_absolute():
        return p
    return (dataset_root / p).resolve()


def _row_images(row: Dict[str, Any], dataset_root: Path) -> List[Image.Image]:
    stage = row.get("stage")
    images = row.get("images", {}) or {}
    keys = SPLIT_IMAGE_KEYS if stage == "split" else MERGE_IMAGE_KEYS

    loaded: List[Image.Image] = []
    for key in keys:
        rel = images.get(key)
        if not rel:
            continue
        path = _resolve_image_path(dataset_root, rel)
        if not path.exists():
            continue
        loaded.append(Image.open(path).convert("RGB"))
    return loaded


def _assistant_target(row: Dict[str, Any], target_mode: str) -> str:
    action = str(row.get("label_action", "")).strip().upper()
    rationale = str(row.get("label_reason", "")).strip()
    if target_mode == "action_json":
        return json.dumps({"action": action}, ensure_ascii=False)
    if target_mode == "original":
        t = str(row.get("target", "")).strip()
        if t:
            return t
    if target_mode == "action_label":
        return action
    # reasoned_json default
    if not rationale:
        return json.dumps({"action": action}, ensure_ascii=False)
    return json.dumps({"action": action, "rationale": rationale}, ensure_ascii=False)


def _to_messages(
    row: Dict[str, Any],
    dataset_root: Path,
    target_mode: str,
    image_order: str,
) -> Dict[str, Any]:
    prompt = str(row["prompt"])
    if target_mode == "action_json":
        prompt = prompt.split("\nReturn only final answer JSON", 1)[0].rstrip()
        allowed = "|".join(str(item) for item in row.get("allowed_actions", []))
        prompt += (
            "\n\nReturn one JSON object only, with no explanation or extra keys: "
            f'{{"action":"{allowed}"}}'
        )
    images = _row_images(row, dataset_root=dataset_root)
    if not images:
        raise ValueError(f"No images found for sample {row.get('id')}")

    user_content: List[Dict[str, Any]] = []
    if image_order == "image_first":
        for img in images:
            user_content.append({"type": "image", "image": img})
        user_content.append({"type": "text", "text": prompt})
    else:
        user_content.append({"type": "text", "text": prompt})
        for img in images:
            user_content.append({"type": "image", "image": img})

    return {
        "id": row.get("id"),
        "channel": row.get("channel"),
        "stage": row.get("stage"),
        "label_action": row.get("label_action"),
        "profile": row.get("profile"),
        "messages": [
            {"role": "user", "content": user_content},
            {
                "role": "assistant",
                "content": [{"type": "text", "text": _assistant_target(row, target_mode=target_mode)}],
            },
        ],
    }


def _distribution(rows: List[Dict[str, Any]]) -> Dict[str, Dict[str, int]]:
    by_channel: Dict[str, int] = {}
    by_action: Dict[str, int] = {}
    by_stage: Dict[str, int] = {}
    for row in rows:
        c = str(row.get("channel", ""))
        a = str(row.get("label_action", ""))
        s = str(row.get("stage", ""))
        by_channel[c] = by_channel.get(c, 0) + 1
        by_action[a] = by_action.get(a, 0) + 1
        by_stage[s] = by_stage.get(s, 0) + 1
    return {"by_channel": by_channel, "by_action": by_action, "by_stage": by_stage}


def main() -> None:
    parser = argparse.ArgumentParser(description="Train Gemma-4-E4B (Unsloth vision SFT).")
    parser.add_argument("--input-jsonl", default="output/real_manifest_action_dataset_20260914/samples.jsonl")
    parser.add_argument("--dataset-root", default="output/real_manifest_action_dataset_20260914")
    parser.add_argument("--model-name", default="unsloth/gemma-4-E4B-it")
    parser.add_argument("--output-dir", default="output/finetune_gemma4_e4b_lora")
    parser.add_argument("--target-mode", default="action_json", choices=["action_json", "reasoned_json", "action_label", "original"])
    parser.add_argument(
        "--train-recording-blocks",
        default="cM2-e004_001-003,cM2-e004_011-015,cM2-e007_012-017",
    )
    parser.add_argument("--eval-recording-blocks", default="cM2-e008_021-028")
    parser.add_argument("--image-order", default="text_first", choices=["image_first", "text_first"])
    parser.add_argument("--max-rows", type=int, default=0)
    parser.add_argument("--load-in-4bit", action="store_true", default=False)
    parser.add_argument("--max-seq-length", type=int, default=0, help="0 disables VLM token truncation")
    parser.add_argument("--per-device-batch-size", type=int, default=1)
    parser.add_argument("--grad-accum-steps", type=int, default=4)
    parser.add_argument("--learning-rate", type=float, default=2e-4)
    parser.add_argument("--max-steps", type=int, default=200)
    parser.add_argument("--lora-r", type=int, default=16)
    parser.add_argument("--lora-alpha", type=int, default=16)
    parser.add_argument("--seed", type=int, default=3407)
    args = parser.parse_args()

    try:
        from unsloth import FastVisionModel
        from unsloth.trainer import UnslothVisionDataCollator
        from trl import SFTConfig, SFTTrainer
    except Exception as e:
        raise RuntimeError("Missing dependencies for Gemma4 Unsloth training.") from e

    input_jsonl = Path(args.input_jsonl)
    dataset_root = Path(args.dataset_root)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    if not input_jsonl.exists():
        raise FileNotFoundError(f"Input JSONL not found: {input_jsonl}")
    if not dataset_root.exists():
        raise FileNotFoundError(f"Dataset root not found: {dataset_root}")

    train_blocks = _parse_values(args.train_recording_blocks)
    eval_blocks = _parse_values(args.eval_recording_blocks)
    overlap = set(train_blocks) & set(eval_blocks)
    if overlap:
        raise ValueError(f"Recording-block leakage between train/eval: {sorted(overlap)}")
    rows = _read_jsonl(input_jsonl, max_rows=args.max_rows)
    train_rows = [row for row in rows if row.get("recording_block") in set(train_blocks)]
    eval_rows = [row for row in rows if row.get("recording_block") in set(eval_blocks)]
    if not train_rows:
        raise ValueError("No rows in training JSONL.")

    train_dataset = [
        _to_messages(
            row,
            dataset_root=dataset_root,
            target_mode=args.target_mode,
            image_order=args.image_order,
        )
        for row in train_rows
    ]
    eval_dataset = [
        _to_messages(
            row,
            dataset_root=dataset_root,
            target_mode=args.target_mode,
            image_order=args.image_order,
        )
        for row in eval_rows
    ] if eval_rows else None

    model, tokenizer = FastVisionModel.from_pretrained(
        args.model_name,
        load_in_4bit=args.load_in_4bit,
        use_gradient_checkpointing="unsloth",
    )
    model = FastVisionModel.get_peft_model(
        model,
        finetune_vision_layers=True,
        finetune_language_layers=True,
        finetune_attention_modules=True,
        finetune_mlp_modules=True,
        r=args.lora_r,
        lora_alpha=args.lora_alpha,
        lora_dropout=0,
        bias="none",
        random_state=args.seed,
    )
    FastVisionModel.for_training(model)

    trainer = SFTTrainer(
        model=model,
        tokenizer=tokenizer,
        data_collator=UnslothVisionDataCollator(model, tokenizer),
        train_dataset=train_dataset,
        eval_dataset=eval_dataset,
        args=SFTConfig(
            output_dir=str(output_dir / "checkpoints"),
            per_device_train_batch_size=args.per_device_batch_size,
            gradient_accumulation_steps=args.grad_accum_steps,
            warmup_steps=5,
            max_steps=args.max_steps,
            learning_rate=args.learning_rate,
            logging_steps=1,
            optim="adamw_8bit",
            weight_decay=0.001,
            lr_scheduler_type="linear",
            seed=args.seed,
            report_to="none",
            remove_unused_columns=False,
            dataset_text_field="",
            dataset_kwargs={"skip_prepare_dataset": True},
            max_length=None if args.max_seq_length <= 0 else args.max_seq_length,
        ),
    )
    stats = trainer.train()

    adapter_dir = output_dir / "adapter"
    model.save_pretrained(str(adapter_dir))
    tokenizer.save_pretrained(str(adapter_dir))

    merged_dir = output_dir / "merged_16bit"
    try:
        model.save_pretrained_merged(str(merged_dir), tokenizer, save_method="merged_16bit")
    except Exception as e:
        print(f"[Warn] merged_16bit export failed: {e}")

    summary = {
        "model_name": args.model_name,
        "input_jsonl": str(input_jsonl),
        "train_recording_blocks": train_blocks,
        "eval_recording_blocks": eval_blocks,
        "dataset_root": str(dataset_root),
        "target_mode": args.target_mode,
        "image_order": args.image_order,
        "load_in_4bit": args.load_in_4bit,
        "n_train": len(train_dataset),
        "n_eval": len(eval_dataset) if eval_dataset is not None else 0,
        "train_distribution": _distribution(train_rows),
        "eval_distribution": _distribution(eval_rows),
        "max_steps": args.max_steps,
        "learning_rate": args.learning_rate,
        "lora_r": args.lora_r,
        "lora_alpha": args.lora_alpha,
        "train_runtime_s": stats.metrics.get("train_runtime"),
    }
    with open(output_dir / "train_summary.json", "w") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)

    print(f"Saved adapter: {adapter_dir}")
    print(f"Saved summary: {output_dir / 'train_summary.json'}")


if __name__ == "__main__":
    main()
