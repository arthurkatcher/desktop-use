const $ = id => document.getElementById(id);
const esc = s => String(s).replace(/[&<>"]/g,
  c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));

function ago(ts) {
  const s = Math.floor(Date.now() / 1000 - ts);
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return new Date(ts * 1000).toLocaleDateString();
}
function dur(m) {
  if (!m.ended) return '…';
  const s = Math.round(m.ended - m.started);
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
}

/* ── nav ─────────────────────────────────────────── */
let nav = 'sessions';
document.querySelectorAll('.nav-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    nav = btn.dataset.nav;
    document.querySelectorAll('.nav-btn').forEach(b =>
      b.classList.toggle('active', b.dataset.nav === nav));
    document.querySelectorAll('.panel').forEach(p =>
      p.classList.toggle('active', p.id === 'panel-' + nav));
    $('head-state').textContent = nav.toUpperCase();
    if (nav === 'sessions') loadSessions(true);
    if (nav === 'screens') loadScreens(true);
    if (nav === 'settings') loadSettings();
  });
});

/* ── sessions ────────────────────────────────────── */
const PAGE = 10;
let sessOffset = 0;
let sessRows = [];
let sessTotal = 0;
let anyRunning = false;

async function loadScreenPicker() {
  try {
    const data = await (await fetch('/api/screens?limit=50&status=on&lease=free')).json();
    const items = data.items || [];
    const eligible = items.filter(s => (s.control || {}).holder !== 'human');
    const sel = $('screen-pick');
    const prev = sel.value;
    sel.innerHTML = '<option value="">(local / boot desk)</option>' +
      eligible.map(s =>
        `<option value="${esc(s.id)}">${esc(s.name)} — ${esc(s.id)}</option>`
      ).join('');
    if (prev && [...sel.options].some(o => o.value === prev)) sel.value = prev;
  } catch (e) { /* screens API may 503 in odd boots */ }
}

async function loadSessions(reset) {
  if (reset) { sessOffset = 0; sessRows = []; }
  const status = $('sess-status').value;
  const q = $('sess-q').value.trim();
  const url = new URL('/api/sessions', location.origin);
  url.searchParams.set('limit', PAGE);
  url.searchParams.set('offset', sessOffset);
  if (status) url.searchParams.set('status', status);
  if (q) url.searchParams.set('q', q);
  const data = await (await fetch(url)).json();
  const items = data.items || data;
  const total = data.total != null ? data.total : items.length;
  if (reset) sessRows = items;
  else sessRows = sessRows.concat(items);
  sessTotal = total;
  anyRunning = sessRows.some(r => r.active);
  $('lamp').className = 'lamp' + (anyRunning ? ' thinking' : '');
  if (nav === 'sessions')
    $('head-state').textContent = anyRunning ? 'SESSION RUNNING' : 'SESSIONS';
  $('count').textContent = sessTotal ? `${sessRows.length} / ${sessTotal}` : '';
  $('launch-btn').disabled = anyRunning;
  $('hint').innerHTML = anyRunning
    ? 'one session at a time — <a href="/s/' +
      esc(sessRows.find(r => r.active).id) + '">watch the running one</a>'
    : '';
  $('sess-more').style.display =
    sessRows.length < sessTotal ? 'inline-flex' : 'none';

  if (!sessRows.length) {
    $('list').innerHTML = '<div class="empty">No sessions yet. ' +
      'Pick a screen (optional), describe a task, and launch.</div>';
    return;
  }
  $('list').innerHTML = sessRows.map(m => {
    const status = m.active
      ? (m.status === 'idle' ? 'idle' : 'running')
      : m.status;
    const shotName = (m.active || m.status === 'running' || m.status === 'idle')
      ? m.steps : 'final';
    const shot = m.steps > 0
      ? `style="background-image:url('/shot/${esc(m.id)}/${shotName}.png')"` : '';
    const thumb = m.steps > 0
      ? `<span class="thumb" ${shot}></span>`
      : `<span class="thumb blank">NO IMG</span>`;
    const scr = m.screen_id
      ? `<span>scr ${esc(m.screen_id)}</span>` : '';
    return `<a class="row${m.active ? ' active' : ''}" href="/s/${esc(m.id)}">
      ${thumb}
      <span class="row-main">
        <span class="row-task">${esc(m.task)}</span>
        <span class="row-sub">
          <span>${esc((m.model || '').split('/').pop())}</span>
          <span>${m.steps || 0} steps</span>
          <span>${dur(m)}</span>
          <span>${ago(m.started)}</span>
          ${scr}
        </span>
      </span>
      <span class="badge ${esc(status)}">${esc(status).toUpperCase()}</span>
    </a>`;
  }).join('');
}

