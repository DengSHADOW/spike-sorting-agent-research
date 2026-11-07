/* Opt-in startup/refresh regression. An isolated browser context reuses CH3 and
 * CH31; saved reviews never leave that context. Only the public catalog is
 * delayed or failed, and private Runpod endpoints are never contacted. */
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const browserModule = process.env.SPIKE_REVIEW_BROWSER_MODULE;
const baseURL = process.env.SPIKE_REVIEW_TEST_URL;

test('public startup and refresh preserve the correct workspace without old pages or removed feedback prompts', {
  skip: !browserModule || !baseURL,
  timeout: 420000,
}, async t => {
  const {chromium} = require(browserModule);
  const browser = await chromium.launch({headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
  const context = await browser.newContext({viewport:{width:1600,height:1000}});
  await context.route(/^https?:\/\/[^/]*\.proxy\.runpod\.net(?:\/|$)/,route=>route.abort('blockedbyclient'));
  await context.addInitScript(() => {
    const report=window.__publicBoot={activations:[],legacyFrames:[],positiveFrames:[]};
    function visible(el) {
      if(!el?.getClientRects().length || ['hidden','collapse'].includes(getComputedStyle(el).visibility))return false;
      for(let node=el;node instanceof Element;node=node.parentElement) {
        const style=getComputedStyle(node);
        if(style.display==='none'||Number(style.opacity)===0||style.contentVisibility==='hidden')return false;
      }
      // inert changes hit-testing, not painted visibility. Do not use
      // elementFromPoint: it would miss the historical-page flash being tested.
      const r=el.getBoundingClientRect();
      return r.right>0&&r.bottom>0&&r.left<innerWidth&&r.top<innerHeight;
    }
    const active=()=>typeof WorkingReview!=='undefined'&&WorkingReview.active;
    function recordActivation() {
      const dataset=active()?WorkingReview.state.dataset_id:null;
      if(dataset&&report.activations.at(-1)!==dataset)report.activations.push(dataset);
    }
    function frame() {
      recordActivation();
      const title=document.querySelector('#case-title');
      const legacy=[...document.querySelectorAll('#case-list [data-case]')].some(visible)||(!active()&&visible(title)&&!!title.textContent.trim());
      if(legacy&&report.legacyFrames.length<20)report.legacyFrames.push({ms:Math.round(performance.now()),title:title?.textContent,source:document.querySelector('#source-badge')?.textContent});
      if([...document.querySelectorAll('#positive-panel,#positive-comment,label[for="positive-comment"]')].some(visible)&&report.positiveFrames.length<20)report.positiveFrames.push(Math.round(performance.now()));
      requestAnimationFrame(frame);
    }
    new MutationObserver(recordActivation).observe(document,{childList:true,subtree:true});
    requestAnimationFrame(frame);
  });
  const errors=[];
  function watch(page) {
    page.on('pageerror',error=>errors.push(error.stack||error.message));
    page.on('dialog',dialog=>dialog.accept());
  }
  const page=await context.newPage();watch(page);
  const dataset=channel=>'cM2-e004_004-006_'+channel;
  const url=channel=>baseURL.replace(/\/$/,'')+'/?public='+dataset(channel);
  const snapshot=()=>page.evaluate(()=>JSON.parse(JSON.stringify(WorkingReview.state)));
  const manifestPattern='**/public-data/manifest.json*';
  let ch31Initial,ch3Saved;
  const ch31Note='Preserve this CH31 draft while loading the exact channel requested by the URL.';

  async function settled(channel,target=page) {
    await target.waitForFunction(id=>typeof WorkingReview!=='undefined'&&WorkingReview.active&&!WorkingReview.busy&&WorkingReview.state.dataset_id===id,dataset(channel),{timeout:120000});
    await target.waitForFunction(()=>document.querySelector('[data-main-wave] canvas')?.dataset.complete==='true',null,{timeout:120000});
  }
  async function screenshot(name,target=page) {
    if(!process.env.SPIKE_REVIEW_SCREENSHOT_DIR)return;
    fs.mkdirSync(process.env.SPIKE_REVIEW_SCREENSHOT_DIR,{recursive:true});
    await target.screenshot({path:path.join(process.env.SPIKE_REVIEW_SCREENSHOT_DIR,name)});
  }
  async function noRemovedPrompts(target=page) {
    const text=await target.locator('body').innerText();
    assert.doesNotMatch(text,/Inspect both panels,\s*then confirm\.?/i);
    assert.doesNotMatch(text,/Positive feedback/i);
    assert.doesNotMatch(text,/Preview only\b/i);
    assert.doesNotMatch(text,/Agree selected\.|Disagree selected\./);
    assert.equal(await target.getByRole('button',{name:'Cancel choice',exact:true}).count(),0);
    assert.doesNotMatch(text,/Jacob reference\s*·\s*historical annotation/i);
    assert.doesNotMatch(text,/No exact reference matches this grouping and selection\.?/i);
    for(const selector of ['#positive-panel','#positive-comment','label[for="positive-comment"]']) {
      assert.equal(await target.locator(selector).isVisible(),false,selector+' stays hidden, including compatibility storage fields');
    }
  }
  async function bootReport(target=page) {
    return target.evaluate(()=>({active:typeof WorkingReview!=='undefined'&&WorkingReview.active,...window.__publicBoot}));
  }
  function assertPending(report,label) {
    assert.equal(report.active,false,label+': no workspace activates before the catalog is ready');
    assert.deepEqual(report.activations,[],label+': no historical or other-channel activation');
    assert.deepEqual(report.legacyFrames,[],label+': no painted snapshot or demo');
    assert.deepEqual(report.positiveFrames,[],label+': no Positive feedback flash');
  }
  async function assertFinished(channel,target=page) {
    const report=await bootReport(target);
    assert.deepEqual(report.activations,[dataset(channel)],'Only the requested channel activates during startup');
    assert.deepEqual(report.legacyFrames,[],'No historical or synthetic records were painted');
    assert.deepEqual(report.positiveFrames,[],'Positive feedback never became visible');
    await noRemovedPrompts(target);
  }
  async function gatedNavigation(channel,reload=false) {
    let release,hits=0,seen;
    const gate=new Promise(resolve=>{release=resolve;});
    const requestSeen=new Promise(resolve=>{seen=resolve;});
    const route=async request=>{hits++;seen();await gate;await request.continue();};
    await page.route(manifestPattern,route);
    try {
      if(reload)await page.reload({waitUntil:'domcontentloaded'});
      else await page.goto(url(channel),{waitUntil:'domcontentloaded'});
      await page.locator('#public-load-panel').waitFor({state:'visible',timeout:8000});
      await requestSeen;
      await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
      assert.ok(hits>0,'The catalog request was held pending for a real rendered frame');
      const report=await bootReport();
      await screenshot((reload?'refresh-':'startup-')+channel+'-pending.png');
      return report;
    } finally {
      release();
      await page.unroute(manifestPattern,route);
    }
  }
  async function loadButton(channel) {
    const group=page.locator('.queue-file-group').filter({has:page.locator('summary strong[title="'+dataset(channel)+'"]')});
    assert.equal(await group.count(),1,'The original per-file dataset group is retained');
    if(!await group.evaluate(el=>el.open))await group.locator('summary').click();
    await group.getByRole('button',{name:'Load data',exact:true}).click();
    await settled(channel);
  }

  try {
    await t.test('cold startup shows loading instead of an old snapshot or demo',async()=>{
      assertPending(await gatedNavigation('CH31'),'Cold CH31 load');
      await settled('CH31');await assertFinished('CH31');
      assert.equal(await page.locator('[data-working-cluster]').count(),17);
      await page.waitForFunction(()=>document.body.dataset.publicPreload==='ready',null,{timeout:120000});
      ch31Initial=await snapshot();
    });
    await t.test('refresh hides the old page and preserves an unconfirmed CH31 draft',async()=>{
      await page.locator('#disagree-button').click();
      await page.locator('#action-grid [data-action="KEEP"]').click();
      await page.locator('#general-reason-choice').selectOption('__new__');
      await page.locator('#review-note').fill(ch31Note);
      await noRemovedPrompts();
      assertPending(await gatedNavigation('CH31',true),'CH31 refresh');
      await settled('CH31');await assertFinished('CH31');
      assert.deepEqual(await snapshot(),ch31Initial);
      assert.equal(await page.locator('#review-note').inputValue(),ch31Note);
      assert.equal(await page.locator('#disagree-button').getAttribute('aria-pressed'),'true');
    });
    await t.test('a CH3 URL never activates the previously saved CH31 workspace first',async()=>{
      assertPending(await gatedNavigation('CH3'),'CH3 navigation with CH31 saved');
      await settled('CH3');await assertFinished('CH3');
      assert.equal(await page.locator('[data-working-cluster]').count(),25);
      assert.equal(await page.evaluate(()=>WorkingReview.selected.source),1);
      assert.ok(Number(await page.locator('[data-main-wave] canvas').getAttribute('data-drawn'))>0);
      assert.equal(await page.locator('#suggestion .suggestion-action strong').innerText(),'SPLIT');
    });
    await t.test('Agree previews and confirms the real SPLIT without removed prompts or positive feedback',async()=>{
      await page.locator('#agree-button').click();
      await page.waitForFunction(()=>WorkingReview.preview?.ready,null,{timeout:90000});
      assert.equal(await page.evaluate(()=>WorkingReview.preview.action),'SPLIT');
      await noRemovedPrompts();await screenshot('CH3-clean-agree-split-preview.png');
      await page.waitForFunction(()=>!document.querySelector('#save-button').disabled,null,{timeout:30000});
      await page.locator('#save-button').click();
      await page.waitForFunction(()=>WorkingReview.state.version===1&&!WorkingReview.busy,null,{timeout:30000});
      await settled('CH3');ch3Saved=await snapshot();
      const choice=ch3Saved.events.at(-1).choice;
      assert.equal(choice.action,'SPLIT');assert.equal(choice.agree,true);
      assert.equal(choice.agent_id,'Agent_1');assert.equal(choice.preview.confirmed,true);
      assert.equal(await page.evaluate(()=>WorkingReview.preview),null);
      await noRemovedPrompts();
    });
    await t.test('public reviews remain visible in History and Revisions and Export JSON still works',async()=>{
      const eventId='working:'+ch3Saved.workspace_id+':'+ch3Saved.events.at(-1).id;
      for(const [view,root] of [['history','#history-list'],['revisions','#revision-list']]) {
        await page.locator('#primary-navigation [data-view="'+view+'"]').click();
        const record=page.locator(root+' [data-activity-record="'+eventId+'"]');
        await record.waitFor({state:'visible',timeout:20000});
        assert.match(await record.innerText(),/Working curation/);
        assert.match(await record.innerText(),/split/i);
        await record.getByRole('button',{name:'Open current workspace',exact:true}).click();
        await page.waitForFunction(()=>currentView==='review',null,{timeout:20000});
        await settled('CH3');assert.deepEqual(await snapshot(),ch3Saved);
      }
      await page.locator('#primary-navigation [data-view="history"]').click();
      await page.locator('#history-list [data-activity-record="'+eventId+'"]').waitFor({state:'visible',timeout:20000});
      assert.equal(await page.evaluate(()=>realCatalog),null,'Public startup must not fabricate a historical catalog');
      const [download]=await Promise.all([page.waitForEvent('download',{timeout:20000}),page.locator('#history-export').click()]);
      const stream=await download.createReadStream();let json='';
      for await(const chunk of stream)json+=chunk.toString('utf8');
      const exported=JSON.parse(json);
      assert.equal(exported.schema,'spikeagent-human-review-v3');
      assert.equal(exported.synthetic,false);assert.equal(exported.storage_mode,'browser');
      assert.equal(Array.isArray(exported.reviews),true);assert.equal(Array.isArray(exported.events),true);
      await page.locator('.studio-link').click();await settled('CH3');await noRemovedPrompts();
    });
    await t.test('confirmed CH3 grouping and history survive a clean refresh',async()=>{
      assertPending(await gatedNavigation('CH3',true),'Saved CH3 refresh');
      await settled('CH3');await assertFinished('CH3');
      assert.deepEqual(await snapshot(),ch3Saved);
      await screenshot('CH3-clean-restored-split.png');
    });
    await t.test('the original Load data controls restore the other channel draft without new dataset tabs',async()=>{
      assert.equal(await page.locator('#public-dataset-picker').count(),0);
      assert.deepEqual(await page.locator('#data-source option').evaluateAll(nodes=>nodes.map(node=>node.value).sort()),[dataset('CH3'),dataset('CH31')].sort());
      assert.equal(await page.locator('.queue-file-group').count(),2);
      await loadButton('CH31');assert.deepEqual(await snapshot(),ch31Initial);
      assert.equal(await page.locator('#review-note').inputValue(),ch31Note);
      await noRemovedPrompts();
    });
    await t.test('a failed catalog leaves a visible Retry entry without exposing historical records',async()=>{
      const failed=await context.newPage();watch(failed);
      const fail=route=>route.fulfill({status:503,contentType:'text/plain',body:'Catalog temporarily unavailable for startup regression.'});
      try {
        await failed.route(manifestPattern,fail);
        await failed.goto(url('CH3'),{waitUntil:'domcontentloaded'});
        await failed.locator('#public-load-retry').waitFor({state:'visible',timeout:20000});
        assert.equal(await failed.locator('#public-load-retry').isEnabled(),true);
        assert.equal(await failed.locator('#public-load-panel').getAttribute('role'),'alert');
        assertPending(await bootReport(failed),'Catalog failure');await noRemovedPrompts(failed);
        await screenshot('public-catalog-failure-retry.png',failed);
        await failed.unroute(manifestPattern,fail);
        await Promise.all([failed.waitForNavigation({waitUntil:'domcontentloaded'}),failed.locator('#public-load-retry').click()]);
        await settled('CH3',failed);await assertFinished('CH3',failed);
        assert.deepEqual(await failed.evaluate(()=>JSON.parse(JSON.stringify(WorkingReview.state))),ch3Saved,'Retry opens the existing CH3 workspace without resetting it');
        assert.deepEqual(await snapshot(),ch31Initial,'The recovery tab does not mutate another open workspace');
        assert.equal(await page.locator('#review-note').inputValue(),ch31Note);
      } finally {await failed.close();}
    });
    await t.test('the original import page keeps its Runpod/upload tabs and file controls',async()=>{
      await page.locator('#inline-change-dataset').click();await page.waitForURL(/data-sources\.html/);
      await page.waitForFunction(()=>document.querySelectorAll('#public-datasets .dataset-row').length===2,null,{timeout:30000});
      assert.equal(await page.locator('.source-tabs').isVisible(),true);
      assert.equal(await page.locator('[data-source="runpod"]').isVisible(),true);
      await page.locator('[data-source="upload"]').click();
      assert.equal(await page.locator('#upload-panel').isVisible(),true);
      assert.equal(await page.locator('#dataset-files').isVisible(),true);
      assert.equal(await page.locator('#recording-name').isVisible(),true);
      assert.equal(await page.locator('#dataset-files').getAttribute('type'),'file');
      await page.locator('[data-source="runpod"]').click();
      await page.locator('#public-datasets a.public-open[href$="public='+dataset('CH31')+'"]').click();
      await settled('CH31');await assertFinished('CH31');
      assert.deepEqual(await snapshot(),ch31Initial);
      assert.equal(await page.locator('#review-note').inputValue(),ch31Note);
    });
    await t.test('startup, refresh, preview, save, history and recovery produce no uncaught JavaScript errors',()=>assert.deepEqual(errors,[]));
  } finally {await context.close();await browser.close();}
});
