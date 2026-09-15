"""
Stage 1: Simulate extracellular recordings with MEArec.

For each channel index (0 … n_channels-1):
  1. Generate a MEArec recording with the setting's noise/drift/overlap parameters
  2. Run spike sorting (MountainSort5) to produce overclustering
  3. Save numpy arrays compatible with ClusterManager to output/<setting_id>/<ch_id>/raw/

Usage:
  uv run python scripts/01_simulate.py --config configs/settings/setting_001.yaml
  uv run python scripts/01_simulate.py --config configs/settings/setting_001.yaml \\
      --channel-id ch_007
  uv run python scripts/01_simulate.py --config configs/settings/setting_001.yaml \\
      --n-channels 5 --output-dir output/ --force
"""

import argparse
import sys
from pathlib import Path

# Ensure project root is on sys.path when run directly
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.simulate.setting import SettingConfig
from src.simulate.generator import generate_recording
from src.simulate.overcluster import overcluster_recording


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Stage 1: Generate MEArec recordings + overclustering.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--config", required=True,
        help="Path to per-setting YAML (e.g. configs/settings/setting_001.yaml).",
    )
    parser.add_argument(
        "--global-config", default="config.yaml",
        help="Path to global config.yaml (merged under per-setting YAML).",
    )
    parser.add_argument(
        "--n-channels", type=int, default=None,
        help="Override n_channels from the setting YAML.",
    )
    parser.add_argument(
        "--channel-id", default=None,
        help=(
            "Process one channel only (for example ch_007) without changing the "
            "setting's configured n_channels."
        ),
    )
    parser.add_argument(
        "--output-dir", default="output",
        help="Root output directory.",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Re-generate channels even if outputs already exist.",
    )
    args = parser.parse_args()

    if args.channel_id is not None and args.n_channels is not None:
        parser.error("--channel-id and --n-channels are mutually exclusive.")

    overrides = {}
    if args.n_channels is not None:
        overrides = {"simulation": {"n_channels": args.n_channels}}

    cfg = SettingConfig.load(
        setting_yaml=args.config,
        global_config=args.global_config if Path(args.global_config).exists() else None,
        overrides=overrides if overrides else None,
    )

    if args.channel_id is not None:
        if not args.channel_id.startswith("ch_"):
            parser.error("--channel-id must use the ch_NNN form, for example ch_007.")
        try:
            channel_indices = [int(args.channel_id.removeprefix("ch_"))]
        except ValueError:
            parser.error("--channel-id must use the ch_NNN form, for example ch_007.")
        if channel_indices[0] < 0 or channel_indices[0] >= cfg.n_channels:
            parser.error(
                f"--channel-id {args.channel_id} is outside this setting's "
                f"configured range ch_000..ch_{cfg.n_channels - 1:03d}."
            )
    else:
        channel_indices = list(range(cfg.n_channels))

    import yaml
    setting_out = Path(args.output_dir) / cfg.setting_id
    setting_out.mkdir(parents=True, exist_ok=True)
    with open(setting_out / "setting_config.yaml", "w") as f:
        yaml.dump(cfg.to_dict(), f, default_flow_style=False)

    print(f"\n{'='*60}")
    print(f"Stage 1 — Simulate: {cfg.setting_id}")
    print(f"  n_channels   : {cfg.n_channels}")
    print(f"  noise_level  : {cfg.noise_level} uV RMS ({cfg.noise_label})")
    print(f"  drift        : {'enabled' if cfg.drift_enabled else 'disabled'}")
    print(f"  probe        : {cfg.probe}")
    print(
        f"  hierarchy    : {'enabled' if cfg.hierarchy_enabled else 'disabled'} "
        f"(metric={cfg.hierarchy_similarity_metric}, min_sim={cfg.hierarchy_min_similarity})"
    )
    print(f"  output_dir   : {args.output_dir}")
    print(f"{'='*60}\n")

    for position, ch_idx in enumerate(channel_indices, start=1):
        print(f"[{position}/{len(channel_indices)}] Generating ch_{ch_idx:03d}...")
        generate_recording(cfg, ch_idx, args.output_dir, force=args.force)

        print(f"[{position}/{len(channel_indices)}] Overclustering ch_{ch_idx:03d}...")
        overcluster_recording(cfg, ch_idx, args.output_dir, force=args.force)

    print(f"\nStage 1 complete. Outputs in {Path(args.output_dir) / cfg.setting_id}/")


if __name__ == "__main__":
    main()