$('sess-more').addEventListener('click', () => {
  sessOffset = sessRows.length;
  loadSessions(false);
});
$('sess-status').addEventListener('change', () => loadSessions(true));
let qTimer;
$('sess-q').addEventListener('input', () => {
  clearTimeout(qTimer);
  qTimer = setTimeout(() => loadSessions(true), 250);
});

async function launch() {
  const task = $('task').value.trim();
  if (!task) { $('task').focus(); return; }
  $('launch-btn').disabled = true;
  const body = { task };
  const scr = $('screen-pick').value;
  if (scr) body.screen_id = scr;
  const res = await fetch('/run', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  const data = await res.json();
  if (res.ok) {
    closeSessOverlay();
    location.href = '/s/' + data.id;
  } else {
    $('sess-flash').className = 'flash err';
    $('sess-flash').textContent = data.error || 'launch failed';
    $('launch-btn').disabled = false;
  }
}
$('launch-btn').addEventListener('click', launch);
$('task').addEventListener('keydown', e => {
  if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) launch();
});

/* ── screens ─────────────────────────────────────── */
let scrOffset = 0;
let scrRows = [];
let scrTotal = 0;

async function loadScreens(reset) {
  if (reset) { scrOffset = 0; scrRows = []; }
  const url = new URL('/api/screens', location.origin);
  url.searchParams.set('limit', PAGE);
  url.searchParams.set('offset', scrOffset);
  const st = $('scr-status').value;
  const lease = $('scr-lease').value;
  const q = $('scr-q').value.trim();
  if (st) url.searchParams.set('status', st);
  if (lease) url.searchParams.set('lease', lease);
  if (q) url.searchParams.set('q', q);
  const data = await (await fetch(url)).json();
  const items = data.items || [];
  scrTotal = data.total || 0;
  if (reset) scrRows = items;
  else scrRows = scrRows.concat(items);
  $('scr-count').textContent = scrTotal ? `${scrRows.length} / ${scrTotal}` : '';
  $('scr-more').style.display =
    scrRows.length < scrTotal ? 'inline-flex' : 'none';
  if (!scrRows.length) {
    $('scr-list').innerHTML = '<div class="empty" style="grid-column:1/-1">' +
      'No screens yet. Add an external sandbox URL above ' +
      '(health must pass).</div>';
    return;
  }
  $('scr-list').innerHTML = scrRows.map(s => {
    const leaseId = (s.lease || {}).session_id;
    const holder = (s.control || {}).holder || 'none';
    const health = s.health || {};
    const conn = s.connection || {};
    let host = conn.sandbox_url || '';
    try { host = new URL(host).host; } catch (e) { /* keep raw */ }
    const res = (health.width && health.height)
      ? `<span>${health.width}×${health.height}</span>` : '';
    return `<a class="row" href="/screen/${esc(s.id)}">
      <span class="dot-status ${esc(s.status)}"></span>
      <span class="row-main">
        <span class="row-task">${esc(s.name || s.id)}</span>
        <span class="row-sub">
          <span>${esc(host)}</span>
          <span>${esc(s.id)}</span>
          ${res ? `<span>${res}</span>` : ''}
        </span>
      </span>
      <span class="badge ${esc(s.status)}">${esc(s.status).toUpperCase()}</span>
      <span class="badge ${leaseId ? 'leased' : 'free'}">${
        leaseId ? 'LEASED' : 'FREE'}</span>
      <span class="badge ${esc(holder)}">INPUT ${esc(holder).toUpperCase()}</span>
    </a>`;
  }).join('');
  loadScreenPicker();
}

$('scr-more').addEventListener('click', () => {
  scrOffset = scrRows.length;
  loadScreens(false);
});
$('scr-status').addEventListener('change', () => loadScreens(true));
$('scr-lease').addEventListener('change', () => loadScreens(true));
let sqTimer;
$('scr-q').addEventListener('input', () => {
  clearTimeout(sqTimer);
  sqTimer = setTimeout(() => loadScreens(true), 250);
});

