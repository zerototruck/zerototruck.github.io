const $ = (s, r = document) => r.querySelector(s);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const usd = n => '$' + Number(n || 0).toLocaleString('en-US', { maximumFractionDigits: 0 });
const httpsOnly = u => /^https:\/\//i.test(u || '');
const MILESTONES = [[100, '100% — now we really have to buy the truck.'], [75, '75% — someone should probably find a truck.'], [50, '50% — half a truck belongs to the internet.'], [25, '25% — okay, this is getting real.'], [0, '0% — still imaginary.']];
const getJSON = async p => { const r = await fetch(p + '?t=' + Date.now(), { cache: 'no-store' }); if (!r.ok) throw new Error(p); return r.json(); };
let cfg, selected = null;

/* Clickjacking / look-alike frame guard. A <meta> CSP cannot set frame-ancestors, so refuse to run inside another site's frame. */
if (window.top !== window.self) {
  const a = document.createElement('a'); a.href = location.href; a.target = '_top'; a.rel = 'noopener'; a.textContent = location.href;
  const p = document.createElement('p'); p.className = 'framed';
  p.append('This page is shown inside another website. For your safety, open the original instead: ', a);
  document.body.replaceChildren(p);
  throw new Error('framed');
}

/* nav, share, reveal */
$('#theme').onclick = () => {
  const t = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
  document.documentElement.dataset.theme = t;
  try { localStorage.setItem('theme-v2', t); } catch (e) {}
};
const nav = $('.nav');
const onScroll = () => nav.classList.toggle('scrolled', scrollY > 40);
addEventListener('scroll', onScroll, { passive: true }); onScroll();
$('.menu').onclick = e => { const o = nav.classList.toggle('open'); e.currentTarget.setAttribute('aria-expanded', o); };
nav.querySelectorAll('nav a').forEach(x => x.addEventListener('click', () => nav.classList.remove('open')));
const sb = $('#shareBtn'), sbLabel = sb.textContent;
sb.onclick = async () => {
  const d = { title: 'Zero to Truck', text: 'Can the internet buy a truck?', url: location.href.split('#')[0] };
  try {
    if (navigator.share) await navigator.share(d);
    else { await navigator.clipboard.writeText(d.url); sb.textContent = 'LINK COPIED ✓'; setTimeout(() => { sb.textContent = sbLabel; }, 2000); }
  } catch (e) {}
};
const io = 'IntersectionObserver' in window ? new IntersectionObserver(es => es.forEach(x => { if (x.isIntersecting) { x.target.classList.add('show'); io.unobserve(x.target); } }), { threshold: .2 }) : null;
document.querySelectorAll('.reveal').forEach(el => io ? io.observe(el) : el.classList.add('show'));

/* wallets — everything is built from config.json */
const enabled = () => cfg.assets.filter(a => a.enabled !== false);

function renderPicker() {
  $('#picker').innerHTML = enabled().map(a =>
    `<button class="chip" role="tab" data-id="${esc(a.id)}" aria-selected="${a.id === selected}">${esc(a.id)}</button>`).join('');
}

/* QR codes are built here from the same string that is shown as text, so the picture cannot disagree with the address.
   Order: generated QR -> pre-made image (config qr_image, local assets/ only) -> text message.
   Set "qr_mode": "image" on a wallet to prefer the image. scripts/verify_qr.py decodes every image and checks it. */
