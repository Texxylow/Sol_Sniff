/* ═══════════════════════════════════════════════════════════════════════════
   SOLSNIFF — app.js
   ═══════════════════════════════════════════════════════════════════════════ */

/* ── Config ─────────────────────────────────────────────────────────────── */
const API_BASE        = 'http://localhost:8000/api';
const REFRESH_SEC     = 60;
const ACCESS_CODE     = 'NEZER';         // correct code (case-insensitive check)
const MAX_USES        = 10;              // ← raise this number to allow more users

/* ── State ──────────────────────────────────────────────────────────────── */
let refreshTimer    = null;
let countdownTimer  = null;
let countdownSecs   = REFRESH_SEC;
let isLoading       = false;

/* ═══════════════════════════════════════════════════════════════════════════
   HELPERS
   ═══════════════════════════════════════════════════════════════════════════ */
const fmt = {
  usd(n) {
    n = parseFloat(n) || 0;
    if (n >= 1_000_000) return '$' + (n / 1_000_000).toFixed(2) + 'M';
    if (n >= 1_000)     return '$' + (n / 1_000).toFixed(1) + 'K';
    return '$' + n.toFixed(2);
  },
  pct(n) {
    const v = parseFloat(n || 0).toFixed(1);
    return (n >= 0 ? '+' : '') + v + '%';
  },
  age(h) {
    if (!h && h !== 0) return 'N/A';
    if (h < 1)  return Math.round(h * 60) + 'm old';
    if (h < 24) return h.toFixed(1) + 'h old';
    return Math.floor(h / 24) + 'd old';
  },
};

