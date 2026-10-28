"""Build read-only all-waveform image layers for the original review comparison.

Every member waveform is rasterized. Only PNGs and compact cluster membership
metadata are published; no raw waveform arrays, MAT/NPZ, times or labels leave
private storage. No inference, curation operation, expert label or database write.
"""
from __future__ import annotations
from collections import defaultdict
import argparse
from hashlib import sha256 as digest
from io import BytesIO
import json
from pathlib import Path
import threading
import numpy as np
from dataset_inputs import DatasetManager, ALLOWED
from import_jacob import ROOT, sha256, scoped_row
from review_analysis import AnalysisService

WIDTH, HEIGHT = 1600, 900
SCHEMA = 'review-all-waveform-layers-v1'


def rasterize(waves, fs):
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.collections import LineCollection
    from PIL import Image
    waves=np.asarray(waves,dtype=float)
    if waves.ndim!=2 or not len(waves) or waves.shape[1]<2 or not np.isfinite(waves).all():
        raise ValueError('Expected finite nonempty waveform rows')
    low,high=float(waves.min()),float(waves.max())
    pad=max((high-low)*.015,1e-6);bottom,top=low-pad,high+pad
    duration=(waves.shape[1]-1)/fs*1000
    fig=Figure(figsize=(WIDTH/100,HEIGHT/100),dpi=100,facecolor=(0,0,0,0))
    ax=fig.add_axes([0,0,1,1],frame_on=False);ax.patch.set_alpha(0)
    ax.set_xlim(0,duration);ax.set_ylim(bottom,top);ax.set_axis_off()
    canvas=FigureCanvasAgg(fig);x=np.arange(waves.shape[1])/fs*1000
    # No stride, selection limit, means, envelopes or quantile bands.
    for start in range(0,len(waves),4096):
        block=waves[start:start+4096]
        segments=np.empty((len(block),waves.shape[1],2),dtype=float)
        segments[:,:,0]=x;segments[:,:,1]=block
        ax.add_collection(LineCollection(segments,colors=[(1,1,1,.17)],linewidths=.45,antialiaseds=True))
    canvas.draw();stream=BytesIO();Image.fromarray(np.asarray(canvas.buffer_rgba())).save(stream,format='PNG',compress_level=6)
    fig.clear()
    return stream.getvalue(),dict(n_waveforms=len(waves),raw_low=low,raw_high=high,
        image_low=bottom,image_high=top,width=WIDTH,height=HEIGHT,all_members=True,mean=False,sd_band=False)


def manager():
    m=DatasetManager.__new__(DatasetManager);m.lock=threading.RLock();m.bundles={};m.paths={}
    for row in json.loads((ROOT/'output/curation_review_local/manager/registry.json').read_text()):
        m._load(row['id'],Path(row['path']))
    visible={r['id'] for r in m.summaries()}
    m.bundles={k:v for k,v in m.bundles.items() if k in visible};m.paths={k:v for k,v in m.paths.items() if k in visible}
    if len(m.bundles)!=7 or len(m.all_cases())!=262:raise ValueError('Review changed public scope before building')
    return m


def build(destination):
    destination=Path(destination).resolve()
    if destination!=ROOT/'output/curation_review_local/review_waveforms_v1':raise ValueError('Use the private reviewer image cache')
    destination.mkdir(parents=True,exist_ok=True);(destination/'atoms').mkdir(exist_ok=True)
    m=manager();service=AnalysisService(m);grouped=defaultdict(list)
    for case in m.all_cases().values():
        if case['dataset_id'] not in ALLOWED:raise ValueError('Unapproved dataset')
        grouped[case['dataset_id']].append(case)
    report=dict(schema=SCHEMA,states=0,cases=0,atoms=0,source_waveforms=0,raw_arrays_published=False,new_model_calls=0,datasets=[])
    for dataset,cases in grouped.items():
        first=cases[0];_,bundle,_=m.find_case(first['id']);inputs=service._inputs(first,bundle)
        fs,times,waves,over,initial,tree=inputs;row,path=scoped_row(dataset)
        if sha256(path)!=row['mat_sha256']:raise ValueError('Source MAT integrity mismatch')
        pairs=np.stack([initial,over],axis=1);unique,first_rows,inverse,counts=np.unique(pairs,axis=0,return_index=True,return_inverse=True,return_counts=True)
        states={}
        for case in cases:
            _,bundle,_=m.find_case(case['id'])
            if case['state_sha256'] not in states:
                assigns=service._assignments(case,bundle,inputs);labels=assigns[first_rows]
                if not np.array_equal(labels[inverse],assigns):raise ValueError('Snapshot splits an immutable overcluster atom')
                states[case['state_sha256']]=labels
            labels=states[case['state_sha256']]
            for unit in case['units']:
                if int(counts[labels==unit['cluster_id']].sum())!=unit['spikes']:raise ValueError('Snapshot member count mismatch')
        used=set(int(i) for labels in states.values() for i in np.flatnonzero(labels>0));atoms={}
        order=np.argsort(inverse,kind='stable');starts=np.r_[0,np.cumsum(counts)]
        for i in sorted(used):
            ids=order[starts[i]:starts[i+1]];png,meta=rasterize(waves[ids],fs);checksum=digest(png).hexdigest();name=dataset+'--'+str(i)+'--'+checksum+'.png'
            target=destination/'atoms'/name
            if not target.exists():target.write_bytes(png)
            if sha256(target)!=checksum:raise ValueError('Image cache integrity mismatch')
            atoms[str(i)]={**meta,'url':'/review-waveforms/atoms/'+name,'sha256':checksum}
            if len(atoms)%100==0:print(json.dumps(dict(dataset=dataset,atoms=len(atoms),of=len(used))),flush=True)
        for state,labels in states.items():
            units={str(int(cid)):dict(n_spikes=int(counts[labels==cid].sum()),atoms=[str(int(i)) for i in np.flatnonzero(labels==cid)]) for cid in np.unique(labels) if cid>0}
            needed={i for u in units.values() for i in u['atoms']}
            value=dict(schema=SCHEMA,dataset_id=dataset,state_sha256=state,mat_sha256=row['mat_sha256'],sampling_rate_hz=fs,n_samples=waves.shape[1],
                x_min_ms=0,x_max_ms=(waves.shape[1]-1)/fs*1000,units=units,atoms={i:atoms[i] for i in needed},all_members=True,random_sampling=False,mean=False,sd_band=False)
            (destination/(dataset+'--'+state+'.json')).write_text(json.dumps(value,separators=(',',':'))+'\n')
        item=dict(dataset_id=dataset,source_sha256=row['mat_sha256'],source_waveforms=len(waves),visible_source_waveforms=int(counts[list(used)].sum()),atoms=len(atoms),states=len(states),cases=len(cases))
        report['datasets'].append(item);report['states']+=len(states);report['cases']+=len(cases);report['atoms']+=len(atoms);report['source_waveforms']+=len(waves)
        print(json.dumps(item),flush=True)
    (destination/'BUILD_REPORT.json').write_text(json.dumps(report,indent=2)+'\n');return report


