"""Private upload lifecycle and explicit local-Agent jobs; no real inference requests."""
import json
import time
import uuid
from pathlib import Path
import unittest
from unittest.mock import patch

from tests import test_curation_server_compute as fixtures


class UploadTests(unittest.TestCase):
    assert_private = fixtures.ComputeTests.assert_private
    @classmethod
    def setUpClass(cls):fixtures.ComputeTests.setUpClass.__func__(cls)
    @classmethod
    def tearDownClass(cls):fixtures.ComputeTests.tearDownClass.__func__(cls)

    def setUp(self):
        self.token = self.service.request('session', {}, '')[0]['token']
        self.auth = 'Bearer '+self.token
        self.owner = self.service.identity(self.auth)

    def call(self, op, body=None):return self.service.request(op, body or {}, self.auth)[0]

    def input(self):
        return json.dumps({'sampling_rate_hz': 30000, 'times_seconds': [i*.01 for i in range(12)],
                          'spike_clusters': [1]*8+[2]*4,
                          'waveforms': [[i, -i-1, i/3] for i in range(12)]}).encode()

    def upload(self, raw=None):
        raw = self.input() if raw is None else raw
        result = self.call('upload-create', {'recording_id': 'private-test', 'files': [{'name':'spikes.json', 'size':len(raw)}]})
        identifier = result['upload_id']
        self.service.uploads.chunk(self.owner, identifier, 0, 0, raw)
        self.call('upload-finish', {'upload_id': identifier})
        for _ in range(200):
            value = self.call('upload-status', {'upload_id': identifier})
            if value['status'] in ('complete', 'failed'):return value
            time.sleep(.05)
        self.fail('Upload validation did not finish')

    def test_real_members_upload_opens_for_owner_only_and_no_automatic_agent(self):
        result = self.upload()
        self.assertEqual(result['status'], 'complete', result)
        dataset = result['result']['dataset_id']
        view = self.call('open', {'dataset_id': dataset})
        self.assertEqual([u['n'] for u in view['units']], [8, 4])
        self.assertEqual(view['suggestions'], [])
        self.assertEqual(len(self.call('datasets')['datasets']), 5)
        another = self.service.request('session', {}, '')[0]['token']
        self.assertEqual(len(self.service.request('datasets', {}, 'Bearer '+another)[0]['datasets']), 4)
        with self.assertRaises(ValueError):self.service.request('open', {'dataset_id': dataset}, 'Bearer '+another)
        fixtures.ComputeTests.assert_private(self, view)

    def test_chunk_retransmit_is_idempotent_and_wrong_owner_is_rejected(self):
        raw = self.input()
        value = self.call('upload-create', {'recording_id': 'chunks', 'files':[{'name':'rows.json','size':len(raw)}]})
        identifier = value['upload_id'];half = len(raw)//2
        first = self.service.uploads.chunk(self.owner, identifier, 0, 0, raw[:half])
        again = self.service.uploads.chunk(self.owner, identifier, 0, 0, raw[:half])
        self.assertEqual(first['received'], again['received'])
        with self.assertRaises(ValueError):self.service.uploads.chunk('0'*32, identifier, 0, half, raw[half:])
        with self.assertRaises(ValueError):self.service.uploads.chunk(self.owner, identifier, 0, half+1, raw[half:])
        with self.assertRaises(ValueError):self.call('upload-finish', {'upload_id':identifier})
        self.service.uploads.chunk(self.owner, identifier, 0, half, raw[half:])
        self.assertEqual(self.call('upload-status',{'upload_id':identifier})['received'],len(raw))

    def test_bad_or_template_only_input_never_registers_a_dataset(self):
        result = self.upload(b'{"sampling_rate_hz":30000,"templates":[[1,2,3]]}')
        self.assertEqual(result['status'],'failed')
        self.assertEqual(len(self.call('datasets')['datasets']),4)
        with self.assertRaises(ValueError):self.call('upload-create',{'recording_id':'x','files':[{'name':'../secret.mat','size':10}]})
        with self.assertRaises(ValueError):self.call('upload-create',{'recording_id':'x','files':[{'name':'huge.mat','size':257*1024*1024}]})

    def test_optional_agent_is_off_by_default_and_cannot_use_paid_or_external_urls(self):
        settings = self.call('agent-options')
        self.assertFalse(settings['auto_run']);self.assertFalse(settings['paid_providers_enabled'])
        self.assertEqual([r['agent_id'] for r in settings['agents']],['Agent_1','Agent_2'])
        self.assertTrue(all(not r['available'] for r in settings['agents']))
        with self.assertRaises(ValueError):self.service.agents.transport('https://external.example',1)
        with self.assertRaises(ValueError):self.call('agent-start',{'confirm':False})

    def test_explicit_local_agent_job_saves_suggestion_without_changing_grouping(self):
        upload = self.upload();view = self.call('open',{'dataset_id':upload['result']['dataset_id']})
        before = view['state_sha256'];profile = 'Qwen/Qwen3.5-27B';posts=[]
        def transport(url,timeout):
            def send(method,path,body=None):
                if path=='/v1/models':return {'data':[{'id':profile}]}
                if path=='/version':return {'version':'offline-fixture'}
                posts.append(body)
                return {'model':profile,'choices':[{'finish_reason':'stop','message':{'content':json.dumps({'action':'KEEP','rationale':'Offline test judgment'})}}],'usage':{'prompt_tokens':1,'completion_tokens':1}}
            return send
        path=self.service.agents.config_path;original=path.read_text();config=json.loads(original);config['enabled']=True;path.write_text(json.dumps(config))
        try:
            with patch.object(self.service.agents,'transport',transport):
                body={'workspace_id':view['state']['workspace_id'],'version':0,'source':view['selected']['source'],'target':None,'agent_id':'Agent_1','scope':'current','confirm':True,'request_id':str(uuid.uuid4())}
                result=self.call('agent-start',body)
                for _ in range(300):
                    result=self.call('agent-status',{'job_id':result['job_id']})
                    if result['status'] in ('complete','failed'):break
                    time.sleep(.05)
                self.assertEqual(result['status'],'complete',result)
        finally:path.write_text(original)
        self.assertEqual(len(posts),1)
        after=self.call('view',{'workspace_id':view['state']['workspace_id'],'source':view['selected']['source']})
        self.assertEqual(after['state_sha256'],before);self.assertEqual(after['state']['version'],0)
        self.assertEqual(after['suggestions'][0]['rationale'],'Offline test judgment')
        fixtures.ComputeTests.assert_private(self,after)
        choice=self.call('confirm',{'workspace_id':view['state']['workspace_id'],'version':0,
                         'source':view['selected']['source'],'target':None,'request_id':str(uuid.uuid4()),
                         'choice':{'action':'KEEP','agent_id':'Agent_1','agree':True}})
        self.assertEqual(choice['state_sha256'],before)


if __name__=='__main__':unittest.main()
