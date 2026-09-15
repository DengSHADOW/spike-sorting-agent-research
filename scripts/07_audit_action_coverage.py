"""Audit simulated action coverage before adaptation or model evaluation."""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.actions.coverage import audit_trajectory_coverage
from src.simulate.setting import SettingConfig


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit simulated trajectory action coverage.")
    parser.add_argument("--config", required=True, help="Path to per-setting YAML.")
    parser.add_argument("--output-dir", default="output")
    parser.add_argument("--adapter-run-id", default=None)
    parser.add_argument("--report-path", default=None)
    args = parser.parse_args()

    cfg = SettingConfig.load(args.config)
    report = audit_trajectory_coverage(
        setting_id=cfg.setting_id,
        output_dir=args.output_dir,
        adapter_run_id=args.adapter_run_id,
    )
    report_path = Path(args.report_path) if args.report_path else (
        Path(args.output_dir) / f"{cfg.setting_id}_action_coverage.json"
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)

    print(f"Stage 2 canonical coverage: {report['stage2_action_counts']}")
    print(f"Stage 3 GT coverage: {report['stage3_gt_action_counts']}")
    print(f"Effective trajectory coverage: {report['trajectory_action_counts']}")
    print(f"Missing trajectory actions: {report['missing_trajectory_actions']}")
    print(f"Missing train supervised actions: {report['missing_train_supervised_actions']}")
    print(f"Report: {report_path}")


if __name__ == "__main__":
    main()
