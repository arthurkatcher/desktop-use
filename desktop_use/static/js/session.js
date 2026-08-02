import RFB from '/novnc/core/rfb.js';

const $ = id => document.getElementById(id);
const transcript = $('transcript');
const sid = decodeURIComponent(location.pathname.split('/').pop());

/* ── sticky-to-bottom transcript scroll ──────────────
   IntersectionObserver on a 1px sentinel kept as the transcript's last
   child. The IO signal IS the stickiness state — no scrollTop math, so a
   tall card can never silently unstick the rail. While stuck, every
   append (and any late-loading content) re-pins the bottom; scrolling up
   more than the rootMargin grace unsticks, scrolling back down re-sticks. */
let stickToBottom = true;
const sentinel = document.createElement('div');
sentinel.style.cssText = 'height:1px;flex:none;';
transcript.appendChild(sentinel);
transcript.style.overflowAnchor = 'none';   // our pin, not the browser's
new IntersectionObserver(entries => {
  stickToBottom = entries[0].isIntersecting;
}, { root: transcript, rootMargin: '0px 0px 140px 0px' }).observe(sentinel);
function pinBottom() {
  if (stickToBottom) transcript.scrollTop = transcript.scrollHeight;
}
// late-growing content inside cards (images etc.) re-pins while stuck
transcript.addEventListener('load', pinBottom, true);

/* ── live VM ─────────────────────────────────────── */
let rfb = null, vncUp = false;
function connectVNC() {
  // Server replaces only the value token below for remote sandbox mode.
  // Unreplaced / empty keeps the local websockify port. Guard uses a
  // different pattern so the replace cannot make the check self-defeating.
  const injected = window.DU.streamUrl;
  const streamUrl = (injected && !injected.startsWith('__'))
    ? injected
    : `ws://${location.hostname}:${window.DU.wsPort}`;
  rfb = new RFB($('screen'), streamUrl);
  rfb.viewOnly = true;
  rfb.scaleViewport = true;
  rfb.addEventListener('connect', () => { vncUp = true; refreshLabel(); });
  rfb.addEventListener('disconnect', () => {
    vncUp = false; refreshLabel();
    setTimeout(connectVNC, 2000);
  });
}
connectVNC();

/* ── modal ───────────────────────────────────────── */
function modal(title, text, buttons) {
  $('modal-title').textContent = title;
  $('modal-text').textContent = text;
  const row = $('modal-row');
  row.innerHTML = '';
  for (const [label, cls, fn] of buttons) {
    const b = document.createElement('button');
    b.textContent = label;
    if (cls) b.className = cls;
    b.addEventListener('click', () => { $('overlay').classList.remove('open'); fn?.(); });
    row.appendChild(b);
  }
  $('overlay').classList.add('open');
}

/* ── take / release control ──────────────────────── */
// sessionLive: desktop session open (running or idle). agentBusy: model loop.
let running = false, agentBusy = false, controlHeld = false, pausedRun = false;
let idleTimeoutS = 60;
const MSG_PLACEHOLDER_RUN = 'Message the agent mid-task…';
const MSG_PLACEHOLDER_IDLE = 'Session idle — send a message to continue…';

function grantControl() {
  if (!running) return;  // never drive desktop after session end
  controlHeld = true;
  if (rfb) rfb.viewOnly = false;
  $('control-btn').classList.add('on');
  $('control-btn').textContent = 'RELEASE CONTROL';
  setMode('live');
  refreshLabel();
}
function revokeControl() {
  controlHeld = false;
  pausedRun = false;
  if (rfb) rfb.viewOnly = true;
  $('control-btn').classList.remove('on');
  $('control-btn').textContent = 'TAKE CONTROL';
  refreshLabel();
}
/** Session finished (stop / error / idle timeout): lock out control + VNC input. */
function lockControlEnded(why) {
  running = false;
  agentBusy = false;
  pausedRun = false;
  if (controlHeld) revokeControl();
  else if (rfb) rfb.viewOnly = true;
  $('control-btn').disabled = true;
  $('control-btn').title = why
    || 'session has ended — take control is only available while a session is live';
  $('control-btn').classList.remove('on');
  $('control-btn').textContent = 'TAKE CONTROL';
}

