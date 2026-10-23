"""Extract verbatim default reasons from three approved Jacob XLSX sheets.

This expert-reference tool is separate from MAT/model inputs and metrics. It
does not replay actions, read expert terminal assignments, or call a model.
"""
from __future__ import annotations

import argparse
from collections import OrderedDict
import hashlib
import json
from pathlib import Path
import re
import unicodedata
from xml.etree import ElementTree as ET
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[3]
WORKBOOK = "Jacob Bedke-Annotated_spike_sorting_data_w_chronux/action_sheet.xlsx"
WORKBOOK_SHA256 = "b182b90d8a245b6e83a6ca8a9db57a59ef81cdd7442ee8d3045151d2bb501de7"
SHEETS = {"cM2-e004_004-006_CH3":97,"cM2-e004_004-006_CH31":54,"cM2-e008_021-028_CH30":90}
VERSION = "jacob-reason-seed-v1"
DEFAULT_SEED = ROOT / "output/curation_review_local/jacob_reason_seed_v1.json"
NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def clean(text):
    # Match the existing audited parser's removal of spreadsheet quote wrappers.
    return " ".join(text.strip().strip("'\"").strip().split())


def reason_key(text):
    return unicodedata.normalize("NFKC", " ".join(text.split())).casefold()


def extract(workbook):
    workbook = Path(workbook)
    if hashlib.sha256(workbook.read_bytes()).hexdigest() != WORKBOOK_SHA256:
        raise ValueError("Jacob workbook hash differs from the reviewed source")
    groups = OrderedDict()
    counts = {}
    with ZipFile(workbook) as archive:
        strings = []
        if "xl/sharedStrings.xml" in archive.namelist():
            strings = ["".join(n.text or "" for n in item.iter("{"+NS+"}t")) for item in ET.fromstring(archive.read("xl/sharedStrings.xml"))]
        relationships = {r.attrib["Id"]:r.attrib["Target"] for r in ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))}
        for sheet in ET.fromstring(archive.read("xl/workbook.xml")).findall("{"+NS+"}sheets/{"+NS+"}sheet"):
            dataset = sheet.attrib["name"]
            if dataset not in SHEETS:
                continue  # Never open unselected worksheet content.
            target = relationships[sheet.attrib["{"+REL+"}id"]]
            target = target.lstrip("/") if target.startswith("/") else target if target.startswith("xl/") else "xl/"+target
            count = 0
            for row in ET.fromstring(archive.read(target)).findall("{"+NS+"}sheetData/{"+NS+"}row"):
                number = int(row.attrib["r"])
                cells = {}
                for cell in row.findall("{"+NS+"}c"):
                    column = re.match(r"[A-Z]+",cell.attrib["r"]).group()
                    value = cell.find("{"+NS+"}v")
                    if cell.get("t") == "inlineStr":
                        text = "".join(n.text or "" for n in cell.iter("{"+NS+"}t"))
                    elif value is None:
                        text = ""
                    else:
                        text = strings[int(value.text)] if cell.get("t") == "s" else value.text or ""
                    cells[column] = text
                if number == 1:
                    if clean(cells.get("A","")) != "Actions" or clean(cells.get("B","")) != "Action Reasoning":
                        raise ValueError("Unexpected Jacob worksheet columns")
                    continue
                action, text = clean(cells.get("A","")), clean(cells.get("B",""))
                if not action and not text:
                    continue
                if not re.fullmatch(r"(?:s\s+\d+|m\s+\d+\s+\d+)",action,re.I) or not text or len(text)>2000:
                    raise ValueError("Unexpected Jacob action/reason row")
                count += 1
                key = reason_key(text)
                entry = groups.setdefault(key,dict(text=text,sources=[]))
                entry["sources"].append(dict(dataset_id=dataset,sheet=dataset,row=number,
                    original_text=cells["B"],raw_action=cells["A"],file=WORKBOOK,sha256=WORKBOOK_SHA256))
            if count != SHEETS[dataset]:
                raise ValueError("Jacob worksheet row count differs from audited source")
            counts[dataset] = count
    if counts != SHEETS:
        raise ValueError("Missing approved Jacob worksheet")
    return dict(schema=VERSION,version=VERSION,source_file=WORKBOOK,source_sha256=WORKBOOK_SHA256,
        counts=counts,n_source_rows=sum(counts.values()),reasons=list(groups.values()),
        excluded=dict(dataset_id="cM2-e004_011-015_CH20",reason="Old worksheet has a non-executable step; canonical MAT action log has no text reasons"),
        notice="Expert vocabulary for human reviewers only; not a matched answer for any current case, not actor input, and not evidence of a unified CH30 action/terminal trajectory")


def load_seed(path):
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if payload.get("schema") != VERSION or payload.get("source_sha256") != WORKBOOK_SHA256 or payload.get("counts") != SHEETS:
        raise ValueError("Unsupported Jacob reason seed provenance")
    reasons = payload.get("reasons")
    if not isinstance(reasons,list) or not 0<len(reasons)<=sum(SHEETS.values()):
        raise ValueError("Invalid Jacob seed reasons")
    seen=set(); rows=set()
    for entry in reasons:
        text=entry.get("text")
        if not isinstance(text,str) or not text.strip() or len(text)>2000 or reason_key(text) in seen:
            raise ValueError("Invalid or duplicate Jacob seed text")
        seen.add(reason_key(text))
        if not isinstance(entry.get("sources"),list) or not entry["sources"]:
            raise ValueError("Missing Jacob reason source")
        for source in entry["sources"]:
            dataset=source.get("dataset_id")
            row=source.get("row")
            if (dataset not in SHEETS or source.get("sheet")!=dataset or type(row) is not int or not 2<=row<=SHEETS[dataset]+1
                    or source.get("sha256")!=WORKBOOK_SHA256 or source.get("file")!=WORKBOOK
                    or not isinstance(source.get("original_text"),str) or clean(source["original_text"])!=clean(text)
                    or (dataset,row) in rows):
                raise ValueError("Invalid Jacob reason source reference")
            rows.add((dataset,row))
    if len(rows)!=sum(SHEETS.values()):
        raise ValueError("Incomplete Jacob seed references")
    return payload


if __name__ == "__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output",type=Path,default=DEFAULT_SEED)
    args=parser.parse_args()
    output=args.output.resolve()
    if not output.is_relative_to((ROOT/"output/curation_review_local").resolve()):
        raise ValueError("Write a new seed only under output/curation_review_local")
    payload=extract(ROOT/WORKBOOK)
    output.parent.mkdir(parents=True,exist_ok=True)
    with output.open("x",encoding="utf-8") as stream:
        json.dump(payload,stream,ensure_ascii=False,allow_nan=False,indent=2)
    print(json.dumps(dict(output=str(output),source_rows=payload["n_source_rows"],unique_reasons=len(payload["reasons"]),source_sha256=WORKBOOK_SHA256)))
