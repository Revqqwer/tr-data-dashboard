/* ════════════════════════════════════════
   TR DATA DASHBOARD – main.js
   ════════════════════════════════════════ */

/* ── Sidebar toggle ────────────────────────────────────────────────────────── */
(function () {
  // Sayfa yüklenirken animasyonsuz olarak önceki durumu uygula
  if (localStorage.getItem('sb_collapsed') === '1') {
    document.body.classList.add('no-sb-transition', 'sidebar-collapsed');
    requestAnimationFrame(function () {
      requestAnimationFrame(function () {
        document.body.classList.remove('no-sb-transition');
      });
    });
  }
})();

function toggleSidebar() {
  document.body.classList.toggle('sidebar-collapsed');
  var collapsed = document.body.classList.contains('sidebar-collapsed');
  localStorage.setItem('sb_collapsed', collapsed ? '1' : '0');
  // Transition bittikten sonra grafikleri/gridleri yeniden boyutlandır
  setTimeout(function () {
    window.dispatchEvent(new Event('resize'));
    Object.values(charts).forEach(function (c) { try { c.resize(); } catch (e) {} });
    Object.values(grids).forEach(function (g) { try { g.onParentResize(); } catch (e) {} });
  }, 270);
}
/* ─────────────────────────────────────────────────────────────────────────── */

let allDth    = [];
let allMenkul = [];
let allKredi      = [];
let allKrediDetay = [];
let allButce      = [];
let allDT         = [];
let allTurizm     = [];
let allBoP        = [];
let allKonut      = [];
let allEnflasyon  = [];
let allMakro      = [];
let allAbSurplus  = [];
let adminMakroForecast = []; // 3N Finans admin tahmini (server'dan gelir)

/* Ortak inline plugin: son veri noktasını x ekseninde zorla göster */
const forceLastTickPlugin = {
  id: 'forceLastTick',
  afterBuildTicks(chart) {
    const xScale = chart.scales.x;
    if (!xScale) return;
    const lastVal = chart.data.labels.length - 1;
    const ticks   = xScale.ticks;
    if (ticks.length && ticks[ticks.length - 1].value !== lastVal)
      ticks.push({ value: lastVal, label: chart.data.labels[lastVal] });
  }
};
let charts        = {};
let grids     = {};
let changeMode = 'haftalik';
let flowMode   = 'yil';
let currentPage     = 'dth';

/* ── Formatters ── */
const fmtRaw = v => v == null ? '—' : Math.round(v).toLocaleString('tr-TR');
const fmtDec = (v, d=2) => v == null ? '—' : v.toLocaleString('tr-TR', { minimumFractionDigits: d, maximumFractionDigits: d });

function formatDateTR(str) {
  const [d, m, y] = str.split('-');
  const mo = ['Oca','Şub','Mar','Nis','May','Haz','Tem','Ağu','Eyl','Eki','Kas','Ara'];
  return `${parseInt(d)} ${mo[parseInt(m)-1]} ${y}`;
}

function monthKey(str) {
  const [, m, y] = str.split('-');
  const mo = ['Oca','Şub','Mar','Nis','May','Haz','Tem','Ağu','Eyl','Eki','Kas','Ara'];
  return `${mo[parseInt(m)-1]} ${y}`;
}

function rolling13(arr) {
  return arr.map((_, i) => {
    if (i < 1) return null;
    const start = Math.max(0, i - 13);
    let sum = 0;
    for (let j = start + 1; j <= i; j++) {
      const delta = arr[j] != null && arr[j-1] != null ? arr[j] - arr[j-1] : null;
      if (delta == null) return null;
      sum += delta;
    }
    return sum;
  });
}

/* Rolling 13-week sum of raw weekly values (for flow data, not stock) */
function rolling13Flow(arr) {
  return arr.map((_, i) => {
    if (i < 13) return null;
    let sum = 0;
    for (let j = i - 12; j <= i; j++) {
      if (arr[j] == null) return null;
      sum += arr[j];
    }
    return sum;
  });
}

/* ── Chart defaults ── */
const baseScales = {
  x: { ticks: { color: '#6b7a99', maxTicksLimit: 10, font: { size: 11 } }, grid: { color: 'rgba(0,0,0,0.06)' } },
  y: { ticks: { color: '#6b7a99', font: { size: 11 } }, grid: { color: 'rgba(0,0,0,0.06)' } }
};
const baseTip = {
  backgroundColor: '#fff', borderColor: '#d8e0ef', borderWidth: 1,
  titleColor: '#6b7a99', bodyColor: '#1a2340', padding: 10
};

function makeChart(id, type, data, options, plugins) {
  const canvas = document.getElementById(id);
  if (!canvas) return null;

  // Var olan chart'ı update et — destroy/recreate yerine (flicker yok)
  if (charts[id]) {
    try {
      charts[id].data = data;
      if (options) charts[id].options = options;
      charts[id].update('none');   // animasyon yok, anında güncelleme
      return charts[id];
    } catch(e) {
      charts[id].destroy();
      charts[id] = null;
    }
  }

  charts[id] = new Chart(canvas, { type, data, options, plugins: plugins || [] });
  requestAnimationFrame(() => { if (charts[id]) charts[id].resize(); });
  return charts[id];
}

// Hangi sayfaların daha önce render edildiğini takip et
const _pageRendered = {};

/* ════════════════════════════════════════
   PAGE NAVIGATION
   ════════════════════════════════════════ */
function switchPage(page) {
  currentPage = page;

  // Nav active state
  document.querySelectorAll('.nav-item[data-page]').forEach(el => {
    el.classList.toggle('active', el.dataset.page === page);
  });

  // Topbars
  document.querySelectorAll('.topbar').forEach(el => el.classList.add('hidden'));
  const topbar = document.getElementById('topbar-' + page);
  if (topbar) topbar.classList.remove('hidden');

  // Sayfaları gizle
  document.querySelectorAll('.page-content').forEach(el => el.classList.add('hidden'));

  // GridStack — iframe sayfalarını atla, henüz yoksa init et
  const _iframePages = ['tefas','kripto','bist','bist-endeks-getiri','bist-endeks-karisim','global','market-briefs'];
  if (!grids[page] && !_iframePages.includes(page)) {
    grids[page] = GridStack.init({
      column: 12,
      cellHeight: 60,
      margin: 6,
      animate: true,
      float: false,
      resizable: { handles: 'se, sw, ne, nw, e, w, n, s' },
      draggable: { handle: '.chart-header' },
    }, '#page-' + page);

    grids[page].on('resizestop', (_, el) => {
      const wrap = el.querySelector('.chart-wrap');
      if (!wrap) return;
      const id = wrap.dataset.canvas;
      if (charts[id]) charts[id].resize();
    });
  }

  // Veri yoksa yükle; varsa önce render edilmişse sadece göster, değilse render et
  function _show(pageId, renderFn) {
    document.getElementById('page-' + pageId).classList.remove('hidden');
    if (!_pageRendered[pageId]) { renderFn(); _pageRendered[pageId] = true; }
  }

  if (page === 'dth') {
    if (!allDth.length) { loadDth(); }
    else { _show('dth', renderDth); }
  } else if (page === 'menkul') {
    if (!allMenkul.length) { loadMenkul(); }
    else { _show('menkul', renderMenkul); }
  } else if (page === 'kredi') {
    if (!allKredi.length) { loadKredi(); }
    else { _show('kredi', renderKredi); }
  } else if (page === 'kredi-detay') {
    if (!allKrediDetay.length) { loadKrediDetay(); }
    else { _show('kredi-detay', renderKrediDetay); }
  } else if (page === 'butce') {
    if (!allButce.length) { loadButce(); }
    else { _show('butce', renderButce); }
  } else if (page === 'dis-ticaret') {
    if (!allDT.length) { loadDT(); }
    else { _show('dis-ticaret', renderDT); }
  } else if (page === 'turizm') {
    if (!allTurizm.length) { loadTurizm(); }
    else { _show('turizm', renderTurizm); }
  } else if (page === 'odeme-dengesi') {
    if (!allBoP.length) { loadBoP(); }
    else { _show('odeme-dengesi', renderBoP); }
  } else if (page === 'konut') {
    if (!allKonut.length) { loadKonut(); }
    else { _show('konut', renderKonut); }
  } else if (page === 'enflasyon') {
    if (!allEnflasyon.length) { loadEnflasyon(); }
    else { _show('enflasyon', renderEnflasyon); }
  } else if (page === 'tcmb-ab') {
    if (!allAbSurplus.length) { loadAbSurplus(); }
    else { _show('tcmb-ab', renderAbSurplus); }
  } else if (page === 'makro') {
    if (!allMakro.length) { loadMakro(); }
    else { _show('makro', renderMakro); }
  } else if (page === 'tefas') {
    document.getElementById('page-tefas').classList.remove('hidden');
  } else if (page === 'kripto') {
    document.getElementById('page-kripto').classList.remove('hidden');
  } else if (page === 'bist') {
    document.getElementById('page-bist').classList.remove('hidden');
  } else if (page === 'bist-endeks-getiri') {
    document.getElementById('page-bist-endeks-getiri').classList.remove('hidden');
  } else if (page === 'bist-endeks-karisim') {
    document.getElementById('page-bist-endeks-karisim').classList.remove('hidden');
  } else if (page === 'global') {
    document.getElementById('page-global').classList.remove('hidden');
  } else if (page === 'market-briefs') {
    document.getElementById('page-market-briefs').classList.remove('hidden');
    mbLoad();
  }
}

document.querySelectorAll('.nav-item[data-page]:not(.disabled)').forEach(el => {
  el.addEventListener('click', e => { e.preventDefault(); switchPage(el.dataset.page); });
});

/* ════════════════════════════════════════
   DTH PAGE
   ════════════════════════════════════════ */
async function loadDth() {
  showLoading(true);
  try {
    const resp = await fetch('/api/dth');
    allDth = await resp.json();
    showLoading(false);
    document.getElementById('page-dth').classList.remove('hidden');
    renderDth();
  } catch (e) {
    console.error('DTH yüklenemedi:', e);
    showLoading(false);
    document.getElementById('page-dth').classList.remove('hidden');
  }
}

function getFilteredDth() {
  const w = parseInt(document.getElementById('rangeSelect').value);
  return w === 0 ? allDth : allDth.slice(-w);
}

function renderDth() {
  const data = getFilteredDth();
  if (!data.length) return;
  renderStockChart(data);
  renderChangeChart(data);
  renderRatioChart(data);
  renderCumChart(data);
  renderSpreadChart(data);
  bindToggleLegends();
}

function renderStockChart(data) {
  makeChart('dthChart', 'line', {
    labels: data.map(d => formatDateTR(d.tarih)),
    datasets: [
      { label: 'Toplam',   data: data.map(d => d.toplam),   borderColor: '#3b7ef8', backgroundColor: 'rgba(59,126,248,0.08)', fill: true, tension: 0.3, pointRadius: 0, borderWidth: 2 },
      { label: 'Bireysel', data: data.map(d => d.bireysel), borderColor: '#10b981', backgroundColor: 'transparent', tension: 0.3, pointRadius: 0, borderWidth: 2 },
      { label: 'Tüzel',    data: data.map(d => d.tuzel),    borderColor: '#f59e0b', backgroundColor: 'transparent', tension: 0.3, pointRadius: 0, borderWidth: 2 },
    ]
  }, {
    responsive: true, maintainAspectRatio: false,
    interaction: { mode: 'index', intersect: false },
    plugins: { legend: { display: false }, tooltip: { ...baseTip, callbacks: { label: c => ` ${c.dataset.label}: ${fmtRaw(c.parsed.y)} Mn` } } },
    scales: { ...baseScales, y: { ...baseScales.y, ticks: { ...baseScales.y.ticks, callback: v => (v/1000).toFixed(0) + 'Mrd' } } }
  });
}

function buildChangeData(data) {
  if (changeMode === 'haftalik') {
    const labels = data.slice(1).map(d => formatDateTR(d.tarih));
    const bD = data.slice(1).map((d, i) => d.bireysel != null && data[i].bireysel != null ? d.bireysel - data[i].bireysel : null);
    const tD = data.slice(1).map((d, i) => d.tuzel    != null && data[i].tuzel    != null ? d.tuzel    - data[i].tuzel    : null);
    return { labels, bD, tD };
  } else {
    const monthly = {};
    for (let i = 1; i < data.length; i++) {
      const key = monthKey(data[i].tarih);
      if (!monthly[key]) monthly[key] = { b: 0, t: 0, hasNull: false };
      const bD = data[i].bireysel != null && data[i-1].bireysel != null ? data[i].bireysel - data[i-1].bireysel : null;
      const tD = data[i].tuzel    != null && data[i-1].tuzel    != null ? data[i].tuzel    - data[i-1].tuzel    : null;
      if (bD == null || tD == null) { monthly[key].hasNull = true; continue; }
      monthly[key].b += bD; monthly[key].t += tD;
    }
    const labels = Object.keys(monthly).filter(k => !monthly[k].hasNull);
    return { labels, bD: labels.map(k => monthly[k].b), tD: labels.map(k => monthly[k].t) };
  }
}

function renderChangeChart(data) {
  const { labels, bD, tD } = buildChangeData(data);
  makeChart('changeChart', 'bar', {
    labels,
    datasets: [
      { label: 'Bireysel Δ', data: bD, backgroundColor: bD.map(v => v >= 0 ? 'rgba(16,185,129,0.8)'  : 'rgba(239,68,68,0.9)'),   borderRadius: 3 },
      { label: 'Tüzel Δ',    data: tD, backgroundColor: tD.map(v => v >= 0 ? 'rgba(245,158,11,0.8)' : 'rgba(124,58,237,0.85)'), borderRadius: 3 },
    ]
  }, {
    responsive: true, maintainAspectRatio: false,
    interaction: { mode: 'index', intersect: false },
    plugins: { legend: { display: false }, tooltip: { ...baseTip, callbacks: { label: c => ` ${c.dataset.label}: ${fmtRaw(c.parsed.y)} Mn` } } },
    scales: { x: { ...baseScales.x, stacked: true }, y: { ...baseScales.y, stacked: true } }
  });
}

function renderRatioChart(data) {
  makeChart('ratioChart', 'line', {
    labels: data.map(d => formatDateTR(d.tarih)),
    datasets: [{ label: 'B/T Rasyosu', data: data.map(d => d.bireysel && d.tuzel ? parseFloat((d.bireysel/d.tuzel).toFixed(4)) : null), borderColor: '#a78bfa', backgroundColor: 'rgba(167,139,250,0.08)', fill: true, tension: 0.3, pointRadius: 0, borderWidth: 2 }]
  }, {
    responsive: true, maintainAspectRatio: false,
    interaction: { mode: 'index', intersect: false },
    plugins: { legend: { display: false }, tooltip: { ...baseTip, callbacks: { label: c => ` B/T: ${c.parsed.y.toFixed(3)}x` } } },
    scales: { ...baseScales, y: { ...baseScales.y, ticks: { ...baseScales.y.ticks, callback: v => v.toFixed(2) + 'x' } } }
  });
}

function renderCumChart(data) {
  makeChart('cumChart', 'line', {
    labels: data.map(d => formatDateTR(d.tarih)),
    datasets: [
      { label: 'Toplam 13H',   data: rolling13(data.map(d => d.toplam)),   borderColor: '#3b7ef8', backgroundColor: 'transparent', tension: 0.3, pointRadius: 0, borderWidth: 2 },
      { label: 'Bireysel 13H', data: rolling13(data.map(d => d.bireysel)), borderColor: '#10b981', backgroundColor: 'transparent', tension: 0.3, pointRadius: 0, borderWidth: 2 },
      { label: 'Tüzel 13H',    data: rolling13(data.map(d => d.tuzel)),    borderColor: '#f59e0b', backgroundColor: 'transparent', tension: 0.3, pointRadius: 0, borderWidth: 2 },
    ]
  }, {
    responsive: true, maintainAspectRatio: false,
    interaction: { mode: 'index', intersect: false },
    plugins: { legend: { display: false }, tooltip: { ...baseTip, callbacks: { label: c => ` ${c.dataset.label}: ${fmtRaw(c.parsed.y)} Mn` } } },
    scales: baseScales
  });
}

function renderSpreadChart(data) {
  makeChart('spreadChart', 'line', {
    labels: data.map(d => formatDateTR(d.tarih)),
    datasets: [{ label: 'Birey–Tüzel Spread', data: data.map(d => d.bireysel != null && d.tuzel != null ? d.bireysel - d.tuzel : null), borderColor: '#f472b6', backgroundColor: 'rgba(244,114,182,0.08)', fill: true, tension: 0.3, pointRadius: 0, borderWidth: 2 }]
  }, {
    responsive: true, maintainAspectRatio: false,
    interaction: { mode: 'index', intersect: false },
    plugins: { legend: { display: false }, tooltip: { ...baseTip, callbacks: { label: c => ` Spread: ${fmtRaw(c.parsed.y)} Mn` } } },
    scales: { ...baseScales, y: { ...baseScales.y, ticks: { ...baseScales.y.ticks, callback: v => fmtRaw(v) } } }
  });
}

/* ── DTH toggles ── */
document.getElementById('btnHaftalik').addEventListener('click', () => {
  changeMode = 'haftalik';
  document.getElementById('btnHaftalik').classList.add('active');
  document.getElementById('btnAylik').classList.remove('active');
  renderChangeChart(getFilteredDth());
});
document.getElementById('btnAylik').addEventListener('click', () => {
  changeMode = 'aylik';
  document.getElementById('btnAylik').classList.add('active');
  document.getElementById('btnHaftalik').classList.remove('active');
  renderChangeChart(getFilteredDth());
});
document.getElementById('rangeSelect').addEventListener('change', renderDth)
document.getElementById('rangeSelect').addEventListener('change', () => { _pageRendered['dth'] = false; });;
document.getElementById('refreshBtn').addEventListener('click', () => { allDth = []; _pageRendered['dth'] = false; loadDth(); });

/* ════════════════════════════════════════
   MENKUL KIYMET PAGE
   ════════════════════════════════════════ */
async function loadMenkul() {
  showLoading(true);
  try {
    const resp = await fetch('/api/menkul');
    allMenkul = await resp.json();
    showLoading(false);
    document.getElementById('page-menkul').classList.remove('hidden');
    renderMenkul();
  } catch (e) {
    console.error('Menkul yüklenemedi:', e);
    showLoading(false);
    document.getElementById('page-menkul').classList.remove('hidden');
  }
}

function renderMenkul() {
  if (!allMenkul.length) return;
  renderCumFlowChart();
  renderFlowChart();
  initCumStartDate();
  renderCumStartChart();
  bindToggleLegends();
}

/* 13 Haftalık Hisse & DİBS – tek line chart */
function renderCumFlowChart() {
  const labels   = allMenkul.map(d => formatDateTR(d.tarih));
  const hisse13  = rolling13Flow(allMenkul.map(d => d.hisse));
  const dibs13   = rolling13Flow(allMenkul.map(d => d.dibs));

  makeChart('cumFlowChart', 'line', {
    labels,
    datasets: [
      { label: 'Hisse 13H', data: hisse13, borderColor: '#3b7ef8', backgroundColor: 'rgba(59,126,248,0.08)', fill: true, tension: 0.3, pointRadius: 0, borderWidth: 2 },
      { label: 'DİBS 13H',  data: dibs13,  borderColor: '#a855f7', backgroundColor: 'rgba(168,85,247,0.08)', fill: true, tension: 0.3, pointRadius: 0, borderWidth: 2 },
    ]
  }, {
    responsive: true, maintainAspectRatio: false,
    interaction: { mode: 'index', intersect: false },
    plugins: {
      legend: { display: false },
      tooltip: { ...baseTip, callbacks: { label: c => ` ${c.dataset.label}: ${fmtDec(c.parsed.y)} Mn` } }
    },
    scales: { ...baseScales, y: { ...baseScales.y, ticks: { ...baseScales.y.ticks, callback: v => fmtRaw(v) } } }
  });
}

