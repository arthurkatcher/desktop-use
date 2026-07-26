import RFB from '/novnc/core/rfb.js';

const $ = id => document.getElementById(id);
const scrId = decodeURIComponent(location.pathname.split('/').pop());
document.title = scrId + ' · desktop-use';

let meta = null;
let rfb = null, vncUp = false, streamOk = false;
let holding = false;      // this browser holds human control
let delArmed = false;

/* ── live stream ─────────────────────────────────── */
function connectVNC() {
  const injected = window.DU.streamUrl;
  if (!injected || injected.startsWith('__')) {
    streamOk = false;
    paint();
    return;
  }
  streamOk = true;
  rfb = new RFB($('screen'), injected);
  rfb.viewOnly = !holding;
  rfb.scaleViewport = true;
  rfb.addEventListener('connect', () => { vncUp = true; paint(); });
  rfb.addEventListener('disconnect', () => {
    vncUp = false; paint();
    setTimeout(connectVNC, 2000);
  });
}

/* ── state ───────────────────────────────────────── */
async function refresh() {
  try {
    const r = await fetch('/api/screens/' + encodeURIComponent(scrId));
    if (r.status === 404) { location.href = '/'; return; }
    meta = await r.json();
  } catch (e) { return; }
  // Server-side TTL may have expired while we held control.
  if (holding && (meta.control || {}).holder !== 'human') dropControl(true);
  paint();
}

function paint() {
  if (!meta) return;
  const conn = meta.connection || {};
  const ctrl = meta.control || {};
  const lease = (meta.lease || {}).session_id;
  const health = meta.health || {};

  $('scr-name').textContent = meta.name || scrId;
  $('lamp').className = 'lamp' +
    (meta.status === 'on' ? ' on' : meta.status === 'error' ? ' bad' : '');

  const pw = $('g-power');
  pw.textContent = (meta.status || '?').toUpperCase();
  pw.className = 'v ' + (meta.status === 'on' ? 'ok'
    : meta.status === 'error' ? 'bad' : '');
  $('g-lease').textContent = lease ? 'LEASED' : 'FREE';
  $('g-lease').className = 'v ' + (lease ? 'warn' : '');
  const holder = ctrl.holder || 'none';
  $('g-input').textContent = holder.toUpperCase();
  $('g-input').className = 'v ' + (holder === 'human' ? 'warn' : '');
  const w = health.width, h = health.height;
  $('g-res').textContent = (w && h) ? `${w}×${h}` : '–';

  const sig = $('ro-signal');
  sig.textContent = !streamOk ? 'NO STREAM' : vncUp ? 'LIVE' : 'NO LINK';
  sig.className = 'v ' + (streamOk && vncUp ? 'ok' : 'bad');

  // lease banner
  const banner = $('lease-banner');
  if (lease) {
    banner.classList.add('on');
    $('lease-text').textContent =
      'Leased to session ' + lease + ' — taking control pauses the agent.';
    $('lease-link').href = '/s/' + encodeURIComponent(lease);
  } else {
    banner.classList.remove('on');
  }

  // veil (off / no stream)
  const off = meta.status === 'off';
  const err = meta.status === 'error';
  const veil = $('veil');
  if (off) {
    veil.classList.add('on');
    $('veil-title').textContent = 'SCREEN OFF';
    $('veil-sub').textContent =
      'This screen is powered off. Turn it on to probe health and stream.';
    $('veil-btn').style.display = 'inline-flex';
  } else if (!streamOk) {
    veil.classList.add('on');
    $('veil-title').textContent = 'NO STREAM CONFIGURED';
    $('veil-sub').textContent =
      'No stream_url on this screen and the sandbox health probe did not ' +
      'report one. Retry health or edit the connection.';
    $('veil-btn').style.display = 'none';
  } else if (err) {
    veil.classList.add('on');
    $('veil-title').textContent = 'HEALTH ERROR';
    $('veil-sub').textContent =
      (meta.last_error || {}).message || 'last health check failed';
    $('veil-btn').style.display = 'none';
  } else {
    veil.classList.remove('on');
  }

  // deck
  $('power-btn').textContent = off ? 'TURN ON' : 'TURN OFF';
  $('del-btn').style.display = lease ? 'none' : 'inline-flex';
  $('control-btn').disabled = off;
  $('deck-hint').textContent = holding
    ? 'your mouse and keyboard reach the desktop'
    : (holder === 'human' ? 'another operator holds input' : '');
}