const SVGNS = 'http://www.w3.org/2000/svg';
function qrSvg(payload) {
  const q = qrcode(0, 'M'); q.addData(payload); q.make();
  const n = q.getModuleCount(), m = 4, size = n + m * 2; let d = '';
  for (let r = 0; r < n; r++) for (let c = 0; c < n; c++) if (q.isDark(r, c)) d += 'M' + (c + m) + ' ' + (r + m) + 'h1v1h-1z';
  const svg = document.createElementNS(SVGNS, 'svg'), bg = document.createElementNS(SVGNS, 'rect'), p = document.createElementNS(SVGNS, 'path');
  svg.setAttribute('viewBox', '0 0 ' + size + ' ' + size); svg.setAttribute('shape-rendering', 'crispEdges');
  bg.setAttribute('width', size); bg.setAttribute('height', size); bg.setAttribute('fill', '#fff');
  p.setAttribute('d', d); p.setAttribute('fill', '#000'); svg.append(bg, p);
  return svg;
}
function qrImage(a, label) {
  return new Promise((ok, no) => {
    const p = a.qr_image || '';
    if (!/^assets\/[\w.\/-]+$/.test(p) || p.includes('..')) return no(new Error('no image'));
    const img = new Image(); img.alt = label; img.onload = () => ok(img); img.onerror = () => no(new Error('image failed')); img.src = p;
  });
}
async function drawQR(a) {
  const el = $('#qr'), label = 'QR code for the ' + a.id + ' address';
  const code = async () => { const s = qrSvg(a.qr_uri || a.address); s.setAttribute('role', 'img'); s.setAttribute('aria-label', label); return s; };
  const order = a.qr_mode === 'image' ? [() => qrImage(a, label), code] : [code, () => qrImage(a, label)];
  for (const make of order) {
    try { const node = await make(); if (el.isConnected) el.replaceChildren(node); return; } catch (e) { /* try the next source */ }
  }
  if (el.isConnected) el.textContent = 'QR unavailable. Copy the address instead.';
}

async function copy(text, btn) {
  try { await navigator.clipboard.writeText(text); }
  catch (e) {
    const t = document.createElement('textarea'); t.value = text; document.body.append(t); t.select();
    try { document.execCommand('copy'); } catch (_) {}
    t.remove();
  }
  const orig = btn.dataset.l || (btn.dataset.l = btn.textContent);
  btn.textContent = 'COPIED ✓';
  clearTimeout(btn._t);
  btn._t = setTimeout(() => { btn.textContent = orig; }, 2000);
}

function renderWallet() {
  const a = enabled().find(x => x.id === selected);
  if (!a) { $('#wallet').textContent = 'No wallets configured.'; return; }
  const url = httpsOnly(a.explorer) ? a.explorer + encodeURIComponent(a.address) : null;
  $('#wallet').innerHTML = `
    <div class="w-head"><b>${esc(a.symbol || a.id)}</b><span>${esc(a.network)}</span></div>
    <div class="w-body">
      <div class="w-info">
        <small>WALLET ADDRESS</small><code>${esc(a.address)}</code>
        ${a.destination_tag ? `<small>DESTINATION TAG (REQUIRED)</small><code>${esc(a.destination_tag)}</code>` : ''}
        <div class="row">
          <button class="btn primary" id="copy">COPY ADDRESS</button>
          ${url ? `<a class="btn secondary" target="_blank" rel="noopener noreferrer" href="${esc(url)}">OPEN EXPLORER ↗</a>` : ''}
        </div>
      </div>
      <div class="qr" id="qr"></div>
    </div>
    <p class="w-warn">${esc(a.warning || 'VERIFY ASSET + NETWORK BEFORE SENDING')}</p><p class="w-note" id="note" aria-live="polite">Scan with your wallet app (Send → Scan). A phone camera may only show the address as text.</p>`;
  drawQR(a);
  $('#copy').onclick = e => { track('copy-' + a.id); copy(a.address, e.currentTarget); $('#note').textContent = 'Copied. Before sending, compare the last 6 characters (' + a.address.slice(-6) + ') in your wallet.'; }; // closure: always this wallet
}

$('#picker').onclick = e => {
  const b = e.target.closest('[data-id]');
  if (!b) return;
  selected = b.dataset.id; renderPicker(); renderWallet();
};