/* Yabancı Hisse & Tahvil Alımı – filtrelenebilir stacked bar */
function getFlowSlice() {
  const thisYear = new Date().getFullYear();
  if (flowMode === '3ay')  return allMenkul.slice(-13);
  if (flowMode === 'yil')  return allMenkul.slice(-52);
  if (flowMode === 'ytd')  return allMenkul.filter(d => d.yil === thisYear);
  return allMenkul; // 'tum'
}

function renderFlowChart() {
  const data   = getFlowSlice();
  const labels = data.map(d => formatDateTR(d.tarih));
  const hisse  = data.map(d => d.hisse);
  const dibs   = data.map(d => d.dibs);
  makeChart('flowChart', 'bar', {
    labels,
    datasets: [
      { label: 'Hisse', data: hisse, backgroundColor: hisse.map(v => v >= 0 ? 'rgba(59,126,248,0.8)' : 'rgba(59,126,248,0.4)'),  borderRadius: 2 },
      { label: 'DİBS',  data: dibs,  backgroundColor: dibs.map(v  => v >= 0 ? 'rgba(168,85,247,0.8)' : 'rgba(168,85,247,0.4)'), borderRadius: 2 },
    ]
  }, {
    responsive: true, maintainAspectRatio: false,
    interaction: { mode: 'index', intersect: false },
    plugins: {
      legend: { display: false },
      tooltip: {
        ...baseTip,
        callbacks: {
          label: c => ` ${c.dataset.label}: ${fmtDec(c.parsed.y)} Mn`,
          afterBody: items => {
            const net = items.reduce((s, i) => s + (i.parsed.y || 0), 0);
            return [`Net: ${fmtDec(net)} Mn`];
          }
        }
      }
    },
    scales: {
      x: { ...baseScales.x, stacked: true },
      y: { ...baseScales.y, stacked: true, ticks: { ...baseScales.y.ticks, callback: v => fmtRaw(v) } }
    }
  });
}

/* Flow buton event'leri */
function setFlowMode(mode) {
  flowMode = mode;
  const map = { '3ay': 'btnFlow3Ay', 'yil': 'btnFlow1Yil', 'ytd': 'btnFlowYtd', 'tum': 'btnFlowTum' };
  Object.entries(map).forEach(([m, id]) => {
    const btn = document.getElementById(id);
    if (btn) btn.classList.toggle('active', m === mode);
  });
  renderFlowChart();
}

document.getElementById('btnFlow3Ay') .addEventListener('click', () => setFlowMode('3ay'));
document.getElementById('btnFlow1Yil').addEventListener('click', () => setFlowMode('yil'));
document.getElementById('btnFlowYtd') .addEventListener('click', () => setFlowMode('ytd'));
document.getElementById('btnFlowTum') .addEventListener('click', () => setFlowMode('tum'));

/* Kümülatif Hisse & DİBS – kullanıcı seçilen tarihten itibaren */
function initCumStartDate() {
  const el = document.getElementById('cumStartDate');
  if (!el || el.value) return;
  // Varsayılan: mevcut yılın başı
  el.value = `${new Date().getFullYear()}-01-01`;
}

function renderCumStartChart() {
  const el  = document.getElementById('cumStartDate');
  const val = el ? el.value : '';   // "YYYY-MM-DD"

  let data = allMenkul;
  if (val) {
    const [y, m, d] = val.split('-');
    const t0 = new Date(+y, +m - 1, +d);
    data = allMenkul.filter(row => {
      const [rd, rm, ry] = row.tarih.split('-');
      return new Date(+ry, +rm - 1, +rd) >= t0;
    });
  }
  if (!data.length) return;

  const labels = data.map(row => formatDateTR(row.tarih));
  let hCum = 0, dCum = 0;
  const hisseCum = data.map(row => { hCum += row.hisse || 0; return parseFloat(hCum.toFixed(2)); });
  const dibsCum  = data.map(row => { dCum += row.dibs  || 0; return parseFloat(dCum.toFixed(2)); });

  makeChart('cumStartChart', 'line', {
    labels,
    datasets: [
      { label: 'Hisse Kümülatif', data: hisseCum, borderColor: '#3b7ef8', backgroundColor: 'rgba(59,126,248,0.08)', fill: true, tension: 0.3, pointRadius: 0, borderWidth: 2 },
      { label: 'DİBS Kümülatif',  data: dibsCum,  borderColor: '#a855f7', backgroundColor: 'rgba(168,85,247,0.08)', fill: true, tension: 0.3, pointRadius: 0, borderWidth: 2 },
    ]
  }, {
    responsive: true, maintainAspectRatio: false,
    interaction: { mode: 'index', intersect: false },
    plugins: {
      legend: { display: false },
      tooltip: {
        ...baseTip,
        callbacks: {
          label: c => ` ${c.dataset.label}: ${fmtDec(c.parsed.y)} Mn`,
          afterBody: items => [`Net: ${fmtDec(items.reduce((s, i) => s + (i.parsed.y || 0), 0))} Mn`]
        }
      },
      annotation: {
        annotations: {
          zeroLine: { type: 'line', yMin: 0, yMax: 0, borderColor: 'rgba(0,0,0,0.18)', borderWidth: 1, borderDash: [4, 4] }
        }
      }
    },
    scales: { ...baseScales, y: { ...baseScales.y, ticks: { ...baseScales.y.ticks, callback: v => fmtRaw(v) } } }
  });
}

document.getElementById('cumStartDate').addEventListener('change', renderCumStartChart);

document.getElementById('refreshBtnMenkul').addEventListener('click', () => { allMenkul = []; _pageRendered['menkul'] = false; loadMenkul(); });

/* ════════════════════════════════════════
   SHARED UTILITIES
   ════════════════════════════════════════ */
function showLoading(show) {
  document.getElementById('loadingState').classList.toggle('hidden', !show);
  if (show) document.querySelectorAll('.page-content').forEach(el => el.classList.add('hidden'));
}

function bindToggleLegends() {
  document.querySelectorAll('.toggle-legend').forEach(el => {
    el.onclick = () => {
      const chart = charts[el.dataset.chart];
      if (!chart) return;
      const meta = chart.getDatasetMeta(parseInt(el.dataset.idx));
      meta.hidden = !meta.hidden;
      el.style.opacity = meta.hidden ? '0.3' : '1';
      chart.update();
    };
  });
}

/* ════════════════════════════════════════
   KREDİ SAYFASI
   ════════════════════════════════════════ */
async function loadKredi() {
  showLoading(true);
  try {
    const resp = await fetch('/api/credit');
    allKredi = await resp.json();
    showLoading(false);
    document.getElementById('page-kredi').classList.remove('hidden');
    renderKredi();
  } catch (e) {
    console.error('Kredi yüklenemedi:', e);
    showLoading(false);
    document.getElementById('page-kredi').classList.remove('hidden');
  }
}

function getFilteredKredi() {
  const w = parseInt(document.getElementById('rangeSelectKredi').value);
  return w === 0 ? allKredi : allKredi.slice(-w);
}

function renderKredi() {
  const data = getFilteredKredi();
  if (!data.length) return;
  renderKrediYoy(data);
  renderKredi13w(data);
  renderTukMomYoy(data);
  renderTicMomYoy(data);
  renderKrediUsd(data);
  bindToggleLegends();
}

/* ── Büyüme hesaplama yardımcıları ── */
function yoy(arr, offset = 52) {
  return arr.map((v, i) =>
    i < offset || v == null || arr[i - offset] == null ? null
    : parseFloat(((v - arr[i - offset]) / arr[i - offset] * 100).toFixed(2))
  );
}
function growth13w(arr) {
  return arr.map((v, i) =>
    i < 13 || v == null || arr[i - 13] == null ? null
    : parseFloat(((v - arr[i - 13]) / arr[i - 13] * 100).toFixed(2))
  );
}
function mom4w(arr) {
  return arr.map((v, i) =>
    i < 4 || v == null || arr[i - 4] == null ? null
    : parseFloat(((v - arr[i - 4]) / arr[i - 4] * 100).toFixed(2))
  );
}

/* 4: Yıllık büyüme */
function renderKrediYoy(data) {
  const labels  = data.map(d => formatDateTR(d.tarih));
  const tukYoy  = yoy(data.map(d => d.tuketici));
  const ticYoy  = yoy(data.map(d => d.ticari));
  makeChart('krediYoyChart', 'line', {
    labels,
    datasets: [
      { label: 'Tüketici YoY', data: tukYoy, borderColor: '#10b981', backgroundColor: 'transparent', tension: 0.3, pointRadius: 0, borderWidth: 2 },
      { label: 'Ticari YoY',   data: ticYoy, borderColor: '#3b7ef8', backgroundColor: 'transparent', tension: 0.3, pointRadius: 0, borderWidth: 2 },
    ]
  }, {
    responsive: true, maintainAspectRatio: false,
    interaction: { mode: 'index', intersect: false },
    plugins: {
      legend: { display: false },
      tooltip: { ...baseTip, callbacks: { label: c => ` ${c.dataset.label}: %${fmtDec(c.parsed.y, 1)}` } },
      annotation: { annotations: { zero: { type: 'line', yMin: 0, yMax: 0, borderColor: 'rgba(0,0,0,0.15)', borderWidth: 1, borderDash: [4,4] } } }
    },
    scales: { ...baseScales, y: { ...baseScales.y, ticks: { ...baseScales.y.ticks, callback: v => '%' + v } } }
  });
}

/* 5: 13 haftalık büyüme */
function renderKredi13w(data) {
  const labels = data.map(d => formatDateTR(d.tarih));
  const tuk13  = growth13w(data.map(d => d.tuketici));
  const tic13  = growth13w(data.map(d => d.ticari));
  makeChart('kredi13wChart', 'line', {
    labels,
    datasets: [
      { label: 'Tüketici 13H', data: tuk13, borderColor: '#10b981', backgroundColor: 'transparent', tension: 0.3, pointRadius: 0, borderWidth: 2 },
      { label: 'Ticari 13H',   data: tic13, borderColor: '#3b7ef8', backgroundColor: 'transparent', tension: 0.3, pointRadius: 0, borderWidth: 2 },
    ]
  }, {
    responsive: true, maintainAspectRatio: false,
    interaction: { mode: 'index', intersect: false },
    plugins: {
      legend: { display: false },
      tooltip: { ...baseTip, callbacks: { label: c => ` ${c.dataset.label}: %${fmtDec(c.parsed.y, 1)}` } },
      annotation: { annotations: { zero: { type: 'line', yMin: 0, yMax: 0, borderColor: 'rgba(0,0,0,0.15)', borderWidth: 1, borderDash: [4,4] } } }
    },
    scales: { ...baseScales, y: { ...baseScales.y, ticks: { ...baseScales.y.ticks, callback: v => '%' + v } } }
  });
}

/* 6: Tüketici aylık & yıllık – dual axis */
function renderTukMomYoy(data) {
  const labels  = data.map(d => formatDateTR(d.tarih));
  const tukMom  = mom4w(data.map(d => d.tuketici));
  const tukYoy_ = yoy(data.map(d => d.tuketici));
  makeChart('tukMomYoyChart', 'line', {
    labels,
    datasets: [
      { label: 'Aylık %',  data: tukMom,  borderColor: '#10b981', backgroundColor: 'transparent', tension: 0.3, pointRadius: 0, borderWidth: 2, yAxisID: 'y' },
      { label: 'Yıllık %', data: tukYoy_, borderColor: '#a855f7', backgroundColor: 'transparent', tension: 0.3, pointRadius: 0, borderWidth: 2, yAxisID: 'y1' },
    ]
  }, dualAxisOpts('%', 'Aylık %', '%', 'Yıllık %'));
}

/* 7: Ticari aylık & yıllık – dual axis */
function renderTicMomYoy(data) {
  const labels  = data.map(d => formatDateTR(d.tarih));
  const ticMom  = mom4w(data.map(d => d.ticari));
  const ticYoy_ = yoy(data.map(d => d.ticari));
  makeChart('ticMomYoyChart', 'line', {
    labels,
    datasets: [
      { label: 'Aylık %',  data: ticMom,  borderColor: '#3b7ef8', backgroundColor: 'transparent', tension: 0.3, pointRadius: 0, borderWidth: 2, yAxisID: 'y' },
      { label: 'Yıllık %', data: ticYoy_, borderColor: '#f59e0b', backgroundColor: 'transparent', tension: 0.3, pointRadius: 0, borderWidth: 2, yAxisID: 'y1' },
    ]
  }, dualAxisOpts('%', 'Aylık %', '%', 'Yıllık %'));
}

/* Dual axis chart options helper */
function dualAxisOpts(leftSuffix, leftLabel, rightSuffix, rightLabel) {
  return {
    responsive: true, maintainAspectRatio: false,
    interaction: { mode: 'index', intersect: false },
    plugins: {
      legend: { display: false },
      tooltip: { ...baseTip, callbacks: { label: c => ` ${c.dataset.label}: %${fmtDec(c.parsed.y, 2)}` } },
      annotation: { annotations: { zero: { type: 'line', yMin: 0, yMax: 0, borderColor: 'rgba(0,0,0,0.15)', borderWidth: 1, borderDash: [4,4] } } }
    },
    scales: {
      x: baseScales.x,
      y:  { ...baseScales.y, position: 'left',  ticks: { ...baseScales.y.ticks, callback: v => '%' + v } },
      y1: { ...baseScales.y, position: 'right', grid: { drawOnChartArea: false }, ticks: { ...baseScales.y.ticks, callback: v => '%' + v } },
    }
  };
}

/* Aylık TL Kredi Akımı (USD) */
function renderKrediMonthlyFlow(data) {
  // Her hafta için ay anahtarı oluştur, ayın son gözlemini sakla
  const mo = ['Oca','Şub','Mar','Nis','May','Haz','Tem','Ağu','Eyl','Eki','Kas','Ara'];
  const byMonth = {};
  data.forEach(d => {
    const [, m, y] = d.tarih.split('-');
    const key = `${y}-${m.padStart(2,'0')}`;
    byMonth[key] = d;  // son gözlem kazanır (veri sıralı geliyor)
  });

  const keys = Object.keys(byMonth).sort();
  const labels    = [];
  const flowsUsd  = [];

  for (let i = 1; i < keys.length; i++) {
    const curr = byMonth[keys[i]];
    const prev = byMonth[keys[i - 1]];
    if (curr.tuketici == null || prev.tuketici == null ||
        curr.ticari   == null || prev.ticari   == null) continue;

    const flowTl  = (curr.tuketici + curr.ticari) - (prev.tuketici + prev.ticari);
    const flowUsd = curr.usdtry ? parseFloat((flowTl / curr.usdtry).toFixed(0)) : null;

    const [y, m] = keys[i].split('-');
    labels.push(`${mo[parseInt(m) - 1]} ${y}`);
    flowsUsd.push(flowUsd);
  }

  makeChart('krediMonthlyFlowChart', 'line', {
    labels,
    datasets: [{
      label: 'Aylık USD Akım',
      data: flowsUsd,
      borderColor: '#3b7ef8',
      backgroundColor: flowsUsd.map(v => v == null ? 'transparent' : v >= 0 ? 'rgba(59,126,248,0.12)' : 'rgba(239,68,68,0.12)'),
      fill: true,
      tension: 0.3,
      pointRadius: 0,
      borderWidth: 2,
    }]
  }, {
    responsive: true, maintainAspectRatio: false,
    interaction: { mode: 'index', intersect: false },
    plugins: {
      legend: { display: false },
      tooltip: { ...baseTip, callbacks: { label: c => ` Aylık Flow: ${fmtRaw(c.parsed.y)} Mn $` } },
      annotation: {
        annotations: {
          zero: { type: 'line', yMin: 0, yMax: 0, borderColor: 'rgba(0,0,0,0.18)', borderWidth: 1, borderDash: [4, 4] }
        }
      }
    },
    scales: {
      ...baseScales,
      y: { ...baseScales.y, ticks: { ...baseScales.y.ticks, callback: v => (v / 1000).toFixed(0) + 'Mrd' } }
    }
  });
}

/* ── Ticari USD ── */
function renderKrediUsd(data) {
  const usdData = data.filter(d => d.ticari_usd != null);
  if (!usdData.length) return;
  makeChart('krediUsdChart', 'line', {
    labels: usdData.map(d => formatDateTR(d.tarih)),
    datasets: [{ label: 'Ticari USD', data: usdData.map(d => d.ticari_usd), borderColor: '#f59e0b', backgroundColor: 'rgba(245,158,11,0.08)', fill: true, tension: 0.3, pointRadius: 0, borderWidth: 2 }]
  }, {
    responsive: true, maintainAspectRatio: false,
    interaction: { mode: 'index', intersect: false },
    plugins: { legend: { display: false }, tooltip: { ...baseTip, callbacks: { label: c => ` Ticari USD: ${fmtRaw(c.parsed.y)} Mn $` } } },
    scales: { ...baseScales, y: { ...baseScales.y, ticks: { ...baseScales.y.ticks, callback: v => (v/1000).toFixed(0) + 'Mrd' } } }
  });
}

/* Kredi kontrolleri */
document.getElementById('rangeSelectKredi').addEventListener('change', renderKredi);
document.getElementById('refreshBtnKredi').addEventListener('click', () => { allKredi = []; _pageRendered['kredi'] = false; loadKredi(); });

/* ════════════════════════════════════════
   KREDİ DETAY SAYFASI
   ════════════════════════════════════════ */
async function loadKrediDetay() {
  showLoading(true);
  try {
    const resp = await fetch('/api/credit-detail');
    allKrediDetay = await resp.json();
    showLoading(false);
    document.getElementById('page-kredi-detay').classList.remove('hidden');
    renderKrediDetay();
  } catch (e) {
    console.error('Kredi Detay yüklenemedi:', e);
    showLoading(false);
    document.getElementById('page-kredi-detay').classList.remove('hidden');
  }
}

function getFilteredKrediDetay() {
  const w = parseInt(document.getElementById('rangeSelectKrediDetay').value);
  return w === 0 ? allKrediDetay : allKrediDetay.slice(-w);
}

function renderKrediDetay() {
  const data = getFilteredKrediDetay();
  if (!data.length) return;
  renderKdKonut(data);
  renderKdIhtiyac(data);
  renderKdTasit(data);
  renderKdKobiYoy(data);
  renderKdKkYoy(data);
  renderKd13w(data);
  bindToggleLegends();
}

const KD_COLORS = {
  konut:  '#3b7ef8',
  tasit:  '#10b981',
  ihtiyac:'#f59e0b',
  kk:     '#a855f7',
  kobi:   '#f472b6',
  taksitli:  '#6366f1',
  taksitsiz: '#ec4899',
};

function kdLine(label, field, color, data) {
  return {
    label, data: data.map(d => d[field]),
    borderColor: color, backgroundColor: 'transparent',
    tension: 0.3, pointRadius: 0, borderWidth: 2
  };
}

