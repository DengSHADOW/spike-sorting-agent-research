"""Explicit single-request model preview. Disabled by default, no key discovery.

Only dedicated SPIKE_CLOSED_* configuration is used. No .env loading, retries,
fallbacks, experiments or curation execution. API secrets never leave the server.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import struct
import time
from urllib.request import Request,build_opener,HTTPRedirectHandler
from urllib.error import HTTPError
from urllib.parse import urlsplit

DEFAULT_ENDPOINTS={'openai':'https://api.openai.com/v1/chat/completions','anthropic':'https://api.anthropic.com/v1/messages'}
ALLOWED_HOSTS={'openai':{'api.openai.com','openrouter.ai'},'anthropic':{'api.anthropic.com'}}


class PreviewError(Exception):pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):raise PreviewError('Provider redirects are not allowed')


def digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def configuration(env=None):
    env=os.environ if env is None else env
    providers={}
    for provider in DEFAULT_ENDPOINTS:
        prefix='SPIKE_CLOSED_'+provider.upper()
        models=[v.strip() for v in env.get(prefix+'_MODELS','').split(',') if v.strip()]
        providers[provider]=dict(key=env.get(prefix+'_API_KEY',''),models=models,
            endpoint=env.get(prefix+'_ENDPOINT',DEFAULT_ENDPOINTS[provider]))
    tokens=int(env.get('SPIKE_CLOSED_MAX_OUTPUT_TOKENS','1024'))
    if not 64<=tokens<=4096:raise ValueError('Output token limit must be 64–4096')
    limit=int(env.get('SPIKE_CLOSED_MAX_REQUESTS','10'))
    if not 1<=limit<=100:raise ValueError('Gateway request limit must be 1–100')
    return dict(enabled=env.get('SPIKE_CLOSED_ENABLE_REQUESTS')=='1',providers=providers,max_tokens=tokens,
                max_requests=limit)


def public_status(config):
    return dict(enabled=config['enabled'],protocol='reviewer-snapshot-preview-v1',
        providers=[dict(id=name,configured=bool(row['key'] and row['models']),models=row['models']) for name,row in config['providers'].items()],
        max_output_tokens=config['max_tokens'],notice='Single live preview only. Provider charges may apply. No automatic experiment or curation execution.')


def validate_images(images):
    if not isinstance(images,list) or not 1<=len(images)<=3:raise ValueError('Capture one to three visible evidence plots')
    result=[]
    for image in images:
        if not isinstance(image,str) or not image.startswith('data:image/png;base64,'):raise ValueError('Only inline PNG plots are supported')
        try:data=base64.b64decode(image.split(',',1)[1],validate=True)
        except Exception:raise ValueError('Invalid PNG encoding')
        if not 33<=len(data)<=2_000_000 or not data.startswith(b'\x89PNG\r\n\x1a\n') or data[12:16]!=b'IHDR':raise ValueError('Invalid or oversized plot')
        width,height=struct.unpack('>II',data[16:24])
        if not 1<=width<=2000 or not 1<=height<=2000 or width*height>3_000_000:raise ValueError('Plot dimensions exceed the preview limit')
        result.append(dict(url=image,data=image.split(',',1)[1],sha256=hashlib.sha256(data).hexdigest()))
    return result


def build_payload(config,body,case):
    provider=body.get('provider');model=body.get('model')
    if provider not in config['providers']:raise ValueError('Choose a supported provider interface')
    settings=config['providers'][provider]
    if not settings['key'] or model not in settings['models']:raise ValueError('This model is not configured on the gateway')
    endpoint=urlsplit(settings['endpoint'])
    if endpoint.scheme!='https' or endpoint.hostname not in ALLOWED_HOSTS[provider] or endpoint.username or endpoint.password or endpoint.query or endpoint.fragment:
        raise ValueError('Provider endpoint is outside the configured official API hosts')
    expected_paths={'openai':{'/v1/chat/completions','/api/v1/chat/completions'},'anthropic':{'/v1/messages'}}
    if endpoint.path not in expected_paths[provider] or endpoint.port not in (None,443):raise ValueError('Unsupported provider API path')
    if body.get('confirm_paid') is not True:raise PermissionError('Explicit authorization for this single paid preview is required')
    view=body.get('view')
    if view not in {'detail','pair','quality','overview','gallery','timing','amplitude','stability','tree','pca','correlation'}:raise ValueError('Unknown evidence view')
    if body.get('state_sha256')!=case['state_sha256']:raise ValueError('Evidence state changed; reopen the live window')
    units={u['id']:u for u in case['units']}
    inspected=body.get('inspected_cluster_id')
    target=body.get('target_cluster_id')
    if inspected not in units or target is not None and (target not in units or target==case['source']):raise ValueError('Unknown inspected/candidate cluster')
    images=validate_images(body.get('images'))
    actions=['MERGE','NOT_MERGE','DISCARD','DEFER'] if target else ['KEEP','DISCARD','DEFER']+(['SPLIT'] if units[case['source']]['split'] else [])
    metadata=dict(dataset_id=case['dataset_id'],state_sha256=case['state_sha256'],source_cluster=units[case['source']]['cluster_id'],
        source_spikes=units[case['source']]['spikes'],inspected_cluster=units[inspected]['cluster_id'],
        target_cluster=units[target]['cluster_id'] if target else None,view=view,
        evidence_scope='client-rendered reviewer plots; not the frozen experiment input protocol',allowed_actions=actions)
    descriptions=body.get('plot_descriptions',[])
    if not isinstance(descriptions,list) or len(descriptions)!=len(images) or any(not isinstance(v,str) or len(v)>4000 for v in descriptions):
        raise ValueError('Each plot needs a bounded evidence title and legend')
    metadata['plot_descriptions']=descriptions
    if case.get('evidence_origin'):metadata['evidence_origin']=case['evidence_origin']
    prompt='Assess this extracellular-spike cluster using only the provided reviewer evidence and metadata. Images correspond to the named view and inspected cluster, which may differ from the review source. If evidence is insufficient, choose DEFER. Do not infer missing measurements. Return only JSON with action (one of allowed_actions) and a brief rationale. Do not execute anything.\n'+json.dumps(metadata,sort_keys=True)
    if provider=='openai':
        content=[dict(type='text',text=prompt)]+[dict(type='image_url',image_url=dict(url=image['url'],detail='high')) for image in images]
        token_field='max_completion_tokens' if endpoint.hostname=='api.openai.com' else 'max_tokens'
        payload=dict(model=model,messages=[dict(role='user',content=content)],stream=False)
        payload[token_field]=config['max_tokens']
        headers={'Authorization':'Bearer '+settings['key'],'Content-Type':'application/json'}
    else:
        content=[dict(type='image',source=dict(type='base64',media_type='image/png',data=image['data'])) for image in images]+[dict(type='text',text=prompt)]
        payload=dict(model=model,max_tokens=config['max_tokens'],messages=[dict(role='user',content=content)],stream=False)
        headers={'x-api-key':settings['key'],'anthropic-version':'2023-06-01','Content-Type':'application/json'}
    return settings['endpoint'],headers,payload,metadata,images


def send_once(endpoint,headers,payload):
    data=json.dumps(payload).encode()
    try:
        with build_opener(NoRedirect()).open(Request(endpoint,data=data,headers=headers,method='POST'),timeout=45) as response:
            raw=response.read(2_000_001)
            if len(raw)>2_000_000:raise PreviewError('Provider response exceeds the preview limit')
            return json.loads(raw)
    except HTTPError as exc:
        raise PreviewError(f'Provider returned HTTP {exc.code}; this request is not retried automatically') from None
    except (OSError,ValueError):
        raise PreviewError('Provider connection or response failed; do not retry without checking possible charges') from None


def working_case(body):
    """Validate client-provided private-state metadata; never claim server reconstruction."""
    value=body.get('working_case')
    if not isinstance(value,dict) or value.get('schema')!='spikeagent-live-working-case-v1':raise ValueError('Unknown case on this gateway')
    identity=value.get('id')
    if not isinstance(identity,str) or not re.fullmatch(r'working:[A-Za-z0-9_-]{8,100}:[0-9]+:[0-9]+',identity) or identity!=body.get('case_id'):raise ValueError('Invalid working case identity')
    if not isinstance(value.get('dataset_id'),str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,150}',value['dataset_id']):raise ValueError('Invalid working dataset')
    if any(not isinstance(value.get(k),str) or not re.fullmatch(r'[a-f0-9]{64}',value[k]) for k in ('state_sha256','mat_sha256')):raise ValueError('Invalid working state hash')
    rows=value.get('units')
    if not isinstance(rows,list) or not 1<=len(rows)<=10000:raise ValueError('Invalid working cluster list')
    units=[];ids=set();total=0
    for row in rows:
        if not isinstance(row,dict) or type(row.get('cluster_id')) is not int or not 1<=row['cluster_id']<=2147483647 or row.get('id')!='U-'+str(row['cluster_id']) or row['id'] in ids or type(row.get('spikes')) is not int or not 1<=row['spikes']<=2000000 or type(row.get('split')) is not bool:raise ValueError('Invalid working cluster metadata')
        ids.add(row['id']);total+=row['spikes'];units.append({k:row[k] for k in ('id','cluster_id','spikes','split')})
    if total>2000000 or value.get('source') not in ids:raise ValueError('Invalid working source or member count')
    return dict(id=identity,dataset_id=value['dataset_id'],state_sha256=value['state_sha256'],mat_sha256=value['mat_sha256'],source=value['source'],units=units,evidence_origin='client-provided private working state; no server membership reconstruction')


class ClosedPreview:
    def __init__(self,cases,path,config=None,transport=None):
        self.cases=cases;self.path=Path(path);self.config=configuration() if config is None else config
        self.transport=transport or send_once
        self.path.parent.mkdir(parents=True,exist_ok=True)
        with sqlite3.connect(self.path) as db:
            db.execute('CREATE TABLE IF NOT EXISTS preview_requests (id TEXT PRIMARY KEY,fingerprint TEXT NOT NULL,status TEXT NOT NULL,payload TEXT)')
        self.path.chmod(0o600)

    def status(self):return public_status(self.config)

    def request(self,body):
        if not self.config['enabled']:raise PermissionError('Live model requests are disabled on this gateway')
        identity=body.get('request_id')
        if not isinstance(identity,str) or not re.fullmatch(r'[A-Za-z0-9_-]{8,100}',identity):raise ValueError('A request ID is required')
        case=self.cases.get(body.get('case_id'))
        if case is None:case=working_case(body)
        from dataset_inputs import ALLOWED
        if case['dataset_id'] not in ALLOWED and not case.get('evidence_origin'):raise ValueError('Dataset is outside the approved review scope')
        endpoint,headers,payload,metadata,images=build_payload(self.config,body,case)
        fingerprint=digest(body)
        with sqlite3.connect(self.path,timeout=10) as db:
            db.execute('BEGIN IMMEDIATE')
            previous=db.execute('SELECT fingerprint,status,payload FROM preview_requests WHERE id=?',(identity,)).fetchone()
            if previous:
                if previous[0]!=fingerprint:raise ValueError('Request ID belongs to different input')
                if previous[1]=='completed':return json.loads(previous[2])
                raise PreviewError('This request was already submitted or failed; it will not be sent again automatically')
            count=db.execute('SELECT count(*) FROM preview_requests').fetchone()[0]
            if count>=self.config['max_requests']:raise PermissionError('Gateway request limit reached; owner configuration is required')
            db.execute('INSERT INTO preview_requests VALUES(?,?,?,NULL)',(identity,fingerprint,'submitted'))
        try:
            raw=self.transport(endpoint,headers,payload)
            if body['provider']=='openai':
                choice=raw['choices'][0]
                if choice.get('finish_reason')!='stop':raise PreviewError('The model did not finish normally; no action is accepted')
                text=choice['message']['content']
            else:
                if raw.get('stop_reason')!='end_turn':raise PreviewError('The model did not finish normally; no action is accepted')
                text=''.join(part.get('text','') for part in raw['content'] if part.get('type')=='text')
            decision=json.loads(text)
            if not isinstance(decision,dict):raise PreviewError('Expected a JSON decision object')
            if decision.get('action') not in metadata['allowed_actions'] or not isinstance(decision.get('rationale'),str) or len(decision['rationale'])>10000:
                raise PreviewError('Model output did not match the preview action schema')
            result=dict(schema='spikeagent-live-preview-v1',agent_id='Agent_3',case_id=case['id'],state_sha256=case['state_sha256'],
                request_id=identity,request_sha256=digest(payload),response_sha256=digest(raw),image_sha256=[i['sha256'] for i in images],
                provider=body['provider'],model=body['model'],metadata=metadata,decision=decision,usage=raw.get('usage',{}),
                timestamp=time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),execution_status='not_executed',review_saved=False)
            with sqlite3.connect(self.path) as db:db.execute('UPDATE preview_requests SET status=?,payload=? WHERE id=?',('completed',json.dumps(result),identity))
            return result
        except Exception as exc:
            with sqlite3.connect(self.path) as db:db.execute('UPDATE preview_requests SET status=? WHERE id=?',('failed',identity))
            if isinstance(exc,PreviewError):raise
            raise PreviewError('Invalid model response or local processing failure; this request is not retried') from None