def compact():
    from PIL import Image
    from hashlib import sha256
    root=ROOT;source=root/'output/curation_review_local/review_waveforms_v1';dest=root/'output/curation_review_local/review_waveforms_full_v1';dest.mkdir(exist_ok=True);(dest/'atoms').mkdir(exist_ok=True);memo={};report={'states':0,'unit_images':0,'raw_arrays_published':False,'all_members':True}
    if not (source/'BUILD_REPORT.json').is_file():raise ValueError('Full layer generation has not completed')
    source_report=json.loads((source/'BUILD_REPORT.json').read_text());assert source_report['cases']==262 and len(list(source.glob('*--*.json')))==source_report['states']
    for path in sorted(source.glob('*--*.json')):
     state=json.loads(path.read_text());out_atoms={};out_units={}
     for cid,unit in state['units'].items():
      original=[state['atoms'][i] for i in unit['atoms']];key=sha256(json.dumps(sorted((a['sha256'],a['n_waveforms']) for a in original)).encode()).hexdigest()
      if key not in memo:
       low=min(a['raw_low'] for a in original);high=max(a['raw_high'] for a in original);pad=max((high-low)*.03,1e-6);bottom,top=low-pad,high+pad;canvas=Image.new('RGBA',(1600,900),(255,255,255,0));drawn=0
       for a in sorted(original,key=lambda a:-a['n_waveforms']):
        p=source/a['url'].removeprefix('/review-waveforms/');blob=p.read_bytes()
        if sha256(blob).hexdigest()!=a['sha256']:raise ValueError('Atomic layer integrity mismatch')
        image=Image.open(BytesIO(blob)).convert('RGBA');y=round((top-a['image_high'])/(top-bottom)*900);h=max(1,round((a['image_high']-a['image_low'])/(top-bottom)*900));image=image.resize((1600,h),resample=Image.Resampling.LANCZOS);canvas.alpha_composite(image,(0,y));drawn+=a['n_waveforms']
       if drawn!=unit['n_spikes']:raise ValueError('Not all waveforms were composed')
       # White mask + 256-entry alpha palette is lossless, not a density summary.
       alpha=canvas.getchannel('A');encoded=Image.fromarray(np.asarray(alpha),mode='P');encoded.putpalette([255]*768);encoded.info['transparency']=bytes(range(256));stream=BytesIO();encoded.save(stream,format='PNG',compress_level=6);blob=stream.getvalue();checksum=sha256(blob).hexdigest();name=state['dataset_id']+'--all-members--'+checksum+'.png';(dest/'atoms'/name).write_bytes(blob)
       memo[key]=dict(n_waveforms=drawn,raw_low=low,raw_high=high,image_low=bottom,image_high=top,width=1600,height=900,all_members=True,mean=False,sd_band=False,n_source_atom_layers=len(original),source_atom_layers_sha256=key,url='/review-waveforms/atoms/'+name,sha256=checksum)
       if len(memo)%20==0:print(json.dumps({'unit_images':len(memo),'states':report['states']}),flush=True)
      out_atoms[key]=memo[key];out_units[cid]={'n_spikes':unit['n_spikes'],'atoms':[key]}
     state['atoms']=out_atoms;state['units']=out_units;state['rendering']='all individual waveform image layers; no mean, SD, quantiles or sampling';(dest/path.name).write_text(json.dumps(state,separators=(',',':'))+'\n');report['states']+=1
    report['unit_images']=len(memo);report['bytes']=sum(p.stat().st_size for p in dest.rglob('*') if p.is_file());(dest/'BUILD_REPORT.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report),flush=True);return report

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,default=ROOT/'output/curation_review_local/review_waveforms_v1');p.add_argument('--compact-only',action='store_true');args=p.parse_args()
    if not args.compact_only:print(json.dumps(build(args.output)),flush=True)
    compact()
