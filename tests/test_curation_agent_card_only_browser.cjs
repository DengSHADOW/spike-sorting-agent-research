/* Opt-in regression for the original Firebase import/channel UI with only the
 * saved Agent card fixed. All choices live in this isolated browser context.
 * Private Runpod requests are blocked; no inference or upload is performed. */
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const browserModule = process.env.SPIKE_REVIEW_BROWSER_MODULE;
const baseURL = process.env.SPIKE_REVIEW_TEST_URL;

test('original Firebase controls remain intact while Agent cards show uploaded actions and rationales', {
  skip: !browserModule || !baseURL,
  timeout: 300000,
}, async t => {
  const {chromium} = require(browserModule);
  const browser = await chromium.launch({headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
  const context = await browser.newContext({viewport:{width:1600,height:1000}});
  const errors = [];
  // The rollback deliberately retains the original import UI/endpoint. Its
  // availability is outside this display-only change, so never contact it.
  await context.route(/^https?:\/\/[^/]*\.proxy\.runpod\.net(?:\/|$)/, route=>route.abort('blockedbyclient'));
  const page = await context.newPage();
  page.on('pageerror',e=>errors.push(e.stack||e.message));
  page.on('dialog',dialog=>dialog.accept());
  const dataset = channel=>'cM2-e004_004-006_'+channel;
  const url = channel=>baseURL.replace(/\/$/,'')+'/?public='+dataset(channel);
  const snapshot = ()=>page.evaluate(()=>JSON.parse(JSON.stringify(WorkingReview.state)));
  const normalized = text=>text.replace(/\s+/g,' ').trim();
  const requests = {CH3:0,CH31:0};
  page.on('request',request=>{
    for(const channel of ['CH3','CH31']) {
      if(request.url().includes('/public-data/packages/'+dataset(channel)+'.'))requests[channel]++;
    }
  });
  async function settled(channel) {
    await page.waitForFunction(id=>typeof WorkingReview!=='undefined'&&WorkingReview.active&&!WorkingReview.busy&&WorkingReview.state.dataset_id===id,dataset(channel),{timeout:120000});
    await page.waitForFunction(()=>document.querySelector('[data-main-wave] canvas')?.dataset.complete==='true',null,{timeout:120000});
  }
  async function noAILabels() {
    assert.equal(await page.locator('#case-list .agent-object-label').count(),0,'Queue thumbnails have no AI action labels');
    assert.equal(await page.locator('#case-list .queue-thumb-card.agent-object').count(),0,'Queue thumbnails have no Agent-specific outline');
  }
  async function checkAgents(channel,total) {
    assert.equal(await page.locator('[data-working-cluster]').count(),total);
    assert.equal(await page.evaluate(()=>WorkingReview.selected.source),1);
    assert.ok(Number(await page.locator('[data-main-wave] canvas').getAttribute('data-drawn'))>0,'Verified member waveforms finish drawing');
    for(const agent of ['Agent_1','Agent_2']) {
      await page.locator('#agent-choice').selectOption(agent);
      const row = await page.evaluate(agentId=>WorkingReview.input.data.agent_suggestions.find(r=>r.agent_id===agentId&&r.phase==='phase1'&&r.source===1&&(r.target??null)===null&&r.state_sha256===WorkingReview.input.data.initial_state_sha256),agent);
      assert.ok(row,'The displayed Agent decision exists in the uploaded package');
      assert.equal(row.action,'SPLIT');
      assert.equal(await page.locator('#suggestion .suggestion-action strong').innerText(),row.action);
      assert.equal(normalized(await page.locator('#suggestion .rationale').innerText()),normalized(row.rationale),'Card rationale matches the uploaded decision');
      const replay=page.locator('#suggestion details.agent-replay-details');
      assert.equal(await replay.evaluate(el=>el.open),false);
      assert.doesNotMatch(await page.locator('#suggestion').innerText(),/Expert replay, step /);
      await replay.locator('summary').click();
      assert.match(await replay.innerText(),/Expert replay, step /);
      if(agent==='Agent_2')assert.match(await replay.innerText(),new RegExp('learned on '+(channel==='CH3'?'CH31':'CH3')));
      await replay.locator('summary').click();
      await noAILabels();
    }
    await page.locator('#agent-choice').selectOption('Agent_1');
  }
  async function makeCacheOld() {
    await page.evaluate(async()=>{
      const db=await new Promise((resolve,reject)=>{
        const r=indexedDB.open('spikeagent-working-curation-v1',1);
        r.onsuccess=()=>resolve(r.result);r.onerror=()=>reject(r.error);
      });
      try {
        await new Promise((resolve,reject)=>{
          const tx=db.transaction('datasets','readwrite'),store=tx.objectStore('datasets'),r=store.get(WorkingReview.state.source_sha256);
          r.onsuccess=()=>{const data=r.result;delete data.agent_suggestions;data.public_package_sha256='0'.repeat(64);store.put(data);};
          tx.oncomplete=resolve;tx.onerror=tx.onabort=()=>reject(tx.error);
        });
      }finally{db.close();}
    });
  }
  async function keepDraft(note) {
    await page.locator('#action-grid [data-action="KEEP"]').click();
    await page.locator('#general-reason-choice').selectOption('__new__');
    await page.locator('#review-note').fill(note);
  }
  async function chooseUsingOriginalLoadButton(channel) {
    const group=page.locator('.queue-file-group').filter({has:page.locator('summary strong[title="'+dataset(channel)+'"]')});
    assert.equal(await group.count(),1,'The original per-file dataset group remains available');
    if(!await group.evaluate(el=>el.open))await group.locator('summary').click();
    await group.getByRole('button',{name:'Load data',exact:true}).click();
    await settled(channel);
  }
  async function chooseUsingOriginalSelector(channel) {
    await page.evaluate(id=>{
      const select=document.querySelector('#data-source');
      select.value=id;select.dispatchEvent(new Event('change',{bubbles:true}));
    },dataset(channel));
    await settled(channel);
  }
  async function screenshot(name) {
    if(!process.env.SPIKE_REVIEW_SCREENSHOT_DIR)return;
    fs.mkdirSync(process.env.SPIKE_REVIEW_SCREENSHOT_DIR,{recursive:true});
    await page.screenshot({path:path.join(process.env.SPIKE_REVIEW_SCREENSHOT_DIR,name)});
  }
  let ch3Saved,ch31Initial;
  const ch3Note='Keep this original-interface CH3 choice while the Agent card data is refreshed.';
  const ch31Note='Retain this CH31 draft with the original Firebase dataset controls.';
  try {
    await page.goto(url('CH3'),{waitUntil:'domcontentloaded'});await settled('CH3');
    await page.waitForFunction(()=>document.body.dataset.publicPreload==='ready',null,{timeout:120000});
    await t.test('CH3 shows its 25 real clusters and both uploaded Agent actions/rationales, without AI thumbnail tags',()=>checkAgents('CH3',25));
    await t.test('the original dataset selector and per-file Load data UI are retained instead of new top-level tabs',async()=>{
      assert.equal(await page.locator('#public-dataset-picker').count(),0);
      assert.deepEqual(await page.locator('#data-source option').evaluateAll(nodes=>nodes.map(n=>n.value).sort()),[dataset('CH3'),dataset('CH31')].sort());
      assert.equal(await page.locator('#inline-change-dataset').count(),1);
      assert.equal(await page.locator('#inline-start-working').count(),1);
      assert.equal(await page.locator('.queue-file-group').count(),2);
    });
    await t.test('saved user actions retain their own thumbnail label and reviewer history',async()=>{
      await keepDraft(ch3Note);
      await page.waitForFunction(()=>!document.querySelector('#save-button').disabled,null,{timeout:30000});
      await page.locator('#save-button').click();
      await page.waitForFunction(()=>WorkingReview.state.version===1&&!WorkingReview.busy,null,{timeout:30000});
      ch3Saved=await snapshot();
      assert.equal(ch3Saved.events.at(-1).choice.action,'KEEP');
      assert.equal(ch3Saved.events.at(-1).choice.note,ch3Note);
      assert.match(await page.locator('[data-working-cluster="1"] .queue-item-meta').innerText(),/KEEP/,'Removing AI badges does not remove saved reviewer tags');
      await noAILabels();
    });
    await t.test('an old CH3 browser package upgrades its Agent data without replacing confirmed work',async()=>{
      await makeCacheOld();const before=requests.CH3;
      await page.reload({waitUntil:'domcontentloaded'});await settled('CH3');
      assert.ok(requests.CH3>before,'The outdated package is revalidated');
      assert.deepEqual(await snapshot(),ch3Saved,'Exact labels, events, undo and workspace identity remain intact');
      await checkAgents('CH3',25);
      assert.match(await page.locator('[data-working-cluster="1"] .queue-item-meta').innerText(),/KEEP/);
      await screenshot('CH3-agent-card-original-interface.png');
    });
    await t.test('the original Load data button switches to CH31 and its two saved Agent cards',async()=>{
      await chooseUsingOriginalLoadButton('CH31');await checkAgents('CH31',17);
      ch31Initial=await snapshot();
    });
    await t.test('updating an old CH31 package also preserves its unconfirmed draft and original grouping',async()=>{
      await keepDraft(ch31Note);await page.evaluate(()=>WorkingReview.cacheDraft());
      await makeCacheOld();const before=requests.CH31;
      await page.reload({waitUntil:'domcontentloaded'});await settled('CH31');
      assert.ok(requests.CH31>before,'The old CH31 Agent package is revalidated');
      assert.deepEqual(await snapshot(),ch31Initial);
      assert.equal(await page.locator('#review-note').inputValue(),ch31Note);
      assert.equal(await page.evaluate(()=>WorkingReview.input.data.agent_suggestions.length),94);
      assert.equal(await page.locator('#suggestion .suggestion-action strong').innerText(),'SPLIT');
      await noAILabels();
    });
    await t.test('the original selector returns to CH3 while both channels keep their saved work',async()=>{
      await chooseUsingOriginalSelector('CH3');assert.deepEqual(await snapshot(),ch3Saved);
      await chooseUsingOriginalSelector('CH31');assert.deepEqual(await snapshot(),ch31Initial);
      assert.equal(await page.locator('#review-note').inputValue(),ch31Note);
      await chooseUsingOriginalSelector('CH3');assert.deepEqual(await snapshot(),ch3Saved);
      await noAILabels();
    });
    await t.test('the original import page keeps its Runpod/upload tabs and file controls',async()=>{
      await page.locator('#inline-change-dataset').click();
      await page.waitForURL(/data-sources\.html/);
      await page.waitForFunction(()=>document.querySelectorAll('#public-datasets .dataset-row').length===2,null,{timeout:30000});
      assert.equal(await page.locator('.source-tabs').isVisible(),true);
      assert.equal(await page.locator('[data-source="runpod"]').isVisible(),true);
      assert.equal(await page.locator('[data-source="upload"]').isVisible(),true);
      await page.locator('[data-source="upload"]').click();
      assert.equal(await page.locator('#upload-panel').isVisible(),true);
      assert.equal(await page.locator('#dataset-files').isVisible(),true);
      assert.equal(await page.locator('#recording-name').isVisible(),true);
      assert.equal(await page.locator('#dataset-files').getAttribute('type'),'file');
      assert.equal(await page.locator('#start-upload').count(),1);
      await page.locator('[data-source="runpod"]').click();
      await page.locator('#public-datasets a.public-open[href$="public='+dataset('CH3')+'"]').click();
      await settled('CH3');assert.deepEqual(await snapshot(),ch3Saved);
      await noAILabels();
    });
    await t.test('Agent display, original controls and cache upgrades have no uncaught JavaScript errors',()=>assert.deepEqual(errors,[]));
  }finally{await context.close();await browser.close();}
});