/* ── Kredi Detay: dual-axis yardımcısı ── */
function kdDualAxis(canvasId, labelLeft, colorLeft, dataLeft, labelRight, colorRight, dataRight, labels, label13h, data13h) {
  const pctTick  = v => '%' + v.toFixed(1);
  const zeroLine = { type: 'line', yMin: 0, yMax: 0, borderColor: 'rgba(0,0,0,0.15)', borderWidth: 1, borderDash: [4, 4] };
  const datasets = [
    {
      label: labelLeft, data: dataLeft, yAxisID: 'y',
      borderColor: colorLeft, backgroundColor: 'transparent',
      tension: 0.3, pointRadius: 0, borderWidth: 2
    },
    {
      label: labelRight, data: dataRight, yAxisID: 'y1',
      borderColor: colorRight, backgroundColor: 'transparent',
      tension: 0.3, pointRadius: 0, borderWidth: 2
    },
  ];
  if (label13h != null) {
    datasets.push({
      label: label13h, data: data13h, yAxisID: 'y',
      borderColor: '#64748b', backgroundColor: 'transparent',
      tension: 0.3, pointRadius: 0, borderWidth: 2
    });
  }
  makeChart(canvasId, 'line', { labels, datasets }, {
    responsive: true, maintainAspectRatio: false,
    interaction: { mode: 'index', intersect: false },
    plugins: {
      legend: { display: false },
      tooltip: { ...baseTip, callbacks: { label: c => ` ${c.dataset.label}: %${fmtDec(c.parsed.y, 1)}` } },
      annotation: { annotations: { zero: zeroLine } }
    },
    scales: {
      x: baseScales.x,
      y: {
        ...baseScales.y,
        position: 'left',
        title: { display: true, text: labelLeft, color: colorLeft, font: { size: 11 } },
        ticks: { ...baseScales.y.ticks, callback: pctTick, color: colorLeft }
      },
      y1: {
        ...baseScales.y,
        position: 'right',
        grid: { drawOnChartArea: false },
        title: { display: true, text: labelRight, color: colorRight, font: { size: 11 } },
        ticks: { ...baseScales.y.ticks, callback: pctTick, color: colorRight }
      }
    }
  });
}

/* Konut – YoY (sol) + Aylık (sağ) + 13H */
function renderKdKonut(data) {
  const vals = data.map(d => d.konut);
  kdDualAxis(
    'kdKonutChart',
    'YoY %',  KD_COLORS.konut, yoy(vals),
    'Aylık %', '#93c5fd',      mom4w(vals),
    data.map(d => formatDateTR(d.tarih)),
    '13H %', growth13w(vals)
  );
}

/* İhtiyaç – YoY (sol) + Aylık (sağ) + 13H */
function renderKdIhtiyac(data) {
  const isoTarih = str => { const [d,m,y] = str.split('-'); return `${y}-${m}-${d}`; };
  const filtered = data.filter(d => isoTarih(d.tarih) >= '2016-02-26');
  const vals = filtered.map(d => d.ihtiyac);
  kdDualAxis(
    'kdIhtiyacChart',
    'YoY %',  KD_COLORS.ihtiyac, yoy(vals),
    'Aylık %', '#fcd34d',        mom4w(vals),
    filtered.map(d => formatDateTR(d.tarih)),
    '13H %', growth13w(vals)
  );
}

/* Taşıt – YoY (sol) + Aylık (sağ) + 13H */
function renderKdTasit(data) {
  const vals = data.map(d => d.tasit);
  kdDualAxis(
    'kdTasitChart',
    'YoY %',  KD_COLORS.tasit, yoy(vals),
    'Aylık %', '#6ee7b7',      mom4w(vals),
    data.map(d => formatDateTR(d.tarih)),
    '13H %', growth13w(vals)
  );
}

/* KOBİ – YoY (sol) + Aylık (sağ) */
function renderKdKobiYoy(data) {
  const filtered = data.filter(d => d.kobi != null);
  const vals = filtered.map(d => d.kobi);
  kdDualAxis(
    'kdKobiYoyChart',
    'YoY %',  KD_COLORS.kobi, yoy(vals),
    'Aylık %', '#fbcfe8',     mom4w(vals),
    filtered.map(d => formatDateTR(d.tarih))
  );
}

/* KK – Taksitli YoY (sol) vs Taksitsiz YoY (sağ) + KK Toplam YoY */
function renderKdKkYoy(data) {
  kdDualAxis(
    'kdKkYoyChart',
    'Taksitli YoY %',  KD_COLORS.taksitli,  yoy(data.map(d => d.kk_taksitli)),
    'Taksitsiz YoY %', KD_COLORS.taksitsiz, yoy(data.map(d => d.kk_taksitsiz)),
    data.map(d => formatDateTR(d.tarih)),
    'KK Toplam YoY %', yoy(data.map(d => d.kk_toplam))
  );
}

/* 13 Haftalık büyüme */
function renderKd13w(data) {
  // "DD-MM-YYYY" → "YYYY-MM-DD" karşılaştırması için
  const isoTarih = str => { const [d,m,y] = str.split('-'); return `${y}-${m}-${d}`; };
  const d13 = data.filter(d => isoTarih(d.tarih) >= '2015-06-19');
  const labels = d13.map(d => formatDateTR(d.tarih));
  makeChart('kd13wChart', 'line', {
    labels,
    datasets: [
      { label: 'Konut',     data: growth13w(d13.map(d => d.konut)),     borderColor: KD_COLORS.konut,   backgroundColor: 'transparent', tension: 0.3, pointRadius: 0, borderWidth: 2 },
      { label: 'Taşıt',     data: growth13w(d13.map(d => d.tasit)),     borderColor: KD_COLORS.tasit,   backgroundColor: 'transparent', tension: 0.3, pointRadius: 0, borderWidth: 2 },
      { label: 'İhtiyaç',   data: growth13w(d13.map(d => d.ihtiyac)),   borderColor: KD_COLORS.ihtiyac, backgroundColor: 'transparent', tension: 0.3, pointRadius: 0, borderWidth: 2 },
      { label: 'KK Toplam', data: growth13w(d13.map(d => d.kk_toplam)), borderColor: KD_COLORS.kk,      backgroundColor: 'transparent', tension: 0.3, pointRadius: 0, borderWidth: 2 },
      { label: 'KOBİ',      data: growth13w(d13.map(d => d.kobi)),      borderColor: KD_COLORS.kobi,    backgroundColor: 'transparent', tension: 0.3, pointRadius: 0, borderWidth: 2 },
    ]
  }, {
    responsive: true, maintainAspectRatio: false,
    interaction: { mode: 'index', intersect: false },
    plugins: {
      legend: { display: false },
      tooltip: { ...baseTip, callbacks: { label: c => ` ${c.dataset.label}: %${fmtDec(c.parsed.y, 1)}` } },
      annotation: { annotations: { zero: { type: 'line', yMin: 0, yMax: 0, borderColor: 'rgba(0,0,0,0.15)', borderWidth: 1, borderDash: [4,4] } } }
    },
    scales: { ...baseScales, y: { ...baseScales.y, ticks: { ...baseScales.y.ticks, callback: v => '%' + v } } }
  });
}

/* Kredi Detay kontrolleri */
document.getElementById('rangeSelectKrediDetay').addEventListener('change', renderKrediDetay);
document.getElementById('refreshBtnKrediDetay').addEventListener('click', () => { allKrediDetay = []; _pageRendered['kredi-detay'] = false; loadKrediDetay(); });

/* ════════════════════════════════════════
   BÜTÇE DENGESİ SAYFASI
   ════════════════════════════════════════ */

async function loadButce() {
  showLoading(true);
  try {
    const resp = await fetch('/api/butce');
    allButce = await resp.json();
    showLoading(false);
    document.getElementById('page-butce').classList.remove('hidden');
    renderButce();
  } catch (e) {
    console.error('Bütçe yüklenemedi:', e);
    showLoading(false);
  }
}

function getFilteredButce() {
  const w = parseInt(document.getElementById('rangeSelectButce').value);
  return w === 0 ? allButce : allButce.slice(-w);
}

function renderButce() {
  if (!allButce.length) return;
  renderButceUsd();
  renderButceYtd();
  renderButceFaiz();
  renderButceFaizYoy();
  renderButceTable();
  bindToggleLegends();
}

/* Nakit Denge – 12 aylık kümülatif Mn USD (Mayıs 2015'den itibaren) */
function renderButceUsd() {
  const isoT  = str => { const [d,m,y] = str.split('-'); return `${y}-${m}-${d}`; };
  const toUsd = (v, kur) => (v != null && kur) ? v / kur / 1000 : null;

  // 12 aylık rolling sum tüm veri üzerinde hesaplanır (lookback için)
  const allUsd = allButce.map(d => toUsd(d.nakit_denge, d.usdtry));
  const roll12 = allUsd.map((_, i) => {
    if (i < 11) return null;
    let sum = 0;
    for (let j = i - 11; j <= i; j++) {
      if (allUsd[j] == null) return null;
      sum += allUsd[j];
    }
    return parseFloat(sum.toFixed(0));
  });

  // Mayıs 2015'ten itibaren filtrele
  const filtered = allButce
    .map((d, i) => ({ tarih: d.tarih, val: roll12[i] }))
    .filter(d => isoT(d.tarih) >= '2015-05-01');

  const zero = { type: 'line', yMin: 0, yMax: 0, borderColor: 'rgba(0,0,0,0.12)', borderWidth: 1, borderDash: [4, 4] };
  makeChart('butceUsdChart', 'line', {
    labels: filtered.map(d => monthKey(d.tarih)),
    datasets: [{
      label: '12A Kümülatif Nakit Denge',
      data: filtered.map(d => d.val),
      borderColor: '#f59e0b', backgroundColor: 'rgba(245,158,11,0.08)',
      fill: true, tension: 0.3, pointRadius: 0, borderWidth: 2
    }]
  }, {
    responsive: true, maintainAspectRatio: false,
    interaction: { mode: 'index', intersect: false },
    plugins: {
      legend: { display: false },
      tooltip: { ...baseTip, callbacks: { label: c => ` 12A Kümülatif: ${fmtRaw(c.parsed.y)} Mn USD` } },
      annotation: { annotations: { zero } }
    },
    scales: { ...baseScales, y: { ...baseScales.y, ticks: { ...baseScales.y.ticks, callback: v => (v/1000).toFixed(0)+'Mrd' } } }
  });
}

/* YTD Nakit Açık – yıllara göre line chart (2016+) */
function renderButceYtd() {
  const MONTHS = ['Oca','Şub','Mar','Nis','May','Haz','Tem','Ağu','Eyl','Eki','Kas','Ara'];

  // ytdMo = verinin son ayı (0-indexed)
  const lastEntry = allButce[allButce.length - 1];
  const ytdMo     = parseInt(lastEntry.tarih.split('-')[1]) - 1;

  // pivot: { year: { mo: Mn USD } }
  const pivot = {};
  for (const d of allButce) {
    const [, mm, yy] = d.tarih.split('-');
    const yr = parseInt(yy), mo = parseInt(mm) - 1;
    if (!pivot[yr]) pivot[yr] = {};
    pivot[yr][mo] = (d.nakit_denge != null && d.usdtry) ? d.nakit_denge / d.usdtry / 1000 : null;
  }

  // 2016'dan itibaren filtrele
  const years = Object.keys(pivot).map(Number).sort().filter(y => y >= 2016);

  // Her yıl için Oca→ytdMo toplamı
  const ytdVals = years.map(yr => {
    let sum = 0, valid = true;
    for (let mo = 0; mo <= ytdMo; mo++) {
      const v = pivot[yr]?.[mo];
      if (v == null) { valid = false; break; }
      sum += v;
    }
    return valid ? parseFloat(sum.toFixed(0)) : null;
  });

  // Başlık güncelle
  const rangeLabel = `Oca–${MONTHS[ytdMo]}`;
  document.getElementById('butceYtdTitle').textContent = `YTD Nakit Açık (${rangeLabel}) – Mn USD`;

  const zero = { type: 'line', yMin: 0, yMax: 0, borderColor: 'rgba(0,0,0,0.2)', borderWidth: 1, borderDash: [4, 4] };
  makeChart('butceYtdChart', 'line', {
    labels: years.map(String),
    datasets: [{
      label: `YTD ${rangeLabel}`,
      data: ytdVals,
      borderColor: '#ef4444',
      backgroundColor: 'rgba(239,68,68,0.08)',
      fill: true, tension: 0.3, pointRadius: 4, pointBackgroundColor: '#ef4444', borderWidth: 2,
    }]
  }, {
    responsive: true, maintainAspectRatio: false,
    interaction: { mode: 'index', intersect: false },
    plugins: {
      legend: { display: false },
      tooltip: { ...baseTip, callbacks: {
        title: items => `${items[0].label} YTD (${rangeLabel})`,
        label: c => ` ${fmtRaw(c.parsed.y)} Mn USD`
      }},
      annotation: { annotations: { zero } }
    },
    scales: {
      x: { ...baseScales.x, ticks: { ...baseScales.x.ticks, maxRotation: 45 } },
      y: { ...baseScales.y, ticks: { ...baseScales.y.ticks, callback: v => (v/1000).toFixed(0)+'Mrd' } }
    }
  });
}

/* Faiz Harcamaları / Bütçe Geliri — 12 aylık hareketli toplam oranı */
function renderButceFaiz() {
  const isoT = str => { const [d,m,y] = str.split('-'); return `${y}-${m}-${d}`; };

  const roll12 = arr => arr.map((_, i) => {
    if (i < 11) return null;
    let sum = 0;
    for (let j = i - 11; j <= i; j++) {
      if (arr[j] == null) return null;
      sum += arr[j];
    }
    return sum;
  });

  const gelirRoll = roll12(allButce.map(d => d.gelir));
  const faizRoll  = roll12(allButce.map(d => d.faiz));

  const filtered = allButce
    .map((d, i) => {
      const g = gelirRoll[i], f = faizRoll[i];
      const val = (g != null && f != null && g !== 0)
        ? parseFloat((f / g * 100).toFixed(2))
        : null;
      return { tarih: d.tarih, val };
    })
    .filter(d => isoT(d.tarih) >= '2007-01-01');

  const zero = { type: 'line', yMin: 0, yMax: 0, borderColor: 'rgba(0,0,0,0.12)', borderWidth: 1, borderDash: [4,4] };
  makeChart('butceFaizChart', 'line', {
    labels: filtered.map(d => monthKey(d.tarih)),
    datasets: [{
      label: 'Faiz/Gelir',
      data: filtered.map(d => d.val),
      borderColor: '#ef4444',
      backgroundColor: 'rgba(239,68,68,0.08)',
      fill: true, tension: 0.3, pointRadius: 0, borderWidth: 2
    }]
  }, {
    responsive: true, maintainAspectRatio: false,
    interaction: { mode: 'index', intersect: false },
    plugins: {
      legend: { display: false },
      tooltip: { ...baseTip, callbacks: { label: c => ` Faiz/Gelir: %${fmtDec(c.parsed.y, 1)}` } },
      annotation: { annotations: { zero } }
    },
    scales: { ...baseScales, y: { ...baseScales.y, ticks: { ...baseScales.y.ticks, callback: v => '%' + v.toFixed(1) } } }
  });
}