/* progress */
let lastPct = 0;
function placeTruck(pct) {
  const x = (pct / 100 - .5) * $('.truckometer').clientWidth * .6;
  $('.truck-silhouette').style.transform = `translateX(${x}px)`;
}
window.addEventListener('resize', () => placeTruck(lastPct));
function renderProgress(p) {
  const goal = cfg.campaign.goal_usd; // fixed public goal; never raised automatically
  const raised = Math.max(0, Number(p && p.verified_raised_usd) || 0);
  const pct = Math.min(100, raised / goal * 100);
  lastPct = pct;
  renderMilestones(pct);
  $('#amount').textContent = usd(raised);
  $('#heroRaised').textContent = usd(raised);
  $('#heroGoal').textContent = usd(goal);
  $('#heroPct').textContent = Math.floor(pct) + '%';
  $('#heroFill').style.width = pct + '%';
  $('#percent').textContent = Math.floor(pct) + '%';
  $('#milestone').textContent = MILESTONES.find(([t]) => Math.floor(pct) >= t)[1];
  $('#verified').textContent = raised ? 'Last updated: ' + ((p && p.updated) || 'not set') + '. Check the wallets yourself.' : 'No verified contributions yet.';
  $('#barFill').style.width = pct + '%';
  renderCoverage(p);
  placeTruck(pct);
  $('#engine').hidden = raised < goal;
  $('#excess').textContent = usd(Math.max(0, raised - goal));
}

function renderCoverage(p) {
  const c = p && p.coverage, el = $('#coverage');
  if (!c) { el.textContent = ''; return; }
  const list = v => (Array.isArray(v) ? v : []).map(String).join(', ');
  const parts = [];
  if ((c.auto_tracked || []).length) parts.push('Tracked automatically: ' + list(c.auto_tracked) + '.');
  if ((c.manual_only || []).length) parts.push('Added by hand: ' + list(c.manual_only) + '.');
  if ((c.incomplete || []).length) parts.push('⚠ History not fully scanned yet for: ' + list(c.incomplete) + '.');
  const dis = (c.balance_check && c.balance_check.discrepancies) || [];
  if (dis.length) parts.push('⚠ The on-chain balance of ' + dis.map(d => String(d.asset)).join(', ') + ' is higher than the recorded contributions, so the total may be understated until the ledger is updated.');
  const gone = (c.recheck && c.recheck.unconfirmed) || [];
  if (gone.length) parts.push('⚠ ' + gone.length + ' recorded contribution(s) could not be re-confirmed on-chain and are being reviewed.');
  el.textContent = parts.join(' ');
}

function renderLedger(l) {
  const rows = ((l && l.entries) || []).filter(r => r && r.status === 'confirmed');
  $('#ledgerTable').hidden = !rows.length;
  $('#ledgerEmpty').hidden = !!rows.length;
  $('#ledgerTable tbody').innerHTML = rows.map(r => `<tr>
    <td>${esc(r.amount_native ?? '')}</td><td>${esc(r.asset)}</td><td>${esc(r.network)}</td>
    <td><code>${esc(String(r.tx_hash || '').slice(0, 10))}…</code></td><td>${usd(r.usd_value)}</td>
    <td>${esc(r.timestamp)}</td><td>${esc(r.status)}</td>
    <td>${httpsOnly(r.explorer_url) ? `<a href="${esc(r.explorer_url)}" target="_blank" rel="noopener noreferrer">View ↗</a>` : ''}</td></tr>`).join('');
}

function renderProof(pr, ch) {
  const items = (pr && pr.purchase_proof) || [];
  $('#purchase').innerHTML = items.length
    ? items.map(i => httpsOnly(i.url) ? `<li><a href="${esc(i.url)}" target="_blank" rel="noopener noreferrer">${esc(i.title)}</a></li>` : '').join('')
    : '<li>Nothing purchased yet, so nothing to show yet.</li>';
  const log = ((ch && ch.entries) || []).slice().reverse().slice(0, 3);
  $('#changes').innerHTML = log.map(c => `<li><time>${esc(c.date)}</time>${esc(c.text)}</li>`).join('');
}

async function loadData() {
  const [p, l, pr, ch, fq] = await Promise.all(['progress', 'ledger', 'proof', 'changes', 'faq'].map(n => getJSON(`data/${n}.json`).catch(() => null)));
  renderProgress(p); renderLedger(l); renderProof(pr, ch); renderTimeline(ch); renderFaq(fq);
}

