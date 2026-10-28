"""Exact centered waveform PCA, and public raster layers for approved states only."""
from __future__ import annotations
from collections import defaultdict
from hashlib import sha256 as digest
import hashlib
from io import BytesIO
import json
from pathlib import Path
import numpy as np
from PIL import Image
from build_pair_waveforms import manager
from import_jacob import ROOT, scoped_row, sha256
from review_analysis import AnalysisService
from dataset_inputs import ALLOWED


def fit(waves):
    waves=np.asarray(waves,dtype=np.float64)
    if waves.ndim!=2 or not len(waves) or not np.isfinite(waves).all():raise ValueError('Finite waveform rows required')
    mean=waves.mean(axis=0);center=waves-mean
    cov=center.T@center/max(1,len(waves)-1);values,vectors=np.linalg.eigh(cov);order=np.argsort(-values,kind='stable');values=np.maximum(values[order],0);vectors=vectors[:,order[:3]]
    for i in range(vectors.shape[1]):
        if vectors[np.argmax(abs(vectors[:,i])),i]<0:vectors[:,i]*=-1
    scores=center@vectors
    if scores.shape[1]<3:scores=np.pad(scores,((0,0),(0,3-scores.shape[1])))
    return scores,[float(v/values.sum()) if values.sum() else 0. for v in values[:3]]


def raster(points,bounds,width=1100,height=650):
    # Every projected member contributes to the raster, including coincident points.
    lowx,highx,lowy,highy=bounds;counts=np.zeros((height,width),dtype=np.uint32)
    x=np.clip(np.rint((points[:,0]-lowx)/(highx-lowx)*(width-1)).astype(int),0,width-1);y=np.clip(np.rint((highy-points[:,1])/(highy-lowy)*(height-1)).astype(int),0,height-1)
    np.add.at(counts,(y,x),1)
    alpha=np.asarray(np.rint(255*(-np.expm1(counts.astype(float)*np.log(.82)))),dtype=np.uint8)
    im=Image.fromarray(alpha,mode='P');im.putpalette([255]*768);im.info['transparency']=bytes(range(256));stream=BytesIO();im.save(stream,format='PNG',compress_level=6);return stream.getvalue()


def compact_layers(destination):
    root=Path(destination);memo={};images=0
    for p in sorted(root.glob('*--*.json')):
     d=json.loads(p.read_text());atoms={}
     for cid,u in d['units'].items():
      key=hashlib.sha256(json.dumps(sorted(u['atoms'])).encode()+d['dataset_id'].encode()).hexdigest()
      if key not in memo:
       views=[]
       for pc in range(2):
        canvas=Image.new('RGBA',(1100,650),(255,255,255,0))
        for atom in u['atoms']:
         a=d['atoms'][atom]['views'][pc];f=root/a['url'].removeprefix('/review-pca/');assert hashlib.sha256(f.read_bytes()).hexdigest()==a['sha256'];canvas.alpha_composite(Image.open(f).convert('RGBA'))
        alpha=canvas.getchannel('A');im=Image.fromarray(__import__('numpy').asarray(alpha),mode='P');im.putpalette([255]*768);im.info['transparency']=bytes(range(256));stream=BytesIO();im.save(stream,format='PNG',compress_level=6);blob=stream.getvalue();sha=hashlib.sha256(blob).hexdigest();name=d['dataset_id']+'--members--'+sha+'.png';(root/'atoms'/name).write_bytes(blob);views.append({'url':'/review-pca/atoms/'+name,'sha256':sha});images+=1
       memo[key]={'n_spikes':u['n_spikes'],'views':views}
      atoms[key]=memo[key];u['atoms']=[key]
     d['atoms']=atoms;p.write_text(json.dumps(d,separators=(',',':'))+'\n')
    return {'states':len(list(root.glob('*--*.json'))),'combined_images':images}


