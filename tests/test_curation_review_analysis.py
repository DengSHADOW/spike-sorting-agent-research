"""Offline numerical/provenance tests; synthetic fixtures, no model or real MAT."""
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile

import numpy as np

BACKEND=Path(__file__).resolve().parents[1]/"mockups/curation-review/backend"
sys.path.insert(0,str(BACKEND))
import review_analysis as analysis
import jacob_reasons as reasons


class MeasurementsTests(unittest.TestCase):
    def test_isi_uses_all_intervals_and_has_undefined_small_population(self):
        value=analysis.interval_statistics([0,.001,.1])
        self.assertEqual(value["denominator"],2)
        self.assertEqual(value["violation_count"],1)
        self.assertEqual(value["violation_rate"],.5)
        self.assertEqual(sum(value["counts"]),1)
        self.assertEqual(value["outside_display_count"],1)
        self.assertEqual(analysis.interval_statistics([0,.002])["violation_count"],0)
        self.assertIsNone(analysis.interval_statistics([1])["violation_rate"])

    def test_amplitude_cv_population_sd_and_chronological_drift(self):
        waves=np.array([[0,4],[0,2]],dtype=float)
        original=waves.copy()
        value=analysis.population_statistics(waves,[1,0],1000,1)
        self.assertAlmostEqual(value["amplitude"]["cv"],1/3)
        self.assertEqual(value["waveform"]["mean"],[0,3])
        self.assertEqual(value["waveform"]["std"],[0,1])
        self.assertEqual(value["drift"]["delta_percent"],100)
        np.testing.assert_array_equal(waves,original)
        zero=analysis.population_statistics(np.zeros((1,2)),[0],1000,1)
        self.assertIsNone(zero["amplitude"]["cv"])
        self.assertIsNone(zero["drift"]["delta_percent"])

    def test_correlation_is_unshifted_and_undefined_for_constant_means(self):
        a=analysis.population_statistics([[0,1,0],[0,3,0]],[0,1],1000,1)
        b=analysis.population_statistics([[0,-2,0]],[0],1000,2)
        self.assertAlmostEqual(analysis.compare_populations(a,b)["waveform_correlation"],-1)
        b["waveform"]["mean"]=[2,2,2]
        self.assertIsNone(analysis.compare_populations(a,b)["waveform_correlation"])

    def test_invalid_alignment_nonfinite_inputs_and_sampling_rate_are_rejected(self):
        for waves,times,fs in (([[0,1]],[0,1],1000),([[0,float('nan')]],[0],1000),([[0,1]],[0],0)):
            with self.assertRaises(ValueError):analysis.population_statistics(waves,times,fs,1)

    def fixture(self):
        case=dict(id="fixture",dataset_id=analysis.DATASETS[0],source="U-001",state_sha256="a"*64,mat_sha256="b"*64,
            units=[dict(id="U-001",cluster_id=1,spikes=2),dict(id="U-002",cluster_id=2,spikes=2),dict(id="U-003",cluster_id=3,spikes=2)])
        class Datasets:
            def find_case(self,case_id):
                if case_id!="fixture":raise KeyError(case_id)
                return case,{},None
        service=analysis.AnalysisService(Datasets())
        times=np.array([0,.01,.001,.011,.05,.06])
        assigns=np.array([1,1,2,2,3,3])
        waves=np.array([[0,1,0],[0,2,0],[0,3,0],[0,4,0],[0,1,0],[0,2,0]],float)
        inputs=(1000.,times,waves,assigns,assigns,np.zeros((4,0)))
        return case,service,inputs,assigns

    def test_candidate_pair_isi_changes_with_target_and_inputs_are_unchanged(self):
        case,service,inputs,assigns=self.fixture()
        before=[item.copy() for item in inputs[1:]]
        with patch.object(service,"_inputs",return_value=inputs),patch.object(service,"_assignments",return_value=assigns):
            one=service.get("fixture",target_id="U-002")
            two=service.get("fixture",target_id="U-003")
            self.assertAlmostEqual(one["merged_isi"]["violation_rate"],2/3)
            self.assertEqual(two["merged_isi"]["violation_rate"],0)
            self.assertNotEqual(one["analysis_sha256"],two["analysis_sha256"])
            self.assertIs(service.get("fixture",target_id="U-002"),one)
        for old,new in zip(before,inputs[1:]):np.testing.assert_array_equal(old,new)
        self.assertFalse(one["expert_fields_accessed"])
        self.assertEqual(one["execution_status"],"not_executed")

    def test_scope_self_merge_cross_state_and_wrong_source_rejected_before_open(self):
        case,service,_,_=self.fixture()
        with patch.object(service,"_inputs",side_effect=AssertionError("must not open inputs")):
            for cluster,target in ((None,"U-001"),(None,"U-999"),("U-002","U-003")):
                with self.assertRaises(ValueError):service.get("fixture",cluster,target)
            case["dataset_id"]="cM2-e007_012-017_CH1"
            with self.assertRaises(ValueError):service.get("fixture")

    def test_correlograms_use_exact_membership_and_validate_scope_before_io(self):
        case, service, inputs, assigns = self.fixture()
        before = [item.copy() for item in inputs[1:]]
        with patch.object(service, '_inputs', return_value=inputs), patch.object(service, '_assignments', return_value=assigns):
            value = service.correlograms('fixture', target_id='U-002')
            self.assertEqual([row['n_source'] for row in value['acg']], [2, 2])
            self.assertEqual([sum(row['counts']) for row in value['acg']], [2, 2])
            self.assertEqual(sum(value['ccg']['counts']), 4)
            self.assertFalse(value['ccg']['self_pairs_excluded'])
            self.assertEqual(value['execution_status'], 'not_executed')
        for old, new in zip(before, inputs[1:]):
            np.testing.assert_array_equal(old, new)
        with patch.object(service, '_inputs', side_effect=AssertionError('must reject before input IO')):
            for cluster, target in [('U-001', 'U-001'), ('U-002', 'U-003'), ('U-999', None)]:
                with self.assertRaises(ValueError):
                    service.correlograms('fixture', cluster, target)
            case['dataset_id'] = 'unapproved-recording'
            with self.assertRaises(ValueError):
                service.correlograms('fixture')

    def test_private_snapshot_export_can_request_verified_tree_without_changing_inputs(self):
        case, service, inputs, assigns = self.fixture()
        case['review_mode'] = 'manual'
        case['state_sha256'] = analysis.arrays_hash(assigns, inputs[5])
        result, tree = service._assignments(case, {}, inputs, return_tree=True)
        np.testing.assert_array_equal(result, assigns)
        np.testing.assert_array_equal(tree, inputs[5])
        self.assertIsInstance(service._assignments(case, {}, inputs), np.ndarray)

    def test_initial_state_hash_rejects_tampered_assignments(self):
        case,service,inputs,assigns=self.fixture()
        case["review_mode"]="manual"
        case["state_sha256"]=analysis.arrays_hash(assigns,inputs[5])
        np.testing.assert_array_equal(service._assignments(case,{},inputs),assigns)
        tampered=list(inputs);tampered[4]=assigns.copy();tampered[4][0]=3
        with self.assertRaisesRegex(ValueError,"hash"):
            service._assignments(case,{},tampered)