$('#refresh').onclick = async e => { const b = e.currentTarget; b.textContent = 'Refreshing…'; await loadData(); b.textContent = 'Refresh'; };

(async () => {
  try { cfg = await getJSON('config.json'); }
  catch (e) { $('#wallet').textContent = 'Could not load config.json. Serve this folder over http (for example: python3 -m http.server).'; return; }
  const first = enabled()[0]; selected = first && first.id; renderSocial(); initAnalytics();
  renderPicker(); renderWallet();
  await loadData();
})();

/* timeline, milestones, FAQ, social links, analytics */
/* Analytics = GoatCounter (privacy-friendly, no cookies) used as a plain 1x1 image request. No third-party script ever runs on this page. */
let gcHost = ''; const gcKeep = [];
const ping = (path, title, event) => { if (!gcHost) return; try {
  const q = new URLSearchParams({ p: path, t: title || document.title, rnd: Math.random().toString(36).slice(2) });
  if (event) q.set('e', 'true'); else if (document.referrer) q.set('r', document.referrer);
  const i = new Image(1, 1); i.alt = ''; i.src = `https://${gcHost}.goatcounter.com/count?${q}`; gcKeep.push(i);
} catch (e) {} };
const track = n => ping('event/' + n, n, true);
function renderTimeline(ch) {
  const rows = ((ch && ch.entries) || []).slice().reverse();
  $('#timeline').innerHTML = rows.length ? rows.map(c => `<li><time>${esc(c.date)}</time><span class="tag">${esc(c.type || 'update')}</span><p>${esc(c.text)}</p></li>`).join('') : '<li>No updates yet.</li>';
}
function renderMilestones(pct) {
  const url = (cfg.site && cfg.site.url) || location.href.split('#')[0];
  $('#milestones').innerHTML = (cfg.milestones || []).map(m => {
    const hit = pct >= m.pct, text = `Zero to Truck — ${m.line} Public wallets, public receipts. ${url}`;
    return `<li class="${hit ? 'hit' : ''}"><b>${m.pct}%</b><span>${esc(m.line.replace(/^\d+% — /, ''))}</span>` + (hit
      ? `<button class="btn secondary sm" data-copy="${esc(text)}">COPY TEXT</button><a class="btn secondary sm" target="_blank" rel="noopener noreferrer" href="https://twitter.com/intent/tweet?text=${encodeURIComponent(text)}">SHARE ON X ↗</a>`
      : '<em>not yet</em>') + '</li>';
  }).join('');
}
$('#milestones').onclick = e => { const b = e.target.closest('[data-copy]'); if (b) copy(b.dataset.copy, b); };
function renderFaq(f) {
  $('#faqList').innerHTML = ((f && f.items) || []).map(i => `<details><summary>${esc(i.q)}</summary><p>${esc(i.a)}</p></details>`).join('');
}
function renderSocial() {
  const s = cfg.social || {}, L = [['X', s.x], ['Instagram', s.instagram]].filter(x => httpsOnly(x[1]));
  const html = L.map(([n, u]) => `<a href="${esc(u)}" target="_blank" rel="noopener noreferrer">${n} ↗</a>`).join('');
  $('#social').innerHTML = html; $('.nav nav').insertAdjacentHTML('beforeend', html);
}
function initAnalytics() { // off until config.analytics.goatcounter is set; skipped for Do-Not-Track and local previews
  const g = cfg.analytics && cfg.analytics.goatcounter;
  if (!/^[a-z0-9][a-z0-9-]{1,28}[a-z0-9]$/.test(g || '') || navigator.doNotTrack === '1' || /^(localhost|127\.0\.0\.1|\[::1\]|)$/.test(location.hostname)) return;
  gcHost = g; ping('/', document.title);
  $('.footer-copy small').append(' · Anonymous, cookie-free visit counts');
  if ('IntersectionObserver' in window) { const o = new IntersectionObserver(es => { if (es[0].isIntersecting) { track('fuel-viewed'); o.disconnect(); } }, { threshold: .3 }); o.observe($('#fuel')); }
}
