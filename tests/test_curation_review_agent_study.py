"""Anonymous-agent provenance and checkpoint boundaries; no model/network calls."""
import copy
import hashlib
from pathlib import Path
import sys
import tempfile
import unittest
import numpy as np

BACKEND=Path(__file__).resolve().parents[1]/'mockups/curation-review/backend'
sys.path.insert(0,str(BACKEND))
from test_curation_review_backend import case,body,server
import review_analysis as analysis
from import_agent_study import parse_reference,indexed


def study_case(slot='Agent_1'):
    value=case()
    value.update(id='matched--'+slot,agent_id=slot,study_agent_id=slot,study_id='anonymous-agents-v1',
        comparison_case_id='matched-reference-1',review_mode='blind_comparison',rationale_available=slot!='Agent_2',
        response_sha256='f'*64,evidence_scope='reconstructed-reviewer-evidence')
    if slot=='Agent_2':value['rationale']=None
    return value


class StudyTests(unittest.TestCase):
    def test_canonical_agent_and_matching_metadata_ignore_client_identity(self):
        c=study_case();data=body();data['context'].update(agent_id='Fake',study_agent_id='Agent_3')
        result=server.build_review(c,data['draft'],data['context'])
        self.assertEqual(result['agent_id'],'Agent_1')
        self.assertEqual(result['study_agent_id'],'Agent_1')
        self.assertEqual(result['comparison_case_id'],'matched-reference-1')
        self.assertEqual(result['review_mode'],'blinded_historical_comparison')
        self.assertTrue(result['additional_evidence_viewed'])

    def test_same_reference_has_separate_reviews_and_survives_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            store=server.ReviewStore(Path(directory)/'reviews.sqlite3')
            a,b=study_case(),study_case('Agent_2')
            store.write('reviewer-a',a,body())
            store.write('reviewer-a',b,body('request-0002'))
            payload=store.export('reviewer-a')
            self.assertEqual(len(payload['reviews']),2)
            self.assertEqual({r['study_agent_id'] for r in payload['reviews']},{'Agent_1','Agent_2'})
            store.import_backup('reviewer-b',payload,{a['id']:a,b['id']:b},preview=False)
            self.assertEqual(len(store.reviews('reviewer-b')),2)

    def test_missing_rationale_cannot_be_reviewed_or_claimed_viewed(self):
        c=study_case('Agent_2');data=body()
        self.assertIsNone(server.build_review(c,data['draft'],data['context'])['ai_rationale'])
        for exposure in [False,True]:
            invalid=copy.deepcopy(data)
            if exposure:invalid['context']['rationale_viewed']=True
            else:invalid['draft']['disagreement_types']=['rationale']
            with self.assertRaisesRegex(ValueError,'No retained rationale'):server.build_review(c,invalid['draft'],invalid['context'])

    def test_study_checkpoint_requires_file_and_array_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'states').mkdir()
            assigns=np.array([1,1,2]);tree=np.zeros((4,0));path=root/'states/state.npz'
            np.savez_compressed(path,assigns=assigns,tree=tree)
            c=study_case();c['state_sha256']=analysis.arrays_hash(assigns,tree);c['checkpoint_sha256']=hashlib.sha256(path.read_bytes()).hexdigest()
            bundle={'study_checkpoints':{c['state_sha256']:{'file':'state.npz','sha256':c['checkpoint_sha256']}}}
            class Datasets:
                def find_case(self,identity):return c,bundle,root
            service=analysis.AnalysisService(Datasets());inputs=(1000,np.array([0,.01,.02]),np.ones((3,2)),assigns,assigns,tree)
            np.testing.assert_array_equal(service._assignments(c,bundle,inputs),assigns)
            path.write_bytes(b'bad')
            with self.assertRaisesRegex(ValueError,'integrity'):service._assignments(c,bundle,inputs)

    def test_reference_parser_and_duplicate_prediction_guard(self):
        self.assertEqual(parse_reference('m 0 211'),('DISCARD',211,None))
        self.assertEqual(parse_reference('m 31 211'),('MERGE',211,31))
        self.assertEqual(parse_reference('s 211'),('SPLIT',211,None))
        with self.assertRaises(ValueError):parse_reference('keep 211')
        with self.assertRaises(ValueError):indexed([{'job_id':'cM2-e008_021-028_CH30_000001'}]*90,'job_id')


if __name__=='__main__':unittest.main()