/* Faiz Harcamaları YoY % */
function renderButceFaizYoy() {
  const isoT = str => { const [d,m,y] = str.split('-'); return `${y}-${m}-${d}`; };
  const faiz = allButce.map(d => d.faiz);
  // 12 aylık hareketli toplam
  const roll12 = faiz.map((_, i) => {
    if (i < 11) return null;
    let sum = 0;
    for (let j = i - 11; j <= i; j++) { if (faiz[j] == null) return null; sum += faiz[j]; }
    return sum;
  });
  // Hareketli toplamın YoY % değişimi
  const yoy = roll12.map((v, i) => {
    if (i < 24) return null;
    const prev = roll12[i - 12];
    if (v == null || prev == null || prev === 0) return null;
    return parseFloat(((v - prev) / Math.abs(prev) * 100).toFixed(2));
  });
  const filtered = allButce
    .map((d, i) => ({ tarih: d.tarih, val: yoy[i] }))
    .filter(d => isoT(d.tarih) >= '2008-01-01' && d.val != null);

  const zero = { type: 'line', yMin: 0, yMax: 0, borderColor: 'rgba(0,0,0,0.15)', borderWidth: 1, borderDash: [4,4] };
  const labels = filtered.map(d => monthKey(d.tarih));
  if (charts['butceFaizYoyChart']) charts['butceFaizYoyChart'].destroy();
  const canvas = document.getElementById('butceFaizYoyChart');
  if (!canvas) return;
  charts['butceFaizYoyChart'] = new Chart(canvas, {
    type: 'line',
    data: {
      labels,
      datasets: [{
        label: 'Faiz 12A Toplam YoY %',
        data: filtered.map(d => d.val),
        borderColor: '#f97316',
        backgroundColor: 'rgba(249,115,22,0.12)',
        borderWidth: 2,
        pointRadius: 0,
        fill: true,
        tension: 0.3,
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: 'index', intersect: false },
      plugins: {
        legend: { display: false },
        tooltip: { ...baseTip, callbacks: {
          title: items => items[0].label,
          label: ctx => ' ' + ctx.parsed.y.toFixed(1) + '%'
        }},
        annotation: { annotations: { zero } }
      },
      scales: {
        x: {
          ...baseScales.x,
          ticks: {
            ...baseScales.x.ticks,
            maxTicksLimit: 10,
            callback(val, idx, ticks) {
              if (idx === ticks.length - 1) return this.getLabelForValue(val);
              return this.getLabelForValue(val);
            }
          }
        },
        y: { ...baseScales.y, ticks: { ...baseScales.y.ticks, callback: v => '%' + v.toFixed(1) } }
      }
    },
    plugins: [{
      id: 'forceLastTick',
      afterBuildTicks(chart) {
        const xScale = chart.scales.x;
        if (!xScale) return;
        const lastVal = chart.data.labels.length - 1;
        const ticks = xScale.ticks;
        if (ticks.length && ticks[ticks.length - 1].value !== lastVal) {
          ticks.push({ value: lastVal, label: chart.data.labels[lastVal] });
        }
      }
    }]
  });
  requestAnimationFrame(() => { if (charts['butceFaizYoyChart']) charts['butceFaizYoyChart'].resize(); });
}

/* Özet tablo — yıl × ay pivot (USD bazında) */
function renderButceTable() {
  const field  = document.getElementById('butceTableField').value;
  // "denge" seçeneği nakit_denge alanını kullanır
  const fieldMap = { denge: 'nakit_denge', gelir: 'gelir', gider: 'gider' };
  const dbField  = fieldMap[field];
  const titleMap = { denge: 'Nakit Denge', gelir: 'Gelir', gider: 'Gider' };
  document.getElementById('butceTableTitle').textContent = `${titleMap[field]} (Mn USD)`;

  const MONTHS = ['Ocak','Şubat','Mart','Nisan','Mayıs','Haziran','Temmuz','Ağustos','Eylül','Ekim','Kasım','Aralık'];

  // pivot: { year: { monthIdx: Mn USD değeri } }
  const pivot = {};
  for (const d of allButce) {
    const [, mm, yy] = d.tarih.split('-');
    const yr = parseInt(yy), mo = parseInt(mm) - 1;
    if (!pivot[yr]) pivot[yr] = {};
    const raw = d[dbField];
    pivot[yr][mo] = (raw != null && d.usdtry) ? raw / d.usdtry / 1000 : null;
  }
  const years = Object.keys(pivot).map(Number).sort().filter(y => y >= 2016);

  // YTD kesim ayı: verinin en son ayı (0-indeksli)
  const lastEntry = allButce[allButce.length - 1];
  const ytdMo = parseInt(lastEntry.tarih.split('-')[1]) - 1;  // 0-indexed

  let html = '<table class="butce-table"><thead><tr><th>Yıl</th>';
  for (const m of MONTHS) html += `<th>${m}</th>`;
  html += `<th>Toplam</th><th>YTD (Oca–${MONTHS[ytdMo]})</th></tr></thead><tbody>`;

  for (const yr of years) {
    html += `<tr><td>${yr}</td>`;
    let total = 0, allPresent = true;
    let ytd = 0, ytdPresent = false;
    for (let mo = 0; mo < 12; mo++) {
      const val = pivot[yr][mo];
      if (val == null) {
        html += '<td class="cell-empty">—</td>';
        allPresent = false;
      } else {
        total += val;
        const cls = val >= 0 ? 'cell-pos' : 'cell-neg';
        html += `<td class="${cls}">${Math.round(val).toLocaleString('tr-TR')}</td>`;
      }
      if (mo <= ytdMo && val != null) { ytd += val; ytdPresent = true; }
    }
    const totCls = !allPresent ? 'cell-empty' : total >= 0 ? 'cell-pos' : 'cell-neg';
    html += `<td class="${totCls}">${Math.round(total).toLocaleString('tr-TR')}</td>`;
    if (ytdPresent) {
      const ytdCls = ytd >= 0 ? 'cell-pos' : 'cell-neg';
      html += `<td class="${ytdCls}" style="font-weight:600">${Math.round(ytd).toLocaleString('tr-TR')}</td>`;
    } else {
      html += '<td class="cell-empty">—</td>';
    }
    html += '</tr>';
  }

  html += '</tbody></table>';
  document.getElementById('butceTableWrap').innerHTML = html;
}

/* Bütçe kontrolleri */
document.getElementById('rangeSelectButce').addEventListener('change', renderButce);
document.getElementById('refreshBtnButce').addEventListener('click', () => { allButce = []; _pageRendered['butce'] = false; loadButce(); });
document.getElementById('butceTableField').addEventListener('change', renderButceTable);

/* ════════════════════════════════════════
   DIŞ TİCARET
   ════════════════════════════════════════ */

async function loadDT() {
  try {
    const res = await fetch('/api/dis-ticaret');
    allDT = await res.json();
    document.getElementById('page-dis-ticaret').classList.remove('hidden');
    renderDT();
  } catch(e) { console.error('Dış Ticaret yüklenemedi:', e); }
}

function getFilteredDT() {
  const val = parseInt(document.getElementById('rangeSelectDT').value);
  return val > 0 ? allDT.slice(-val) : allDT;
}

function renderDT() {
  if (!allDT.length) return;
  renderDTRoll12();
  renderDTMA();
  renderDTTable();
  renderDTYoY();
  bindToggleLegends();
}

/* Grafik: İhracat & İthalat YoY % */
function renderDTYoY() {
  const data    = allDT;
  const labels  = data.map(d => monthKey(d.tarih));
  const ihracat = yoy(data.map(d => d.ihracat), 12);
  const ithalat = yoy(data.map(d => d.ithalat), 12);
  if (charts['dtYoyChart']) charts['dtYoyChart'].destroy();
  const canvas = document.getElementById('dtYoyChart');
  if (!canvas) return;
  charts['dtYoyChart'] = new Chart(canvas, {
    type: 'line',
    data: {
      labels,
      datasets: [
        {
          label: 'İhracat YoY %',
          data: ihracat,
          borderColor: '#10b981', backgroundColor: 'rgba(16,185,129,0.07)',
          borderWidth: 2, pointRadius: 0, tension: 0.3, fill: false,
        },
        {
          label: 'İthalat YoY %',
          data: ithalat,
          borderColor: '#ef4444', backgroundColor: 'rgba(239,68,68,0.07)',
          borderWidth: 2, pointRadius: 0, tension: 0.3, fill: false,
        },
      ]
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      interaction: { mode: 'index', intersect: false },
      plugins: {
        legend: { display: false },
        tooltip: {
          mode: 'index', intersect: false,
          callbacks: {
            label: ctx => ` ${ctx.dataset.label}: ${ctx.parsed.y != null ? ctx.parsed.y.toFixed(1) + '%' : '—'}`
          }
        }
      },
      scales: {
        x: { ticks: { color: '#6b7a99', font: { size: 10 } }, grid: { color: 'rgba(255,255,255,0.04)' } },
        y: { ticks: { color: '#6b7a99', font: { size: 10 }, callback: v => v + '%' }, grid: { color: 'rgba(255,255,255,0.06)' } }
      }
    }
  });
  requestAnimationFrame(() => { if (charts['dtYoyChart']) charts['dtYoyChart'].resize(); });
}

/* Grafik 1: 12A Hareketli Toplam */
function renderDTRoll12() {
  const isoT = str => { const [d,m,y] = str.split('-'); return `${y}-${m}-${d}`; };
  const roll12 = arr => arr.map((_, i) => {
    if (i < 11) return null;
    let sum = 0;
    for (let j = i - 11; j <= i; j++) { if (arr[j] == null) return null; sum += arr[j]; }
    return parseFloat(sum.toFixed(0));
  });
  const ih = allDT.map(d => d.ihracat);
  const it = allDT.map(d => d.ithalat);
  const ac = allDT.map(d => d.acik);
  const rIh = roll12(ih), rIt = roll12(it), rAc = roll12(ac);
  const labels = allDT.map(d => monthKey(d.tarih));

  if (charts['dtRoll12Chart']) charts['dtRoll12Chart'].destroy();
  const canvas = document.getElementById('dtRoll12Chart');
  if (!canvas) return;
  charts['dtRoll12Chart'] = new Chart(canvas, {
    type: 'line',
    data: {
      labels,
      datasets: [
        { label: 'İhracat 12A', data: rIh, borderColor: '#22c55e', backgroundColor: 'rgba(34,197,94,0.08)', borderWidth: 2, pointRadius: 0, tension: 0.3, yAxisID: 'y' },
        { label: 'İthalat 12A', data: rIt, borderColor: '#ef4444', backgroundColor: 'rgba(239,68,68,0.08)',  borderWidth: 2, pointRadius: 0, tension: 0.3, yAxisID: 'y' },
        { label: 'Ticaret Açığı 12A', data: rAc, borderColor: '#f97316', backgroundColor: 'rgba(249,115,22,0.10)', borderWidth: 2, pointRadius: 0, tension: 0.3, fill: true, yAxisID: 'y1' },
      ]
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      interaction: { mode: 'index', intersect: false },
      plugins: {
        legend: { display: true, position: 'top', labels: { boxWidth: 12, font: { size: 11 } } },
        tooltip: { ...baseTip, callbacks: { label: ctx => ` ${ctx.dataset.label}: ${ctx.parsed.y != null ? ctx.parsed.y.toLocaleString('tr-TR') + ' Mn' : '—'}` } }
      },
      scales: {
        x: { ...baseScales.x },
        y:  { ...baseScales.y, position: 'left',  title: { display: true, text: 'İhracat / İthalat (Mn USD)', font: { size: 10 }, color: '#6b7a99' } },
        y1: { ...baseScales.y, position: 'right', grid: { drawOnChartArea: false },
              title: { display: true, text: 'Ticaret Açığı (Mn USD)', font: { size: 10 }, color: '#6b7a99' } }
      }
    },
    plugins: [{
      id: 'forceLastTick',
      afterBuildTicks(chart) {
        const xScale = chart.scales.x;
        if (!xScale) return;
        const lastVal = chart.data.labels.length - 1;
        const ticks = xScale.ticks;
        if (ticks.length && ticks[ticks.length - 1].value !== lastVal) {
          ticks.push({ value: lastVal, label: chart.data.labels[lastVal] });
        }
      }
    }]
  });
  requestAnimationFrame(() => { if (charts['dtRoll12Chart']) charts['dtRoll12Chart'].resize(); });
}

/* Grafik 2: Hareketli Ortalamalar */
function renderDTMA() {
  const ma = (arr, n) => arr.map((_, i) => {
    if (i < n - 1) return null;
    let sum = 0;
    for (let j = i - n + 1; j <= i; j++) { if (arr[j] == null) return null; sum += arr[j]; }
    return parseFloat((sum / n).toFixed(1));
  });
  const ac = allDT.map(d => d.acik);
  const labels = allDT.map(d => monthKey(d.tarih));

  if (charts['dtMaChart']) charts['dtMaChart'].destroy();
  const canvas = document.getElementById('dtMaChart');
  if (!canvas) return;
  charts['dtMaChart'] = new Chart(canvas, {
    type: 'line',
    data: {
      labels,
      datasets: [
        { label: '3A Ort.', data: ma(ac, 3),  borderColor: '#60a5fa', backgroundColor: 'transparent', borderWidth: 1.5, pointRadius: 0, tension: 0.3 },
        { label: '6A Ort.', data: ma(ac, 6),  borderColor: '#f59e0b', backgroundColor: 'transparent', borderWidth: 2,   pointRadius: 0, tension: 0.3 },
        { label: '12A Ort.', data: ma(ac, 12), borderColor: '#ef4444', backgroundColor: 'rgba(239,68,68,0.08)', borderWidth: 2.5, pointRadius: 0, tension: 0.3, fill: true },
      ]
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      interaction: { mode: 'index', intersect: false },
      plugins: {
        legend: { display: true, position: 'top', labels: { boxWidth: 12, font: { size: 11 } } },
        tooltip: { ...baseTip, callbacks: { label: ctx => ` ${ctx.dataset.label}: ${ctx.parsed.y != null ? ctx.parsed.y.toLocaleString('tr-TR') + ' Mn' : '—'}` } }
      },
      scales: { ...baseScales, y: { ...baseScales.y, ticks: { ...baseScales.y.ticks, callback: v => v.toLocaleString('tr-TR') } } }
    },
    plugins: [{
      id: 'forceLastTick',
      afterBuildTicks(chart) {
        const xScale = chart.scales.x;
        if (!xScale) return;
        const lastVal = chart.data.labels.length - 1;
        const ticks = xScale.ticks;
        if (ticks.length && ticks[ticks.length - 1].value !== lastVal) {
          ticks.push({ value: lastVal, label: chart.data.labels[lastVal] });
        }
      }
    }]
  });
  requestAnimationFrame(() => { if (charts['dtMaChart']) charts['dtMaChart'].resize(); });
}

/* Tablo: Yıl × Ay pivot (Mn USD) */
function renderDTTable() {
  const MONTHS = ['Ocak','Şubat','Mart','Nisan','Mayıs','Haziran','Temmuz','Ağustos','Eylül','Ekim','Kasım','Aralık'];
  const pivot = {};
  for (const d of allDT) {
    const [, mm, yy] = d.tarih.split('-');
    const yr = parseInt(yy), mo = parseInt(mm) - 1;
    if (!pivot[yr]) pivot[yr] = {};
    pivot[yr][mo] = d.acik != null ? d.acik : null;
  }
  const years = Object.keys(pivot).map(Number).sort();
  const lastEntry = allDT[allDT.length - 1];
  const ytdMo = parseInt(lastEntry.tarih.split('-')[1]) - 1;

  let html = '<table class="butce-table"><thead><tr><th>Yıl</th>';
  for (const m of MONTHS) html += `<th>${m}</th>`;
  html += `<th>Toplam</th><th>YTD (Oca–${MONTHS[ytdMo]})</th></tr></thead><tbody>`;

  for (const yr of years) {
    html += `<tr><td>${yr}</td>`;
    let total = 0, allPresent = true, ytd = 0, ytdPresent = false;
    for (let mo = 0; mo < 12; mo++) {
      const val = pivot[yr][mo];
      if (val == null) {
        html += '<td class="cell-empty">—</td>';
        allPresent = false;
      } else {
        total += val;
        const cls = val >= 0 ? 'cell-pos' : 'cell-neg';
        html += `<td class="${cls}">${Math.round(val).toLocaleString('tr-TR')}</td>`;
      }
      if (mo <= ytdMo && val != null) { ytd += val; ytdPresent = true; }
    }
    const totCls = !allPresent ? 'cell-empty' : total >= 0 ? 'cell-pos' : 'cell-neg';
    html += `<td class="${totCls}">${Math.round(total).toLocaleString('tr-TR')}</td>`;
    html += ytdPresent
      ? `<td class="${ytd >= 0 ? 'cell-pos' : 'cell-neg'}" style="font-weight:600">${Math.round(ytd).toLocaleString('tr-TR')}</td>`
      : '<td class="cell-empty">—</td>';
    html += '</tr>';
  }
  html += '</tbody></table>';
  const dtWrap = document.getElementById('dtTableWrap');
  dtWrap.innerHTML = html;
  dtWrap.scrollTop = dtWrap.scrollHeight;
}

/* Dış Ticaret kontrolleri */
document.getElementById('rangeSelectDT').addEventListener('change', renderDT);
document.getElementById('refreshBtnDT').addEventListener('click', () => { allDT = []; _pageRendered['dis-ticaret'] = false; loadDT(); });

/* ════════════════════════════════════════
   ÖDEMELER DENGESİ
   ════════════════════════════════════════ */

async function loadBoP() {
  try {
    const res = await fetch('/api/odeme-dengesi');
    allBoP = await res.json();
    document.getElementById('page-odeme-dengesi').classList.remove('hidden');
    renderBoP();
  } catch(e) { console.error('Ödemeler Dengesi yüklenemedi:', e); }
}

function getFilteredBoP() {
  const val = parseInt(document.getElementById('rangeSelectBoP').value);
  return val > 0 ? allBoP.slice(-val) : allBoP;
}

function renderBoP() {
  if (!allBoP.length) return;
  renderBoPTable(getFilteredBoP());
  renderBoPCharts();
  renderBoPYTD();
}

/* 12A Hareketli Toplam — tek seri için yardımcı */
function makeBopMA12Chart(canvasId, label, values, labels, color) {
  const ma12 = arr => arr.map((_, i) => {
    if (i < 11) return null;
    let sum = 0;
    for (let j = i - 11; j <= i; j++) { if (arr[j] == null) return null; sum += arr[j]; }
    return Math.round(sum);
  });
  const maVals = ma12(values);

  if (charts[canvasId]) charts[canvasId].destroy();
  const canvas = document.getElementById(canvasId);
  if (!canvas) return;
  charts[canvasId] = new Chart(canvas, {
    type: 'line',
    data: {
      labels,
      datasets: [{
        label,
        data: maVals,
        borderColor: color,
        backgroundColor: color.replace(')', ',0.08)').replace('rgb', 'rgba'),
        borderWidth: 1.8,
        pointRadius: 0,
        tension: 0.3,
        fill: true,
      }]
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      interaction: { mode: 'index', intersect: false },
      plugins: {
        legend: { display: false },
        tooltip: { ...baseTip, callbacks: {
          label: ctx => ` ${ctx.dataset.label}: ${ctx.parsed.y != null ? ctx.parsed.y.toLocaleString('tr-TR') + ' Mn' : '—'}`
        }}
      },
      scales: {
        ...baseScales,
        y: { ...baseScales.y, ticks: { ...baseScales.y.ticks, callback: v => v.toLocaleString('tr-TR') } }
      }
    },
    plugins: [forceLastTickPlugin]
  });
}

function renderBoPCharts() {
  /* Tüm veri üzerinden hesapla (filtered değil) */
  const sorted = [...allBoP].sort((a, b) => {
    const n = t => { const [, m, y] = t.split('-'); return parseInt(y) * 100 + parseInt(m); };
    return n(a.tarih) - n(b.tarih);
  });
  const labels = sorted.map(d => monthKey(d.tarih));

  const cfg = [
    { id: 'bop12DisticChart',     label: 'Dış Ticaret',                          key: 'dis_tic',        color: '#ef4444' },
    { id: 'bop12HizmetChart',     label: 'Hizmet',                               key: 'hizmet',         color: '#10b981' },
    { id: 'bop12BirincilChart',   label: 'Birincil Gelir',                       key: 'birincil',       color: '#f59e0b' },
    { id: 'bop12NetHataChart',    label: 'Net Hata & Noksan',                    key: 'net_hata',       color: '#8b5cf6' },
    { id: 'bop12FinansChart',     label: 'Finans Hesabı',                        key: 'finans',         color: '#3b7ef8' },
    { id: 'bop12RezervChart',     label: 'Rezervler',                            key: 'rezerv',         color: '#06b6d4' },
    { id: 'bop12DigerChart',      label: 'Diğer Yatırımlar',                     key: 'diger_yat',      color: '#f97316' },
    { id: 'bop12PortfoyChart',    label: 'Portföy',                              key: 'portfoy',        color: '#ec4899' },
    { id: 'bop12PfVarlikChart',   label: 'Portföy – Türk Yat. Yurt Dışı',       key: 'portfoy_varlik', color: '#0ea5e9' },
    { id: 'bop12PfYukumChart',    label: 'Portföy – Yabancı Yat. Türkiye',      key: 'portfoy_yukum',  color: '#a855f7' },
    { id: 'bop12DydVarlikChart',  label: 'DYY – Türklerin Yurt Dışı Yat.',      key: 'dyd_varlik',     color: '#14b8a6' },
    { id: 'bop12DydYukumChart',   label: 'DYY – Yabancıların TR\'ye Yat.',      key: 'dyd_yukum',      color: '#22c55e' },
  ];

  for (const { id, label, key, color } of cfg) {
    makeBopMA12Chart(id, label, sorted.map(d => d[key]), labels, color);
  }

  /* Portföy Karşılaştırma: Q119 (12A) ve Q115 (12A) tek grafikte */
  const roll12 = arr => arr.map((_, i) => {
    if (i < 11) return null;
    let sum = 0;
    for (let j = i - 11; j <= i; j++) { if (arr[j] == null) return null; sum += arr[j]; }
    return Math.round(sum);
  });
  const yukum12  = roll12(sorted.map(d => d.portfoy_yukum));
  const varlik12 = roll12(sorted.map(d => d.portfoy_varlik));

  if (charts['bop12NetPfChart']) charts['bop12NetPfChart'].destroy();
  const netPfCanvas = document.getElementById('bop12NetPfChart');
  if (netPfCanvas) {
    charts['bop12NetPfChart'] = new Chart(netPfCanvas, {
      type: 'line',
      data: {
        labels,
        datasets: [
          {
            label: 'Yabancı Yat. Türkiye (Q119)',
            data: yukum12,
            borderColor: '#a855f7',
            backgroundColor: 'rgba(168,85,247,0.08)',
            fill: true,
            borderWidth: 2,
            pointRadius: 0,
            tension: 0.3,
          },
          {
            label: 'Türk Yat. Yurt Dışı (Q115)',
            data: varlik12,
            borderColor: '#0ea5e9',
            backgroundColor: 'rgba(14,165,233,0.08)',
            fill: true,
            borderWidth: 2,
            pointRadius: 0,
            tension: 0.3,
          },
        ]
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        interaction: { mode: 'index', intersect: false },
        plugins: {
          legend: { display: true, position: 'top', labels: { boxWidth: 12, font: { size: 11 } } },
          tooltip: { ...baseTip, callbacks: {
            label: ctx => ` ${ctx.dataset.label}: ${ctx.parsed.y != null ? ctx.parsed.y.toLocaleString('tr-TR') + ' Mn $' : '—'}`
          }},
          annotation: {
            annotations: {
              zero: { type: 'line', yMin: 0, yMax: 0, borderColor: 'rgba(0,0,0,0.2)', borderWidth: 1.5, borderDash: [4, 4] }
            }
          }
        },
        scales: {
          x: { ...baseScales.x },
          y: { ...baseScales.y, ticks: { ...baseScales.y.ticks, callback: v => v.toLocaleString('tr-TR') } }
        }
      },
      plugins: [forceLastTickPlugin]
    });
    requestAnimationFrame(() => { if (charts['bop12NetPfChart']) charts['bop12NetPfChart'].resize(); });
  }
}

function renderBoPTable(data) {
  const val = v => v != null ? Math.abs(v).toLocaleString('tr-TR') : '—';
  const cls = (v, extra = '') => {
    const base = v == null ? 'cell-empty' : v < 0 ? 'cell-neg' : v > 0 ? 'cell-pos' : 'cell-zero';
    return extra ? `${base} ${extra}` : base;
  };

  /* Sıralama: en yeniden eskiye — tarih "DD-MM-YYYY" formatında olduğu için sayısal çevir */
  const dateNum = t => { const [, m, y] = t.split('-'); return parseInt(y) * 100 + parseInt(m); };
  const rows = [...data].sort((a, b) => dateNum(b.tarih) - dateNum(a.tarih));

  let html = `
  <table class="bop-table">
    <thead>
      <tr class="bop-group-row">
        <th rowspan="2" class="bop-date-hd bop-g-cari" style="background:var(--surface2);color:var(--text-muted)">Tarih</th>
        <th colspan="5" class="bop-g-cari">1. Cari İşlemler Hesabı</th>
        <th colspan="1" class="bop-g-sermaye">2. Sermaye Hesabı</th>
        <th colspan="1" class="bop-g-nethata">Net Hata &amp; Noksan</th>
        <th colspan="5" class="bop-g-finans">4. Finans Hesabı</th>
      </tr>
      <tr class="bop-sub-row">
        <th class="bop-c-cari"  style="font-weight:800">Toplam</th>
        <th class="bop-c-cari">Dış Ticaret</th>
        <th class="bop-c-cari">Hizmet</th>
        <th class="bop-c-cari">Birincil Gelir</th>
        <th class="bop-c-cari">İkincil Gelir</th>
        <th class="bop-c-sermaye" style="font-weight:800">Toplam</th>
        <th class="bop-c-nethata" style="font-weight:800">Toplam</th>
        <th class="bop-c-finans"  style="font-weight:800">Toplam</th>
        <th class="bop-c-finans">Diğer Yat.</th>
        <th class="bop-c-finans">Portföy</th>
        <th class="bop-c-finans">Doğrudan Yat.</th>
        <th class="bop-c-finans">Rezervler</th>
      </tr>
    </thead>
    <tbody>`;

  for (const d of rows) {
    const [, m, y] = d.tarih.split('-');
    const sign = v => v == null ? '' : v < 0 ? '−' : v > 0 ? '+' : '';
    const cell = (v, main = false) =>
      `<td class="${cls(v, main ? 'bop-main' : '')}">${v != null ? sign(v) + val(v) : '—'}</td>`;

    html += `<tr>
      <td class="bop-date">${y}-${m}</td>
      ${cell(d.cari,    true)}
      ${cell(d.dis_tic)}
      ${cell(d.hizmet)}
      ${cell(d.birincil)}
      ${cell(d.ikincil)}
      ${cell(d.sermaye,  true)}
      ${cell(d.net_hata, true)}
      ${cell(d.finans,   true)}
      ${cell(d.diger_yat)}
      ${cell(d.portfoy)}
      ${cell(d.dogrudan)}
      ${cell(d.rezerv)}
    </tr>`;
  }

  html += '</tbody></table>';
  const wrap = document.getElementById('bopTableWrap');
  wrap.innerHTML = html;
  wrap.scrollTop = 0;
}

/* ── Yıllara Göre Cari Açıklar ── */
function renderBoPYTD() {
  const MONTHS   = ['Ocak','Şubat','Mart','Nisan','Mayıs','Haziran','Temmuz','Ağustos','Eylül','Ekim','Kasım','Aralık'];
  const MO_SHORT = ['Oca','Şub','Mar','Nis','May','Haz','Tem','Ağu','Eyl','Eki','Kas','Ara'];

  /* Kronolojik sıra */
  const sorted = [...allBoP].sort((a, b) => {
    const n = t => { const [, m, y] = t.split('-'); return parseInt(y)*100 + parseInt(m); };
    return n(a.tarih) - n(b.tarih);
  });

  /* Yıl × ay pivot — ham aylık Cari İşlemler */
  const byYear = {};
  for (const d of sorted) {
    const [, mm, yy] = d.tarih.split('-');
    const yr = parseInt(yy), mo = parseInt(mm) - 1;
    if (!byYear[yr]) byYear[yr] = {};
    byYear[yr][mo] = d.cari;
  }

  const years = Object.keys(byYear).map(Number).sort();

  /* YTD kümülatif toplam: ytdData[yr][mo] = Ocak..mo arası toplam */
  const ytdData = {};
  for (const yr of years) {
    ytdData[yr] = {};
    let cum = 0;
    for (let mo = 0; mo < 12; mo++) {
      const v = byYear[yr][mo];
      if (v != null) { cum += v; ytdData[yr][mo] = Math.round(cum); }
      else            { ytdData[yr][mo] = null; }
    }
  }

  /* Son veri noktasını bul (YTD kesim ayı) */
  const lastEntry = sorted[sorted.length - 1];
  const [, lm] = lastEntry.tarih.split('-');
  const ytdMo = parseInt(lm) - 1; // 0-indexed

  /* Renk paleti — her yıl için sabit renk */
  const PALETTE = [
    '#94a3b8','#64748b','#475569','#a8b0c0',
    '#0ea5e9','#06b6d4','#14b8a6','#22c55e',
    '#84cc16','#eab308','#f97316','#ef4444',
    '#a855f7','#ec4899','#f59e0b','#3b7ef8',
    '#10b981','#1e40af','#7c3aed',
  ];
  const recentYears = years.filter(y => y >= 2010);
  const currentYear = new Date().getFullYear();

  /* ── Grafik ── */
  const datasets = recentYears.map((yr, i) => {
    const color  = PALETTE[i % PALETTE.length];
    const isCurr = yr === currentYear;
    return {
      label:           String(yr),
      data:            Array.from({ length: 12 }, (_, mo) => ytdData[yr][mo] ?? null),
      borderColor:     color,
      backgroundColor: 'transparent',
      borderWidth:     isCurr ? 3 : 1.5,
      pointRadius:     0,
      tension:         0.3,
      borderDash:      isCurr ? [] : [],
    };
  });

  if (charts['bopYtdChart']) charts['bopYtdChart'].destroy();
  const canvas = document.getElementById('bopYtdChart');
  if (!canvas) return;
  charts['bopYtdChart'] = new Chart(canvas, {
    type: 'line',
    data: { labels: MO_SHORT, datasets },
    options: {
      responsive: true, maintainAspectRatio: false,
      interaction: { mode: 'index', intersect: false },
      plugins: {
        legend: {
          display: true,
          position: 'right',
          labels: { boxWidth: 12, font: { size: 11 }, color: '#6b7a99' }
        },
        tooltip: {
          ...baseTip,
          callbacks: {
            title: items => `${items[0].label} – YTD`,
            label: ctx => ` ${ctx.dataset.label}: ${ctx.parsed.y != null ? ctx.parsed.y.toLocaleString('tr-TR') + ' Mn $' : '—'}`
          }
        },
        annotation: {
          annotations: {
            zero: { type: 'line', yMin: 0, yMax: 0, borderColor: 'rgba(0,0,0,0.2)', borderWidth: 1.5, borderDash: [4, 4] }
          }
        }
      },
      scales: {
        x: { ...baseScales.x, ticks: { ...baseScales.x.ticks, maxTicksLimit: 12 } },
        y: { ...baseScales.y, ticks: { ...baseScales.y.ticks, callback: v => v.toLocaleString('tr-TR') } }
      }
    }
  });
  requestAnimationFrame(() => { if (charts['bopYtdChart']) charts['bopYtdChart'].resize(); });

  /* ── Tablo ── */
  const descYears = [...recentYears].reverse();
  let html = '<table class="butce-table"><thead><tr><th>Yıl</th>';
  for (const m of MONTHS) html += `<th>${m}</th>`;
  html += `<th>Yıllık</th><th>YTD (Oca–${MONTHS[ytdMo]})</th></tr></thead><tbody>`;

  for (const yr of descYears) {
    html += `<tr><td>${yr}</td>`;
    let total = 0, allPresent = true;
    for (let mo = 0; mo < 12; mo++) {
      const v = byYear[yr][mo];
      if (v == null) { html += '<td class="cell-empty">—</td>'; allPresent = false; }
      else {
        total += v;
        html += `<td class="${v >= 0 ? 'cell-pos' : 'cell-neg'}">${Math.round(v).toLocaleString('tr-TR')}</td>`;
      }
    }
    /* Yıllık toplam */
    const totCls = !allPresent ? 'cell-empty' : total >= 0 ? 'cell-pos' : 'cell-neg';
    html += `<td class="${totCls}" style="font-weight:700">${allPresent ? Math.round(total).toLocaleString('tr-TR') : '—'}</td>`;
    /* YTD kümülatif */
    const ytdVal = ytdData[yr][ytdMo];
    if (ytdVal != null) {
      html += `<td class="${ytdVal >= 0 ? 'cell-pos' : 'cell-neg'}" style="font-weight:700">${ytdVal.toLocaleString('tr-TR')}</td>`;
    } else {
      html += '<td class="cell-empty">—</td>';
    }
    html += '</tr>';
  }
  html += '</tbody></table>';
  const wrap = document.getElementById('bopYtdTableWrap');
  if (wrap) { wrap.innerHTML = html; wrap.scrollTop = 0; }
}

/* BoP kontrolleri */
document.getElementById('rangeSelectBoP').addEventListener('change', () => {
  if (!allBoP.length) return;
  renderBoPTable(getFilteredBoP());
});
document.getElementById('refreshBtnBoP').addEventListener('click', () => { allBoP = []; _pageRendered['odeme-dengesi'] = false; loadBoP(); });

/* ════════════════════════════════════════
   TURİZM
   ════════════════════════════════════════ */

async function loadTurizm() {
  try {
    const res = await fetch('/api/turizm');
    allTurizm = await res.json();
    document.getElementById('page-turizm').classList.remove('hidden');
    renderTurizm();
  } catch(e) { console.error('Turizm yüklenemedi:', e); }
}

function getFilteredTurizm() {
  const val = parseInt(document.getElementById('rangeSelectTurizm').value);
  return val > 0 ? allTurizm.slice(-val) : allTurizm;
}

function renderTurizm() {
  if (!allTurizm.length) return;
  const data = getFilteredTurizm();
  renderTurizmKisiBasi(data);
  renderTurizmZiyaretci(data);
  renderTurizmGelirTable();
  bindToggleLegends();
}

/* Grafik 1: Kişi Başı Harcama (USD) */
function renderTurizmKisiBasi(data) {
  const labels = data.map(d => monthKey(d.tarih));
  const vals   = data.map(d => d.kisi_basi);

  if (charts['turizmKisiBasiChart']) charts['turizmKisiBasiChart'].destroy();
  const canvas = document.getElementById('turizmKisiBasiChart');
  if (!canvas) return;
  charts['turizmKisiBasiChart'] = new Chart(canvas, {
    type: 'line',
    data: {
      labels,
      datasets: [{
        label: 'Kişi Başı Harcama',
        data: vals,
        borderColor: '#3b7ef8',
        backgroundColor: 'rgba(59,126,248,0.08)',
        borderWidth: 2,
        pointRadius: 0,
        tension: 0.3,
        fill: true,
      }]
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      interaction: { mode: 'index', intersect: false },
      plugins: {
        legend: { display: false },
        tooltip: { ...baseTip, callbacks: { label: ctx => ` ${ctx.dataset.label}: ${ctx.parsed.y != null ? ctx.parsed.y.toLocaleString('tr-TR') + ' USD' : '—'}` } }
      },
      scales: { ...baseScales, y: { ...baseScales.y, ticks: { ...baseScales.y.ticks, callback: v => v.toLocaleString('tr-TR') + ' $' } } }
    },
    plugins: [forceLastTickPlugin]
  });
}

/* Grafik 2: Toplam Ziyaretçi – 12 Aylık Toplam */
function renderTurizmZiyaretci(data) {
  /* Rolling 12 tüm veri üzerinde hesaplanır; sonra filtreli aralığa kesilir */
  const allRaw = allTurizm.map(d => d.ziyaretci);
  const roll12 = allRaw.map((_, i) => {
    if (i < 11) return null;
    const sum = allRaw.slice(i - 11, i + 1).reduce((a, v) => a + (v ?? 0), 0);
    return +(sum / 1e6).toFixed(2);
  });
  const offset = allTurizm.length - data.length;
  const vals   = roll12.slice(offset);
  const labels = data.map(d => monthKey(d.tarih));

  if (charts['turizmZiyaretciChart']) charts['turizmZiyaretciChart'].destroy();
  const canvas = document.getElementById('turizmZiyaretciChart');
  if (!canvas) return;
  charts['turizmZiyaretciChart'] = new Chart(canvas, {
    type: 'line',
    data: {
      labels,
      datasets: [{
        label: '12A Toplam Ziyaretçi',
        data: vals,
        borderColor: '#10b981',
        backgroundColor: 'rgba(16,185,129,0.08)',
        borderWidth: 2,
        pointRadius: 0,
        tension: 0.3,
        fill: true,
      }]
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      interaction: { mode: 'index', intersect: false },
      plugins: {
        legend: { display: false },
        tooltip: { ...baseTip, callbacks: { label: ctx => ` ${ctx.dataset.label}: ${ctx.parsed.y != null ? ctx.parsed.y.toLocaleString('tr-TR') + ' Mn Kişi' : '—'}` } }
      },
      scales: { ...baseScales, y: { ...baseScales.y, ticks: { ...baseScales.y.ticks, callback: v => v.toLocaleString('tr-TR') + ' Mn' } } }
    },
    plugins: [forceLastTickPlugin]
  });
}

/* Tablo: Yıl × Ay pivot (Mn USD) — tüm veri gösterilir */
function renderTurizmGelirTable() {
  const MONTHS = ['Ocak','Şubat','Mart','Nisan','Mayıs','Haziran','Temmuz','Ağustos','Eylül','Ekim','Kasım','Aralık'];

  const pivot = {};
  for (const d of allTurizm) {
    const [, mm, yy] = d.tarih.split('-');
    const yr = parseInt(yy), mo = parseInt(mm) - 1;
    if (!pivot[yr]) pivot[yr] = {};
    pivot[yr][mo] = d.gelir != null ? d.gelir : null;
  }

  const years   = Object.keys(pivot).map(Number).sort();
  const lastEntry = allTurizm[allTurizm.length - 1];
  const ytdMo   = parseInt(lastEntry.tarih.split('-')[1]) - 1;

  let html = '<table class="butce-table"><thead><tr><th>Yıl</th>';
  for (const m of MONTHS) html += `<th>${m}</th>`;
  html += `<th>Toplam</th><th>YTD (Oca–${MONTHS[ytdMo]})</th></tr></thead><tbody>`;

  for (const yr of years) {
    html += `<tr><td>${yr}</td>`;
    let total = 0, allPresent = true, ytd = 0, ytdPresent = false;
    for (let mo = 0; mo < 12; mo++) {
      const val = pivot[yr][mo];
      if (val == null) {
        html += '<td class="cell-empty">—</td>';
        allPresent = false;
      } else {
        total += val;
        html += `<td class="cell-pos">${Math.round(val).toLocaleString('tr-TR')}</td>`;
      }
      if (mo <= ytdMo && val != null) { ytd += val; ytdPresent = true; }
    }
    const totCls = !allPresent ? 'cell-empty' : 'cell-pos';
    html += `<td class="${totCls}">${Math.round(total).toLocaleString('tr-TR')}</td>`;
    html += ytdPresent
      ? `<td class="cell-pos" style="font-weight:600">${Math.round(ytd).toLocaleString('tr-TR')}</td>`
      : '<td class="cell-empty">—</td>';
    html += '</tr>';
  }

  html += '</tbody></table>';
  const wrap = document.getElementById('turizmTableWrap');
  wrap.innerHTML = html;
  wrap.scrollTop = wrap.scrollHeight;
}

/* Turizm kontrolleri */
document.getElementById('rangeSelectTurizm').addEventListener('change', () => {
  if (!allTurizm.length) return;
  const data = getFilteredTurizm();
  renderTurizmKisiBasi(data);
  renderTurizmZiyaretci(data);
});
document.getElementById('refreshBtnTurizm').addEventListener('click', () => { allTurizm = []; _pageRendered['turizm'] = false; loadTurizm(); });

/* ════════════════════════════════════════
   KONUT
   ════════════════════════════════════════ */

async function loadKonut() {
  try {
    const res = await fetch('/api/konut');
    allKonut = await res.json();
    document.getElementById('page-konut').classList.remove('hidden');
    renderKonut();
  } catch(e) { console.error('Konut yüklenemedi:', e); }
}

function getFilteredKonut() {
  const w = parseInt(document.getElementById('rangeSelectKonut').value);
  return w > 0 ? allKonut.slice(-w) : allKonut;
}

function renderKonut() {
  if (!allKonut.length) return;
  renderKonutKfeYoy(allKonut);
  renderKonutYkfeYoy(allKonut);
  renderKonutYkkeYoy(allKonut);
  renderKonutCarpan(allKonut);
  renderKonutSatis(allKonut);
  renderKonutSatis12a(allKonut);
  renderKonutSatisRasyo(allKonut);
  bindToggleLegends();
}

/* YoY yardımcı: (val_t / val_{t-12} - 1) × 100 — tüm diziye uygular */
function konutYoY(arr) {
  return arr.map((v, i) => {
    if (i < 12) return null;
    const prev = arr[i - 12];
    if (v == null || prev == null || prev === 0) return null;
    return parseFloat(((v / prev - 1) * 100).toFixed(2));
  });
}

/* Ortak küçük grafik seçenekleri */
function konutChartOpts(tooltip) {
  return {
    responsive: true, maintainAspectRatio: false,
    interaction: { mode: 'index', intersect: false },
    plugins: {
      legend: { display: true, position: 'top', labels: { boxWidth: 10, font: { size: 11 } } },
      tooltip: { ...baseTip, callbacks: { label: ctx =>
        ` ${ctx.dataset.label}: ${ctx.parsed.y != null ? (ctx.parsed.y >= 0 ? '+' : '') + ctx.parsed.y.toFixed(1) + (tooltip || '%') : '—'}`
      }},
      annotation: { annotations: { zero: {
        type: 'line', yMin: 0, yMax: 0,
        borderColor: 'rgba(0,0,0,0.15)', borderWidth: 1, borderDash: [4, 4]
      }}}
    },
    scales: {
      x: { ...baseScales.x },
      y: { ...baseScales.y, ticks: { ...baseScales.y.ticks,
        callback: v => (tooltip ? v.toFixed(2) : (v >= 0 ? '+' : '') + v.toFixed(0) + '%')
      }}
    }
  };
}

/* 1 — KFE TR + KFE İstanbul YoY */
function renderKonutKfeYoy(data) {
  const labels  = data.map(d => monthKey(d.tarih));
  const yoyTr   = konutYoY(data.map(d => d.kfe_tr));
  const yoyIst  = konutYoY(data.map(d => d.kfe_ist));
  makeChart('konutKfeYoyChart', 'line', {
    labels,
    datasets: [
      { label: 'KFE Türkiye',  data: yoyTr,  borderColor: '#3b7ef8', backgroundColor: 'rgba(59,126,248,0.07)', fill: true,  tension: 0.3, pointRadius: 0, borderWidth: 2 },
      { label: 'KFE İstanbul', data: yoyIst, borderColor: '#f59e0b', backgroundColor: 'rgba(245,158,11,0.07)',  fill: false, tension: 0.3, pointRadius: 0, borderWidth: 2 },
    ]
  }, konutChartOpts(), [forceLastTickPlugin]);
}

/* 2 — YKFE + YOKFE YoY */
function renderKonutYkfeYoy(data) {
  const labels = data.map(d => monthKey(d.tarih));
  const yoyY   = konutYoY(data.map(d => d.ykfe));
  const yoyYo  = konutYoY(data.map(d => d.yokfe));
  makeChart('konutYkfeYoyChart', 'line', {
    labels,
    datasets: [
      { label: 'Yeni Konut FE',         data: yoyY,  borderColor: '#10b981', backgroundColor: 'rgba(16,185,129,0.07)', fill: true,  tension: 0.3, pointRadius: 0, borderWidth: 2 },
      { label: 'Yeni Olmayan Konut FE', data: yoyYo, borderColor: '#a855f7', backgroundColor: 'rgba(168,85,247,0.07)', fill: false, tension: 0.3, pointRadius: 0, borderWidth: 2 },
    ]
  }, konutChartOpts(), [forceLastTickPlugin]);
}

/* 3 — YKKE TR + YKKE İstanbul YoY */
function renderKonutYkkeYoy(data) {
  const filtered = data.filter(d => d.ykke_tr != null || d.ykke_ist != null);
  const labels  = filtered.map(d => monthKey(d.tarih));
  const yoyTr   = konutYoY(filtered.map(d => d.ykke_tr));
  const yoyIst  = konutYoY(filtered.map(d => d.ykke_ist));
  makeChart('konutYkkeYoyChart', 'line', {
    labels,
    datasets: [
      { label: 'Kira Endeksi TR',       data: yoyTr,  borderColor: '#ef4444', backgroundColor: 'rgba(239,68,68,0.07)', fill: true,  tension: 0.3, pointRadius: 0, borderWidth: 2 },
      { label: 'Kira Endeksi İstanbul', data: yoyIst, borderColor: '#f97316', backgroundColor: 'rgba(249,115,22,0.07)', fill: false, tension: 0.3, pointRadius: 0, borderWidth: 2 },
    ]
  }, konutChartOpts(), [forceLastTickPlugin]);
}

/* 4 — Konut Çarpanı: KFE İstanbul / YKKE İstanbul */
function renderKonutCarpan(data) {
  const filtered = data.filter(d => d.kfe_ist != null && d.ykke_ist != null);
  const labels   = filtered.map(d => monthKey(d.tarih));
  const carpan   = filtered.map(d => parseFloat((d.kfe_ist / d.ykke_ist).toFixed(4)));
  makeChart('konutCarpanChart', 'line', {
    labels,
    datasets: [{
      label: 'KFE İst / YKKE İst',
      data: carpan,
      borderColor: '#6366f1',
      backgroundColor: 'rgba(99,102,241,0.10)',
      fill: true,
      tension: 0.3,
      pointRadius: 0,
      borderWidth: 2,
    }]
  }, {
    responsive: true, maintainAspectRatio: false,
    interaction: { mode: 'index', intersect: false },
    plugins: {
      legend: { display: false },
      tooltip: { ...baseTip, callbacks: {
        label: ctx => ` Çarpan: ${ctx.parsed.y != null ? ctx.parsed.y.toFixed(3) : '—'}`
      }}
    },
    scales: {
      x: { ...baseScales.x },
      y: { ...baseScales.y, ticks: { ...baseScales.y.ticks, callback: v => v.toFixed(2) } }
    }
  }, [forceLastTickPlugin]);
}

/* 5 — Konut Satışları: Toplam (sol eksen) + İpotekli (sağ eksen) */
function renderKonutSatis(data) {
  const filtered = data.filter(d => d.satis_toplam != null || d.satis_ipotekli != null);
  if (!filtered.length) return;
  const labels   = filtered.map(d => monthKey(d.tarih));
  const toplam   = filtered.map(d => d.satis_toplam);
  const ipotekli = filtered.map(d => d.satis_ipotekli);
  const fmtK = v => v != null ? Math.round(v).toLocaleString('tr-TR') : '—';
  makeChart('konutSatisChart', 'line', {
    labels,
    datasets: [
      { label: 'Toplam Satış',   data: toplam,   borderColor: '#3b7ef8', backgroundColor: 'rgba(59,126,248,0.08)', fill: true,  tension: 0.3, pointRadius: 0, borderWidth: 2, yAxisID: 'y'  },
      { label: 'İpotekli Satış', data: ipotekli, borderColor: '#f97316', backgroundColor: 'rgba(249,115,22,0.08)',  fill: false, tension: 0.3, pointRadius: 0, borderWidth: 2, yAxisID: 'y1' },
    ]
  }, {
    responsive: true, maintainAspectRatio: false,
    interaction: { mode: 'index', intersect: false },
    plugins: {
      legend: { display: true, position: 'top', labels: { boxWidth: 10, font: { size: 11 } } },
      tooltip: { ...baseTip, callbacks: {
        label: ctx => ` ${ctx.dataset.label}: ${fmtK(ctx.parsed.y)}`
      }}
    },
    scales: {
      x: { ...baseScales.x },
      y:  { type: 'linear', position: 'left',  grid: { color: 'rgba(0,0,0,0.04)' }, ticks: { ...baseScales.y.ticks, callback: v => (v/1000).toFixed(0) + 'K' }, title: { display: true, text: 'Toplam (adet)', font: { size: 10 } } },
      y1: { type: 'linear', position: 'right', grid: { drawOnChartArea: false },     ticks: { ...baseScales.y.ticks, callback: v => (v/1000).toFixed(0) + 'K' }, title: { display: true, text: 'İpotekli (adet)', font: { size: 10 } } },
    }
  }, [forceLastTickPlugin]);
}

/* 6 — Konut Satışları: 12 Aylık Hareketli Toplam */
function renderKonutSatis12a(data) {
  const filtered = data.filter(d => d.satis_toplam != null || d.satis_ipotekli != null);
  if (!filtered.length) return;
  const labels = filtered.map(d => monthKey(d.tarih));
  const roll12 = arr => arr.map((_, i) => {
    if (i < 11) return null;
    let sum = 0;
    for (let j = i - 11; j <= i; j++) { if (arr[j] == null) return null; sum += arr[j]; }
    return Math.round(sum);
  });
  const toplam12   = roll12(filtered.map(d => d.satis_toplam));
  const ipotekli12 = roll12(filtered.map(d => d.satis_ipotekli));
  const fmtK = v => v != null ? Math.round(v).toLocaleString('tr-TR') : '—';
  makeChart('konutSatis12aChart', 'line', {
    labels,
    datasets: [
      { label: 'Toplam 12A',   data: toplam12,   borderColor: '#3b7ef8', backgroundColor: 'rgba(59,126,248,0.08)', fill: true,  tension: 0.3, pointRadius: 0, borderWidth: 2, yAxisID: 'y'  },
      { label: 'İpotekli 12A', data: ipotekli12, borderColor: '#f97316', backgroundColor: 'rgba(249,115,22,0.08)',  fill: false, tension: 0.3, pointRadius: 0, borderWidth: 2, yAxisID: 'y1' },
    ]
  }, {
    responsive: true, maintainAspectRatio: false,
    interaction: { mode: 'index', intersect: false },
    plugins: {
      legend: { display: true, position: 'top', labels: { boxWidth: 10, font: { size: 11 } } },
      tooltip: { ...baseTip, callbacks: {
        label: ctx => ` ${ctx.dataset.label}: ${fmtK(ctx.parsed.y)}`
      }}
    },
    scales: {
      x: { ...baseScales.x },
      y:  { type: 'linear', position: 'left',  grid: { color: 'rgba(0,0,0,0.04)' }, ticks: { ...baseScales.y.ticks, callback: v => (v/1000).toFixed(0) + 'K' }, title: { display: true, text: 'Toplam (adet)', font: { size: 10 } } },
      y1: { type: 'linear', position: 'right', grid: { drawOnChartArea: false },     ticks: { ...baseScales.y.ticks, callback: v => (v/1000).toFixed(0) + 'K' }, title: { display: true, text: 'İpotekli (adet)', font: { size: 10 } } },
    }
  }, [forceLastTickPlugin]);
}

/* 7 — Toplam Satış / İpotekli Satış Rasyosu */
function renderKonutSatisRasyo(data) {
  const filtered = data.filter(d => d.satis_toplam != null && d.satis_ipotekli != null && d.satis_ipotekli > 0);
  if (!filtered.length) return;
  const labels = filtered.map(d => monthKey(d.tarih));
  const rasyo  = filtered.map(d => parseFloat((d.satis_toplam / d.satis_ipotekli).toFixed(3)));
  makeChart('konutSatisRasyoChart', 'line', {
    labels,
    datasets: [{
      label: 'Toplam / İpotekli',
      data: rasyo,
      borderColor: '#8b5cf6',
      backgroundColor: 'rgba(139,92,246,0.08)',
      fill: true,
      tension: 0.3,
      pointRadius: 0,
      borderWidth: 2,
    }]
  }, {
    responsive: true, maintainAspectRatio: false,
    interaction: { mode: 'index', intersect: false },
    plugins: {
      legend: { display: false },
      tooltip: { ...baseTip, callbacks: {
        label: ctx => ` Rasyo: ${ctx.parsed.y != null ? ctx.parsed.y.toFixed(2) : '—'}x`
      }}
    },
    scales: {
      x: { ...baseScales.x },
      y: { ...baseScales.y, ticks: { ...baseScales.y.ticks, callback: v => v.toFixed(1) + 'x' } }
    }
  }, [forceLastTickPlugin]);
}

document.getElementById('refreshBtnKonut').addEventListener('click', () => { allKonut = []; _pageRendered['konut'] = false; loadKonut(); });

/* ════════════════════════════════════════
   ENFLASYON
   ════════════════════════════════════════ */

/* TÜİK 2026 resmi TÜFE sepet ağırlıkları (COICOP 2018 · 2025=100) */
const TUFE_WEIGHTS = {
  gida:    0.244444,
  alkol:   0.027549,
  giyim:   0.079038,
  konut:   0.114020,
  mobilya: 0.079201,
  saglik:  0.027923,
  ulasim:  0.166169,
  bilgi:   0.031035,
  eglence: 0.043382,
  egitim:  0.020215,
  lokanta: 0.111349,
  sigorta: 0.010740,
  kisisel: 0.044935,
};

const TUFE_LABELS = {
  gida:    '01 · Gıda ve Alkolsüz İçecekler',
  alkol:   '02 · Alkollü İçecekler, Tütün',
  giyim:   '03 · Giyim ve Ayakkabı',
  konut:   '04 · Konut, Su, Elektrik, Gaz',
  mobilya: '05 · Mobilya ve Ev Ekipmanları',
  saglik:  '06 · Sağlık',
  ulasim:  '07 · Ulaştırma',
  bilgi:   '08 · Bilgi ve İletişim',
  eglence: '09 · Eğlence, Dinlence, Kültür',
  egitim:  '10 · Eğitim Hizmetleri',
  lokanta: '11 · Lokantalar ve Konaklama',
  sigorta: '12 · Sigorta ve Finansal Hizmetler',
  kisisel: '13 · Kişisel Bakım ve Çeşitli',
};

const TUFE_CATS = Object.keys(TUFE_WEIGHTS);

async function loadEnflasyon() {
  try {
    const res = await fetch('/api/enflasyon');
    allEnflasyon = await res.json();
    document.getElementById('page-enflasyon').classList.remove('hidden');
    renderEnflasyon();
  } catch(e) { console.error('Enflasyon yüklenemedi:', e); }
}

function renderEnflasyon() {
  if (allEnflasyon.length < 2) return;
  populateEnflasyonAySelect();
  renderEnflasyonTable();
  renderEnflasyonHistTable();
}

function populateEnflasyonAySelect() {
  const sel = document.getElementById('enflasyonAySelect');
  const current = sel.value;
  sel.innerHTML = '';
  /* Son 36 ay — en güncel en üstte */
  const recent = allEnflasyon.slice(-36).reverse();
  for (const d of recent) {
    const opt = document.createElement('option');
    opt.value = d.tarih;
    opt.textContent = monthKey(d.tarih);
    sel.appendChild(opt);
  }
  /* İlk yüklemede en güncel ayı seç */
  if (current && [...sel.options].some(o => o.value === current)) {
    sel.value = current;
  } else {
    sel.selectedIndex = 0;
  }
}

function renderEnflasyonTable() {
  const tarih  = document.getElementById('enflasyonAySelect').value;
  const idx    = allEnflasyon.findIndex(d => d.tarih === tarih);
  if (idx < 1) return;

  const curr = allEnflasyon[idx];
  const prev = allEnflasyon[idx - 1];

  /* Genel MoM */
  const genelMom = prev.genel ? (curr.genel / prev.genel - 1) * 100 : null;

  /* Katkılar (pp) = ağırlık × kategori_mom */
  const rows = TUFE_CATS.map(cat => {
    const mom   = (curr[cat] && prev[cat]) ? (curr[cat] / prev[cat] - 1) * 100 : null;
    const katki = (mom != null) ? TUFE_WEIGHTS[cat] * mom : null;
    return { cat, mom, katki };
  });

  /* Katkı payı (%) = katki / genelMom * 100 */
  const totalKatki = rows.reduce((s, r) => s + (r.katki ?? 0), 0);
  const maxAbsKatki = Math.max(...rows.map(r => Math.abs(r.katki ?? 0)));

  /* Başlık */
  document.getElementById('enflasyonTableTitle').textContent =
    `TÜFE Katkı Analizi — ${monthKey(tarih)}`;

  /* Tablo */
  let html = `
  <table class="enf-table">
    <thead>
      <tr>
        <th style="width:36px">#</th>
        <th>Ana Kalem</th>
        <th style="text-align:right">Ağırlık</th>
        <th class="enf-th-mom">Aylık Değ. (%)</th>
        <th class="enf-th-katki">Katkı (pp)</th>
        <th class="enf-th-katki" style="min-width:160px">Katkı Payı</th>
      </tr>
    </thead>
    <tbody>`;

  /* Genel satırı */
  const genelCls = genelMom == null ? '' : genelMom >= 0 ? 'cell-neg' : 'cell-pos';
  html += `
      <tr class="enf-genel">
        <td>—</td>
        <td>Genel TÜFE</td>
        <td style="color:var(--text-muted)">%100.00</td>
        <td class="${genelCls}" style="font-size:14px">${genelMom != null ? (genelMom >= 0 ? '+' : '') + genelMom.toFixed(2) + '%' : '—'}</td>
        <td class="${genelCls}">${totalKatki ? (totalKatki >= 0 ? '+' : '') + totalKatki.toFixed(2) + 'pp' : '—'}</td>
        <td></td>
      </tr>`;

  /* Kategori satırları — katkıya göre büyükten küçüğe sırala */
  const sorted = [...rows].sort((a, b) => (b.katki ?? 0) - (a.katki ?? 0));

  for (const { cat, mom, katki } of sorted) {
    const momCls   = mom   == null ? 'cell-empty' : mom   >= 0 ? 'cell-neg' : 'cell-pos';
    const katkiCls = katki == null ? 'cell-empty' : katki >= 0 ? 'cell-neg' : 'cell-pos';
    const pay      = (katki != null && genelMom) ? katki / genelMom * 100 : null;
    const barW     = maxAbsKatki > 0 && katki != null ? Math.abs(katki) / maxAbsKatki * 100 : 0;
    const barCls   = katki != null && katki >= 0 ? 'enf-bar-pos' : 'enf-bar-neg';
    const num      = cat === 'gida' ? '01' : cat === 'alkol' ? '02' : cat === 'giyim' ? '03' :
                     cat === 'konut' ? '04' : cat === 'mobilya' ? '05' : cat === 'saglik' ? '06' :
                     cat === 'ulasim' ? '07' : cat === 'bilgi' ? '08' : cat === 'eglence' ? '09' :
                     cat === 'egitim' ? '10' : cat === 'lokanta' ? '11' : cat === 'sigorta' ? '12' : '13';

    html += `
      <tr>
        <td>${num}</td>
        <td>${TUFE_LABELS[cat]}</td>
        <td style="color:var(--text-muted)">%${(TUFE_WEIGHTS[cat]*100).toFixed(2)}</td>
        <td class="${momCls}">${mom != null ? (mom >= 0 ? '+' : '') + mom.toFixed(2) + '%' : '—'}</td>
        <td class="${katkiCls}">${katki != null ? (katki >= 0 ? '+' : '') + katki.toFixed(2) + 'pp' : '—'}</td>
        <td>
          <div class="enf-bar-wrap">
            <span style="font-size:11px;color:${katki != null && katki >= 0 ? 'var(--green)' : 'var(--red)'}">
              ${pay != null ? (pay >= 0 ? '+' : '') + pay.toFixed(1) + '%' : '—'}
            </span>
            <div class="enf-bar ${barCls}" style="width:${barW.toFixed(1)}px;max-width:80px"></div>
          </div>
        </td>
      </tr>`;
  }

  html += '</tbody></table>';
  const wrap = document.getElementById('enflasyonTableWrap');
  wrap.innerHTML = html;
}

/* ── Tarihsel tablo: her satır = 1 ay, kategoriler marjinal etki (pp) ── */
function renderEnflasyonHistTable() {
  /* Hesapla — tüm veri, en güncel en üstte */
  const rows = [];
  for (let i = allEnflasyon.length - 1; i >= 1; i--) {
    const curr = allEnflasyon[i];
    const prev = allEnflasyon[i - 1];
    const mom    = key => (curr[key] && prev[key]) ? (curr[key] / prev[key] - 1) * 100 : null;
    const katki  = key => { const m = mom(key); return m != null ? TUFE_WEIGHTS[key] * m : null; };
    rows.push({
      tarih: curr.tarih,
      genel: mom('genel'),
      ...Object.fromEntries(TUFE_CATS.map(c => [c, katki(c)])),
    });
  }

  /* Renk — katkı (pp) bazlı: negatif=yeşil, pozitif=kırmızı yoğunluğu */
  const cellStyleGenel = v => {
    if (v == null) return 'color:#c0c8d8';
    if (v < 0)   return `background:rgba(16,185,129,${Math.min(Math.abs(v)/8,1)*0.35});color:#065f46`;
    if (v < 1)   return `background:rgba(251,191,36,0.12);color:#92400e`;
    if (v < 3)   return `background:rgba(251,191,36,0.28);color:#78350f`;
    if (v < 5)   return `background:rgba(249,115,22,0.30);color:#9a3412`;
    if (v < 8)   return `background:rgba(239,68,68,0.35);color:#7f1d1d`;
    return               `background:rgba(185,28,28,0.50);color:#fff`;
  };
  /* Katkı rengi: daha dar aralık (pp cinsinden küçük sayılar) */
  const cellStyleKatki = v => {
    if (v == null) return 'color:#c0c8d8';
    if (v < 0)    return `background:rgba(16,185,129,${Math.min(Math.abs(v)/1.5,1)*0.40});color:#065f46`;
    if (v < 0.05) return `color:var(--text-muted)`;
    if (v < 0.20) return `background:rgba(251,191,36,0.15);color:#92400e`;
    if (v < 0.50) return `background:rgba(251,191,36,0.30);color:#78350f`;
    if (v < 0.80) return `background:rgba(249,115,22,0.32);color:#9a3412`;
    if (v < 1.20) return `background:rgba(239,68,68,0.38);color:#7f1d1d`;
    return                `background:rgba(185,28,28,0.52);color:#fff`;
  };

  const CAT_SHORT = {
    gida:'Gıda', alkol:'Alkol', giyim:'Giyim', konut:'Konut',
    mobilya:'Mobilya', saglik:'Sağlık', ulasim:'Ulaşım', bilgi:'Bilgi',
    eglence:'Eğlence', egitim:'Eğitim', lokanta:'Lokanta',
    sigorta:'Sigorta', kisisel:'Kişisel',
  };

  let html = `<table class="enf-hist-table"><thead><tr>
    <th>Tarih</th>
    <th>Genel %</th>`;
  for (const cat of TUFE_CATS) html += `<th>${CAT_SHORT[cat]}</th>`;
  html += `</tr></thead><tbody>`;

  for (const row of rows) {
    html += `<tr><td>${monthKey(row.tarih)}</td>`;
    const g = row.genel;
    html += `<td style="${cellStyleGenel(g)}">${g != null ? (g >= 0 ? '+' : '') + g.toFixed(2) + '%' : '—'}</td>`;
    for (const cat of TUFE_CATS) {
      const v = row[cat];
      html += `<td style="${cellStyleKatki(v)}">${v != null ? (v >= 0 ? '+' : '') + v.toFixed(2) : '—'}</td>`;
    }
    html += `</tr>`;
  }

  html += `</tbody></table>`;
  const wrap = document.getElementById('enflasyonHistTableWrap');
  if (wrap) { wrap.innerHTML = html; wrap.scrollTop = 0; }
}

document.getElementById('enflasyonAySelect').addEventListener('change', renderEnflasyonTable);
document.getElementById('refreshBtnEnflasyon').addEventListener('click', () => { allEnflasyon = []; _pageRendered['enflasyon'] = false; loadEnflasyon(); });

/* ════════════════════════════════════════
   MAKRO TAHMİN
   ════════════════════════════════════════ */

const MAKRO_LS = 'makro_v2';

function makroLoadStorage() {
  try { return JSON.parse(localStorage.getItem(MAKRO_LS)) || { overrides: {}, forecast: [] }; }
  catch { return { overrides: {}, forecast: [] }; }
}
function makroSaveStorage(data) {
  localStorage.setItem(MAKRO_LS, JSON.stringify(data));
}

function makroAddMonth(yyyy_mm_dd) {
  const d = new Date(yyyy_mm_dd + 'T00:00:00');
  d.setMonth(d.getMonth() + 1);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-01`;
}

function makroFmtDate(yyyy_mm_dd) {
  const [y, m] = yyyy_mm_dd.split('-');
  return `${m}.${y}`;
}

/* Tarihsel verideki sepet değerini YYYY-MM-01 tarihine göre bul */
function makroGetSepet(historical, forecastRows, targetRaw) {
  const h = historical.find(r => r.tarih_raw === targetRaw);
  if (h) return h.sepet;
  const f = forecastRows.find(r => r.tarih_raw === targetRaw);
  return f ? f.sepet : null;
}

/* Tahmin satırlarını inputlardan hesapla */
function makroComputeForecast(historical, inputs) {
  const rows = [];
  for (let i = 0; i < inputs.length; i++) {
    const inp    = inputs[i];
    const prev   = i === 0 ? historical[historical.length - 1] : rows[i - 1];
    const tarih_raw = makroAddMonth(i === 0 ? historical[historical.length - 1].tarih_raw : rows[i - 1].tarih_raw);

    const mom_enf  = inp.mom_enf  !== '' && inp.mom_enf  != null ? parseFloat(inp.mom_enf)  : null;
    const mom_kur  = inp.mom_kur  !== '' && inp.mom_kur  != null ? parseFloat(inp.mom_kur)  : null;
    const pol_faiz = inp.pol_faiz !== '' && inp.pol_faiz != null ? parseFloat(inp.pol_faiz) : null;
    const tr2y     = inp.tr2y  !== '' && inp.tr2y  != null ? parseFloat(inp.tr2y)  : null;
    const tr10y    = inp.tr10y !== '' && inp.tr10y != null ? parseFloat(inp.tr10y) : null;

    const sepet  = (mom_enf  != null && prev.sepet  != null) ? parseFloat((prev.sepet  * (1 + mom_enf  / 100)).toFixed(2)) : null;
    const usdtry = (mom_kur  != null && prev.usdtry != null) ? parseFloat((prev.usdtry * (1 + mom_kur  / 100)).toFixed(4)) : null;

    /* YoY: sepet 12 ay önce */
    const d12 = new Date(tarih_raw + 'T00:00:00');
    d12.setFullYear(d12.getFullYear() - 1);
    const raw12 = `${d12.getFullYear()}-${String(d12.getMonth() + 1).padStart(2, '0')}-01`;
    const sepet12 = makroGetSepet(historical, rows, raw12);
    const yoy_enf  = (sepet != null && sepet12 != null) ? parseFloat(((sepet / sepet12 - 1) * 100).toFixed(2)) : null;

    const reel_faiz = (pol_faiz != null && yoy_enf != null) ? parseFloat((pol_faiz - yoy_enf).toFixed(2)) : null;
    const spread    = (tr10y != null && tr2y  != null) ? parseFloat((tr10y - tr2y).toFixed(2)) : null;
    const proxy_kur = (sepet != null && usdtry != null && usdtry > 0) ? parseFloat((sepet / usdtry).toFixed(2)) : null;

    rows.push({ tarih_raw, sepet, mom_enf, yoy_enf, usdtry, mom_kur,
                note: inp.note || '', pol_faiz, reel_faiz, tr2y, tr10y, spread, proxy_kur });
  }
  return rows;
}

async function loadMakro() {
  showLoading(true);
  try {
    const [macroRes, yieldsRes, forecastRes] = await Promise.all([
      fetch('/api/makro'),
      fetch('/api/tr-yields'),
      fetch('/api/makro-forecast'),
    ]);
    const raw    = await macroRes.json();
    const yields = await yieldsRes.json().catch(() => ({}));
    const adminFc = await forecastRes.json().catch(() => []);

    // Server'dan gelen admin tahminini global'e yükle
    adminMakroForecast = Array.isArray(adminFc) ? adminFc : [];

    allMakro = raw.map(d => {
      const [dd, mm, yyyy] = d.tarih.split('-');
      return { ...d, tarih_raw: `${yyyy}-${mm}-${dd}` };
    });

    /* TR yields → overrides'a yaz (yalnızca boş hücrelere) */
    if (yields && !yields.error) {
      /* Scanner cari ayı döndürür (2026-05); DB henüz o ayı içermiyorsa
         en son mevcut aya (2026-04) yaz — en iyi yaklaşım */
      if (allMakro.length) {
        const latestYm = allMakro[allMakro.length - 1].tarih_raw.slice(0, 7);
        const nowYm    = new Date().toISOString().slice(0, 7);
        if (nowYm !== latestYm && yields[nowYm] && !yields[latestYm]) {
          yields[latestYm] = yields[nowYm];
        }
      }
      const storage   = makroLoadStorage();
      const overrides = storage.overrides || {};
      let changed = false;
      for (const d of allMakro) {
        const ym  = d.tarih_raw.slice(0, 7);
        const yld = yields[ym];
        if (!yld) continue;
        overrides[d.tarih_raw] = overrides[d.tarih_raw] || {};
        if (yld.tr2y  != null && !overrides[d.tarih_raw].tr2y)  { overrides[d.tarih_raw].tr2y  = yld.tr2y;  changed = true; }
        if (yld.tr10y != null && !overrides[d.tarih_raw].tr10y) { overrides[d.tarih_raw].tr10y = yld.tr10y; changed = true; }
      }
      if (changed) makroSaveStorage({ ...storage, overrides });
    }

    showLoading(false);
    document.getElementById('page-makro').classList.remove('hidden');
    renderMakro();
  } catch (e) {
    console.error('Makro yüklenemedi:', e);
    showLoading(false);
    document.getElementById('page-makro').classList.remove('hidden');
  }
}

function renderMakro() {
  if (!allMakro.length) return;
  const storage   = makroLoadStorage();
  const overrides = storage.overrides || {};

  // Tahmin inputları: server'dan gelen 3N Finans tahmini (her yüklenmede sıfırlanır)
  // Kullanıcı session boyunca düzenleyebilir ama kayıt olmaz
  const fcInputs = adminMakroForecast.length
    ? adminMakroForecast.map(r => ({ ...r }))   // shallow copy — in-memory düzenleme
    : [];
  while (fcInputs.length < 12) fcInputs.push({ mom_enf:'', mom_kur:'', pol_faiz:'', tr2y:'', tr10y:'', note:'' });
  const fcRows = makroComputeForecast(allMakro, fcInputs);
  const has3n = adminMakroForecast.some(r => r.mom_enf || r.mom_kur || r.pol_faiz || r.tr2y || r.tr10y);

  const pct = (v, dp = 2) => v != null ? `${v >= 0 ? '+' : ''}${v.toFixed(dp)}%` : '—';
  const num = (v, dp = 2) => v != null ? v.toFixed(dp) : '—';
  const clz = v => v == null ? '' : v >= 0 ? 'positive' : 'negative';
  const REFRESH_SVG = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="12" height="12"><path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8"/><path d="M21 3v5h-5"/><path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16"/><path d="M8 16H3v5"/></svg>`;

  /* ── TABLO BAŞLIĞI ── */
  let html = `<table class="makro-tbl" id="makroTbl">
<thead><tr>
  <th>Tarih</th>
  <th>CPI Sepet</th>
  <th>MoM Enfl</th>
  <th>YoY Enfl</th>
  <th>USD/TRY</th>
  <th>MoM Kur</th>
  <th style="min-width:120px;text-align:left">Not</th>
  <th>TCMB Faizi</th>
  <th>Reel Faiz</th>
  <th>TR 2Y</th>
  <th>TR 10Y</th>
  <th>Spread</th>
  <th>Proxy Reel Kur</th>
</tr></thead>
<tbody id="makroFcBody">`;

  /* ── TAHMİN SATIRLARI (en uzak gelecekten en yakına) ── */
  for (let i = fcRows.length - 1; i >= 0; i--) {
    const fc = fcRows[i]; const inp = fcInputs[i];
    html += `<tr class="fc-row" data-idx="${i}">
  <td class="date-cell">${makroFmtDate(fc.tarih_raw)}</td>
  <td data-c="sepet">${fc.sepet != null ? fc.sepet.toFixed(2) : '—'}</td>
  <td><input type="number" class="makro-input fc-input" data-key="mom_enf"  data-idx="${i}" value="${inp.mom_enf  || ''}" placeholder="%" step="0.01"></td>
  <td class="${clz(fc.yoy_enf)}" data-c="yoy_enf">${pct(fc.yoy_enf)}</td>
  <td data-c="usdtry">${fc.usdtry != null ? fc.usdtry.toFixed(4) : '—'}</td>
  <td><input type="number" class="makro-input fc-input" data-key="mom_kur"  data-idx="${i}" value="${inp.mom_kur  || ''}" placeholder="%" step="0.01"></td>
  <td><input type="text"   class="makro-input note-input fc-input" data-key="note" data-idx="${i}" value="${(inp.note || '').replace(/"/g,'&quot;')}" placeholder="Not..."></td>
  <td><input type="number" class="makro-input fc-input" data-key="pol_faiz" data-idx="${i}" value="${inp.pol_faiz || ''}" placeholder="%" step="0.01"></td>
  <td class="${clz(fc.reel_faiz)}" data-c="reel_faiz">${fc.reel_faiz != null ? fc.reel_faiz.toFixed(2) + '%' : '—'}</td>
  <td><input type="number" class="makro-input fc-input" data-key="tr2y"     data-idx="${i}" value="${inp.tr2y    || ''}" placeholder="%" step="0.01"></td>
  <td><input type="number" class="makro-input fc-input" data-key="tr10y"    data-idx="${i}" value="${inp.tr10y   || ''}" placeholder="%" step="0.01"></td>
  <td class="${clz(fc.spread)}" data-c="spread">${fc.spread != null ? (fc.spread >= 0 ? '+' : '') + fc.spread.toFixed(2) : '—'}</td>
  <td data-c="proxy_kur">${fc.proxy_kur != null ? fc.proxy_kur.toFixed(2) : '—'}</td>
</tr>`;
  }

  html += `</tbody>
<tbody><tr class="makro-divider"><td colspan="13">
  ── TAHMİN BÖLÜMÜ ──
  ${has3n ? '<span style="background:rgba(240,180,41,0.15);color:#f0b429;font-size:10px;font-weight:700;letter-spacing:0.8px;padding:2px 8px;border-radius:10px;margin-left:10px;border:1px solid rgba(240,180,41,0.3);">3N FİNANS TAHMİNİ</span>' : ''}
  <span style="color:#475569;font-size:10px;margin-left:8px;">· Düzenlemeler sayfadan ayrılınca sıfırlanır</span>
</td></tr></tbody>
<tbody id="makroHistBody">`;

  /* ── TARİHSEL SATIRLAR (en yeniden eskiye) ── */
  for (const d of [...allMakro].reverse()) {
    const ov   = overrides[d.tarih_raw] || {};
    const tr2y_v  = ov.tr2y  != null && ov.tr2y  !== '' ? parseFloat(ov.tr2y)  : null;
    const tr10y_v = ov.tr10y != null && ov.tr10y !== '' ? parseFloat(ov.tr10y) : null;
    const spr_v   = tr2y_v != null && tr10y_v != null ? +(tr10y_v - tr2y_v).toFixed(2) : null;
    html += `<tr class="hist-row" data-date="${d.tarih_raw}">
  <td class="date-cell">${d.tarih}</td>
  <td>${d.sepet != null ? d.sepet.toFixed(2) : '—'}</td>
  <td class="${clz(d.mom_enf)}">${pct(d.mom_enf)}</td>
  <td class="${clz(d.yoy_enf)}">${pct(d.yoy_enf)}</td>
  <td>${d.usdtry != null ? d.usdtry.toFixed(4) : '—'}</td>
  <td class="${clz(d.mom_kur)}">${pct(d.mom_kur)}</td>
  <td><input type="text"   class="makro-input note-input hist-note" data-key="note"  value="${(ov.note  || '').replace(/"/g,'&quot;')}" placeholder="—"></td>
  <td>${d.pol_faiz != null ? d.pol_faiz.toFixed(2) + '%' : '—'}</td>
  <td class="${clz(d.reel_faiz)}">${d.reel_faiz != null ? d.reel_faiz.toFixed(2) + '%' : '—'}</td>
  <td><input type="number" class="makro-input hist-num" data-key="tr2y"  value="${ov.tr2y  || ''}" placeholder="—" step="0.01"></td>
  <td><input type="number" class="makro-input hist-num" data-key="tr10y" value="${ov.tr10y || ''}" placeholder="—" step="0.01"></td>
  <td class="${clz(spr_v)}" data-c="spread">${spr_v != null ? (spr_v >= 0 ? '+' : '') + spr_v.toFixed(2) : '—'}</td>
  <td>${d.proxy_kur != null ? d.proxy_kur.toFixed(2) : '—'}</td>
</tr>`;
  }

  html += `</tbody></table>`;
  document.getElementById('makroTableWrap').innerHTML = html;

  /* En yeni tarih artık en üstte — başa scroll */
  setTimeout(() => {
    const wrap = document.getElementById('makroTableWrap');
    if (wrap) wrap.scrollTop = 0;
  }, 50);

  bindMakroEvents(fcInputs, overrides);
}

/* Sadece tahmin hesaplanan hücrelerini güncelle (input'a dokunma) */
function makroUpdateFcCells(fcRows) {
  const pct = (v, dp = 2) => v != null ? `${v >= 0 ? '+' : ''}${v.toFixed(dp)}%` : '—';
  const clz = v => v == null ? '' : v >= 0 ? 'positive' : 'negative';
  const tbody = document.getElementById('makroFcBody');
  if (!tbody) return;
  tbody.querySelectorAll('tr.fc-row').forEach(tr => {
    const i = parseInt(tr.dataset.idx);
    if (isNaN(i) || i >= fcRows.length) return;
    const fc = fcRows[i];
    const set = (attr, text, cls) => {
      const el = tr.querySelector(`[data-c="${attr}"]`);
      if (!el) return;
      el.textContent = text;
      el.className   = cls || '';
    };
    set('sepet',     fc.sepet    != null ? fc.sepet.toFixed(2)    : '—');
    set('yoy_enf',   pct(fc.yoy_enf), clz(fc.yoy_enf));
    set('usdtry',    fc.usdtry   != null ? fc.usdtry.toFixed(4)   : '—');
    set('reel_faiz', fc.reel_faiz!= null ? fc.reel_faiz.toFixed(2)+'%':'—', clz(fc.reel_faiz));
    set('spread',    fc.spread   != null ? (fc.spread >= 0 ? '+' : '') + fc.spread.toFixed(2) : '—', clz(fc.spread));
    set('proxy_kur', fc.proxy_kur!= null ? fc.proxy_kur.toFixed(2) : '—');
  });
}

/* Tarihsel satırlarda spread güncelle */
function makroUpdateHistSpread(tr, overrides) {
  const dateKey = tr.dataset.date;
  const ov = overrides[dateKey] || {};
  const tr2y  = ov.tr2y  != null && ov.tr2y  !== '' ? parseFloat(ov.tr2y)  : null;
  const tr10y = ov.tr10y != null && ov.tr10y !== '' ? parseFloat(ov.tr10y) : null;
  const spr   = tr2y != null && tr10y != null ? +(tr10y - tr2y).toFixed(2) : null;
  const el = tr.querySelector('[data-c="spread"]');
  if (!el) return;
  el.textContent = spr != null ? (spr >= 0 ? '+' : '') + spr.toFixed(2) : '—';
  el.className   = spr == null ? '' : spr >= 0 ? 'positive' : 'negative';
}

function bindMakroEvents(fcInputs, overrides) {
  const wrap = document.getElementById('makroTableWrap');
  if (!wrap) return;

  /* Tarihsel satır override'ları (note, tr2y, tr10y) */
  wrap.querySelectorAll('.hist-row').forEach(tr => {
    tr.querySelectorAll('.hist-note, .hist-num').forEach(inp => {
      inp.addEventListener('input', () => {
        const key     = inp.dataset.key;
        const dateKey = tr.dataset.date;
        if (!overrides[dateKey]) overrides[dateKey] = {};
        overrides[dateKey][key] = inp.value;
        makroSaveStorage({ overrides, forecast: fcInputs });
        if (key === 'tr2y' || key === 'tr10y') makroUpdateHistSpread(tr, overrides);
      });
    });
  });

  /* Tahmin satırı inputları — değişiklikler sadece session'da (localStorage'a yazılmaz) */
  wrap.querySelectorAll('.fc-input').forEach(inp => {
    inp.addEventListener('input', () => {
      const idx = parseInt(inp.dataset.idx);
      const key = inp.dataset.key;
      fcInputs[idx][key] = inp.value;
      // Kasıtlı: forecast localStorage'a kaydedilmiyor → yenilemede 3N Finans tahmini geri gelir
      const fcRows = makroComputeForecast(allMakro, fcInputs);
      makroUpdateFcCells(fcRows);
    });
  });
}

/* Tahmin satırı ekle — sadece session'da, kalıcı değil */
document.getElementById('addFcRowBtn').addEventListener('click', () => {
  adminMakroForecast.push({ mom_enf:'', mom_kur:'', pol_faiz:'', tr2y:'', tr10y:'', note:'' });
  if (allMakro.length) renderMakro();
});

document.getElementById('refreshBtnMakro').addEventListener('click', () => { allMakro = []; _pageRendered['makro'] = false; loadMakro(); });

/* ════════════════════════════════════════
   TCMB REZERVLERİ
   ════════════════════════════════════════ */

let tcmbAbMode = '1y';   // '1y' | '3y' | '5y' | '10y' | 'all' | 'custom'

async function loadAbSurplus() {
  try {
    const abRes = await fetch('/api/tcmb-ab');
    allAbSurplus = await abRes.json();
    if (!allMakro.length) {
      const makroRes = await fetch('/api/makro');
      const raw = await makroRes.json();
      allMakro = raw.map(d => {
        const [dd, mm, yyyy] = d.tarih.split('-');
        return { ...d, tarih_raw: `${yyyy}-${mm}-${dd}` };
      });
    }
    document.getElementById('page-tcmb-ab').classList.remove('hidden');
    renderAbSurplus();
  } catch(e) { console.error('TCMB AB yüklenemedi:', e); }
}

function setTcmbAbRange(mode) {
  tcmbAbMode = mode;
  /* Buton aktif durumunu güncelle */
  ['1y','3y','5y','10y','all','custom'].forEach(m => {
    const btn = document.getElementById('tcmbAbBtn' + (m === '1y' ? '1Y' : m === '3y' ? '3Y' : m === '5y' ? '5Y' : m === '10y' ? '10Y' : m === 'all' ? 'All' : 'Ozel'));
    if (btn) btn.classList.toggle('active', m === mode);
  });
  /* Özel tarih inputlarını göster/gizle */
  const wrap = document.getElementById('tcmbAbCustomWrap');
  wrap.style.display = mode === 'custom' ? 'flex' : 'none';
  if (mode !== 'custom') renderAbSurplus();
}

/* Ortak tarih filtresi — DD-MM-YYYY tarih alanı olan herhangi bir dizi için */
function tcmbAbFilterByDate(arr) {
  if (!arr.length) return [];
  if (tcmbAbMode === 'all') return arr;

  if (tcmbAbMode === 'custom') {
    const s = document.getElementById('tcmbAbCustomStart').value;
    const e = document.getElementById('tcmbAbCustomEnd').value;
    return arr.filter(d => {
      const [dd, mm, yyyy] = d.tarih.split('-');
      const iso = `${yyyy}-${mm}-${dd}`;
      if (s && iso < s) return false;
      if (e && iso > e) return false;
      return true;
    });
  }

  const yrs = { '1y': 1, '3y': 3, '5y': 5, '10y': 10 }[tcmbAbMode] || 1;
  const cutoff = new Date();
  cutoff.setFullYear(cutoff.getFullYear() - yrs);
  const cutoffISO = cutoff.toISOString().slice(0, 10);
  return arr.filter(d => {
    const [dd, mm, yyyy] = d.tarih.split('-');
    return `${yyyy}-${mm}-${dd}` >= cutoffISO;
  });
}

function getFilteredAbSurplus()  { return tcmbAbFilterByDate(allAbSurplus); }
function getFilteredProxyKur()   { return tcmbAbFilterByDate(allMakro.filter(d => d.proxy_kur != null)); }

function renderAbSurplus() {
  const abData    = getFilteredAbSurplus();
  const proxyData = getFilteredProxyKur();
  if (!abData.length && !proxyData.length) return;
  if (abData.length)    renderAbSurplusChart(abData);
  if (proxyData.length) renderProxyKurChart(proxyData);
  renderAbSurplusTable();
}

function renderAbSurplusChart(data) {
  makeChart('tcmbAbChart', 'line', {
    labels: data.map(d => formatDateTR(d.tarih)),
    datasets: [{
      label: 'TCMB Rezervleri',
      data: data.map(d => d.deger),
      borderColor: '#3b7ef8',
      backgroundColor: 'rgba(59,126,248,0.07)',
      fill: true,
      tension: 0.3,
      pointRadius: 0,
      borderWidth: 2
    }]
  }, {
    responsive: true, maintainAspectRatio: false,
    interaction: { mode: 'index', intersect: false },
    plugins: {
      legend: { display: false },
      tooltip: { ...baseTip, callbacks: { label: c => ` ${fmtDec(c.parsed.y)}` } }
    },
    scales: {
      ...baseScales,
      y: { ...baseScales.y, ticks: { ...baseScales.y.ticks, callback: v => fmtDec(v, 0) } }
    }
  });
}

function renderProxyKurChart(data) {
  makeChart('proxyKurChart', 'line', {
    labels: data.map(d => formatDateTR(d.tarih)),
    datasets: [{
      label: 'Proxy Reel Kur',
      data: data.map(d => d.proxy_kur),
      borderColor: '#10b981',
      backgroundColor: 'rgba(16,185,129,0.07)',
      fill: true,
      tension: 0.3,
      pointRadius: 0,
      borderWidth: 2
    }]
  }, {
    responsive: true, maintainAspectRatio: false,
    interaction: { mode: 'index', intersect: false },
    plugins: {
      legend: { display: false },
      tooltip: { ...baseTip, callbacks: { label: c => ` Proxy Reel Kur: ${fmtDec(c.parsed.y)}` } }
    },
    scales: {
      ...baseScales,
      y: { ...baseScales.y, ticks: { ...baseScales.y.ticks, callback: v => fmtDec(v, 1) } }
    }
  });
}

function renderAbSurplusTable() {
  /* Aylık ortalamaları hesapla — tüm data üzerinden, en güncel en üstte */
  const monthly = {};
  for (const d of allAbSurplus) {          // tabloda her zaman TÜM veri
    const parts = d.tarih.split('-');      // DD-MM-YYYY
    const key   = `${parts[2]}-${parts[1]}`;
    const label = monthKey(d.tarih);
    if (!monthly[key]) monthly[key] = { label, sum: 0, cnt: 0 };
    if (d.deger != null) { monthly[key].sum += d.deger; monthly[key].cnt++; }
  }

  const keys = Object.keys(monthly).sort().reverse();
  let html = `<table style="width:100%;border-collapse:collapse;font-size:12px;">
    <thead><tr>
      <th style="text-align:left;padding:6px 12px;border-bottom:1px solid var(--border);color:var(--text-muted)">Dönem</th>
      <th style="text-align:right;padding:6px 12px;border-bottom:1px solid var(--border);color:var(--text-muted)">Ort. Değer</th>
      <th style="text-align:right;padding:6px 12px;border-bottom:1px solid var(--border);color:var(--text-muted)">Aylık Δ</th>
    </tr></thead><tbody>`;

  for (let i = 0; i < keys.length; i++) {
    const m     = monthly[keys[i]];
    const avg   = m.cnt ? m.sum / m.cnt : null;
    const prevM = monthly[keys[i + 1]];
    const prevA = prevM && prevM.cnt ? prevM.sum / prevM.cnt : null;
    const chg   = (avg != null && prevA != null) ? avg - prevA : null;
    const chgStyle = chg == null ? '' : chg >= 0 ? 'color:#10b981' : 'color:#ef4444';
    html += `<tr>
      <td style="padding:5px 12px;border-bottom:1px solid var(--border)">${m.label}</td>
      <td style="text-align:right;padding:5px 12px;border-bottom:1px solid var(--border);font-family:monospace">${avg != null ? fmtDec(avg) : '—'}</td>
      <td style="text-align:right;padding:5px 12px;border-bottom:1px solid var(--border);font-family:monospace;${chgStyle}">${chg != null ? (chg >= 0 ? '+' : '') + fmtDec(chg) : '—'}</td>
    </tr>`;
  }

  html += '</tbody></table>';
  document.getElementById('tcmbAbTableWrap').innerHTML = html;
}

/* Özel tarih inputları değişince yeniden render et */
document.getElementById('tcmbAbCustomStart').addEventListener('change', () => { if (tcmbAbMode === 'custom') renderAbSurplus(); });
document.getElementById('tcmbAbCustomEnd').addEventListener('change',   () => { if (tcmbAbMode === 'custom') renderAbSurplus(); });
document.getElementById('refreshBtnTcmbAb').addEventListener('click', () => { allAbSurplus = []; _pageRendered['tcmb-ab'] = false; loadAbSurplus(); });

/* ════════════════════════════════════════
   PİYASA ÖZETİ
   ════════════════════════════════════════ */
let _mbFilter = 'all';
let _mbLoaded = false;

function mbFilter(f) {
  _mbFilter = f;
  ['All','Daily','Weekly'].forEach(x => {
    const btn = document.getElementById('mbBtn' + x);
    if (btn) btn.classList.toggle('active', f === x.toLowerCase() || (x === 'All' && f === 'all'));
  });
  _mbRender(window._mbReports || []);
}

async function mbLoad() {
  if (_mbLoaded) return;
  try {
    const r = await fetch('/api/market-briefs?limit=60');
    const d = await r.json();
    window._mbReports = d.reports || [];
    _mbLoaded = true;
    document.getElementById('mb-loading').style.display = 'none';
    _mbRender(window._mbReports);
    _mbRenderSubscribeBox();
  } catch(e) {
    document.getElementById('mb-loading').innerHTML =
      '<p style="color:var(--red)">Raporlar yüklenemedi: ' + e + '</p>';
  }
}

function _mbRenderSubscribeBox() {
  const list = document.getElementById('mb-list');
  if (!list) return;
  const box = document.createElement('div');
  box.style.cssText = 'margin-top:24px;background:linear-gradient(135deg,rgba(240,180,41,.06),rgba(240,180,41,.02));border:1px solid rgba(240,180,41,.2);border-radius:12px;padding:20px 24px;display:flex;align-items:center;gap:16px;flex-wrap:wrap;';
  box.innerHTML = `
    <div style="flex:1;min-width:200px;">
      <div style="font-size:14px;font-weight:700;color:var(--text);margin-bottom:4px;">📬 Her sabah emailine gelsin</div>
      <div style="font-size:12px;color:var(--text-muted);">Günlük piyasa özeti sabah 09:30'da emailine gönderilir. Ücretsiz.</div>
    </div>
    <div style="display:flex;gap:8px;flex-wrap:wrap;">
      <input id="mb-sub-email" type="email" placeholder="email@adresin.com"
             style="padding:9px 14px;border-radius:8px;border:1px solid var(--border);background:var(--surface2);color:var(--text);font-size:13px;outline:none;width:220px;">
      <button onclick="mbSubscribe()" id="mb-sub-btn"
              style="padding:9px 18px;border-radius:8px;background:#f0b429;border:none;color:#050a14;font-size:13px;font-weight:700;cursor:pointer;">
        Abone Ol
      </button>
    </div>
    <div id="mb-sub-msg" style="width:100%;font-size:12px;display:none;"></div>`;
  list.parentNode.insertBefore(box, list.nextSibling);
}

async function mbSubscribe() {
  const email = document.getElementById('mb-sub-email').value.trim();
  const btn   = document.getElementById('mb-sub-btn');
  const msg   = document.getElementById('mb-sub-msg');
  if (!email) return;
  btn.disabled = true; btn.textContent = '...';
  try {
    const r = await fetch('/api/subscribe', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({email})
    });
    const d = await r.json();
    msg.style.display = 'block';
    msg.style.color = d.ok ? '#10b981' : '#ef4444';
    msg.textContent = d.ok ? '✓ ' + d.msg : '✗ ' + d.error;
    if (d.ok) { btn.textContent = '✓ Gönderildi'; }
    else { btn.disabled = false; btn.textContent = 'Abone Ol'; }
  } catch(e) {
    btn.disabled = false; btn.textContent = 'Abone Ol';
    msg.style.display = 'block'; msg.style.color = '#ef4444';
    msg.textContent = 'Hata oluştu.';
  }
}