$('control-btn').addEventListener('click', () => {
  if (!running || $('control-btn').disabled) return;
  if (!controlHeld) {
    const body = agentBusy
      ? 'Taking control pauses the agent: it will finish deciding its ' +
        'current step but execute nothing until you hand the desktop back.'
      : 'Session is idle. Take the desktop; the agent stays parked until ' +
        'you release control (and will only act after a new message).';
    modal('TAKE CONTROL', body,
      [['PAUSE AGENT & TAKE CONTROL', 'primary', async () => {
         if (!running) return;
         await fetch('/control/take', { method: 'POST' });
         pausedRun = true;
         grantControl();
       }],
       ['CANCEL', '', null]]);
  } else {
    if (pausedRun && running) {
      modal('RELEASE CONTROL',
        'Hand the desktop back. Should the agent continue the task from ' +
        'the current screen state, or stop the session here?',
        [['CONTINUE TASK', 'primary', async () => {
           await fetch('/control/release', { method: 'POST',
             headers: { 'Content-Type': 'application/json' },
             body: JSON.stringify({ continue: true }) });
           revokeControl();
         }],
         ['STOP SESSION', 'danger', async () => {
           await fetch('/control/release', { method: 'POST',
             headers: { 'Content-Type': 'application/json' },
             body: JSON.stringify({ continue: false }) });
           revokeControl();
         }],
         ['KEEP CONTROL', '', null]]);
    } else {
      revokeControl();
    }
  }
});

/* ── view modes ──────────────────────────────────── */
let mode = 'live';
let shots = [];
let selected = -1;
let follow = true;

function refreshLabel() {
  const sig = $('ro-signal');
  sig.textContent = vncUp ? 'LIVE' : 'NO LINK';
  sig.className = 'v ' + (vncUp ? 'vnc-ok' : 'vnc-bad');
  $('ro-view').textContent = mode === 'live'
    ? (typeof controlHeld !== 'undefined' && controlHeld ? 'CONTROL' : 'LIVE')
    : selected >= 0 ? `SNAP ${shots[selected].label}` : '—';
}

function setMode(m) {
  mode = m;
  $('monitor').classList.toggle('snapmode', m === 'steps');
  $('seg-live').classList.toggle('active', m === 'live');
  $('seg-steps').classList.toggle('active', m === 'steps');
  if (m === 'steps' && selected < 0 && shots.length) select(shots.length - 1);
  refreshLabel();
}
$('seg-live').addEventListener('click', () => setMode('live'));
$('seg-steps').addEventListener('click', () => setMode('steps'));

function select(i, fromUser = false) {
  if (i < 0 || i >= shots.length) return;
  selected = i;
  if (fromUser) follow = i === shots.length - 1;
  $('snap').src = shots[i].url;
  [...$('dots').children].forEach((d, j) => {
    d.classList.toggle('sel', j === i);
    if (j === i) d.scrollIntoView({ inline: 'nearest', block: 'nearest' });
  });
  $('tl-prev').disabled = i <= 0;
  $('tl-next').disabled = i >= shots.length - 1;
  document.querySelectorAll('.card.viewing')
    .forEach(c => c.classList.remove('viewing'));
  shots[i].cardEl?.classList.add('viewing');
  if (fromUser) shots[i].cardEl?.scrollIntoView({ block: 'nearest' });
  refreshLabel();
}

$('tl-prev').addEventListener('click', () => {
  setMode('steps');
  select(selected < 0 ? shots.length - 1 : selected - 1, true);
});
$('tl-next').addEventListener('click', () => {
  setMode('steps');
  select(selected < 0 ? shots.length - 1 : selected + 1, true);
});

