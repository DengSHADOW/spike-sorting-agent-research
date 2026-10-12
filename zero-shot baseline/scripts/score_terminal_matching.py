"""Read-only terminal rescoring of a completed minimal-prompt run with the project's scoring cores.

Never calls a model. Bindings come only from the scored run itself:
  - run lineage: the run directory holding score.json, then each manifest's "source" back to the root
    run whose manifest registers the datasets; nothing outside this chain is read;
  - MAT: the root manifest's mat_path / mat_sha256 for that dataset (hash verified);
  - terminal: terminal.npz inside the lineage whose arrays_hash(assigns, tree) equals the recorded
    final_sha256 (identical hashes mean identical arrays, so the first hit is used).
Scores (same functions as scripts/run/run_openrouter_baseline.py):
  - PRIMARY one-to-one: scripts/analysis/curation_one_to_one.evaluate (pre-registered rules
    configs/curation/one_to_one_scoring_v1.json): full-output P/R/F1 over ALL predicted spikes,
    every expert unit (unmatched = 0), unmatched-cluster and pure-noise counts;
  - SUPPLEMENTARY many-to-one: scripts/analysis/collect_curation_stage6.evaluate (historical
    evaluator; must reproduce the run's own recorded metrics). Not comparable with one-to-one.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.analysis import curation_one_to_one  # noqa: E402
from src.io.matlab_loader import load_matlab_spikes  # noqa: E402
from src.pipeline.curation_engineering_v1 import arrays_hash  # noqa: E402


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def lineage(run_dir: Path) -> tuple[list[Path], dict]:
    """Run dir first, then each manifest 'source'; stops at the manifest that registers datasets."""
    chain, current = [], run_dir.resolve()
    while True:
        if current in chain:
            raise ValueError("Cyclic run lineage")
        chain.append(current)
        manifest = json.loads((current / "manifest.json").read_text())
        if isinstance(manifest.get("datasets"), list):
            return chain, manifest
        if not manifest.get("source"):
            raise ValueError(f"{current.name}: no datasets and no source manifest")
        current = Path(manifest["source"]).resolve()


def bind_mat(root_manifest: dict, dataset_id: str) -> tuple[Path, str]:
    rows = [d for d in root_manifest["datasets"] if d.get("dataset_id") == dataset_id]
    if len(rows) != 1:
        raise ValueError(f"{dataset_id}: expected one MAT registration in the root manifest, got {len(rows)}")
    path = ROOT / rows[0]["mat_path"]
    if sha256_file(path) != rows[0]["mat_sha256"]:
        raise ValueError(f"{dataset_id}: MAT SHA256 differs from the run's registration")
    return path, rows[0]["mat_sha256"]


def find_terminal(chain: list[Path], dataset_id: str, final_sha: str) -> Path:
    for run in chain:  # latest first
        path = run / "datasets" / dataset_id / "terminal.npz"
        if path.exists():
            with np.load(path) as z:
                if arrays_hash(z["assigns"], z["tree"]) == final_sha:
                    return path
    raise ValueError(f"{dataset_id}: no terminal with the recorded final_sha256 in this run's lineage")


def score_dataset(pred, init, gt, times, fs) -> dict:
    from scripts.analysis.collect_curation_stage6 import evaluate
    return dict(one_to_one=curation_one_to_one.evaluate(pred, gt),
                many_to_one=evaluate(pred, gt, times, fs),
                no_curation_one_to_one=curation_one_to_one.evaluate(init, gt),
                no_curation_many_to_one=evaluate(init, gt, times, fs))


def f4(x):
    return f"{x:.4f}"


def report_lines(title: str, results: list[dict]) -> list[str]:
    lines = [f"# {title}", "", "Read-only rescoring of saved terminal states; no model calls.",
             f"One-to-one rules: `configs/curation/one_to_one_scoring_v1.json` "
             f"(SHA256 `{results[0]['one_to_one']['scoring_sha256'] if results else ''}`).", "",
             "## Primary: one-to-one (IoU >= 0.5, Hungarian), full output", "",
             "Precision uses every predicted spike, including unmatched clusters.", "",
             "| Dataset | P | R | F1 | Expert units matched | Expert-unit macro accuracy | Predicted clusters | "
             "Unmatched clusters | Pure-noise clusters | Retained noise spikes | Discarded expert spikes | No-curation F1 |",
             "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for r in results:
        o, n = r["one_to_one"], r["no_curation_one_to_one"]
        lines.append(f"| {r['dataset_id']} | {f4(o['full_output']['precision'])} | {f4(o['full_output']['recall'])} | "
                     f"{f4(o['full_output']['f1'])} | {o['n_matched_units']} / {o['n_expert_units']} | "
                     f"{f4(o['expert_unit_macro']['accuracy'])} | {o['n_predicted_units']} | {o['n_unmatched_predicted_units']} | "
                     f"{o['n_pure_reference_noise_clusters']} | {o['retained_reference_noise_spikes']:,} | "
                     f"{o['discarded_expert_spikes']:,} | {f4(n['full_output']['f1'])} |")
    lines += ["", "Per expert unit (unmatched units score 0):", "",
              "| Dataset | Expert unit | Spikes | Matched cluster | IoU (accuracy) | Best candidate IoU | P | R | F1 |",
              "|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for r in results:
        for u in r["one_to_one"]["expert_units"]:
            lines.append(f"| {r['dataset_id']} | {u['reference_unit']} | {u['reference_spikes']:,} | "
                         f"{u['predicted_unit'] if u['matched'] else '—'} | {f4(u['accuracy'])} | {f4(u['best_candidate_iou'])} | "
                         f"{f4(u['precision'])} | {f4(u['recall'])} | {f4(u['f1'])} |")
    lines += ["", "## Supplementary: historical many-to-one (not comparable with one-to-one)", "",
              "| Dataset | Clusters | P | R | F1 | No-curation F1 |", "|---|---:|---:|---:|---:|---:|"]
    for r in results:
        m, n = r["many_to_one"], r["no_curation_many_to_one"]
        lines.append(f"| {r['dataset_id']} | {m['n_curated_clusters']} | {f4(m['overall_precision'])} | "
                     f"{f4(m['overall_recall'])} | {f4(m['overall_f1_score'])} | {f4(n['overall_f1_score'])} |")
    lines += ["", "Do not quote expert-unit scores without the unmatched / pure-noise cluster counts in the same row, "
              "and do not average over matched units only."]
    return lines


def score_run(score_json: Path, output_root: Path) -> list[dict]:
    score = json.loads(score_json.read_text())
    chain, root_manifest = lineage(score_json.parent)
    output_root.mkdir(parents=True, exist_ok=False)
    results = []
    for ds in score["datasets"]:
        if not ds.get("final_metrics"):
            continue  # unfinished dataset: no terminal score
        did = ds["dataset_id"]
        terminal = find_terminal(chain, did, ds["final_sha256"])
        mat_path, mat_sha = bind_mat(root_manifest, did)
        meta = load_matlab_spikes(str(mat_path))
        gt = np.asarray(meta["curation_assigns"]).astype(np.int64)
        init = np.asarray(meta["hierarchy_assigns"]).astype(np.int64)
        times = np.asarray(meta["spiketimes"], dtype=float).ravel()
        fs = float(np.asarray(meta["Fs"]).ravel()[0])
        with np.load(terminal) as z:
            pred = np.asarray(z["assigns"]).astype(np.int64)
        r = dict(dataset_id=did, terminal_path=str(terminal.relative_to(ROOT)), final_sha256=ds["final_sha256"],
                 mat_path=str(mat_path.relative_to(ROOT)), mat_sha256=mat_sha, **score_dataset(pred, init, gt, times, fs))
        own = ds["final_metrics"]
        if (r["many_to_one"]["total_tp"], r["many_to_one"]["total_fp"]) != (own["total_tp"], own["total_fp"]):
            raise ValueError(f"{did}: many-to-one rescoring differs from the run's own metrics")
        results.append(r)
        o = r["one_to_one"]
        print(f"{did}: one-to-one F1 {o['full_output']['f1']:.4f} (units {o['n_matched_units']}/{o['n_expert_units']}, "
              f"unmatched clusters {o['n_unmatched_predicted_units']}) | many-to-one F1 {r['many_to_one']['overall_f1_score']:.4f}",
              flush=True)
    payload = dict(score_json=str(score_json.relative_to(ROOT)), score_json_sha256=sha256_file(score_json),
                   lineage=[str(p.relative_to(ROOT)) for p in chain], api_calls=0, datasets=results)
    (output_root / "terminal_scores.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    (output_root / "TERMINAL_SCORES.md").write_text(
        "\n".join(report_lines(f"Terminal scores — {score_json.parent.name}", results)) + "\n")
    return results


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--score-json", type=Path, required=True, help="score.json of the run to rescore")
    p.add_argument("--output-root", type=Path, required=True, help="new directory; never overwritten")
    args = p.parse_args()
    score_run(args.score_json.resolve(), args.output_root)


if __name__ == "__main__":
    main()