function _mbRender(reports) {
  const list = document.getElementById('mb-list');
  if (!list) return;
  const filtered = _mbFilter === 'all' ? reports : reports.filter(r => r.type === _mbFilter);

  if (!filtered.length) {
    list.innerHTML = '<div style="text-align:center;padding:60px 0;color:var(--text-muted);">Henüz rapor yok.</div>';
    return;
  }

  list.innerHTML = filtered.map((r, i) => {
    const isWeekly = r.type === 'weekly';
    const badge = isWeekly
      ? '<span style="font-size:10px;font-weight:700;padding:2px 8px;border-radius:10px;background:rgba(139,92,246,.15);color:#a78bfa;letter-spacing:.05em;">HAFTALIK</span>'
      : '<span style="font-size:10px;font-weight:700;padding:2px 8px;border-radius:10px;background:rgba(59,130,246,.15);color:#93c5fd;letter-spacing:.05em;">GÜNLÜK</span>';

    const title = r.title || r.date_label || r.date;
    const expanded = i === 0;

    return `
    <div style="background:var(--surface);border:1px solid var(--border);border-radius:12px;overflow:hidden;">
      <div style="padding:18px 24px;display:flex;align-items:center;gap:12px;cursor:pointer;"
           onclick="mbToggle(this)">
        ${badge}
        <div style="flex:1;min-width:0;">
          <div style="font-size:14px;font-weight:600;color:var(--text);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">${title}</div>
          <div style="font-size:11px;color:var(--text-muted);margin-top:2px;">${r.created_at ? r.created_at.replace('T',' ').slice(0,16) : ''}</div>
        </div>
        <svg class="mb-chevron" style="width:16px;height:16px;flex-shrink:0;color:var(--text-muted);transition:transform .2s;${expanded?'transform:rotate(180deg)':''}"
             viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="6 9 12 15 18 9"/></svg>
      </div>
      <div class="mb-body" style="display:${expanded?'block':'none'};padding:0 24px 24px;">
        <div style="height:1px;background:var(--border);margin-bottom:20px;"></div>
        <div style="font-size:13.5px;line-height:1.85;color:var(--text);">${_mbFormat(r.content || '')}</div>
      </div>
    </div>`;
  }).join('');
}

