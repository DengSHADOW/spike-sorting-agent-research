"""Independent executor/portable-input parity; all writes are temporary fixtures."""
import importlib.util
import io
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import numpy as np

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'mockups/curation-review/backend'))
import working_data as wd
sys.path.insert(0,str(ROOT))
from src.cluster.manager import ClusterManager


class WorkingTests(unittest.TestCase):
    def fixture(self):
        times=np.array([0,.01,.02,.021,.03,.04]);waves=np.array([[0,2,0],[0,4,0],[0,1,0],[0,1.5,0],[0,-2,0],[0,-3,0]],dtype=float)
        return 30000.,times,waves,np.array([1,1,3,3,2,2]),np.array([1,1,1,1,2,2]),np.array([[1.],[3.],[.9],[0.]])

    def test_portable_engine_matches_existing_python_executor(self):
        inputs=self.fixture();fs,t,w,o,a,tree=inputs
        m=ClusterManager(a,o,tree.copy(),t,w);m.split_last_merge(1);m.merge_clusters([1,3],target_id=3);m.discard_cluster(2)
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'fixture.mat';path.write_bytes(b'actor-only fixture')
            with patch.object(wd,'load_inputs',return_value=inputs):data=wd.package('fixture',path)
            file=Path(tmp)/'package.json';file.write_text(json.dumps(data))
            script="""const fs=require('node:fs'),C=require(process.argv[1]);(async()=>{const d=JSON.parse(fs.readFileSync(process.argv[2])),i=C.prepare(d);let s=C.initial(i,'test-reviewer');const initial=await C.stateHash(i,s);const apply=async(a,source,target,phase)=>{s.phase=phase;s=await C.apply(i,s,{key:'fixture',phase,source,target},{action:a,reason:'parity'},s.version)};await apply('SPLIT',1,null,'phase1');await apply('MERGE',1,3,'phase2');await apply('DISCARD',2,3,'phase2');process.stdout.write(JSON.stringify({initial,assigns:Array.from(C.assignments(i,s)),tree:s.tree,final:await C.stateHash(i,s)}))})().catch(e=>{console.error(e);process.exit(1)});"""
            result=subprocess.run(['node','-e',script,str(ROOT/'mockups/curation-review/dist/curation-core.js'),str(file)],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            actual=json.loads(result.stdout)
            self.assertEqual(actual['initial'],data['initial_state_sha256'])
            self.assertEqual(actual['assigns'],m.assigns.tolist())
            self.assertEqual(actual['tree'],m.hierarchy_tree.tolist())
            self.assertEqual(actual['final'],wd.state_hash(m.assigns,m.hierarchy_tree))

    def test_full_waveform_package_preserves_every_value_and_has_an_independent_byte_hash(self):
        import base64
        for delta in (0.,1.234567890123):
            inputs=list(self.fixture());inputs[2]=inputs[2].copy();inputs[2][0,1]+=delta
            with tempfile.TemporaryDirectory() as tmp:
                path=Path(tmp)/'fixture.mat';path.write_bytes(b'fixture')
                with patch.object(wd,'load_inputs',return_value=inputs):data=wd.package('fixture',path)
                vector=data['arrays']['waveforms'];raw=base64.b64decode(vector['data']);actual=np.frombuffer(raw,dtype=vector['dtype']).reshape(vector['shape'])
                self.assertTrue(np.array_equal(actual,inputs[2]));self.assertEqual(hashlib.sha256(raw).hexdigest(),vector['sha256']);self.assertTrue(data['all_waveforms_included']);self.assertEqual(data['package_revision'],3);self.assertIsInstance(data['review_snapshots'],list)

    def test_chronux_raw_sample_without_sorting_is_not_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'bad.mat';path.write_bytes(b'fixture')
            with patch.object(wd,'load_inputs',side_effect=AttributeError('hierarchy')):
                with self.assertRaisesRegex(ValueError,'hierarchy'):wd.package('fixture',path)

    def test_upload_size_and_session_validation_precede_file_reads(self):
        with tempfile.TemporaryDirectory() as tmp:
            w=wd.WorkingInputs(tmp)
            with self.assertRaises(ValueError):w.upload(None,wd.MAX_BYTES+1,'new.mat','session')
            with self.assertRaises(ValueError):w.upload(None,10,'new.mat','')

    def test_server_paths_reject_outside_hidden_and_protected_before_open(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'configs/curation').mkdir(parents=True)
            (root/'configs/curation/protocol_v1.json').write_text(json.dumps({'scope':{'reserved_after_prior_exposure':['protected-block'],'quarantined_blocks':[]}}))
            w=wd.WorkingInputs(root)
            with patch.object(wd,'ROOT',root):
                for path in ('/etc/example.mat','.hidden/example.mat','Resume/example.mat','uploads/protected-block_CH1.mat'):
                    with self.subTest(path=path),self.assertRaises(ValueError):w.import_path(path,'session')
                source=root/'uploads/new.mat';source.parent.mkdir();source.write_bytes(b'new fixture')
                with patch.object(w,'upload',return_value={'dataset_id':'fixture'}) as upload:
                    self.assertEqual(w.import_path('uploads/new.mat','session')['dataset_id'],'fixture')
                    self.assertEqual(upload.call_args.args[1:],(11,'new.mat','session'))
                self.assertEqual(source.read_bytes(),b'new fixture')

    def test_renaming_a_protected_recording_cannot_bypass_the_upload_policy(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'configs/curation').mkdir(parents=True);(root/'output/real_split_manifest_20260903').mkdir(parents=True)
            content=b'protected fixture, no real held-out sample';digest=hashlib.sha256(content).hexdigest()
            (root/'configs/curation/protocol_v1.json').write_text(json.dumps({'scope':{'reserved_after_prior_exposure':['heldout-block'],'quarantined_blocks':[]}}))
            (root/'output/real_split_manifest_20260903/manifest.json').write_text(json.dumps({'datasets':[{'dataset_id':'heldout-block_CH1','mat_sha256':digest}]}))
            w=wd.WorkingInputs(root)
            with patch.object(wd,'ROOT',root),patch.object(wd,'package',side_effect=AssertionError('Must not parse protected inputs')):
                with self.assertRaisesRegex(ValueError,'protected'):w.upload(io.BytesIO(content),len(content),'renamed.mat','pretend-new-session')
            self.assertFalse(list(w.root.glob('*.mat')))


if __name__=='__main__':unittest.main()