$('create-screen').addEventListener('click', async () => {
  $('scr-flash').className = 'flash';
  $('scr-flash').textContent = 'probing health…';
  const res = await fetch('/api/screens', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      name: $('scr-name').value.trim(),
      connection: {
        mode: 'external',
        sandbox_url: $('scr-url').value.trim(),
        stream_url: $('scr-stream').value.trim() || null,
        token: $('scr-token').value,
      },
    }),
  });
  const data = await res.json();
  if (res.ok) {
    $('scr-flash').textContent = 'created ' + data.id;
    $('scr-name').value = '';
    $('scr-url').value = '';
    $('scr-stream').value = '';
    $('scr-token').value = '';
    closeScrOverlay();
    loadScreens(true);
  } else {
    $('scr-flash').className = 'flash err';
    $('scr-flash').textContent = data.error || 'create failed';
  }
});

/* ── add screen overlay ──────────────────────────── */
function openScrOverlay() {
  $('scr-overlay').classList.add('on');
  $('scr-flash').className = 'flash';
  $('scr-flash').textContent = '';
  $('scr-name').focus();
}
function closeScrOverlay() {
  $('scr-overlay').classList.remove('on');
}
$('add-screen-btn').addEventListener('click', openScrOverlay);
$('scr-overlay-close').addEventListener('click', closeScrOverlay);
$('scr-overlay').addEventListener('click', e => {
  if (e.target === $('scr-overlay')) closeScrOverlay();
});
document.addEventListener('keydown', e => {
  if (e.key !== 'Escape') return;
  if ($('scr-overlay').classList.contains('on')) closeScrOverlay();
  else if ($('sess-overlay').classList.contains('on')) closeSessOverlay();
});

/* ── new session overlay ─────────────────────────── */
function openSessOverlay() {
  $('sess-overlay').classList.add('on');
  $('sess-flash').className = 'flash';
  $('sess-flash').textContent = '';
  $('task').focus();
}
function closeSessOverlay() {
  $('sess-overlay').classList.remove('on');
}
$('new-session-btn').addEventListener('click', openSessOverlay);
$('sess-overlay-close').addEventListener('click', closeSessOverlay);
$('sess-overlay').addEventListener('click', e => {
  if (e.target === $('sess-overlay')) closeSessOverlay();
});

/* ── settings ────────────────────────────────────── */
const MODEL_GROUPS = {
  holo: [
    ['holo3-1-35b-a3b', 'Holo3 1 · 35B (fast)'],
    ['holo3-122b-a10b', 'Holo3 · 122B (larger)'],
  ],
  generic: [
    ['anthropic/claude-sonnet-5', 'Claude Sonnet 5'],
    ['anthropic/claude-opus-5', 'Claude Opus 5'],
  ],
};
const BASE_HINT = {
  holo: 'https://api.hcompany.ai/v1',
  generic: 'https://openrouter.ai/api/v1',
};
const BACKEND_SUB = {
  auto: 'detect from base url / model id',
  generic: 'OpenRouter / Ollama / any OpenAI-compatible VLM · pixels',
  holo: 'H Company Models API · structured outputs · [0,1000] coords',
};
const CUSTOM = '__custom__';
let setBackend = 'auto';

function rebuildModels(keepValue) {
  const sel = $('set-model');
  const groups = setBackend === 'auto' ? ['holo', 'generic'] : [setBackend];
  let html = '';
  for (const g of groups) {
    const opts = MODEL_GROUPS[g].map(([id, label]) =>
      `<option value="${esc(id)}">${esc(label)}</option>`).join('');
    html += setBackend === 'auto'
      ? `<optgroup label="${g.toUpperCase()}">${opts}</optgroup>` : opts;
  }
  html += `<option value="${CUSTOM}">custom…</option>`;
  sel.innerHTML = html;
  const known = [...sel.options].some(o => o.value === keepValue);
  sel.value = known ? keepValue : (keepValue ? CUSTOM : sel.options[0].value);
  $('set-model-custom-wrap').style.display =
    sel.value === CUSTOM ? 'block' : 'none';
  if (sel.value === CUSTOM) $('set-model-custom').value = keepValue;
}