function addShot(label, url, cls = '') {
  const i = shots.length;
  shots.push({ label, url, cls, cardEl: null });
  const d = document.createElement('button');
  d.className = 'dot ' + cls;
  d.title = `step ${label}`;
  d.addEventListener('click', () => { setMode('steps'); select(i, true); });
  const dots = $('dots');
  dots.querySelector('.latest')?.classList.remove('latest');
  if (!cls) d.classList.add('latest');
  dots.appendChild(d);
  dots.scrollLeft = dots.scrollWidth;
  if (mode === 'steps' && follow) select(i);
  return i;
}

document.addEventListener('keydown', e => {
  if (mode !== 'steps' || document.activeElement === $('msg-input')) return;
  if (e.key === 'ArrowUp' || e.key === 'ArrowLeft')
    select(Math.max(0, selected - 1), true);
  if (e.key === 'ArrowDown' || e.key === 'ArrowRight')
    select(Math.min(shots.length - 1, selected + 1), true);
});

/* ── streaming transcript ────────────────────────── */
let maxSteps = 15, live = false, lastStepN = 0;
const pendingMsgs = [];
function userCard(text) {
  card(`<div class="node">»</div>
        <div class="card-head"><span class="step-label">YOU</span></div>
        <div class="reasoning">${esc(text)}</div>`, 'usermsg');
}
let clockTimer = null, clockAnchor = null;   // Date.now()-based
let runStartTs = null, lastStepTs = null;    // event-ts based
let lastDecision = null, pendingShotIdx = -1;
const lamp = $('lamp');

function card(html, cls = '') {
  $('empty')?.remove();
  const el = document.createElement('div');
  el.className = 'card ' + cls;
  el.innerHTML = html;
  transcript.appendChild(el);
  transcript.appendChild(sentinel);   // sentinel stays the last child
  pinBottom();
  return el;
}
const esc = s => String(s).replace(/[&<>"]/g,
  c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));

function cmdParts(a) {
  const t = a.type;
  if (t === 'click' || t === 'double_click' || t === 'right_click' || t === 'move')
    return [t, `${a.x}, ${a.y}`];
  if (t === 'type')   return ['type', `"${a.text}"`];
  if (t === 'click_type') return ['click_type', `${a.x}, ${a.y} "${a.text}"`];
  if (t === 'key')    return ['key', a.combo];
  if (t === 'scroll') return ['scroll', `${a.direction} × ${a.amount ?? 3}`];
  if (t === 'wait')   return ['wait', `${a.seconds}s`];
  if (t === 'done')   return ['done', a.success ? 'success' : 'failed'];
  return [t ?? '?', ''];
}

const fmtClock = s => `${Math.floor(s / 60)}:${String(Math.round(s) % 60)
                        .padStart(2, '0')}`;

function tickClock() {
  if (clockAnchor !== null)
    $('g-clock').textContent =
      fmtClock((Date.now() - clockAnchor) / 1000);
}

function setProgress(n) {
  $('progress').querySelector('span').style.width =
    `${Math.round(n / maxSteps * 100)}%`;
}

