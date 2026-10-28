/* Review v2 draft: thumbnails + workspace + metrics + AI decision + human decision.
   Real operations run through CurationCore on an in-browser working copy. No model calls. */
(async function () {
  'use strict';
  const C = CurationCore, $ = s => document.querySelector(s);
  const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const fmt = n => n == null ? '—' : Number(n).toLocaleString(undefined, { maximumFractionDigits: 3 });
  const pct = n => n == null ? '—' : (n * 100).toFixed(2) + '%';
  const BLUE = '#2c639e', RED = '#b7362c', GREEN = '#1f7a4d', AMBER = '#a05c19';
  const AGENTS = ['Agent_1', 'Agent_2'];
  // Generic reasons drafted from Jacob's annotation texts, without cluster IDs. To be confirmed by the lab.
  const SEED_REASONS = [
    'Clean single unit: consistent waveform, few ISI violations',
    'Not a neuronal waveform (noise or artifact)',
    'Multiple units mixed: waveform variability and ISI violations',
    'Contains an embedded valid unit; split to isolate it',
    'Same unit across clusters: similar waveforms, merged ISI stays clean',
    'Different units: waveform shape differs or merged ISI violations rise',
    'Too few spikes to stand as a unit',
    'Amplitude drifts over the recording',
  ];
  const TYPE_NEW = '__new__';

  let input = null, state = null, hash = '', views = [], primary = null, secondary = null;
  let action = null, preview = null, suggestions = [];
  let reasons = JSON.parse(localStorage.getItem('review-v2-reasons') || 'null') || SEED_REASONS.slice();

  function notice(text, error = false) { $('#notice').textContent = text; $('#notice').classList.toggle('error', error); }

  // ---------------------------------------------------------------- data --------------------------
  async function unpack(blob) {
    const head = new Uint8Array(await blob.slice(0, 2).arrayBuffer());
    let stream = blob.stream();
    if (head[0] === 31 && head[1] === 139) stream = stream.pipeThrough(new DecompressionStream('gzip'));
    return JSON.parse(await new Response(stream).text());
  }
  function storageKey() { return 'review-v2-state:' + input.data.source_sha256; }
  async function loadDataset(id) {
    notice('Loading ' + id + '…');
    const response = await fetch('/api/curation/package?dataset_id=' + encodeURIComponent(id));
    if (!response.ok) throw Error((await response.json()).error || 'Package failed');
    input = C.prepare(await unpack(await response.blob()));
    const saved = JSON.parse(localStorage.getItem(storageKey()) || 'null');
    state = saved && saved.labels?.length === input.data.atoms.length ? saved : C.initial(input, reviewerName());
    primary = secondary = action = preview = null;
    await refresh();
    notice(`${id}: ${input.data.n_spikes.toLocaleString()} spikes, ${views.length} current clusters. Working copy v${state.version} stays in this browser.`);
  }
  function reviewerName() { return $('#reviewer').value.trim() || 'Reviewer'; }
  async function refresh() {
    state = C.transition(input, state);
    hash = await C.stateHash(input, state);
    localStorage.setItem(storageKey(), JSON.stringify(state));
    views = clusterViews(state);
    suggestions = currentSuggestions();
    $('#undo').disabled = !state.undo.length;
    $('#export').disabled = false;
    render();
  }
  // Every stored member trace of each cluster (12 per atomic component), not a 24-trace sample.
  function clusterViews(s) {
    return C.units(input, s).map(u => {
      const ids = s.labels.flatMap((v, i) => v === u.id ? [i] : []);
      const traces = ids.flatMap(i => input.data.atoms[i].traces.map(t => t.waveform));
      const q = C.quality(input, s, u.id);
      return { ...u, traces, q, kept: s.kept[u.id] === C.signature(s, u.id) };
    }).sort((a, b) => b.n - a.n || (a.q.isi.violation_rate ?? 1) - (b.q.isi.violation_rate ?? 1) || a.id - b.id);
  }
  function currentSuggestions() {
    const out = [];
    for (const t of C.tasks(input, state)) for (const agent of AGENTS) {
      const s = C.suggestion(input, hash, t, agent);
      if (s) out.push({ task: t, agent, action: s.action, rationale: s.rationale });
    }
    return out;
  }
  function view(cid) { return views.find(v => v.id === cid); }
  function task() { return primary == null ? null : C.task(primary, secondary); }

  // ---------------------------------------------------------------- plots -------------------------
  function range(groups) {
    let lo = Infinity, hi = -Infinity;
    for (const g of groups) for (const w of g.traces) for (const v of w) { if (v < lo) lo = v; if (v > hi) hi = v; }
    const pad = Math.max((hi - lo) * .06, 1e-9);
    return [lo - pad, hi + pad];
  }
  const globalRange = () => range(views);
  // groups: [{traces, mean, color}] drawn on shared axes. mini = thumbnail without axes text.
  function wavePlot(groups, lim, mini) {
    const w = mini ? 200 : 760, h = mini ? 110 : 360, l = mini ? 2 : 50, t = mini ? 2 : 14, pw = w - l - (mini ? 2 : 12), ph = h - t - (mini ? 2 : 34);
    const n = input.data.n_samples, [lo, hi] = lim;
    const x = i => l + i / (n - 1) * pw, y = v => t + ph * (1 - (v - lo) / (hi - lo));
    const pts = wv => wv.map((v, i) => x(i).toFixed(1) + ',' + y(v).toFixed(1)).join(' ');
    const cap = mini ? 120 : 600;
    let body = '';
    for (const g of groups) {
      const step = Math.max(1, Math.ceil(g.traces.length / cap));
      for (let i = 0; i < g.traces.length; i += step) body += `<polyline points="${pts(g.traces[i])}" fill="none" stroke="${g.color}" stroke-width="${mini ? .7 : 1}" stroke-opacity="${mini ? .18 : .14}"/>`;
    }
    for (const g of groups) body += `<polyline points="${pts(g.mean)}" fill="none" stroke="${g.color}" stroke-width="${mini ? 1.6 : 2.6}"/>`;
    const zero = lo < 0 && hi > 0 ? `<line x1="${l}" x2="${l + pw}" y1="${y(0).toFixed(1)}" y2="${y(0).toFixed(1)}" stroke="#333" stroke-width=".6" stroke-dasharray="4 4"/>` : '';
    const axes = mini ? '' : `<path d="M${l} ${t}v${ph}h${pw}" fill="none" stroke="#98aebf"/><text x="${l}" y="${h - 14}" font-size="11">0</text><text x="${l + pw}" y="${h - 14}" font-size="11" text-anchor="end">${((n - 1) / input.data.sampling_rate_hz * 1000).toFixed(2)} ms</text><text x="6" y="${t + 10}" font-size="11">${fmt(hi)}</text><text x="6" y="${t + ph}" font-size="11">${fmt(lo)}</text>`;
    return `<svg viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" role="img">${zero}${body}${axes}</svg>`;
  }
  function isiPlot(isi) {
    const top = Math.max(...isi.counts, 1), w = 320, h = 120, l = 6, t = 6, pw = 308, ph = 92;
    return `<svg viewBox="0 0 ${w} ${h}" role="img"><path d="M${l} ${t}v${ph}h${pw}" fill="none" stroke="#98aebf"/>${isi.counts.map((c, i) => `<rect x="${(l + i / 100 * pw).toFixed(1)}" y="${(t + ph * (1 - c / top)).toFixed(1)}" width="${pw / 100}" height="${(ph * c / top).toFixed(1)}" fill="${BLUE}"/>`).join('')}<line x1="${l + 2 / 50 * pw}" x2="${l + 2 / 50 * pw}" y1="${t}" y2="${t + ph}" stroke="${RED}" stroke-dasharray="4 3"/><text x="${l}" y="${h - 2}" font-size="10">0</text><text x="${l + pw}" y="${h - 2}" font-size="10" text-anchor="end">50 ms</text></svg>`;
  }
  // Cross-correlogram of two clusters' spike times, ±50 ms in 1 ms bins (two-pointer window).
  function crossCorrelogram(a, b) {
    const ta = [], tb = [];
    for (const i of input.order) { const c = state.labels[input.index[i]]; if (c === a) ta.push(input.times[i]); else if (c === b) tb.push(input.times[i]); }
    const bins = Array(100).fill(0); let j0 = 0;
    for (const t of ta) {
      while (j0 < tb.length && tb[j0] < t - .05) j0++;
      for (let j = j0; j < tb.length && tb[j] <= t + .05; j++) bins[Math.min(99, Math.floor((tb[j] - t + .05) * 1000))]++;
    }
    const top = Math.max(...bins, 1), w = 320, h = 120, l = 6, t = 6, pw = 308, ph = 92;
    return `<svg viewBox="0 0 ${w} ${h}" role="img"><path d="M${l} ${t}v${ph}h${pw}" fill="none" stroke="#98aebf"/>${bins.map((c, i) => `<rect x="${(l + i / 100 * pw).toFixed(1)}" y="${(t + ph * (1 - c / top)).toFixed(1)}" width="${pw / 100}" height="${(ph * c / top).toFixed(1)}" fill="${AMBER}"/>`).join('')}<line x1="${l + pw / 2}" x2="${l + pw / 2}" y1="${t}" y2="${t + ph}" stroke="#333" stroke-dasharray="4 3"/><text x="${l}" y="${h - 2}" font-size="10">−50 ms</text><text x="${l + pw}" y="${h - 2}" font-size="10" text-anchor="end">+50 ms</text></svg>`;
  }

  // ---------------------------------------------------------------- render ------------------------
  function render() { renderCards(); renderWorkspace(); renderMetrics(); renderAI(); renderHuman(); }

  function renderCards() {
    const involved = new Set(suggestions.flatMap(s => [s.task.source, s.task.target]).filter(v => v != null));
    const lim = $('#shared-scale').checked ? globalRange() : null;
    $('#cluster-count').textContent = views.length;
    $('#cards').innerHTML = views.map(v => {
      const cls = ['card', v.id === primary ? 'primary' : '', v.id === secondary ? 'secondary' : '', involved.has(v.id) ? 'ai-involved' : ''].join(' ');
      const ai = suggestions.filter(s => s.task.source === v.id || s.task.target === v.id).map(s => `<span class="chip ai">AI ${esc(s.action)}${s.task.target != null ? ` ${s.task.source}→${s.task.target}` : ''}</span>`).join('');
      return `<div class="${cls}" data-cid="${v.id}">${wavePlot([{ traces: v.traces, mean: v.mean, color: v.id === secondary ? RED : BLUE }], lim || range([v]), true)}
        <div class="meta"><strong>Cluster ${v.id}</strong><span>${v.n.toLocaleString()} spikes</span><span>ISI ${pct(v.q.isi.violation_rate)}</span></div>
        <div class="chips">${v.kept ? '<span class="chip kept">Kept</span>' : '<span class="chip">Pending</span>'}${v.split ? '' : '<span class="chip">No split available</span>'}${ai}</div></div>`;
    }).join('');
  }

  function renderWorkspace() {
    const a = view(primary), b = view(secondary);
    if (!a) { $('#workspace-title').textContent = 'Workspace'; $('#workspace-sub').textContent = 'Select a cluster to inspect it. Shift+click a second one to overlay.'; $('#workspace-plot').innerHTML = '<div class="empty">No cluster selected.</div>'; return; }
    let groups, key, title;
    if (preview) {
      const pv = clusterViews(preview.state);
      if (preview.action === 'SPLIT') {
        const children = pv.filter(v => preview.children.includes(v.id));
        groups = children.map((v, i) => ({ traces: v.traces, mean: v.mean, color: i ? AMBER : GREEN }));
        title = `Preview: SPLIT cluster ${a.id} → clusters ${preview.children.join(' and ')}`;
        key = children.map((v, i) => `<span class="${i ? 'amber' : 'green'}">Cluster ${v.id} · ${v.n.toLocaleString()} spikes</span>`).join('');
      } else {
        const merged = pv.find(v => v.id === secondary);
        groups = [{ traces: merged.traces, mean: merged.mean, color: GREEN }];
        title = `Preview: MERGE cluster ${a.id} into cluster ${b.id}`;
        key = `<span class="green">Merged cluster ${b.id} · ${merged.n.toLocaleString()} spikes</span>`;
      }
    } else {
      groups = [{ traces: a.traces, mean: a.mean, color: BLUE }];
      key = `<span class="blue">Cluster ${a.id} · ${a.n.toLocaleString()} spikes · ${a.traces.length} traces drawn</span>`;
      title = `Cluster ${a.id}`;
      if (b) { groups.push({ traces: b.traces, mean: b.mean, color: RED }); key += `<span class="red">Cluster ${b.id} · ${b.n.toLocaleString()} spikes · ${b.traces.length} traces drawn</span>`; title += ` vs. cluster ${b.id}`; }
    }
    $('#workspace-title').textContent = title;
    $('#workspace-sub').textContent = preview ? 'Projected result; nothing is applied until you confirm.' : 'All stored member waveforms, shared axes. Bold lines are means over all member spikes.';
    $('#workspace-plot').innerHTML = `<div class="plot-key">${key}</div>` + wavePlot(groups, $('#shared-scale').checked ? globalRange() : range(groups), false);
  }

  function metricBlock(v, color, label) {
    return `<div class="metric-block"><h3 class="${color}">${esc(label)}</h3><table>
      <tr><td>Spikes</td><td>${v.q.n.toLocaleString()}</td></tr>
      <tr><td>ISI &lt; 2 ms</td><td>${v.q.isi.violation_count.toLocaleString()} / ${v.q.isi.denominator.toLocaleString()} (${pct(v.q.isi.violation_rate)})</td></tr>
      <tr><td>Peak-to-trough mean ± SD</td><td>${fmt(v.q.mean_amplitude)} ± ${fmt(v.q.amplitude_sd)}</td></tr>
      <tr><td>Amplitude CV</td><td>${fmt(v.q.amplitude_cv)}</td></tr>
      <tr><td>Late vs early amplitude</td><td>${v.q.drift_percent == null ? '—' : v.q.drift_percent.toFixed(1) + '%'}</td></tr>
      </table>${isiPlot(v.q.isi)}<span class="hint">ISI histogram, 0–50 ms; red line at 2 ms.</span></div>`;
  }
  function renderMetrics() {
    const a = view(primary), b = view(secondary);
    if (!a) { $('#metrics-body').innerHTML = '<div class="empty">Nothing selected.</div>'; return; }
    let html = '';
    if (preview) {
      const pv = clusterViews(preview.state);
      const ids = preview.action === 'SPLIT' ? preview.children : [secondary];
      html += ids.map((id, i) => metricBlock(pv.find(v => v.id === id), i ? 'amber' : 'green', `After ${preview.action}: cluster ${id}`)).join('');
    } else {
      html += metricBlock(a, 'blue', `Cluster ${a.id}`);
      if (b) {
        html += metricBlock(b, 'red', `Cluster ${b.id}`);
        const pooled = C.pairISI(input, state, a.id, b.id);
        html += `<div class="metric-block"><h3>Pair ${a.id} + ${b.id}</h3><table><tr><td>Mean-waveform correlation</td><td>${fmt(C.correlation(a.mean, b.mean))}</td></tr><tr><td>ISI &lt; 2 ms if merged</td><td>${pct(pooled.violation_rate)}</td></tr></table>${isiPlot(pooled)}<span class="hint">Pooled ISI if merged.</span>${crossCorrelogram(a.id, b.id)}<span class="hint">Cross-correlogram, ±50 ms, 1 ms bins (cluster ${b.id} spikes relative to cluster ${a.id}).</span></div>`;
      }
    }
    $('#metrics-body').innerHTML = html;
  }

  function renderAI() {
    const t = task();
    if (!suggestions.length) { $('#ai-body').innerHTML = '<div class="empty">No stored AI decision for this exact state.</div>'; return; }
    $('#ai-body').innerHTML = suggestions.map((s, i) => {
      const current = t && s.task.source === t.source && (s.task.target ?? null) === (t.target ?? null);
      const target = s.task.target != null ? ` cluster ${s.task.source} → cluster ${s.task.target}` : ` cluster ${s.task.source}`;
      return `<div class="ai-card ${current ? 'current' : ''}" data-suggestion="${i}"><span class="action">${esc(s.action)}</span>${esc(target)} <span class="hint">· ${esc(s.agent.replace('_', ' '))}</span><div class="rationale">${esc(s.rationale || 'No rationale retained.')}</div></div>`;
    }).join('');
  }

  function renderHuman() {
    const a = view(primary), b = view(secondary);
    $('#human-context').textContent = !a ? 'Select one cluster, or two to compare.' : b ? `Cluster ${a.id} (blue) and cluster ${b.id} (red). Merge moves ${a.id} into ${b.id}.` : `Cluster ${a.id}.`;
    const options = !a ? [] : b ? ['MERGE', 'NOT_MERGE', 'DISCARD'] : ['KEEP', 'SPLIT', 'DISCARD'];
    $('#actions').innerHTML = options.map(o => `<button data-action="${o}" class="${o === action ? 'selected' : ''}" ${o === 'SPLIT' && !a.split ? 'disabled title="No hierarchical merge to undo"' : ''}>${o}</button>`).join('');
    const note = $('#preview-note');
    note.hidden = !preview;
    if (preview) note.textContent = `The workspace and metrics show the projected ${preview.action}. Confirm to apply it, or reject to discard the preview.`;
    const chosen = $('#reason').value;  // keep the reviewer's choice across re-renders
    $('#reason').innerHTML = reasons.map(r => `<option value="${esc(r)}">${esc(r)}</option>`).join('') + `<option value="${TYPE_NEW}">Type new…</option>`;
    if (chosen && [...$('#reason').options].some(o => o.value === chosen)) $('#reason').value = chosen;
    $('#reason-new').hidden = $('#reason').value !== TYPE_NEW;
    $('#confirm').disabled = !action;
    $('#reject').disabled = !action && !primary;
    $('#confirm').textContent = action ? `Confirm ${action}` : 'Confirm';
  }

  // ---------------------------------------------------------------- interaction -------------------
  function select(cid, shift) {
    preview = null; action = null;
    if (shift && primary != null && cid !== primary) secondary = secondary === cid ? null : cid;
    else if (cid === primary) { primary = secondary; secondary = null; }
    else { primary = cid; secondary = null; }
    render();
  }
  $('#cards').addEventListener('click', e => { const card = e.target.closest('.card'); if (card) select(Number(card.dataset.cid), e.shiftKey); });
  $('#ai-body').addEventListener('click', e => {
    const card = e.target.closest('.ai-card'); if (!card) return;
    const s = suggestions[Number(card.dataset.suggestion)];
    preview = null; action = null; primary = s.task.source; secondary = s.task.target ?? null; render();
  });
  $('#actions').addEventListener('click', async e => {
    const button = e.target.closest('button[data-action]'); if (!button || button.disabled) return;
    action = button.dataset.action; preview = null;
    if (action === 'SPLIT' || action === 'MERGE') {
      try {
        const projected = await C.apply(input, state, task(), { action, reason: 'preview' }, state.version);
        preview = { action, state: projected, children: action === 'SPLIT' ? [primary, C.splitPlan(input, state, primary).breakaway] : null };
      } catch (err) { notice(err.message, true); action = null; }
    }
    render();
  });
  $('#reason').addEventListener('change', () => { $('#reason-new').hidden = $('#reason').value !== TYPE_NEW; if (!$('#reason-new').hidden) $('#reason-new').focus(); });
  $('#reject').onclick = () => { preview = null; action = null; render(); };
  $('#confirm').onclick = async () => {
    if (!action) return;
    let reason = $('#reason').value;
    if (reason === TYPE_NEW) {
      reason = $('#reason-new').value.trim();
      if (!reason) { notice('Type the new reason first.', true); return; }
      if (!reasons.includes(reason)) { reasons.push(reason); localStorage.setItem('review-v2-reasons', JSON.stringify(reasons)); }
    }
    const t = task(), matched = suggestions.find(s => s.task.source === t.source && (s.task.target ?? null) === (t.target ?? null) && s.action === action);
    try {
      const previousChildren = preview?.children;
      state = await C.apply(input, state, t, { action, reason, agent_id: matched?.agent, agree: matched ? true : undefined }, state.version);
      const applied = action, src = primary, tgt = secondary;
      if (applied === 'SPLIT') { primary = src; secondary = previousChildren[1]; }
      else if (applied === 'MERGE') { primary = tgt; secondary = null; }
      else if (applied === 'DISCARD') { primary = secondary = null; }
      action = null; preview = null;
      $('#reason-new').value = '';
      await refresh();
      $('#last-action').textContent = `Applied ${applied} (v${state.version}). Reason: ${reason}`;
    } catch (err) { notice(err.message, true); }
  };
  $('#undo').onclick = async () => { try { state = await C.undo(input, state, reviewerName()); primary = secondary = action = preview = null; await refresh(); $('#last-action').textContent = `Undid the last operation (v${state.version}).`; } catch (err) { notice(err.message, true); } };
  $('#export').onclick = () => {
    const blob = new Blob([JSON.stringify(C.result(input, state))], { type: 'application/json' });
    const a = document.createElement('a'); a.href = URL.createObjectURL(blob); a.download = `${input.data.dataset_id}-review-v2-v${state.version}.json`; a.click();
  };
  $('#shared-scale').onchange = render;
  $('#dataset').onchange = () => loadDataset($('#dataset').value).catch(err => notice(err.message, true));
  $('#reviewer').value = localStorage.getItem('review-v2-reviewer') || '';
  $('#reviewer').onchange = () => localStorage.setItem('review-v2-reviewer', $('#reviewer').value);

  try {
    const { datasets } = await (await fetch('/api/curation/datasets')).json();
    $('#dataset').innerHTML = datasets.map(d => `<option value="${esc(d.dataset_id)}">${esc(d.dataset_id)} · ${d.n_spikes.toLocaleString()} spikes</option>`).join('');
    await loadDataset(datasets[0].dataset_id);
  } catch (err) { notice('Could not load datasets: ' + err.message, true); }
})();