def build():
    dest=ROOT/'output/curation_review_local/review_pca_v1';dest.mkdir(exist_ok=True);(dest/'atoms').mkdir(exist_ok=True)
    m=manager();service=AnalysisService(m);groups=defaultdict(list)
    for c in m.all_cases().values():
        if c['dataset_id'] not in ALLOWED:raise ValueError('Outside approved data scope')
        groups[c['dataset_id']].append(c)
    report={'states':0,'images':0,'cases':0,'raw_arrays_published':False,'new_model_calls':0,'datasets':[]}
    for dataset,cases in groups.items():
        first=cases[0];_,bundle,_=m.find_case(first['id']);inputs=service._inputs(first,bundle);fs,times,waves,over,initial,tree=inputs;row,path=scoped_row(dataset)
        if sha256(path)!=row['mat_sha256']:raise ValueError('MAT integrity mismatch')
        pairs=np.stack([initial,over],axis=1);unique,first_rows,inverse,counts=np.unique(pairs,axis=0,return_index=True,return_inverse=True,return_counts=True);states={}
        for c in cases:
            _,b,_=m.find_case(c['id'])
            if c['state_sha256'] not in states:
                assigns=service._assignments(c,b,inputs);labels=assigns[first_rows]
                if not np.array_equal(labels[inverse],assigns):raise ValueError('Invalid atom membership')
                states[c['state_sha256']]=labels
        used=sorted({int(i) for labels in states.values() for i in np.flatnonzero(labels>0)});visible=np.isin(inverse,used);visible_rows=np.flatnonzero(visible);scores,variance=fit(waves[visible]);mapped=np.full(len(waves),-1,dtype=np.int64);mapped[visible_rows]=np.arange(len(visible_rows));bounds=[]
        for pair in [(0,1),(0,2)]:
            xs,ys=scores[:,pair[0]],scores[:,pair[1]];lx,hx=float(xs.min()),float(xs.max());ly,hy=float(ys.min()),float(ys.max());px=max((hx-lx)*.04,1e-6);py=max((hy-ly)*.04,1e-6);bounds.append([lx-px,hx+px,ly-py,hy+py])
        atoms={}
        for i in used:
            local=mapped[np.flatnonzero(inverse==i)];views=[]
            for k,pair in enumerate([(0,1),(0,2)]):
                png=raster(scores[local][:,pair],bounds[k]);checksum=digest(png).hexdigest();name=f'{dataset}--{i}--{k}--{checksum}.png';(dest/'atoms'/name).write_bytes(png);views.append({'url':'/review-pca/atoms/'+name,'sha256':checksum})
            atoms[str(i)]={'n_spikes':int(counts[i]),'views':views};report['images']+=2
        for state,labels in states.items():
            units={str(int(cid)):{'n_spikes':int(counts[labels==cid].sum()),'atoms':[str(int(i)) for i in np.flatnonzero(labels==cid)]} for cid in np.unique(labels) if cid>0};needed={i for u in units.values() for i in u['atoms']}
            value={'schema':'review-pca-layers-v1','dataset_id':dataset,'state_sha256':state,'mat_sha256':row['mat_sha256'],'fit_n':len(visible_rows),'fit_scope':'all approved member waveforms in this recording; shared basis across saved states','variance':variance,'bounds':bounds,'units':units,'atoms':{i:atoms[i] for i in needed},'all_members':True,'sampled':False,'normalized':False}
            (dest/(dataset+'--'+state+'.json')).write_text(json.dumps(value,separators=(',',':'))+'\n');report['states']+=1
        report['cases']+=len(cases);report['datasets'].append({'dataset_id':dataset,'fit_n':len(visible_rows),'states':len(states),'atoms':len(used)});print(json.dumps(report['datasets'][-1]),flush=True)
    report['compact']=compact_layers(dest);(dest/'BUILD_REPORT.json').write_text(json.dumps(report,indent=2)+'\n');return report

if __name__=='__main__':print(json.dumps(build()),flush=True)