class JacobSourceTests(unittest.TestCase):
    def test_selected_sheets_exact_text_dedup_and_provenance_validation(self):
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)/"fixture.xlsx"
            sheets={name:1 for name in reasons.SHEETS}
            ns=reasons.NS;rel=reasons.REL
            names=list(sheets)+["unselected"]
            with ZipFile(p,"w") as z:
                z.writestr('xl/workbook.xml',f'<workbook xmlns="{ns}" xmlns:r="{rel}"><sheets>'+''.join(f'<sheet name="{name}" r:id="r{i}"/>' for i,name in enumerate(names))+'</sheets></workbook>')
                z.writestr('xl/_rels/workbook.xml.rels','<Relationships>'+''.join(f'<Relationship Id="r{i}" Target="worksheets/sheet{i}.xml"/>' for i in range(len(names)))+'</Relationships>')
                for i,name in enumerate(sheets):
                    text="Waveform mismatch" if i<2 else "Timing differs"
                    rows='<row r="1"><c r="A1" t="inlineStr"><is><t>Actions</t></is></c><c r="B1" t="inlineStr"><is><t>Action Reasoning</t></is></c></row>'
                    rows+=f'<row r="2"><c r="A2" t="inlineStr"><is><t>s 1</t></is></c><c r="B2" t="inlineStr"><is><t>{text}</t></is></c></row>'
                    z.writestr(f'xl/worksheets/sheet{i}.xml',f'<worksheet xmlns="{ns}"><sheetData>{rows}</sheetData></worksheet>')
                # Would fail XML parsing if the importer opened this worksheet.
                z.writestr(f'xl/worksheets/sheet{len(sheets)}.xml','must never read unselected worksheet')
            digest=hashlib.sha256(p.read_bytes()).hexdigest()
            with patch.object(reasons,"WORKBOOK_SHA256",digest),patch.object(reasons,"SHEETS",sheets):
                payload=reasons.extract(p)
                self.assertEqual(payload["n_source_rows"],3)
                self.assertEqual(len(payload["reasons"]),2)
                self.assertEqual(len(payload["reasons"][0]["sources"]),2)
                seed=Path(folder)/"seed.json";seed.write_text(json.dumps(payload))
                self.assertEqual(reasons.load_seed(seed),payload)
                payload["reasons"][0]["sources"][0]["original_text"]="Fabricated replacement"
                seed.write_text(json.dumps(payload))
                with self.assertRaisesRegex(ValueError,"reference"):reasons.load_seed(seed)
            with self.assertRaisesRegex(ValueError,"hash"):reasons.extract(p)


if __name__=="__main__":unittest.main()
