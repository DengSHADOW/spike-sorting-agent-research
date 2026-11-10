/* Opt-in real-browser regression for the Agent card and original Firebase UI.
 * One isolated browser context reuses both public packages. All reviewer data
 * stays in that context; private Runpod requests are blocked, with no inference,
 * upload, or production-data writes.
 */
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const browserModule = process.env.SPIKE_REVIEW_BROWSER_MODULE;
const baseURL = process.env.SPIKE_REVIEW_TEST_URL;

test('Agent feedback works without changing the original dataset and import controls', {
  skip: !browserModule || !baseURL,
  timeout: 420000,
}, async t => {
  const {chromium} = require(browserModule);
  const browser = await chromium.launch({headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
  const context = await browser.newContext({viewport:{width:1600,height:1000}});
  await context.route(/^https?:\/\/[^/]*\.proxy\.runpod\.net(?:\/|$)/,route=>route.abort('blockedbyclient'));
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror',error=>errors.push(error.stack||error.message));
  page.on('dialog',dialog=>dialog.accept());
  const dataset = channel=>'cM2-e004_004-006_'+channel;
  const normalized = value=>value.replace(/\s+/g,' ').trim();
  const snapshot = ()=>page.evaluate(()=>JSON.parse(JSON.stringify(WorkingReview.state)));
  const grouping = state=>({labels:state.labels,tree:state.tree,row_labels:state.row_labels||{}});
  const ch3Note = 'Retain this CH3 cluster after checking its waveform and interval evidence.';
  let ch3Initial,ch3Saved,ch31Initial;

  async function settled(channel) {
    await page.waitForFunction(id=>typeof WorkingReview!=='undefined'&&WorkingReview.active&&!WorkingReview.busy&&WorkingReview.state.dataset_id===id,dataset(channel),{timeout:120000});
    await page.waitForFunction(()=>document.querySelector('[data-main-wave] canvas')?.dataset.complete==='true',null,{timeout:120000});
  }
  async function reload(channel) {
    await page.reload({waitUntil:'domcontentloaded'});
    await settled(channel);
  }
  async function pressed(selector,value) {
    assert.equal(await page.locator(selector).getAttribute('aria-pressed'),String(value));
    assert.equal(await page.locator(selector).evaluate(el=>el.classList.contains('selected')),value);
  }
  async function noUnwantedText() {
    const visible = await page.locator('body').innerText();
    assert.doesNotMatch(visible,/Jacob reference\s*·\s*historical annotation/i);
    assert.doesNotMatch(visible,/No exact reference matches this grouping and selection\.?/i);
    assert.doesNotMatch(visible,/Dataset refreshed; original assignments restored\. No saved actions, labels or history\./);
    assert.equal(await page.locator('#jacob-reference-panel').isVisible(),false);
    assert.equal(await page.locator('#case-list .agent-object-label').count(),0);
    assert.equal(await page.locator('#case-list .queue-thumb-card.agent-object').count(),0);
    assert.equal(await page.locator('#positive-panel').isVisible(),false);
    assert.equal(await page.getByRole('button',{name:'Cancel choice',exact:true}).count(),0);
    assert.doesNotMatch(visible,/Agree selected\.|Disagree selected\.|Inspect both panels|Preview only/);
  }
  async function checkAgentCard(agent) {
    await page.locator(`[data-agent-choice="${agent}"]`).click();
    const row = await page.evaluate(agentId=>WorkingReview.input.data.agent_suggestions.find(r=>r.agent_id===agentId&&r.phase==='phase1'&&r.source===WorkingReview.selected.source&&(r.target??null)===null&&r.state_sha256===WorkingReview.input.data.initial_state_sha256),agent);
    assert.ok(row,'The card uses an actual uploaded Agent suggestion');
    assert.equal(await page.locator('#suggestion .suggestion-action strong').innerText(),row.action);
    assert.match(await page.locator('#suggestion .rationale-label').innerText(),/^Rationale\s*:?$/i);
    assert.equal(await page.locator('#suggestion .rationale').isVisible(),true);
    assert.equal(normalized(await page.locator('#suggestion .rationale').innerText()),normalized(row.rationale));
    const details = page.locator('#suggestion details.agent-replay-details');
    assert.equal(await details.count(),1);
    assert.equal(await details.evaluate(el=>el.open),false,'Expert replay metadata starts collapsed');
    assert.doesNotMatch(await page.locator('#suggestion').innerText(),/Expert replay, step /);
    await details.locator('summary').click();
    assert.equal(await details.evaluate(el=>el.open),true);
    assert.ok(normalized(await details.innerText()).includes(normalized(row.note)),'Replay provenance can still be inspected');
    await details.locator('summary').click();
    await pressed('#agree-button',false);
    await pressed('#disagree-button',false);
    await noUnwantedText();
  }
  async function loadButton(channel) {
    const group=page.locator('.queue-file-group').filter({has:page.locator('summary strong[title="'+dataset(channel)+'"]')});
    assert.equal(await group.count(),1);
    if(!await group.evaluate(el=>el.open))await group.locator('summary').click();
    await group.getByRole('button',{name:'Load data',exact:true}).click();
    await settled(channel);
  }
  async function originalSelector(channel) {
    await page.locator('#data-source').evaluate((select,id)=>{
      select.value=id;select.dispatchEvent(new Event('change',{bubbles:true}));
    },dataset(channel));
    await settled(channel);
  }
  async function confirmVersion(version) {
    await page.waitForFunction(()=>!document.querySelector('#save-button').disabled,null,{timeout:90000});
    await page.locator('#save-button').click();
    await page.waitForFunction(v=>WorkingReview.state.version===v&&!WorkingReview.busy,version,{timeout:30000});
    await settled(await page.evaluate(()=>WorkingReview.state.dataset_id.split('_').at(-1)));
  }
  async function screenshot(name) {
    if(!process.env.SPIKE_REVIEW_SCREENSHOT_DIR)return;
    fs.mkdirSync(process.env.SPIKE_REVIEW_SCREENSHOT_DIR,{recursive:true});
    await page.screenshot({path:path.join(process.env.SPIKE_REVIEW_SCREENSHOT_DIR,name)});
  }

  try {
    await page.goto(baseURL.replace(/\/$/,'')+'/?public='+dataset('CH3'),{waitUntil:'domcontentloaded'});
    await settled('CH3');
    await page.waitForFunction(()=>document.body.dataset.publicPreload==='ready',null,{timeout:120000});
    ch3Initial=await snapshot();

    await t.test('CH3 loads 25 real clusters and both Agent cards show the labelled rationale with collapsed provenance',async()=>{
      assert.equal(await page.locator('[data-working-cluster]').count(),25);
      assert.equal(await page.evaluate(()=>WorkingReview.selected.source),1);
      assert.ok(Number(await page.locator('[data-main-wave] canvas').getAttribute('data-drawn'))>0);
      await checkAgentCard('Agent_1');await checkAgentCard('Agent_2');
      await page.locator('[data-agent-choice="Agent_1"]').click();
    });

    await t.test('Disagree immediately shows selected feedback while assignments remain unchanged and Confirm is disabled',async()=>{
      await page.locator('#disagree-button').click();
      await pressed('#disagree-button',true);await pressed('#agree-button',false);
      assert.equal(await page.locator('#inline-agreement-status').count(),0);
      assert.equal(await page.locator('#save-button').isEnabled(),false);
      assert.equal(await page.locator('#studio-undo').isEnabled(),true);
      assert.equal(await page.locator('#agent-choice').isEnabled(),false,'Pending feedback cannot be assigned to a different Agent');
      for(const button of await page.locator('[data-agent-choice]').all())assert.equal(await button.isEnabled(),false,'The visible toggle locks with pending feedback');
      assert.equal(await page.locator('#action-grid .selected').count(),0);
      assert.deepEqual(await snapshot(),ch3Initial);
    });

    await t.test('an action-free Disagree draft survives refresh and Cancel removes it without changing data',async()=>{
      await reload('CH3');
      assert.equal(await page.locator('#agent-choice').inputValue(),'Agent_1');
      await pressed('#disagree-button',true);await pressed('#agree-button',false);
      assert.equal(await page.locator('#save-button').isEnabled(),false);
      assert.deepEqual(await snapshot(),ch3Initial);
      await page.locator('#studio-undo').click();
      await pressed('#disagree-button',false);await pressed('#agree-button',false);
      assert.equal(await page.locator('#agent-choice').isEnabled(),true);
      for(const button of await page.locator('[data-agent-choice]').all())assert.equal(await button.isEnabled(),true,'Cancel unlocks the visible toggle');
      assert.deepEqual(await snapshot(),ch3Initial);
    });

    await t.test('Agent 2 agreement previews the real Split and Cancel leaves grouping and Agent 1 feedback unchanged',async()=>{
      await page.locator('[data-agent-choice="Agent_2"]').click();
      await pressed('#disagree-button',false);await pressed('#agree-button',false);
      await page.locator('#agree-button').click();
      await pressed('#agree-button',true);await pressed('#disagree-button',false);
      assert.equal(await page.locator('#inline-agreement-status').count(),0);
      assert.equal(await page.evaluate(()=>WorkingReview.preview?.action),'SPLIT');
      assert.deepEqual(await snapshot(),ch3Initial);
      await page.waitForFunction(()=>WorkingReview.preview?.ready,null,{timeout:90000});
      await screenshot('agent2-agree-split-preview.png');
      await page.locator('#studio-undo').click();
      assert.equal(await page.evaluate(()=>WorkingReview.preview),null);
      await pressed('#agree-button',false);await pressed('#disagree-button',false);
      assert.deepEqual(await snapshot(),ch3Initial);
      await page.locator('[data-agent-choice="Agent_1"]').click();
      await pressed('#agree-button',false);await pressed('#disagree-button',false);
    });

    await t.test('Disagree requires an explicit replacement action and reason or note, without an automatic commit',async()=>{
      await page.locator('[data-agent-choice="Agent_2"]').click();
      await page.locator('#disagree-button').click();
      assert.equal(await page.locator('#save-button').isEnabled(),false);
      await page.locator('#action-grid [data-action="KEEP"]').click();
      await pressed('#disagree-button',true);
      assert.equal(await page.locator('#save-button').isEnabled(),false);
      assert.equal(await page.locator('#save-button').innerText(),'Add a reason or note');
      assert.deepEqual(await snapshot(),ch3Initial);
      await page.locator('#general-reason-choice').selectOption('__new__');
      await page.locator('#review-note').fill(ch3Note);
      await page.waitForFunction(()=>!document.querySelector('#save-button').disabled,null,{timeout:30000});
      assert.deepEqual(await snapshot(),ch3Initial);
    });

    await t.test('a complete unconfirmed draft refreshes with the same Agent, selection and note',async()=>{
      await reload('CH3');
      assert.equal(await page.locator('#agent-choice').inputValue(),'Agent_2');
      await pressed('#disagree-button',true);await pressed('#agree-button',false);
      assert.equal(await page.locator('#review-note').inputValue(),ch3Note);
      assert.equal(await page.locator('#action-grid .selected').getAttribute('data-action'),'KEEP');
      assert.equal(await page.locator('#agent-choice').isEnabled(),false);
      assert.deepEqual(await snapshot(),ch3Initial);
    });

    await t.test('Confirm saves the actual Disagree choice and Agent identity, and refresh retains it',async()=>{
      await confirmVersion(1);ch3Saved=await snapshot();
      assert.equal(ch3Saved.events.at(-1).choice.action,'KEEP');
      assert.equal(ch3Saved.events.at(-1).choice.agree,false);
      assert.equal(ch3Saved.events.at(-1).choice.agent_id,'Agent_2');
      assert.equal(ch3Saved.events.at(-1).choice.note,ch3Note);
      assert.match(await page.locator('[data-working-cluster="1"] .queue-item-meta').innerText(),/KEEP/);
      await reload('CH3');assert.deepEqual(await snapshot(),ch3Saved);
      assert.match(await page.locator('#user-choice-card').innerText(),/Disagree/);
      await noUnwantedText();await screenshot('agent2-disagree-saved.png');
    });

    await t.test('the original per-file Load data button opens CH31 with 17 clusters and both Agent cards',async()=>{
      await loadButton('CH31');
      assert.equal(await page.locator('[data-working-cluster]').count(),17);
      assert.equal(await page.evaluate(()=>WorkingReview.selected.source),1);
      ch31Initial=await snapshot();
      await checkAgentCard('Agent_1');await checkAgentCard('Agent_2');
      assert.equal(await page.locator('#public-dataset-picker').count(),0);
      assert.equal(await page.locator('.queue-file-group').count(),2);
      assert.deepEqual(await page.locator('#data-source option').evaluateAll(nodes=>nodes.map(n=>n.value).sort()),[dataset('CH3'),dataset('CH31')].sort());
    });

    await t.test('a pending draft returns to its original Agent after visiting the other channel',async()=>{
      await page.locator('[data-agent-choice="Agent_1"]').click();
      await page.locator('#disagree-button').click();
      await page.locator('#action-grid [data-action="KEEP"]').click();
      await page.locator('#general-reason-choice').selectOption('__new__');
      const note='This CH31 draft belongs to Agent 1 after a channel switch.';
      await page.locator('#review-note').fill(note);
      await loadButton('CH3');
      await page.locator('[data-agent-choice="Agent_2"]').click();
      await loadButton('CH31');
      assert.equal(await page.locator('#agent-choice').inputValue(),'Agent_1');
      await pressed('#disagree-button',true);
      assert.equal(await page.locator('#review-note').inputValue(),note);
      assert.deepEqual(await snapshot(),ch31Initial);
      await page.locator('#studio-undo').click();
      assert.deepEqual(await snapshot(),ch31Initial);
    });

    await t.test('Agree applies a Split only after Confirm and saves its preview provenance',async()=>{
      await page.locator('[data-agent-choice="Agent_1"]').click();
      await page.locator('#agree-button').click();
      await pressed('#agree-button',true);await pressed('#disagree-button',false);
      assert.equal(await page.evaluate(()=>WorkingReview.preview?.action),'SPLIT');
      assert.deepEqual(await snapshot(),ch31Initial);
      await confirmVersion(1);
      const saved=await snapshot(),choice=saved.events.at(-1).choice;
      assert.equal(choice.action,'SPLIT');assert.equal(choice.agree,true);assert.equal(choice.agent_id,'Agent_1');
      assert.equal(choice.preview.confirmed,true);
      assert.notDeepEqual(grouping(saved),grouping(ch31Initial));
      await reload('CH31');assert.deepEqual(await snapshot(),saved);
      assert.equal(await page.locator('.agreement-fieldset').isVisible(),false,'A new grouping cannot inherit an old Agent response');
      await noUnwantedText();
    });

    await t.test('Undo restores the confirmed Split grouping and survives refresh',async()=>{
      await page.locator('#studio-undo').click();
      await page.waitForFunction(()=>WorkingReview.state.version===2&&!WorkingReview.busy,null,{timeout:30000});
      await settled('CH31');
      const undone=await snapshot();
      assert.deepEqual(grouping(undone),grouping(ch31Initial));
      assert.equal(await page.locator('[data-working-cluster]').count(),17);
      await reload('CH31');assert.deepEqual(await snapshot(),undone);
    });

    await t.test('an empty CH31 search and Reviewed filter cannot make CH3 appear empty after Load data',async()=>{
      await page.locator('#case-search').fill('Cluster 999999999');
      await page.locator('[data-filter="reviewed"]').evaluate(el=>el.click());
      await page.waitForFunction(()=>document.querySelectorAll('[data-working-cluster]').length===0,null,{timeout:30000});
      await loadButton('CH3');
      assert.equal(await page.locator('[data-working-cluster]').count(),25);
      const thumbnailVisible=await page.locator('[data-working-cluster="1"]').evaluate(el=>{const r=el.getBoundingClientRect(),q=document.querySelector('#queue-panel').getBoundingClientRect();return r.width>0&&r.top>=q.top&&r.bottom<=q.bottom;});
      assert.equal(thumbnailVisible,true,'The selected CH3 thumbnail is visible instead of remaining above the old scroll position');
      assert.equal(await page.locator('#case-search').inputValue(),'');
      assert.equal(await page.locator('[data-filter="all"]').evaluate(el=>el.classList.contains('active')),true);
      assert.deepEqual(await snapshot(),ch3Saved,'Dataset navigation preserves saved actions and history');
      await noUnwantedText();
    });

    await t.test('the original selector and Refresh keep channels independent without the removed reset notice',async()=>{
      await originalSelector('CH31');
      assert.equal(await page.locator('[data-working-cluster]').count(),17);
      await page.locator('#inline-refresh-dataset').click();
      await page.waitForFunction(()=>WorkingReview.state.version===0&&!WorkingReview.busy,null,{timeout:30000});
      await settled('CH31');await noUnwantedText();
      assert.deepEqual(grouping(await snapshot()),grouping(ch31Initial));
      await originalSelector('CH3');
      assert.equal(await page.locator('[data-working-cluster]').count(),25);
      assert.deepEqual(await snapshot(),ch3Saved,'Refreshing CH31 leaves the CH3 reviewer record intact');
      await noUnwantedText();await screenshot('CH3-original-load-data-restored.png');
    });

    await t.test('the original import page retains Runpod/upload tabs and file inputs',async()=>{
      await page.locator('#inline-change-dataset').click();
      await page.waitForURL(/data-sources\.html/);
      await page.waitForFunction(()=>document.querySelectorAll('#public-datasets .dataset-row').length===2,null,{timeout:30000});
      assert.equal(await page.locator('.source-tabs').isVisible(),true);
      assert.equal(await page.locator('[data-source="runpod"]').isVisible(),true);
      await page.locator('[data-source="upload"]').click();
      assert.equal(await page.locator('#upload-panel').isVisible(),true);
      assert.equal(await page.locator('#dataset-files').isVisible(),true);
      assert.equal(await page.locator('#recording-name').isVisible(),true);
      assert.equal(await page.locator('#dataset-files').getAttribute('type'),'file');
      assert.equal(await page.locator('#start-upload').count(),1);
      await page.locator('[data-source="runpod"]').click();
      await page.locator('#public-datasets a.public-open[href$="public='+dataset('CH3')+'"]').click();
      await settled('CH3');assert.deepEqual(await snapshot(),ch3Saved);
    });

    await t.test('the complete Agent review and dataset flow has no uncaught JavaScript errors',()=>assert.deepEqual(errors,[]));
  }finally{await context.close();await browser.close();}
});