/* ── control ─────────────────────────────────────── */
function grabControl() {
  holding = true;
  if (rfb) rfb.viewOnly = false;
  $('monitor').classList.add('touch');
  $('control-btn').classList.add('on');
  $('control-btn').textContent = 'RELEASE CONTROL';
  $('ro-ctl').textContent = 'YOU';
  $('ro-ctl').className = 'v warn';
}
function dropControl(expired = false) {
  holding = false;
  if (rfb) rfb.viewOnly = true;
  $('monitor').classList.remove('touch');
  $('control-btn').classList.remove('on');
  $('control-btn').textContent = 'TAKE CONTROL';
  $('ro-ctl').textContent = 'VIEW';
  $('ro-ctl').className = 'v';
  $('ro-ttl').textContent = '–';
  if (expired) {
    $('deck-hint').textContent = 'hold expired (TTL)';
    setTimeout(() => paint(), 2500);
  }
}

$('control-btn').addEventListener('click', async () => {
  if (!meta) return;
  if (!holding) {
    const r = await fetch(
      `/api/screens/${encodeURIComponent(scrId)}/control/take`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ via: 'screen' }),
      });
    const data = await r.json();
    if (!r.ok) { $('deck-hint').textContent = data.error || 'failed'; return; }
    meta = data;
    grabControl();
    paint();
  } else {
    await fetch(
      `/api/screens/${encodeURIComponent(scrId)}/control/release`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ continue: true }),
      });
    dropControl();
    refresh();
  }
});

/* hold countdown */
setInterval(() => {
  if (!holding || !meta) return;
  const exp = (meta.control || {}).expires_at;
  if (!exp) { $('ro-ttl').textContent = '–'; return; }
  const left = Math.max(0, Math.round(exp - Date.now() / 1000));
  $('ro-ttl').textContent = `${Math.floor(left / 60)}:` +
    String(left % 60).padStart(2, '0');
}, 1000);

/* ── power / health / delete ─────────────────────── */
$('power-btn').addEventListener('click', async () => {
  if (!meta) return;
  const a = meta.status === 'off' ? 'on' : 'off';
  const r = await fetch(
    `/api/screens/${encodeURIComponent(scrId)}/${a}`, { method: 'POST' });
  const data = await r.json();
  if (!r.ok) $('deck-hint').textContent = data.error || 'failed';
  else meta = data;
  paint();
});
$('veil-btn').addEventListener('click', () => $('power-btn').click());
$('health-btn').addEventListener('click', async () => {
  $('deck-hint').textContent = 'probing…';
  const r = await fetch(
    `/api/screens/${encodeURIComponent(scrId)}/health`, { method: 'POST' });
  if (r.ok) meta = await r.json();
  $('deck-hint').textContent = '';
  paint();
});
$('del-btn').addEventListener('click', async () => {
  if (!delArmed) {
    delArmed = true;
    $('del-btn').textContent = 'CONFIRM DELETE';
    setTimeout(() => { delArmed = false;
      $('del-btn').textContent = 'DELETE'; }, 3000);
    return;
  }
  const r = await fetch(`/api/screens/${encodeURIComponent(scrId)}`,
    { method: 'DELETE' });
  if (r.ok) location.href = '/';
  else {
    const d = await r.json();
    $('deck-hint').textContent = d.error || 'delete failed';
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

/* ── boot ────────────────────────────────────────── */
connectVNC();
await refresh();
setInterval(refresh, 3000);
