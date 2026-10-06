"""Collect the isolated upstream baseline, including empty and failed runs.

Only completed channels have endpoint metrics. An incomplete batch never gets
a four-channel mean. All inputs/images/MATs remain under ignored output/.
"""

import argparse
import csv
import json
from datetime import datetime
from pathlib import Path


def collect(root, repo):
    protocol = json.loads((root / "protocol.json").read_text())
    rows = []
    for ch in protocol["channels"]:
        p = root / ch / "status.json"
        status = json.loads(p.read_text()) if p.exists() else {"status": "pending"}
        perf = status.get("performance", {}) if status["status"] == "complete" else {}
        historical = json.loads((repo / "output/main_gpt-5.1" / ch / "evaluation_report.json").read_text())["overall_performance"]
        requests_path = root / ch / "requests.jsonl"
        requests = [json.loads(s) for s in requests_path.read_text().splitlines()] if requests_path.exists() else []
        decisions_path = root / ch / "decisions.jsonl"
        decisions = [json.loads(s) for s in decisions_path.read_text().splitlines()] if decisions_path.exists() else []
        if status["status"] == "complete":
            assert (root / ch / "final_assigns.npy").exists()
            assert len(requests) == status["api_calls"]
            assert len(decisions) == status["accepted_decisions"]
        elapsed = None
        if status.get("updated_at") and status.get("started_at"):
            elapsed = (datetime.fromisoformat(status["updated_at"]) - datetime.fromisoformat(status["started_at"])).total_seconds()
        rows.append({
            "channel": ch, "status": status["status"], "api_calls": status.get("api_calls", 0),
            "decisions": status.get("accepted_decisions", 0), "units": status.get("final_units"),
            "assigned_spikes": status.get("assigned_spikes"), "precision": perf.get("overall_precision"),
            "recall": perf.get("overall_recall"), "f1": perf.get("overall_f1_score"),
            "tp": perf.get("total_tp"), "fp": perf.get("total_fp"), "fn": perf.get("total_fn"),
            "historical_f1": historical["overall_f1_score"],
            "input_tokens": status.get("input_tokens", 0), "output_tokens": status.get("output_tokens", 0),
            "cached_input_tokens": status.get("cached_input_tokens", 0),
            "incomplete_responses": status.get("incomplete_responses", 0), "elapsed_seconds": elapsed,
            "actual_models": ";".join(status.get("actual_models", [])),
            "error": status.get("error", ""),
        })
    complete = all(r["status"] == "complete" for r in rows)
    macro = {k: sum(r[k] for r in rows) / len(rows) for k in ("precision", "recall", "f1")} if complete else None
    totals = {k: sum(r[k] for r in rows) for k in ("api_calls", "decisions", "input_tokens", "output_tokens", "cached_input_tokens", "incomplete_responses")}
    result = {"upstream_commit": protocol["upstream_commit"], "all_channels_complete": complete,
              "max_output_tokens_per_request": protocol.get("max_output_tokens_per_request", 1000),
              "protocol_variant": protocol.get("protocol_variant", "upstream-original-budget"),
              "macro_mean": macro, "totals": totals, "channels": rows}
    (root / "collected_metrics.json").write_text(json.dumps(result, indent=2) + "\n")
    with (root / "collected_metrics.csv").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    def val(x):
        if x is None: return "—"
        if isinstance(x, float): return f"{x:.4f}"
        return str(x)
    lines = [
        "# Pinned upstream GPT-5.1 full rollout — 2026-09-28", "",
        f"- Upstream commit: `{protocol['upstream_commit']}`.",
        f"- GPT-5.1, reasoning medium; strict upstream action+rationale schema; {protocol.get('max_output_tokens_per_request', 1000):,} output tokens/request; no total call cap.",
        f"- Protocol variant: {protocol.get('protocol_variant', 'upstream-original-budget')}.",
        "- Three upstream views per phase, random 5,000-waveform cap, NumPy seed 0 reset per channel.",
        "- Upstream controller and 500/4,000/5,000 thresholds; no deletion-protection overrides, no RAG or SFT.",
        "- Local environment dependencies are in protocol.json; historical software environment is not claimed identical.",
        "- Provider/mock or exhausted parse failures stop the experiment. Successful actions follow upstream semantics.",
        "- Metrics compare final spike assignments to MAT curation.assigns, using the upstream evaluator; empty endpoints count as zero.",
        "- Historical F1 is a saved-result reference, not a fresh API run or controlled prompt-only comparison.", "",
        "| Channel | Status | Calls | Units | Spikes | Precision | Recall | F1 | Historical F1 |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in rows:
        lines.append("| " + " | ".join(val(r[k]) for k in ("channel", "status", "api_calls", "units", "assigned_spikes", "precision", "recall", "f1", "historical_f1")) + " |")
    lines.extend(["", "Four-channel macro mean: " + (json.dumps(macro) if complete else "not available; batch incomplete."),
                  "", "Totals: " + json.dumps(totals), "",
                  "Totals count rollout calls only; any separate saved-input budget check is recorded in budget_check/.", "",
                  "Artifacts: protocol.json, immutable upstream/src/, per-channel requests.jsonl, responses.jsonl, decisions.jsonl, states/, vlm_inputs/, final_assigns.npy, and evaluation_report.json.", ""])
    (root / "REPORT.md").write_text("\n".join(lines))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output_root", type=Path)
    args = parser.parse_args()
    collect(args.output_root.resolve(), Path(__file__).resolve().parents[2])