function paintBackend() {
  document.querySelectorAll('#backend-seg button').forEach(b =>
    b.classList.toggle('active', b.dataset.b === setBackend));
  $('backend-sub').textContent = BACKEND_SUB[setBackend] || '';
  $('base-sub').textContent = setBackend === 'auto'
    ? 'holo: api.hcompany.ai/v1 · generic: openrouter.ai/api/v1'
    : `suggested: ${BASE_HINT[setBackend]}`;
}

document.querySelectorAll('#backend-seg button').forEach(b => {
  b.addEventListener('click', () => {
    setBackend = b.dataset.b;
    paintBackend();
    rebuildModels(null);
    // follow the backend's canonical endpoint unless the operator
    // typed something custom
    const cur = $('set-base').value.trim();
    if (!cur || Object.values(BASE_HINT).includes(cur))
      $('set-base').value = BASE_HINT[setBackend] || '';
  });
});
$('set-model').addEventListener('change', () => {
  const custom = $('set-model').value === CUSTOM;
  $('set-model-custom-wrap').style.display = custom ? 'block' : 'none';
  if (custom) $('set-model-custom').focus();
});

async function loadSettings() {
  const s = await (await fetch('/api/settings')).json();
  setBackend = s.model_backend || 'auto';
  paintBackend();
  rebuildModels(s.model || '');
  $('set-base').value = s.base_url || '';
  $('set-key').value = '';
  $('set-key').placeholder = s.api_key_set
    ? '(set — leave blank to keep)' : 'optional';
  $('set-key-hint').textContent = s.api_key_set ? 'api key is stored' : '';
  $('set-steps').value = s.max_steps ?? 15;
  $('set-idle').value = s.idle_timeout ?? 60;
  $('set-ttl').value = s.control_ttl_s ?? 120;

  // default screen: dropdown from the registry
  try {
    const d = await (await fetch('/api/screens?limit=50')).json();
    const items = d.items || [];
    $('set-screen').innerHTML =
      '<option value="">(local / boot desk)</option>' +
      items.map(x =>
        `<option value="${esc(x.id)}">${esc(x.name)} — ${esc(x.id)}</option>`
      ).join('');
    $('set-screen').value = s.default_screen_id || '';
  } catch (e) { /* leave the default option only */ }

  const presets = s.presets || [];
  $('preset-chips').innerHTML = presets.map(p =>
    `<button class="chip" data-p="${esc(p.id)}">${esc(p.label || p.id)}</button>`
  ).join('');
  $('preset-chips').querySelectorAll('.chip').forEach(chip => {
    chip.addEventListener('click', async () => {
      const res = await fetch('/api/settings/preset', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id: chip.dataset.p }),
      });
      if (res.ok) {
        await loadSettings();
        $('set-flash').className = 'flash';
        $('set-flash').textContent = 'preset applied';
      }
    });
  });
}

$('save-settings').addEventListener('click', async () => {
  const sel = $('set-model').value;
  const model = sel === CUSTOM
    ? $('set-model-custom').value.trim() : sel;
  const body = {
    model_backend: setBackend,
    model,
    base_url: $('set-base').value.trim(),
    max_steps: Number($('set-steps').value),
    idle_timeout: Number($('set-idle').value),
    control_ttl_s: Number($('set-ttl').value),
    default_screen_id: $('set-screen').value || null,
  };
  const key = $('set-key').value;
  if (key) body.api_key = key;
  const res = await fetch('/api/settings', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  const data = await res.json();
  if (res.ok) {
    $('set-flash').className = 'flash';
    $('set-flash').textContent = 'saved';
    loadSettings();
  } else {
    $('set-flash').className = 'flash err';
    $('set-flash').textContent = data.error || 'save failed';
  }
});

/* ── theme ───────────────────────────────────────── */
const effTheme = () => document.documentElement.dataset.theme
  || (matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark');
function paintThemeBtn() {
  $('theme-btn').textContent = effTheme() === 'dark' ? '☀' : '☾';
}
$('theme-btn').addEventListener('click', () => {
  const next = effTheme() === 'dark' ? 'light' : 'dark';
  document.documentElement.dataset.theme = next;
  localStorage.setItem('du-theme', next);
  paintThemeBtn();
});
matchMedia('(prefers-color-scheme: light)')
  .addEventListener('change', paintThemeBtn);
paintThemeBtn();

loadSessions(true);
loadScreenPicker();
setInterval(() => {
  if (nav === 'sessions') loadSessions(true);
  if (nav === 'screens') loadScreens(true);
}, 5000);