function mbToggle(header) {
  const body = header.nextElementSibling;
  const chevron = header.querySelector('.mb-chevron');
  const open = body.style.display === 'block';
  body.style.display = open ? 'none' : 'block';
  chevron.style.transform = open ? '' : 'rotate(180deg)';
}

/* ── Rapor içeriğini HTML'e çevir ── */
function _mbFormat(raw) {
  const esc = s => s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');

  const lines = raw.split('\n');
  let html = '';
  let inBox = false;

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    const t = line.trim();

    if (!t) {
      if (inBox) continue;
      html += '<div style="height:8px"></div>';
      continue;
    }

    // Ana başlık: 📊 GÜNLÜK / 📋 HAFTALIK
    if (/^(📊|📋)\s/.test(t)) {
      html += `<div style="font-size:16px;font-weight:800;color:var(--text);margin:0 0 16px;letter-spacing:-.3px;">${esc(t)}</div>`;
      continue;
    }

    // Bölüm başlığı: ━━━ ... ━━━
    if (/^━+/.test(t)) {
      const label = t.replace(/━+/g,'').trim();
      if (label) {
        html += `<div style="display:flex;align-items:center;gap:10px;margin:20px 0 10px;font-size:11px;font-weight:700;letter-spacing:.1em;text-transform:uppercase;color:var(--blue);">
          <span style="flex:0 0 24px;height:2px;background:var(--blue);opacity:.4;border-radius:1px;display:block"></span>
          ${esc(label)}
          <span style="flex:1;height:1px;background:var(--border);display:block"></span>
        </div>`;
      } else {
        html += `<div style="height:1px;background:var(--border);margin:16px 0"></div>`;
      }
      continue;
    }

    // Kutu başlığı: ┌─ POZİSYON FIRSATLARI ─...
    if (/^┌/.test(t)) {
      inBox = true;
      const label = t.replace(/[┌─└┘│]+/g,'').trim();
      html += `<div style="margin:12px 0 0;border:1px solid rgba(59,130,246,.3);border-radius:8px;overflow:hidden;">
        <div style="background:rgba(59,130,246,.08);padding:7px 14px;font-size:11px;font-weight:700;letter-spacing:.08em;color:var(--blue);">${esc(label) || 'POZİSYON FIRSATLARI'}</div>
        <div style="padding:10px 14px;">`;
      continue;
    }
    // Kutu alt çizgisi: └───
    if (/^└/.test(t)) {
      inBox = false;
      html += `</div></div>`;
      continue;
    }
    // Kutu içi satır: │ ...
    if (/^│/.test(t)) {
      const content = t.replace(/^│\s*/,'');
      if (content) html += `<div style="font-size:13px;font-weight:600;color:var(--text);padding:2px 0;">${_mbInline(esc(content))}</div>`;
      continue;
    }

    // Numaralı liste: 1. 2. ...
    if (/^\d+\./.test(t)) {
      const num = t.match(/^(\d+)\./)[1];
      const rest = t.replace(/^\d+\.\s*/,'');
      html += `<div style="display:flex;gap:10px;margin:6px 0;padding:8px 12px;background:var(--surface2);border-radius:7px;border-left:3px solid var(--blue);">
        <span style="font-size:12px;font-weight:700;color:var(--blue);min-width:18px;padding-top:1px;">${num}.</span>
        <span style="font-size:13px;color:var(--text);">${_mbInline(esc(rest))}</span>
      </div>`;
      continue;
    }

    // Bullet: • veya -
    if (/^[•\-]\s/.test(t)) {
      const rest = t.replace(/^[•\-]\s*/,'');
      html += `<div style="display:flex;gap:8px;margin:4px 0;color:var(--text-secondary);font-size:13px;">
        <span style="color:var(--blue);font-size:10px;margin-top:5px;">●</span>
        <span>${_mbInline(esc(rest))}</span>
      </div>`;
      continue;
    }

    // Earnings satırı: 🟢/🔴 **SİRKET**: ...
    if (/^(🟢|🔴|⏳)/.test(t)) {
      const isUp = t.startsWith('🟢');
      const isDown = t.startsWith('🔴');
      const color = isUp ? '#10b981' : isDown ? '#ef4444' : '#f59e0b';
      html += `<div style="display:flex;align-items:flex-start;gap:8px;margin:5px 0;padding:8px 12px;background:${isUp?'rgba(16,185,129,.05)':isDown?'rgba(239,68,68,.05)':'rgba(245,158,11,.05)'};border-radius:7px;font-size:13px;">
        <span style="margin-top:1px;">${t[0]}</span>
        <span style="color:var(--text);">${_mbInline(esc(t.slice(1).trim()))}</span>
      </div>`;
      continue;
    }

    // --- ayırıcı
    if (/^-{3,}$/.test(t)) {
      html += `<div style="height:1px;background:var(--border);margin:14px 0"></div>`;
      continue;
    }

    // Normal paragraf
    html += `<p style="margin:4px 0;color:var(--text-secondary);font-size:13.5px;">${_mbInline(esc(t))}</p>`;
  }

  if (inBox) html += '</div></div>';
  return html;
}

/* Inline formatting: **bold**, *italic* */
function _mbInline(s) {
  return s
    .replace(/\*\*([^*]+)\*\*/g, '<strong style="color:var(--text);font-weight:700;">$1</strong>')
    .replace(/\*([^*]+)\*/g,     '<em style="color:var(--text-secondary);">$1</em>')
    .replace(/`([^`]+)`/g,       '<code style="background:var(--surface2);padding:1px 5px;border-radius:4px;font-size:12px;color:var(--blue);">$1</code>');
}

/* ── Bootstrap ── */
switchPage('dth');

