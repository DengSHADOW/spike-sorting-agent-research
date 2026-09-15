"""Compare MAT-internal, CSV, and Excel action sources for four real channels.

The source data are read-only.  Results make the longest executable prefix and
the final-assignment agreement explicit so an unavailable data provider does
not force incompatible annotations into one ground-truth claim.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Optional
from xml.etree import ElementTree as ET
from zipfile import ZipFile

import numpy as np
from sklearn.metrics import adjusted_rand_score

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.cluster.manager import ClusterManager
from src.io.matlab_loader import load_matlab_spikes


XML_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def clean_text(value: object) -> str:
    return str(value).strip().strip("'\"").strip()


def parse_action(action: str) -> tuple[str, int, Optional[int]]:
    text = clean_text(action)
    split_match = re.fullmatch(r"s\s+(\d+)", text, flags=re.IGNORECASE)
    if split_match:
        return "split", int(split_match.group(1)), None
    merge_match = re.fullmatch(r"m\s+(\d+)\s+(\d+)", text, flags=re.IGNORECASE)
    if merge_match:
        target, source = map(int, merge_match.groups())
        return ("discard", source, None) if target == 0 else ("merge", source, target)
    raise ValueError(f"Cannot parse action: {action!r}")


def load_csv_rows(path: Path) -> list[tuple[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        return [
            (clean_text(row.get("Actions", "")), clean_text(row.get("Action Reasoning", "")))
            for row in reader
            if clean_text(row.get("Actions", ""))
        ]


def _xlsx_shared_strings(archive: ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in archive.namelist():
        return []
    root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
    return [
        "".join(node.text or "" for node in item.iter(f"{{{XML_NS}}}t"))
        for item in root.findall(f"{{{XML_NS}}}si")
    ]


def load_xlsx_sheets(path: Path) -> dict[str, list[tuple[str, str]]]:
    """Read the two-column workbook without adding an openpyxl dependency."""
    with ZipFile(path) as archive:
        strings = _xlsx_shared_strings(archive)
        relationships = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        targets = {item.attrib["Id"]: item.attrib["Target"] for item in relationships}
        workbook = ET.fromstring(archive.read("xl/workbook.xml"))
        result: dict[str, list[tuple[str, str]]] = {}
        sheets = workbook.find(f"{{{XML_NS}}}sheets")
        if sheets is None:
            return result

        for sheet in sheets:
            name = sheet.attrib["name"]
            rel_id = sheet.attrib[f"{{{REL_NS}}}id"]
            target = targets[rel_id]
            if target.startswith("/"):
                target = target.lstrip("/")
            elif not target.startswith("xl/"):
                target = f"xl/{target}"

            worksheet = ET.fromstring(archive.read(target))
            decoded_rows: list[dict[str, str]] = []
            for row in worksheet.findall(f".//{{{XML_NS}}}sheetData/{{{XML_NS}}}row"):
                values: dict[str, str] = {}
                for cell in row.findall(f"{{{XML_NS}}}c"):
                    column = re.match(r"[A-Z]+", cell.attrib["r"])
                    if column is None:
                        continue
                    cell_type = cell.attrib.get("t")
                    value_node = cell.find(f"{{{XML_NS}}}v")
                    if cell_type == "inlineStr":
                        value = "".join(
                            node.text or "" for node in cell.iter(f"{{{XML_NS}}}t")
                        )
                    elif value_node is None:
                        value = ""
                    elif cell_type == "s":
                        value = strings[int(value_node.text or "0")]
                    else:
                        value = value_node.text or ""
                    values[column.group()] = value
                decoded_rows.append(values)

            result[name] = [
                (clean_text(row.get("A", "")), clean_text(row.get("B", "")))
                for row in decoded_rows[1:]
                if clean_text(row.get("A", ""))
            ]
    return result


def replay_source(
    channel: str,
    source: str,
    rows: list[tuple[str, str]],
    data: dict[str, object],
    output_dir: Path,
) -> dict[str, object]:
    manager = ClusterManager(
        initial_assigns=data["hierarchy_assigns"],
        overcluster_assigns=data["overcluster_assigns"],
        hierarchy_tree=data["hierarchy_tree"].copy(),
        spike_times=data["spiketimes"],
        waveforms=data["waveforms"],
    )
    details: list[dict[str, object]] = []
    action_counts: Counter[str] = Counter()
    prefix_open = True
    valid_prefix = 0

    for step, (raw_action, reasoning) in enumerate(rows, start=1):
        detail: dict[str, object] = {
            "step": step,
            "raw_action": raw_action,
            "reasoning_present": bool(reasoning),
            "action_type": "",
            "source_cluster": "",
            "target_cluster": "",
            "status": "",
            "detail": "",
        }
        try:
            action_type, source_id, target_id = parse_action(raw_action)
            action_counts[action_type.upper()] += 1
            detail.update(
                action_type=action_type,
                source_cluster=source_id,
                target_cluster="" if target_id is None else target_id,
            )
            if manager.get_cluster_info(source_id) is None:
                raise RuntimeError(f"cluster {source_id} is not active")
            if target_id is not None and manager.get_cluster_info(target_id) is None:
                raise RuntimeError(f"cluster {target_id} is not active")

            if action_type == "split":
                manager.split_last_merge(source_id)
            elif action_type == "discard":
                manager.discard_cluster(source_id)
            else:
                manager.merge_clusters([source_id, int(target_id)], target_id=int(target_id))
            detail["status"] = "applied"
        except ValueError as exc:
            detail.update(status="parse_error", detail=str(exc))
        except RuntimeError as exc:
            detail.update(status="missing_cluster", detail=str(exc))
        except Exception as exc:
            detail.update(status="apply_error", detail=str(exc))

        if prefix_open and detail["status"] == "applied":
            valid_prefix += 1
        else:
            prefix_open = False
        details.append(detail)

    final_assigns = data.get("curation_assigns")
    has_final = final_assigns is not None
    exact = bool(has_final and np.array_equal(final_assigns, manager.assigns))
    status_counts = Counter(str(row["status"]) for row in details)
    result: dict[str, object] = {
        "channel": channel,
        "source": source,
        "n_actions": len(rows),
        "n_reasoning": sum(bool(reasoning) for _, reasoning in rows),
        "valid_prefix_length": valid_prefix,
        "n_applied": status_counts.get("applied", 0),
        "status_counts": dict(sorted(status_counts.items())),
        "action_counts": dict(sorted(action_counts.items())),
        "replay_vs_curation_ari": (
            float(adjusted_rand_score(final_assigns, manager.assigns)) if has_final else None
        ),
        "replay_assigns_exact_match": exact if has_final else None,
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    detail_path = output_dir / f"{channel}_{source}_steps.csv"
    with detail_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(details[0]) if details else ["step"])
        writer.writeheader()
        writer.writerows(details)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mat-root", type=Path, default=Path("Tianmin_Annotated_data/data"))
    parser.add_argument(
        "--csv-root", type=Path, default=Path("Tianmin_Annotated_data/data/action_sheets")
    )
    parser.add_argument(
        "--workbook",
        type=Path,
        default=Path("Jacob Bedke-Annotated_spike_sorting_data_w_chronux/action_sheet.xlsx"),
    )
    parser.add_argument(
        "--output-dir", type=Path, default=Path("output/real_action_source_comparison")
    )
    args = parser.parse_args()

    workbook = load_xlsx_sheets(args.workbook)
    channel_to_sheet = {
        "CH3": "cM2-e004_004-006_CH3",
        "CH31": "cM2-e004_004-006_CH31",
        "CH20": "cM2-e004_011-015_CH20",
        "CH30": "cM2-e008_021-028_CH30",
    }
    summaries: list[dict[str, object]] = []
    sequences: dict[tuple[str, str], list[str]] = {}
    for channel, sheet_name in channel_to_sheet.items():
        print(f"Auditing {channel}", flush=True)
        data = load_matlab_spikes(str(args.mat_root / f"{channel}_spikes.mat"))
        # Do not zip actions directly with reasoning: most internal MAT logs do
        # not contain reasoning, and zip() would silently drop every action.
        internal_actions = list(data.get("curation_actions") or [])
        internal_reasoning = list(data.get("curation_action_reasoning") or [])
        sources = {
            "mat_internal": [
                (action, internal_reasoning[i] if i < len(internal_reasoning) else "")
                for i, action in enumerate(internal_actions)
            ],
            "csv": load_csv_rows(args.csv_root / f"{channel}.csv"),
            "excel": workbook[sheet_name],
        }

        for source, rows in sources.items():
            sequences[(channel, source)] = [clean_text(action) for action, _ in rows]
            result = replay_source(channel, source, rows, data, args.output_dir / "steps")
            summaries.append(result)
            print(
                f"  {source}: prefix={result['valid_prefix_length']}/{result['n_actions']}, "
                f"reasoning={result['n_reasoning']}, ARI={result['replay_vs_curation_ari']}",
                flush=True,
            )

    for result in summaries:
        channel = str(result["channel"])
        source = str(result["source"])
        result["same_actions_as_mat_internal"] = (
            sequences[(channel, source)] == sequences[(channel, "mat_internal")]
        )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    summary_path = args.output_dir / "summary.json"
    summary_path.write_text(json.dumps(summaries, indent=2), encoding="utf-8")
    print(f"Saved {summary_path}", flush=True)


if __name__ == "__main__":
    main()