function esc(s) {
  if (!s) return '';
  return String(s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;')
    .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function scoreClass(s) {
  if (s >= 85) return 'hot';
  if (s >= 70) return 'warm';
  return 'cool';
}

function showToast(msg) {
  const t = document.getElementById('toast');
  if (!t) return;
  t.textContent = msg;
  t.classList.add('show');
  setTimeout(() => t.classList.remove('show'), 2600);
}

/* ═══════════════════════════════════════════════════════════════════════════
   ACCESS GATE
   ═══════════════════════════════════════════════════════════════════════════ */
function getRegistry() {
  try { return JSON.parse(localStorage.getItem('ss_registry') || '{}'); } catch { return {}; }
}
function saveRegistry(r) {
  localStorage.setItem('ss_registry', JSON.stringify(r));
}
function usedCount() {
  return Object.keys(getRegistry()).length;
}

function updateUsageDisplay() {
  const el = document.getElementById('gate-usage');
  if (el) el.textContent = usedCount() + ' / ' + MAX_USES + ' access slots used';
}

function showGateError(msg) {
  const el = document.getElementById('gate-error');
  if (!el) return;
  el.textContent = msg;
  el.classList.remove('hidden');
}
function hideGateError() {
  const el = document.getElementById('gate-error');
  if (el) el.classList.add('hidden');
}

function triggerShake() {
  const card = document.getElementById('gate-card');
  if (!card) return;
  card.classList.remove('shake');
  void card.offsetWidth; // reflow
  card.classList.add('shake');
  setTimeout(() => card.classList.remove('shake'), 600);
}

function attemptAccess() {
  const emailEl = document.getElementById('gate-email');
  const codeEl  = document.getElementById('gate-code');
  const btn     = document.getElementById('gate-btn');

  hideGateError();

  const email = emailEl.value.trim().toLowerCase();
  const code  = codeEl.value.trim().toUpperCase();

  if (!email || !email.includes('@') || !email.includes('.')) {
    showGateError('Please enter a valid email address.');
    triggerShake(); return;
  }
  if (!code) {
    showGateError('Please enter the access code.');
    triggerShake(); return;
  }
  if (code !== ACCESS_CODE) {
    showGateError('Invalid access code. Contact Ebenezer for access.');
    triggerShake(); return;
  }

  btn.disabled = true;
  btn.textContent = 'VERIFYING…';

  setTimeout(() => {
    const reg = getRegistry();

    if (reg[email]) {
      // returning user — skip slot check
      localStorage.setItem('ss_authed', email);
      unlockApp();
      return;
    }

    // new email
    if (usedCount() >= MAX_USES) {
      showGateError('Access limit reached (' + MAX_USES + ' slots). Contact Ebenezer to add more.');
      triggerShake();
      btn.disabled = false;
      btn.textContent = 'UNLOCK ACCESS →';
      return;
    }

    reg[email] = true;
    saveRegistry(reg);
    localStorage.setItem('ss_authed', email);
    unlockApp();
  }, 800);
}

function unlockApp() {
  document.getElementById('gate-screen').style.display = 'none';
  document.getElementById('main-app').classList.remove('hidden');
  initApp();
}

function initGate() {
  updateUsageDisplay();

  // Already authed?
  if (localStorage.getItem('ss_authed')) {
    unlockApp();
    return;
  }

  document.getElementById('gate-btn').addEventListener('click', attemptAccess);
  document.getElementById('gate-email').addEventListener('keydown', e => { if (e.key === 'Enter') document.getElementById('gate-code').focus(); });
  document.getElementById('gate-code').addEventListener('keydown', e => { if (e.key === 'Enter') attemptAccess(); });
}

/* ═══════════════════════════════════════════════════════════════════════════
   ROUTER
   ═══════════════════════════════════════════════════════════════════════════ */
function navigate(page) {
  document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.nav-link').forEach(l => l.classList.remove('active'));

  const pageEl = document.getElementById('page-' + page);
  if (pageEl) pageEl.classList.add('active');

  document.querySelectorAll('.nav-link[data-page="' + page + '"]').forEach(l => l.classList.add('active'));

  window.location.hash = page;

  if (page === 'dashboard') {
    startAutoRefresh();
  } else {
    stopAutoRefresh();
  }
}

/* ═══════════════════════════════════════════════════════════════════════════
   DASHBOARD
   ═══════════════════════════════════════════════════════════════════════════ */
async function fetchTokens(silent = false) {
  if (isLoading) return;
  isLoading = true;

  const grid = document.getElementById('token-grid');
  const btn  = document.getElementById('refresh-btn');
  if (btn) { btn.disabled = true; btn.textContent = 'SCANNING…'; }

  if (!silent) {
    grid.innerHTML = `
      <div class="loading-state">
        <div class="spinner"></div>
        <span class="loading-text">SCANNING SOLANA POOLS…</span>
      </div>`;
  }

  try {
    const res  = await fetch(API_BASE + '/tokens');
    if (!res.ok) throw new Error('HTTP ' + res.status);
    const data = await res.json();

    renderTokenGrid(data.tokens || []);
    updateMetaBar(data);

    if (silent) {
      showToast('⟳ Refreshed — ' + (data.tokens?.length || 0) + ' tokens');
    }
  } catch (err) {
    console.error(err);
    if (!silent) {
      grid.innerHTML = `
        <div class="empty-state">
          <div class="empty-icon">⚠</div>
          <div class="empty-title">BACKEND UNREACHABLE</div>
          <div class="empty-desc">
            Start the backend first:<br>
            <code style="color:var(--cyan)">cd backend && uvicorn main:app --reload</code>
          </div>
        </div>`;
    }
    showToast('⚠ Backend not reachable');
  } finally {
    isLoading = false;
    if (btn) { btn.disabled = false; btn.textContent = 'REFRESH'; }
  }
}

function updateMetaBar(data) {
  const set = (id, val) => { const el = document.getElementById(id); if (el) el.textContent = val; };
  set('token-count', (data.tokens?.length || 0) + ' tokens');
  set('data-source', data.source || 'live');
  set('last-update', new Date().toLocaleTimeString());
}

function renderTokenGrid(tokens) {
  const grid = document.getElementById('token-grid');

  if (!tokens.length) {
    grid.innerHTML = `
      <div class="empty-state" style="grid-column:1/-1">
        <div class="empty-icon">🔍</div>
        <div class="empty-title">NO TOKENS MATCH FILTERS</div>
        <div class="empty-desc">No new pools meet criteria right now. Auto-refreshes every 60 s.</div>
      </div>`;
    return;
  }

  grid.innerHTML = tokens.map((t, i) => {
    const rank  = i + 1;
    const sCls  = scoreClass(t.score);
    const f     = t.security_flags || {};
    const up    = t.price_change_1h >= 0;

    return `
    <div class="token-card">
      <div class="card-rank ${rank <= 3 ? 'top' : ''}">#${rank}</div>

      <div class="card-header">
        <div>
          <div class="card-name">${esc(t.name)}</div>
          <div class="card-symbol">${esc(t.symbol || t.name)}</div>
        </div>
        <div class="score-badge ${sCls}">
          <span class="score-num">${t.score}</span>
          <span class="score-lbl">SCORE</span>
        </div>
      </div>

      <div class="card-stats">
        <div><div class="stat-lbl">FDV</div>        <div class="stat-val">${fmt.usd(t.fdv)}</div></div>
        <div><div class="stat-lbl">LIQUIDITY</div>  <div class="stat-val">${fmt.usd(t.liquidity)}</div></div>
        <div><div class="stat-lbl">1H VOL</div>     <div class="stat-val">${fmt.usd(t.volume_1h)}</div></div>
        <div><div class="stat-lbl">1H CHANGE</div>  <div class="stat-val ${up ? 'up' : 'down'}">${fmt.pct(t.price_change_1h)}</div></div>
        <div><div class="stat-lbl">TOP10 HOLD</div> <div class="stat-val ${(f.top10_holder_pct || 0) > 60 ? 'down' : ''}">${f.top10_holder_pct != null ? f.top10_holder_pct + '%' : 'N/A'}</div></div>
        <div><div class="stat-lbl">SECURITY</div>   <div class="stat-val ${(f.is_honeypot || f.is_mintable) ? 'down' : 'up'}">${f.is_honeypot ? '⚠ HONEY' : f.is_mintable ? '⚠ MINT' : '✓ OK'}</div></div>
      </div>

      <div class="card-footer">
        <a class="dex-link" href="${esc(t.dex_link)}" target="_blank" rel="noopener">↗ DEXSCREENER</a>
        <span class="age-chip">${fmt.age(t.age_hours)}</span>
      </div>
    </div>`;
  }).join('');
}

function startAutoRefresh() {
  stopAutoRefresh();
  fetchTokens();

  countdownSecs = REFRESH_SEC;
  updateCountdown();

  countdownTimer = setInterval(() => {
    countdownSecs--;
    updateCountdown();
    if (countdownSecs <= 0) countdownSecs = REFRESH_SEC;
  }, 1000);

  refreshTimer = setInterval(() => {
    fetchTokens(true);
    countdownSecs = REFRESH_SEC;
  }, REFRESH_SEC * 1000);
}

function stopAutoRefresh() {
  if (refreshTimer)   { clearInterval(refreshTimer);   refreshTimer = null; }
  if (countdownTimer) { clearInterval(countdownTimer); countdownTimer = null; }
}

function updateCountdown() {
  const fill = document.getElementById('countdown-fill');
  const text = document.getElementById('countdown-text');
  if (fill) fill.style.width = ((countdownSecs / REFRESH_SEC) * 100) + '%';
  if (text) text.textContent = countdownSecs + 's';
}

/* ═══════════════════════════════════════════════════════════════════════════
   TRACK
   ═══════════════════════════════════════════════════════════════════════════ */
async function trackToken(address) {
  address = address.trim();
  if (!address) { showToast('Enter a token address'); return; }

  const btn    = document.getElementById('track-btn');
  const result = document.getElementById('track-result');

  btn.disabled = true;
  btn.textContent = 'SCANNING…';

  result.innerHTML = `
    <div class="empty-state">
      <div class="spinner" style="margin:0 auto 16px"></div>
      <div class="loading-text">FETCHING TOKEN DATA…</div>
    </div>`;

  try {
    const res  = await fetch(API_BASE + '/track/' + encodeURIComponent(address));
    if (!res.ok) throw new Error('HTTP ' + res.status);
    const data = await res.json();
    renderTrackResult(data, address);
  } catch (err) {
    result.innerHTML = `
      <div class="empty-state">
        <div class="empty-icon">⚠</div>
        <div class="empty-title">FETCH FAILED</div>
        <div class="empty-desc">${esc(err.message)}</div>
      </div>`;
  } finally {
    btn.disabled = false;
    btn.textContent = 'ANALYZE';
  }
}

function renderTrackResult(d, address) {
  const result = document.getElementById('track-result');
  const f   = d.security_flags || {};
  const bd  = d.breakdown || {};
  const sc  = scoreClass(d.score);
  const up  = (d.price_change_1h || 0) >= 0;

  const bdItems = [
    { label: 'Volume Momentum',    val: bd.volume_momentum,    max: 25 },
    { label: 'Buy Pressure',       val: bd.buy_pressure,       max: 20 },
    { label: 'Liquidity Strength', val: bd.liquidity_strength, max: 20 },
    { label: 'Price Momentum',     val: bd.price_momentum,     max: 15 },
    { label: 'Age Freshness',      val: bd.age_freshness,      max: 10 },
    { label: 'FDV Attractiveness', val: bd.fdv_attractiveness, max: 10 },
  ];

  const barsHtml = bdItems.map(item => {
    const pct = ((item.val || 0) / item.max) * 100;
    return `
    <div class="bar-row">
      <div class="bar-label">${item.label}</div>
      <div class="bar-track"><div class="bar-fill pos" style="width:${pct}%"></div></div>
      <div class="bar-val">${item.val || 0}/${item.max}</div>
    </div>`;
  }).join('');

  const penaltyHtml = (bd.penalties || 0) < 0 ? `
    <div class="bar-row">
      <div class="bar-label" style="color:var(--pink)">Penalties</div>
      <div class="bar-track"><div class="bar-fill neg" style="width:${(Math.abs(bd.penalties) / 90) * 100}%"></div></div>
      <div class="bar-val neg">${bd.penalties}</div>
    </div>` : '';

  const flagsData = [
    { label: 'Honeypot',         safe: !f.is_honeypot },
    { label: 'Mintable',         safe: !f.is_mintable },
    { label: 'Freeze Auth',      safe: !f.freeze_authority },
    { label: 'Owner Renounced',  safe: !!f.owner_renounced },
    { label: `Top10: ${f.top10_holder_pct || 0}%`, safe: (f.top10_holder_pct || 0) <= 60 },
  ];
  const flagsHtml = flagsData.map(flag => `
    <div class="flag-item ${flag.safe ? 'safe' : 'danger'}">
      <div class="flag-dot"></div>
      ${flag.safe ? '✓' : '⚠'} ${flag.label}
    </div>`).join('');

  result.innerHTML = `
  <div class="track-result-card">
    <div class="track-header">
      <div>
        <div class="track-token-name">${esc(d.name || 'Unknown Token')}</div>
        <div class="track-address">${esc(address)}</div>
      </div>
      <div class="big-score ${sc}">
        <span class="big-score-num">${d.score}</span>
        <span class="big-score-lbl">SCORE</span>
      </div>
    </div>

    <div class="track-stats">
      <div class="track-stat"><div class="track-stat-lbl">FDV</div>        <div class="track-stat-val">${fmt.usd(d.fdv)}</div></div>
      <div class="track-stat"><div class="track-stat-lbl">LIQUIDITY</div>  <div class="track-stat-val">${fmt.usd(d.liquidity)}</div></div>
      <div class="track-stat"><div class="track-stat-lbl">1H VOLUME</div>  <div class="track-stat-val">${fmt.usd(d.volume_1h)}</div></div>
      <div class="track-stat"><div class="track-stat-lbl">1H CHANGE</div>  <div class="track-stat-val ${up ? 'up' : 'down'}">${fmt.pct(d.price_change_1h)}</div></div>
      <div class="track-stat"><div class="track-stat-lbl">TOKEN AGE</div>  <div class="track-stat-val">${fmt.age(d.age_hours)}</div></div>
      <div class="track-stat"><div class="track-stat-lbl">PRICE USD</div>  <div class="track-stat-val">$${parseFloat(d.price_usd || 0).toFixed(6)}</div></div>
    </div>

    <div class="section-title-row">SCORE BREAKDOWN</div>
    <div class="breakdown-bars">
      ${barsHtml}
      ${penaltyHtml}
    </div>

    <div class="section-title-row">SECURITY FLAGS</div>
    <div class="flags-grid">${flagsHtml}</div>

    <a class="dex-link" href="${esc(d.dex_link)}" target="_blank" rel="noopener">↗ VIEW ON DEXSCREENER</a>
  </div>`;
}

/* ═══════════════════════════════════════════════════════════════════════════
   ABOUT — image uploads (runtime preview only)
   ═══════════════════════════════════════════════════════════════════════════ */
function initImageUploads() {
  // ── Logo ──────────────────────────────────────────────────────────────────
  const logoInput       = document.getElementById('logo-file-input');
  const logoPreview     = document.getElementById('logo-preview');
  const logoPlaceholder = document.getElementById('logo-placeholder-content');

  if (logoInput) {
    logoInput.addEventListener('change', e => {
      const file = e.target.files[0];
      if (!file) return;
      const url = URL.createObjectURL(file);

      // About page — show image, hide placeholder
      if (logoPreview) {
        logoPreview.src = url;
        logoPreview.classList.remove('hidden');
      }
      if (logoPlaceholder) logoPlaceholder.style.display = 'none';

      // Nav logo — mirror the upload
      const navImg         = document.getElementById('nav-logo-img');
      const navPlaceholder = document.getElementById('nav-logo-placeholder');
      if (navImg) {
        navImg.src = url;
        navImg.classList.remove('hidden');
      }
      if (navPlaceholder) navPlaceholder.style.display = 'none';

      // Gate logo (if still visible somehow)
      showToast('✓ Logo updated');
    });
  }

  // ── Background banner ─────────────────────────────────────────────────────
  const bgInput   = document.getElementById('bg-file-input');
  const bgPreview = document.getElementById('bg-preview');
  const bgOverlay = document.getElementById('bg-upload-overlay');

  if (bgInput) {
    bgInput.addEventListener('change', e => {
      const file = e.target.files[0];
      if (!file) return;
      const url = URL.createObjectURL(file);
      if (bgPreview) {
        bgPreview.src = url;
        bgPreview.classList.remove('hidden');
      }
      // dim overlay text once image is shown
      if (bgOverlay) bgOverlay.style.background = 'rgba(3,3,8,0.55)';
      showToast('✓ Background image updated');
    });
  }
}

/* ═══════════════════════════════════════════════════════════════════════════
   INIT APP  (called after gate is passed)
   ═══════════════════════════════════════════════════════════════════════════ */
function initApp() {
  // Nav routing
  document.querySelectorAll('.nav-link[data-page]').forEach(link => {
    link.addEventListener('click', () => navigate(link.dataset.page));
  });

  window.addEventListener('hashchange', () => {
    const h = window.location.hash.replace('#', '') || 'dashboard';
    navigate(h);
  });

  // Refresh button
  document.getElementById('refresh-btn')?.addEventListener('click', () => {
    fetchTokens();
    countdownSecs = REFRESH_SEC;
  });

  // Track button
  document.getElementById('track-btn')?.addEventListener('click', () => {
    trackToken(document.getElementById('track-input')?.value || '');
  });
  document.getElementById('track-input')?.addEventListener('keydown', e => {
    if (e.key === 'Enter') trackToken(e.target.value || '');
  });

  // Image uploads
  initImageUploads();

  // Route to correct page
  const hash = window.location.hash.replace('#', '') || 'dashboard';
  navigate(hash);
}

/* ═══════════════════════════════════════════════════════════════════════════
   BOOT
   ═══════════════════════════════════════════════════════════════════════════ */
document.addEventListener('DOMContentLoaded', initGate);
