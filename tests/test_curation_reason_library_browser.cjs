/* Opt-in public reason-library regression. All drafts and custom reasons stay
 * in isolated browser storage. No reviews are confirmed or uploaded, and
 * private Runpod endpoints are blocked. */
const test = require('node:test');
const assert = require('node:assert/strict');
const browserModule = process.env.SPIKE_REVIEW_BROWSER_MODULE;
const baseURL = process.env.SPIKE_REVIEW_TEST_URL;

test('public review loads every action reason and preserves drafts during asynchronous loading', {
  skip: !browserModule || !baseURL,
  timeout: 300000,
}, async t => {
  const {chromium} = require(browserModule);
  const browser = await chromium.launch({headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
  const errors = [];
  const contexts = [];
  const dataset = channel=>'cM2-e004_004-006_'+channel;
  const url = channel=>baseURL.replace(/\/$/,'')+'/?public='+dataset(channel);
  const customDiscard = 'Retained reviewer observation for discarding this waveform.';
  const customSplit = 'Retained reviewer observation for separating waveform families.';
  const typedReason = 'Keep this unconfirmed explanation while the complete library arrives.';
  const typedNote = 'This pending draft must survive loading and refresh without being saved.';

  async function newPage(custom=false) {
    const context = await browser.newContext({viewport:{width:1600,height:1000}});
    contexts.push(context);
    await context.route(/^https?:\/\/[^/]*\.proxy\.runpod\.net(?:\/|$)/,route=>route.abort('blockedbyclient'));
    // StaticReview implements an in-browser API, so gate its real export call
    // rather than intercepting an HTTP request that never exists.
    await context.addInitScript(({custom,customDiscard,customSplit})=>{
      if(custom&&!localStorage.getItem('spikeagent-studio-action-reasons-v1')) {
        localStorage.setItem('spikeagent-studio-action-reasons-v1',JSON.stringify([
          {text:customDiscard,actions:['DISCARD']},
          {text:customSplit,actions:['SPLIT']},
        ]));
      }
      const report=window.__reasonLoading={exports:0,released:false};
      let release,adapter;
      const gate=new Promise(resolve=>{release=resolve;});
      window.__releaseReasonExport=()=>{report.released=true;release();};
      Object.defineProperty(window,'StaticReview',{
        configurable:true,
        get:()=>adapter,
        set:value=>{
          const original=value.api.bind(value);
          window.__rawReasonAPI=original;
          adapter={...value,api:async(path,...args)=>{
            if(path==='/api/reasons/export') {report.exports++;await gate;}
            return original(path,...args);
          }};
        },
      });
    },{custom,customDiscard,customSplit});
    const page=await context.newPage();
    page.on('pageerror',error=>errors.push(error.stack||error.message));
    page.on('dialog',dialog=>dialog.accept());
    return page;
  }
  async function settled(page,channel) {
    await page.waitForFunction(id=>typeof WorkingReview!=='undefined'&&WorkingReview.active&&!WorkingReview.busy&&WorkingReview.state.dataset_id===id,dataset(channel),{timeout:120000});
    await page.waitForFunction(()=>document.querySelector('[data-main-wave] canvas')?.dataset.complete==='true',null,{timeout:120000});
  }
  async function exportPending(page) {
    await page.waitForFunction(()=>window.__reasonLoading?.exports>0,null,{timeout:30000});
    assert.equal(await page.evaluate(()=>window.__reasonLoading.released),false);
  }
  async function completeLibrary(page) {
    await page.evaluate(()=>window.__releaseReasonExport());
    await page.waitForFunction(()=>state.reasons.length>=156,null,{timeout:30000});
  }
  async function choose(page,action) {
    if(await page.locator('#disagree-button').getAttribute('aria-pressed')!=='true')await page.locator('#disagree-button').click();
    await page.locator('#action-grid [data-action="'+action+'"]').click();
    if(action==='SPLIT')await page.waitForFunction(()=>WorkingReview.preview?.ready,null,{timeout:90000});
  }
  async function reasons(page,action) {
    return page.evaluate(async action=>{
      const source=(await window.__rawReasonAPI('/api/reasons/export',undefined,reviewerId)).reasons;
      const expected=ActionReasons.forAction(ActionReasons.catalog(CurationReasons.library(source),source,ActionReasons.read(localStorage)),action).map(r=>r.text);
      const actual=[...document.querySelector('#general-reason-choice').options].filter(o=>o.value!==''&&o.value!=='__new__').map(o=>o.textContent);
      return {expected,actual,status:document.querySelector('#general-reason-status').textContent,sourceCount:source.length};
    },action);
  }
  async function assertReasons(page,action,count) {
    await page.waitForFunction(({action,count})=>document.querySelector('#general-reason-status')?.textContent===ACTIONS[action].label+' · '+count+' reasons',{action,count},{timeout:30000});
    const result=await reasons(page,action);
    assert.ok(result.sourceCount>=156,'Compare against the complete exported source library');
    assert.equal(result.expected.length,count);
    assert.deepEqual(result.actual,result.expected,'Options match the sorted complete catalog for this action');
    assert.equal(new Set(result.actual).size,count,'Generalized duplicate reasons appear only once');
    return result;
  }
  async function cancel(page) {
    await page.locator('#studio-undo').click();
    assert.equal(await page.locator('#disagree-button').getAttribute('aria-pressed'),'false');
  }
  async function load(page,channel) {
    const group=page.locator('.queue-file-group').filter({has:page.locator('summary strong[title="'+dataset(channel)+'"]')});
    if(!await group.evaluate(el=>el.open))await group.locator('summary').click();
    await group.getByRole('button',{name:'Load data',exact:true}).click();
    await settled(page,channel);
  }
  async function draftState(page) {
    return page.evaluate(()=>({state:JSON.parse(JSON.stringify(WorkingReview.state)),reason:document.querySelector('#reason-select').value,note:document.querySelector('#review-note').value,action:document.querySelector('#action-grid .selected')?.dataset.action,agree:document.querySelector('#disagree-button').getAttribute('aria-pressed'),agent:document.querySelector('#agent-choice').value}));
  }

  try {
    const clean=await newPage();
    await clean.goto(url('CH3'),{waitUntil:'domcontentloaded'});
    await settled(clean,'CH3');
    await t.test('public startup requests the complete library instead of stopping at the search endpoint limit',async()=>{
      await exportPending(clean);
      assert.ok(await clean.evaluate(()=>state.reasons.length<156),'The full export is deliberately pending');
      await completeLibrary(clean);
    });
    await t.test('CH3 offers all 43 Discard and 13 Split reasons with exact catalog text and no cross-action options',async()=>{
      await choose(clean,'DISCARD');await assertReasons(clean,'DISCARD',43);await cancel(clean);
      await choose(clean,'SPLIT');await assertReasons(clean,'SPLIT',13);await cancel(clean);
    });
    await t.test('the original Load data control opens CH31 with the same complete action catalogs',async()=>{
      await load(clean,'CH31');
      await choose(clean,'DISCARD');await assertReasons(clean,'DISCARD',43);await cancel(clean);
      await choose(clean,'SPLIT');await assertReasons(clean,'SPLIT',13);await cancel(clean);
      await choose(clean,'MERGE');await assertReasons(clean,'MERGE',1);await cancel(clean);
    });

    const cached=await newPage(true);
    await cached.goto(url('CH3'),{waitUntil:'domcontentloaded'});
    await settled(cached,'CH3');await exportPending(cached);
    let pending;
    await t.test('a typed Discard draft and action-specific saved reasons survive a delayed full-library response',async()=>{
      await choose(cached,'DISCARD');
      await cached.locator('#general-reason-choice').selectOption('__new__');
      await cached.locator('#reason-select').fill(typedReason);
      await cached.locator('#review-note').fill(typedNote);
      pending=await draftState(cached);
      assert.equal(pending.reason,typedReason);assert.equal(pending.note,typedNote);
      assert.equal(pending.action,'DISCARD');assert.equal(pending.agree,'true');
      await completeLibrary(cached);
      const result=await assertReasons(cached,'DISCARD',44);
      assert.ok(result.actual.includes(customDiscard));assert.ok(!result.actual.includes(customSplit));
      assert.deepEqual(await draftState(cached),pending,'Loading reasons cannot change the draft or grouping');
      assert.equal(await cached.locator('#general-reason-choice').inputValue(),'__new__');
    });
    await t.test('refresh retains the typed draft and saved custom action association before and after the next export',async()=>{
      await cached.reload({waitUntil:'domcontentloaded'});
      await settled(cached,'CH3');await exportPending(cached);
      assert.deepEqual(await draftState(cached),pending);
      await completeLibrary(cached);await assertReasons(cached,'DISCARD',44);
      assert.deepEqual(await draftState(cached),pending);
      await cancel(cached);
      await choose(cached,'SPLIT');
      const result=await assertReasons(cached,'SPLIT',14);
      assert.ok(result.actual.includes(customSplit));assert.ok(!result.actual.includes(customDiscard));
      await cancel(cached);
      assert.equal(await cached.evaluate(()=>WorkingReview.state.version),0,'No grouping or review was saved by these tests');
    });
    await t.test('full-library loading and draft restoration produce no uncaught JavaScript errors',()=>assert.deepEqual(errors,[]));
  }finally{
    for(const context of contexts)await context.close();
    await browser.close();
  }
});