function handle(ev) {
  // keep the wall clock anchored to server event timestamps
  if (live && runStartTs !== null && ev.ts)
    clockAnchor = Date.now() - (ev.ts - runStartTs) * 1000;

  switch (ev.t) {
    case 'hello':
      $('g-model').textContent = ev.model.split('/').pop();
      $('g-display').textContent = `${ev.display} ${ev.width}×${ev.height}`;
      $('ro-res').textContent = `${ev.width}×${ev.height}`;
      $('feed-sid').textContent = `SESSION ${sid}`;
      maxSteps = ev.max_steps;
      live = ev.live;
      if (ev.idle_timeout != null) idleTimeoutS = Number(ev.idle_timeout);
      if (!live) {
        // finished session or another session owns the desktop
        $('seg-live').disabled = true;
        lockControlEnded(
          'only the active session can take the desktop');
      } else if (ev.status === 'idle' || ev.status === 'running') {
        // reconnect mid-session: keep message bar / stop / control available
        running = true;
        agentBusy = ev.status === 'running';
        $('control-btn').disabled = false;
        $('control-btn').title =
          'Let your mouse and keyboard reach the desktop';
        showSessionActions(true);
        $('msgbar').classList.add('on');
        $('msg-input').placeholder = agentBusy
          ? MSG_PLACEHOLDER_RUN : MSG_PLACEHOLDER_IDLE;
        if (ev.status === 'idle') lamp.className = 'lamp idle';
      } else {
        // live connection but status already terminal (race on reconnect)
        lockControlEnded('session has ended');
      }
      break;
    case 'run_start':
      runStartTs = ev.ts;
      running = true;
      agentBusy = true;
      lamp.className = 'lamp thinking';
      if (live) {
        clockAnchor = Date.now();
        clearInterval(clockTimer);
        clockTimer = setInterval(tickClock, 500);
        showSessionActions(true);
        $('control-btn').disabled = false;
        $('control-btn').title =
          'Let your mouse and keyboard reach the desktop';
        $('progress').classList.add('on');
        $('msgbar').classList.add('on');
        $('msg-input').placeholder = MSG_PLACEHOLDER_RUN;
      }
      if (ev.idle_timeout != null) idleTimeoutS = Number(ev.idle_timeout);
      setProgress(0);
      card(`<div class="node">▶</div>
            <div class="card-head"><span class="step-label">RUN</span></div>
            <div class="reasoning">${esc(ev.task)}</div>`, 'ticket');
      break;
    case 'step':
      lastStepTs = ev.ts;
      lastStepN = ev.n;
      $('g-step').innerHTML =
        `${String(ev.n).padStart(2, '0')}<span class="dim">/${maxSteps}</span>`;
      setProgress(ev.n);
      pendingShotIdx = ev.shot
        ? addShot(String(ev.n).padStart(2, '0'), ev.shot) : -1;
      break;
    case 'decision': {
      const [verb, args] = cmdParts(ev.action);
      const lat = lastStepTs ? (ev.ts - lastStepTs).toFixed(1) + 's' : '';
      lastDecision = card(
        `<div class="node">${String(ev.n).padStart(2, '0')}</div>
         <div class="card-head">
           <span class="step-label">STEP ${String(ev.n).padStart(2, '0')}</span>
           <span class="lat">${lat}</span>
         </div>
         <div class="reasoning">${esc(ev.reasoning)}</div>
         <div class="cmd"><span class="pfx">❯</span><span class="verb">${esc(verb)}</span><span class="args">${esc(args)}</span><span class="res"></span></div>`,
        'clickable');
      if (pendingShotIdx >= 0) {
        const i = pendingShotIdx;
        shots[i].cardEl = lastDecision;
        lastDecision.addEventListener('click',
          () => { setMode('steps'); select(i, true); });
      }
      if (live && ev.action.type !== 'done' && ev.action.type !== 'wait') {
        const m = $('monitor');
        m.classList.add('touch');
        setTimeout(() => m.classList.remove('touch'), 650);
      }
      break;
    }
    case 'result':
      if (lastDecision) {
        const res = lastDecision.querySelector('.res');
        res.classList.add(ev.changed ? 'changed' : 'same');
        res.textContent = ev.changed ? 'screen changed' : 'no change';
      }
      break;
    case 'skipped':
      if (lastDecision) {
        const res = lastDecision.querySelector('.res');
        res.classList.add('same');
        res.textContent = 'not executed';
      }
      break;
    case 'message_sent':          // live echo: render immediately
      pendingMsgs.push(ev.text);
      userCard(ev.text);
      break;
    case 'user_message': {        // persisted at ingestion: replay + dedupe
      const i = pendingMsgs.indexOf(ev.text);
      if (i >= 0) pendingMsgs.splice(i, 1);
      else userCard(ev.text);
      break;
    }
    case 'control_taken':
      lamp.className = 'lamp';
      card(`<div class="node"></div>Manual control taken — agent paused
            until the desktop is handed back.`, 'note warn');
      break;
    case 'control_returned':
      if (ev.resumed) {
        lamp.className = 'lamp thinking';
        card(`<div class="node"></div>Control returned — agent resuming
              the task from the current screen.`, 'note warn');
      }
      break;
    case 'note':
      card(`<div class="node"></div>${esc(ev.msg)}`, 'note');
      break;
    case 'stop_requested':
      card(`<div class="node"></div>Stop signal sent — finishing the current
            step; the pending action will not run.`, 'note warn');
      disableSessionActions('STOPPING…', 'END');
      break;
    case 'end_requested':
      card(`<div class="node"></div>End signal sent — finishing the current
            step; session will close as complete.`, 'note warn');
      disableSessionActions('STOP', 'ENDING…');
      break;
    case 'done': {
      // Task milestone (agent finished → idle) or session end
      // (stop / end / timeout). terminal + reason distinguish them.
      agentBusy = false;
      const total = runStartTs ? fmtClock(ev.ts - runStartTs) : '';
      if (total) $('g-clock').textContent = total;
      $('g-step-k').textContent = 'STEPS';
      $('g-step').innerHTML =
        `${String(lastStepN).padStart(2, '0')}<span class="dim"> total</span>`;
      // timeout may be tagged (reason) or only in summary on older sessions
      const sum = String(ev.summary || '');
      const isIdleTimeout = ev.reason === 'idle_timeout'
        || /idle timeout/i.test(sum);
      const isUserEnd = ev.reason === 'ended';
      const term = !!ev.terminal || isIdleTimeout || ev.reason === 'stopped'
        || isUserEnd;
      let label, headline, cls, lampCls, node, shotCls;
      if (isIdleTimeout) {
        label = 'SESSION ENDED';
        headline = 'Session ended';
        cls = 'session-end';
        lampCls = 'lamp idle';
        node = '–';
        shotCls = '';
      } else if (isUserEnd && ev.ok) {
        label = 'COMPLETE';
        headline = 'Session ended';
        cls = 'done-ok';
        lampCls = 'lamp ok';
        node = '✓';
        shotCls = 'final-ok';
      } else if (term && !ev.ok) {
        label = 'STOPPED';
        headline = 'Session stopped';
        cls = 'done-bad';
        lampCls = 'lamp bad';
        node = '✕';
        shotCls = 'final-bad';
      } else if (ev.ok) {
        // task complete; session usually stays open (idle follows)
        label = 'COMPLETE';
        headline = 'Task complete';
        cls = 'done-ok';
        lampCls = 'lamp ok';
        node = '✓';
        shotCls = 'final-ok';
      } else {
        label = 'STOPPED';
        headline = 'Task did not finish';
        cls = 'done-bad';
        lampCls = 'lamp bad';
        node = '✕';
        shotCls = 'final-bad';
      }
      lamp.className = lampCls;
      const el = card(
        `<div class="node">${node}</div>
         <div class="card-head">
           <span class="step-label">${label}</span>
           <span class="lat">${total}</span>
         </div>
         <div class="reasoning">${headline}</div>
         <div class="summary">${esc(ev.summary)}</div>`,
        'final ' + cls);
      if (ev.shot) {
        const i = addShot(term ? 'END' : (ev.ok ? 'DONE' : 'END'),
                          ev.shot, shotCls);
        shots[i].cardEl = el;
        el.classList.add('clickable');
        el.addEventListener('click',
          () => { setMode('steps'); select(i, true); });
      }
      // terminal done: lock control immediately; run_end reinforces.
      // Task-complete (non-terminal) stays open for idle resume.
      if (term) {
        let why = 'session has ended — take control unavailable';
        if (isIdleTimeout) why = 'session ended — take control unavailable';
        else if (isUserEnd) why = 'session complete — take control unavailable';
        else if (ev.reason === 'stopped')
          why = 'session stopped — take control unavailable';
        lockControlEnded(why);
      }
      break;
    }
    case 'idle': {
      agentBusy = false;
      lamp.className = 'lamp idle';
      $('progress').classList.remove('on');
      if (live) {
        $('msgbar').classList.add('on');
        $('msg-input').placeholder = MSG_PLACEHOLDER_IDLE;
        showSessionActions(true);
      }
      const toS = Number(ev.timeout_s ?? idleTimeoutS);
      const toLabel = toS >= 60
        ? `${Math.round(toS / 60)} min`
        : `${Math.round(toS)}s`;
      card(`<div class="node">…</div>Idle — send a message to continue,
            End to finish successfully, or Stop to abort.
            Auto-ends after ${toLabel} without activity.`,
           'note warn');
      break;
    }
    case 'resumed':
      agentBusy = true;
      lamp.className = 'lamp thinking';
      if (live) {
        $('progress').classList.add('on');
        $('msg-input').placeholder = MSG_PLACEHOLDER_RUN;
      }
      card(`<div class="node">▶</div>Resuming from idle — new message(s)
            are the current objective.`, 'note warn');
      break;
    case 'error':
      agentBusy = false;
      lamp.className = 'lamp bad';
      card(`<div class="node">✕</div>
            <div class="card-head"><span class="step-label">ERROR</span></div>
            <div class="reasoning">${esc(ev.msg)}</div>`, 'final done-bad');
      lockControlEnded('session errored — take control unavailable');
      break;
    case 'run_end':
      clearInterval(clockTimer);
      clockAnchor = null;
      showSessionActions(false);
      $('progress').classList.remove('on');
      $('msgbar').classList.remove('on');
      lockControlEnded('session has ended — take control unavailable');
      requestAnimationFrame(() =>
        transcript.scrollTop = transcript.scrollHeight);
      break;
    case 'eof':
      // finished session: land on its snapshots, newest first
      es.close();
      if (shots.length) { setMode('steps'); select(shots.length - 1); }
      requestAnimationFrame(() =>
        transcript.scrollTop = transcript.scrollHeight);
      break;
  }
}

