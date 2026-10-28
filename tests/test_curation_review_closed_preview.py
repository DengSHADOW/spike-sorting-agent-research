"""Offline tests only: provider HTTP is always replaced with a fake transport."""
import base64
import json
from pathlib import Path
import struct
import sys
import tempfile
import threading
import unittest
from urllib.request import Request, urlopen
from urllib.error import HTTPError

BACKEND=Path(__file__).resolve().parents[1]/'mockups/curation-review/backend'
sys.path.insert(0,str(BACKEND))
from closed_preview import configuration,ClosedPreview,PreviewError,build_payload,validate_images
from closed_gateway import make_gateway

CASE=dict(id='test-case',dataset_id='cM2-e008_021-028_CH30',state_sha256='state-hash',source='U-1',phase_id='phase1',
    ai='SECRET_AI',rationale='SECRET_REASON',units=[dict(id='U-1',cluster_id=1,spikes=100,split=True),dict(id='U-2',cluster_id=2,spikes=50,split=False)])
# Valid header-sized synthetic PNG for schema tests; browser QA tests real PNG capture.
IMAGE='data:image/png;base64,'+base64.b64encode(b'\x89PNG\r\n\x1a\n'+struct.pack('>I',13)+b'IHDR'+struct.pack('>II',10,10)+b'\x00'*9).decode()
BODY=dict(request_id='request-0001',case_id='test-case',state_sha256='state-hash',view='detail',inspected_cluster_id='U-1',target_cluster_id=None,
    provider='openai',model='test-openai',images=[IMAGE],plot_descriptions=['Source mean waveform; amplitude in source units'],confirm_paid=True)

def configured(enabled=True):
    return configuration({'SPIKE_CLOSED_ENABLE_REQUESTS':'1' if enabled else '0',
        'SPIKE_CLOSED_OPENAI_API_KEY':'fake-openai-secret','SPIKE_CLOSED_OPENAI_MODELS':'test-openai',
        'SPIKE_CLOSED_ANTHROPIC_API_KEY':'fake-anthropic-secret','SPIKE_CLOSED_ANTHROPIC_MODELS':'test-anthropic'})

class PreviewTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.calls=[]
        def transport(endpoint,headers,payload):
            self.calls.append((endpoint,headers,payload))
            decision=json.dumps(dict(action='KEEP',rationale='Mock evidence only'))
            if 'anthropic' in endpoint:return dict(content=[dict(type='text',text=decision)],stop_reason='end_turn',usage={'output_tokens':5})
            return dict(choices=[dict(message=dict(content=decision),finish_reason='stop')],usage={'completion_tokens':5})
        self.transport=transport
        self.app=ClosedPreview({CASE['id']:CASE},Path(self.temp.name)/'preview.sqlite3',configured(),transport)
    def test_private_working_state_is_explicit_scoped_idempotent_and_excludes_feedback(self):
        value=dict(schema='spikeagent-live-working-case-v1',id='working:workspace-12345678:3:1',dataset_id='private-upload',mat_sha256='a'*64,state_sha256='b'*64,source='U-1',units=CASE['units'],ai='SECRET_PRIOR_AI',human_reason='SECRET_HUMAN_REASON')
        body={**BODY,'case_id':value['id'],'state_sha256':value['state_sha256'],'working_case':value,'view':'pca'}
        result=self.app.request(body)
        self.assertEqual(result['case_id'],value['id'])
        self.assertEqual(result['state_sha256'],value['state_sha256'])
        self.assertIn('client-provided',result['metadata']['evidence_origin'])
        payload=json.dumps(self.calls[0][2])
        self.assertNotIn('SECRET_PRIOR_AI',payload)
        self.assertNotIn('SECRET_HUMAN_REASON',payload)
        self.assertEqual(self.app.request(body),result)
        self.assertEqual(len(self.calls),1)
        for change in ({'state_sha256':'wrong'},{'units':[dict(id='U-1',cluster_id=1,spikes=-1,split=True)]},{'source':'U-99'}):
            with self.subTest(change=change),self.assertRaises(ValueError):
                self.app.request({**body,'request_id':'request-invalid-'+str(len(change)), 'working_case':{**value,**change}})
        self.assertEqual(len(self.calls),1)

    def tearDown(self):self.temp.cleanup()
    def test_default_gate_and_public_status_have_no_secrets(self):
        self.assertFalse(configuration({})['enabled'])
        self.app.config['enabled']=False
        with self.assertRaises(PermissionError):self.app.request(BODY)
        self.assertEqual(self.calls,[])
        status=json.dumps(self.app.status());self.assertNotIn('fake-openai-secret',status);self.assertNotIn('fake-anthropic-secret',status)
        self.assertEqual({p['id'] for p in self.app.status()['providers']},{'openai','anthropic'})
    def test_openai_payload_is_bounded_and_excludes_existing_answers(self):
        result=self.app.request(BODY)
        self.assertEqual(result['decision']['action'],'KEEP');self.assertFalse(result['review_saved'])
        endpoint,headers,payload=self.calls[0]
        self.assertEqual(headers['Authorization'],'Bearer fake-openai-secret')
        self.assertEqual(payload['max_completion_tokens'],1024)
        self.assertEqual(payload['messages'][0]['content'][1]['type'],'image_url')
        self.assertNotIn('SECRET_',json.dumps(payload));self.assertNotIn('secret',json.dumps(result))
        self.assertEqual(result['metadata']['source_spikes'],100)
    def test_anthropic_interface_uses_messages_image_schema(self):
        body={**BODY,'provider':'anthropic','model':'test-anthropic'}
        self.app.request(body);endpoint,headers,payload=self.calls[0]
        self.assertEqual(headers['anthropic-version'],'2023-06-01')
        self.assertNotIn('Authorization',headers);self.assertEqual(payload['max_tokens'],1024)
        self.assertEqual(payload['messages'][0]['content'][0]['source']['media_type'],'image/png')
    def test_openrouter_compatible_endpoint_uses_max_tokens(self):
        self.app.config['providers']['openai']['endpoint']='https://openrouter.ai/api/v1/chat/completions'
        self.app.request(BODY)
        self.assertIn('max_tokens',self.calls[0][2]);self.assertNotIn('max_completion_tokens',self.calls[0][2])
    def test_completion_is_idempotent_and_id_cannot_change_input(self):
        first=self.app.request(BODY);self.assertEqual(self.app.request(BODY),first);self.assertEqual(len(self.calls),1)
        with self.assertRaisesRegex(ValueError,'different input'):self.app.request({**BODY,'plot_descriptions':['different']})
        self.assertEqual(len(self.calls),1)
    def test_failed_request_never_retries_and_counts_toward_cap(self):
        self.app.transport=lambda *args:(_ for _ in ()).throw(OSError('fake failure'))
        with self.assertRaises(PreviewError):self.app.request(BODY)
        self.app.transport=self.transport
        with self.assertRaisesRegex(PreviewError,'already submitted'):self.app.request(BODY)
        self.app.config['max_requests']=1
        with self.assertRaisesRegex(PermissionError,'limit'):self.app.request({**BODY,'request_id':'request-0002'})
        self.assertEqual(self.calls,[])
    def test_preflight_rejects_paid_approval_model_state_and_target_errors(self):
        for updates in [dict(confirm_paid=False),dict(model='unapproved'),dict(state_sha256='wrong'),dict(target_cluster_id='U-1'),dict(inspected_cluster_id='missing'),dict(plot_descriptions=[]),dict(images=['https://bad.example/image.png'])]:
            with self.subTest(updates=updates):
                with self.assertRaises((ValueError,PermissionError)):self.app.request({**BODY,**updates})
        self.assertEqual(self.calls,[])
    def test_endpoint_rejects_untrusted_hosts_and_ports(self):
        for endpoint in ['http://api.openai.com/v1/chat/completions','https://evil.example/v1/chat/completions','https://api.openai.com:444/v1/chat/completions','https://api.openai.com/v1/chat/completions?key=oops']:
            self.app.config['providers']['openai']['endpoint']=endpoint
            with self.assertRaises(ValueError):self.app.request(BODY)
        self.assertEqual(self.calls,[])
    def test_phase_actions_and_oversized_images(self):
        _,_,_,metadata,_=build_payload(self.app.config,{**BODY,'target_cluster_id':'U-2'},CASE)
        self.assertEqual(metadata['allowed_actions'],['MERGE','NOT_MERGE','DISCARD','DEFER'])
        huge=base64.b64decode(IMAGE.split(',')[1]);huge=huge[:16]+struct.pack('>II',5000,5000)+huge[24:]
        with self.assertRaisesRegex(ValueError,'dimensions'):validate_images(['data:image/png;base64,'+base64.b64encode(huge).decode()])
    def test_scope_and_request_limit_configuration(self):
        self.app.cases['test-case']={**CASE,'dataset_id':'reserved-data'}
        with self.assertRaisesRegex(ValueError,'approved'):self.app.request(BODY)
        for n in ['0','101']:
            with self.assertRaises(ValueError):configuration({'SPIKE_CLOSED_MAX_REQUESTS':n})
    def test_gateway_cors_auth_and_private_routes(self):
        token='fake-gateway-token-for-tests-only-0001';origin='http://127.0.0.1:8767'
        server=make_gateway(self.app,token,[origin],port=0);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        base='http://127.0.0.1:'+str(server.server_port)
        def call(path,method='GET',auth=True,request_origin=origin,body=None):
            headers={'Origin':request_origin}
            if auth:headers['Authorization']='Bearer '+token
            if body is not None:headers['Content-Type']='application/json'
            return urlopen(Request(base+path,headers=headers,method=method,data=json.dumps(body).encode() if body is not None else None))
        try:
            with call('/api/closed/status') as r:
                self.assertEqual(r.status,200);self.assertEqual(r.headers['Access-Control-Allow-Origin'],origin)
            with call('/api/closed/request','OPTIONS',auth=False) as r:self.assertEqual(r.status,204)
            for path,auth,request_origin,code in [('/api/closed/status',False,origin,403),('/api/closed/status',True,'https://bad.example',403),('/api/export',True,origin,404),('/../reviews.sqlite3',True,origin,404)]:
                with self.assertRaises(HTTPError) as error:call(path,auth=auth,request_origin=request_origin)
                self.assertEqual(error.exception.code,code)
            with call('/api/closed/request','POST',body=BODY) as r:self.assertEqual(json.load(r)['decision']['action'],'KEEP')
            self.assertEqual(len(self.calls),1)
        finally:server.shutdown();server.server_close();thread.join()

if __name__=='__main__':unittest.main()
