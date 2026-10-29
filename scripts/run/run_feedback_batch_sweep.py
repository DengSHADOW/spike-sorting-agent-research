"""N=1/4/8 all-feedback chains. Dry-run by default; --execute runs real local-vLLM experiments.

Each N and repetition restarts from the same explicitly provided skill. CH31 -> CH3 -> CH30
learn sequentially; future channels and CH20 are evaluated with each exact exported skill.
Existing directories are never reused. Manifest and final-skill hashes are checked at every link.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shlex
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
DATASETS = {
    "CH31": "cM2-e004_004-006_CH31", "CH3": "cM2-e004_004-006_CH3",
    "CH30": "cM2-e008_021-028_CH30", "CH20": "cM2-e004_011-015_CH20",
}
CHAIN = ("CH31", "CH3", "CH30")


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, data):
    path = Path(path)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def plan(args):
    """All filenames and dependencies are explicit; no shell substitutions or guessed latest skills."""
    jobs = []
    for repeat in range(1, args.repeats + 1):
        root = Path(args.output_root).resolve() / f"repeat_{repeat:02d}"
        for channel in () if args.skip_baseline else DATASETS:
            jobs.append(dict(name=f"baseline_{channel}", n=0, stage=0, repeat=repeat, learn=False,
                             channel=channel, skill=str(Path(args.base_skill).resolve()),
                             out=str(root / "baseline" / channel)))
        for n in args.batch_sizes:
            parent = Path(args.base_skill).resolve()
            for stage, channel in enumerate(CHAIN, 1):
                group = root / f"n{n}{'_protect' if args.protect_correct else ''}"
                out = group / f"learn_{stage}_{channel}"
                jobs.append(dict(name=out.name, n=n, stage=stage, repeat=repeat, learn=True,
                                 channel=channel, skill=str(parent), out=str(out)))
                parent = out / "final_skill.json"
                for test_channel in (*CHAIN[stage:], "CH20"):
                    out = group / f"test_after_{stage}_{test_channel}"
                    jobs.append(dict(name=out.name, n=n, stage=stage, repeat=repeat, learn=False,
                                     channel=test_channel, skill=str(parent), out=str(out)))
    return jobs


def command(args, job):
    # Keep the venv's executable path: resolving its symlink would silently switch Python environments.
    cmd = [str(Path(args.python).absolute()), str(ROOT / "scripts/run/run_expert_replay.py"),
           "--model", "qwen27b", "--no-thinking", "--base-url", args.base_url,
           "--parallel", str(args.parallel), "--dataset", DATASETS[job["channel"]],
           "--skill", job["skill"], "--output-root", job["out"]]
    if job["learn"]:
        cmd += ["--online-learning", "--feedback-batch-size", str(job["n"]),
                "--online-feedback-scope", "all", "--feedback-sources", "all",
                "--regression-k", str(args.regression_k)] + ["--protect-correct"] * args.protect_correct
    elif args.prefetch_tests:
        cmd += ["--prefetch"]
    return cmd


def check_result(job, expected_sha, regression_k=3, protect_correct=False):
    out = Path(job["out"])
    manifest, result = read(out / "run_manifest.json"), read(out / "result.json")
    if manifest["skill_sha256"] != expected_sha or manifest["skill_version"] != read(job["skill"])["version"]:
        raise RuntimeError(f"Wrong starting skill in {out}")
    if sha(job["skill"]) != expected_sha:
        raise RuntimeError(f"Starting skill changed during {out}")
    if manifest["dataset"]["dataset_id"] != DATASETS[job["channel"]]:
        raise RuntimeError(f"Wrong dataset in {out}")
    settings = manifest.get("online_learning")
    if job["learn"]:
        if (not settings or settings.get("batch_size") != job["n"] or settings.get("feedback_scope") != "all"
                or settings.get("sources") != "all" or settings.get("k") != regression_k
                or settings.get("protect_correct") is not protect_correct
                or manifest.get("flush_online_feedback") is not True):
            raise RuntimeError(f"Wrong batch-learning settings in {out}")
    elif settings or manifest.get("learning_session"):
        raise RuntimeError(f"Evaluation must not learn: {out}")
    if result["status"] != "completed" or result.get("error"):
        raise RuntimeError(f"Incomplete run: {out}: {result.get('error')}")
    info = result["final_skill"]
    exported = out / "final_skill.json"
    if info.get("export_path") != "final_skill.json" or sha(exported) != info.get("export_sha256"):
        raise RuntimeError(f"Final skill export hash mismatch in {out}")
    if read(exported)["version"] != info["version"]:
        raise RuntimeError(f"Final skill export version mismatch in {out}")
    if not job["learn"] and read(exported) != read(job["skill"]):
        raise RuntimeError(f"Evaluation changed the skill: {out}")
    return dict(job, input_skill_sha256=expected_sha, final_skill=info,
                simulation=result["simulation"], seconds=result["seconds"],
                online_learning=result.get("online_learning"), profile=manifest["profile"])


def report(out, rows):
    save(out / "summary.json", rows)
    lines = ["# All-feedback N sweep", "", "Recorded edits and derived KEEP remain distinct labels.", "",
             "| repeat | N | run | correct/n | by expert action | seconds | batches / accepted |",
             "|---|---|---|---|---|---|---|"]
    for r in rows:
        sim, ol = r["simulation"], r["online_learning"]
        overall = sim["overall"]
        by = "; ".join(f"{a}: {v['correct']}/{v['n']}" for a, v in sim["by_expert_action"].items())
        batches = f"{ol['attempts']} / {ol['outcomes'].get('accepted', 0)}" if ol else "—"
        lines.append(f"| {r['repeat']} | {r['n']} | {r['name']} | {overall['correct']}/{overall['n']} | "
                     f"{by} | {r['seconds']:.1f} | {batches} |")
    (out / "SUMMARY.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--base-skill", required=True, help="Explicit original skill_v4 snapshot; never infer a default")
    p.add_argument("--output-root", required=True)
    p.add_argument("--batch-sizes", nargs="+", type=int, default=[1, 4, 8])
    p.add_argument("--repeats", type=int, default=1)
    p.add_argument("--regression-k", type=int, default=3)
    p.add_argument("--protect-correct", action="store_true", help="Learning rejects any loss of a correct batch case")
    p.add_argument("--skip-baseline", action="store_true", help="Omit the no-learning baseline runs")
    p.add_argument("--prefetch-tests", action="store_true",
                   help="Frozen tests render all observations first and answer concurrently (accuracy-equivalent)")
    p.add_argument("--parallel", type=int, default=4)
    p.add_argument("--base-url", default="http://127.0.0.1:8000")
    p.add_argument("--python", default=sys.executable)
    p.add_argument("--execute", action="store_true", help="Run the printed campaign using the live model server")
    args = p.parse_args(argv)
    if (any(n < 1 for n in args.batch_sizes) or len(set(args.batch_sizes)) != len(args.batch_sizes)
            or args.repeats < 1 or args.parallel < 1 or args.regression_k < 0):
        p.error("Use unique positive N values, positive repeats/parallel and non-negative K")
    if not Path(args.base_skill).is_file():
        p.error("Explicit starting skill file does not exist")
    jobs = plan(args)
    print(f"{len(jobs)} runs; N={args.batch_sizes}; repetitions={args.repeats}; K={args.regression_k}; "
          f"protect_correct={args.protect_correct}; test prefetch={args.prefetch_tests}; end flush on", flush=True)
    if not args.execute:
        for job in jobs:
            print(shlex.join(command(args, job)))
        return 0
    # Fail early on incompatible upstream skill versions, missing data or a different model server.
    sys.path.insert(0, str(ROOT))
    from scripts.run import run_local_rollout as rollout
    rollout.dec.load_skill(args.base_skill)
    dataset_rows = rollout.ref.stage6.selected_rows(list(DATASETS.values()))
    for row in dataset_rows:
        if not (ROOT / row["mat_path"]).is_file():
            raise FileNotFoundError(ROOT / row["mat_path"])
    profile = read(rollout.PROFILES)["models"]["qwen27b"]
    server = rollout.VllmProvider(profile, Path(args.output_root) / "preflight.jsonl", base_url=args.base_url).check()
    out = Path(args.output_root).resolve()
    out.mkdir(parents=True, exist_ok=False)
    save(out / "campaign.json", dict(config=vars(args), jobs=jobs, base_skill_sha256=sha(args.base_skill), server=server))
    rows, reference_profile = [], None
    for index, job in enumerate(jobs, 1):
        skill_sha = sha(job["skill"])
        print(f"[{index}/{len(jobs)}] {job['out']} input_skill_sha256={skill_sha}", flush=True)
        Path(job["out"]).parent.mkdir(parents=True, exist_ok=True)
        log = out / f"run_{index:03d}.log"
        with log.open("x", encoding="utf-8") as stream:
            subprocess.run(command(args, job), cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT, check=True)
        row = check_result(job, skill_sha, args.regression_k, args.protect_correct)
        if reference_profile is None:
            reference_profile = row["profile"]
        elif row["profile"] != reference_profile:
            raise RuntimeError("Model settings changed across campaign runs")
        rows.append(row)
        report(out, rows)
    save(out / "completed.json", dict(runs=len(rows)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