// dedupe across EventSource auto-reconnects: the server replays the whole
// history on reconnect, which would re-render every card (the "blink")
let maxSeq = -1;
const es = new EventSource(`/events?sid=${encodeURIComponent(sid)}`);
es.onmessage = e => {
  const ev = JSON.parse(e.data);
  if ('seq' in ev) {
    if (ev.seq <= maxSeq) return;
    maxSeq = ev.seq;
  }
  handle(ev);
};

function showSessionActions(on) {
  const wrap = $('session-actions');
  if (on) {
    wrap.classList.add('on');
    $('stop-btn').disabled = false;
    $('stop-btn').textContent = 'STOP';
    $('end-btn').disabled = false;
    $('end-btn').textContent = 'END';
  } else {
    wrap.classList.remove('on');
    $('stop-btn').disabled = false;
    $('stop-btn').textContent = 'STOP';
    $('end-btn').disabled = false;
    $('end-btn').textContent = 'END';
  }
}
function disableSessionActions(stopLabel, endLabel) {
  $('stop-btn').disabled = true;
  $('stop-btn').textContent = stopLabel;
  $('end-btn').disabled = true;
  $('end-btn').textContent = endLabel;
}

$('stop-btn').addEventListener('click', () => {
  disableSessionActions('STOPPING…', 'END');
  fetch('/stop', { method: 'POST' });
});
$('end-btn').addEventListener('click', () => {
  disableSessionActions('STOP', 'ENDING…');
  fetch('/end', { method: 'POST' });
});

/* ── mid-flight messages ─────────────────────────── */
function sendMsg() {
  const text = $('msg-input').value.trim();
  if (!text) return;
  $('msg-input').value = '';
  fetch('/message', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text }),
  });
  // re-arm stickiness: the user just spoke, they want to follow the reply
  stickToBottom = true;
  transcript.scrollTop = transcript.scrollHeight;
}
$('msg-send').addEventListener('click', sendMsg);
$('msg-input').addEventListener('keydown', e => {
  if (e.key === 'Enter') sendMsg();
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
