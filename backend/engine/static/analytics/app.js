/* ================================================================
   Analytics Dashboard — Frontend logic
   Vanilla JS, Chart.js for charts, no framework.
   ================================================================ */

// ── State ────────────────────────────────────────────────────────
const S = {
  dates: [],
  selectedDate: null,
  level: 'State',
  searchTerm: '',
  sortCol: null,
  sortDir: 'asc',
};

let barChart = null;
let dpdChart = null;
let smCharts = [];
let trendChartD = null;

// ── Bootstrap ────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', init);

async function init() {
  await loadDates();
  setupListeners();
}

// ── Data Loaders ─────────────────────────────────────────────────

async function loadDates() {
  const data = await fetchJSON('/analytics/api/dates');
  S.dates = (data && data.dates) || [];

  const sel = document.getElementById('date-select');
  sel.innerHTML = S.dates.length
    ? S.dates.map(d =>
        `<option value="${d.date_iso}">${d.date_display}</option>`
      ).join('')
    : '<option value="">No cached dates</option>';

  if (S.dates.length) {
    S.selectedDate = S.dates[S.dates.length - 1].date_iso;
    sel.value = S.selectedDate;
    loadAll();
  }
}

async function loadAll() {
  if (!S.selectedDate) return;
  const d = S.selectedDate;

  // Parallel fetches
  const [dashboard, rankings, dpdDist, regionTrends, projection, slippage] =
    await Promise.all([
      fetchJSON(`/analytics/api/dashboard?date=${d}`),
      fetchJSON(`/analytics/api/rankings?date=${d}&level=${S.level}&n=5`),
      fetchJSON(`/analytics/api/dpd-dist?date=${d}`),
      fetchJSON('/analytics/api/heatmap?level=Region'),
      fetchJSON('/analytics/api/projection?entity=NLPL'),
      fetchJSON(`/analytics/api/slippage?date=${d}`),
    ]);

  renderKPIs(dashboard);
  renderRankings(rankings);
  await loadBarChart();
  renderDPDChart(dpdDist);

  const cleanTrends = dedupeRegions(regionTrends);
  renderTrendsB(cleanTrends);
  renderTrendsD(cleanTrends);
  renderProjection(projection);
  renderSlippage(slippage);
}

async function loadBarChart() {
  const data = await fetchJSON(
    `/analytics/api/rankings?date=${S.selectedDate}&level=${S.level}&n=999`
  );
  renderBarChart(data);
}

async function reloadRankingsAndChart() {
  const [rankings] = await Promise.all([
    fetchJSON(`/analytics/api/rankings?date=${S.selectedDate}&level=${S.level}&n=5`),
  ]);
  renderRankings(rankings);
  await loadBarChart();
}

// ── Event Listeners ──────────────────────────────────────────────

function setupListeners() {
  document.getElementById('date-select').addEventListener('change', e => {
    S.selectedDate = e.target.value;
    loadAll();
  });

  document.getElementById('search-input').addEventListener('input', e => {
    S.searchTerm = e.target.value.toLowerCase();
    applySearch();
  });

  // Level tabs for Rankings + Bar chart
  setupTabs('rankings-tabs', level => { S.level = level; reloadRankingsAndChart(); });
  setupTabs('chart-tabs', level => { S.level = level; reloadRankingsAndChart(); });

}

function setupTabs(containerId, onChange) {
  const container = document.getElementById(containerId);
  if (!container) return;
  container.addEventListener('click', e => {
    const btn = e.target.closest('.level-tab');
    if (!btn) return;
    container.querySelectorAll('.level-tab').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');

    // Sync all tab groups that share a level type
    const level = btn.dataset.level;
    if (containerId === 'rankings-tabs' || containerId === 'chart-tabs') {
      syncTabs('rankings-tabs', level);
      syncTabs('chart-tabs', level);
    }
    onChange(level);
  });
}

function syncTabs(containerId, level) {
  const container = document.getElementById(containerId);
  if (!container) return;
  container.querySelectorAll('.level-tab').forEach(b => {
    b.classList.toggle('active', b.dataset.level === level);
  });
}

// ── Render: KPIs ─────────────────────────────────────────────────

function renderKPIs(data) {
  const el = document.getElementById('kpi-strip');
  if (!data || data.error) { el.innerHTML = emptyState('KPI data unavailable'); return; }

  const topCards = [
    { label: 'Regular Demand', value: fmtN(data.total_demand), cls: '' },
    { label: 'Collection', value: fmtN(data.total_collection), cls: '' },
    { label: 'FTOD', value: fmtN(data.ftod), cls: '' },
    { label: 'Collection %', value: fmt(data.collection_pct, 1) + '%', cls: pctClass(data.collection_pct) },
    { label: 'Total Collection', value: '\u20B9 ' + fmtIndian(data.collection_amount), cls: '' },
  ];

  const bottomCards = [
    { label: 'Best Performing Region', value: data.best_region ? data.best_region.name : '\u2014',
      sub: data.best_region ? fmt(data.best_region.collection_pct, 1) + '%' : '', cls: 'kpi-green' },
    { label: 'Lowest Performing Region', value: data.worst_region ? data.worst_region.name : '\u2014',
      sub: data.worst_region ? fmt(data.worst_region.collection_pct, 1) + '%' : '', cls: 'kpi-red' },
  ];

  const renderCard = c => `
    <div class="kpi-card ${c.cls}">
      <div class="kpi-label">${c.label}</div>
      <div class="kpi-value">${c.value}</div>
      ${c.sub ? `<div class="kpi-sub">${c.sub}</div>` : ''}
    </div>
  `;

  el.innerHTML = `
    <div class="kpi-row-top">${topCards.map(renderCard).join('')}</div>
    <div class="kpi-row-bottom">${bottomCards.map(renderCard).join('')}</div>
  `;
}

// ── Render: Rankings ─────────────────────────────────────────────

function renderRankings(data) {
  const el = document.getElementById('rankings-content');
  if (!data || data.error) { el.innerHTML = emptyState('No ranking data'); return; }

  el.innerHTML = `
    <div>
      <div class="rank-label rank-label-top">Top 5</div>
      <div class="data-table-wrap">
        <table class="data-table" id="rank-top-table">
          <thead><tr>
            <th>#</th><th>Name</th><th data-key="demand">Demand</th>
            <th data-key="collection">Collection</th><th data-key="collection_pct">Collection %</th>
          </tr></thead>
          <tbody>${rankRows(data.top, 'rank-top')}</tbody>
        </table>
      </div>
    </div>
    <div>
      <div class="rank-label rank-label-bottom">Bottom 5</div>
      <div class="data-table-wrap">
        <table class="data-table" id="rank-bottom-table">
          <thead><tr>
            <th>#</th><th>Name</th><th data-key="demand">Demand</th>
            <th data-key="collection">Collection</th><th data-key="collection_pct">Collection %</th>
          </tr></thead>
          <tbody>${rankRows(data.bottom, 'rank-bottom')}</tbody>
        </table>
      </div>
    </div>
  `;

  attachSort('rank-top-table');
  attachSort('rank-bottom-table');
}

function rankRows(rows, cls) {
  if (!rows || !rows.length) return '<tr><td colspan="5" class="empty-state">No data</td></tr>';
  return rows.map((r, i) => `
    <tr class="${cls} searchable" data-name="${(r.name || '').toLowerCase()}">
      <td><span class="rank-badge">${i + 1}</span></td>
      <td>${r.name || ''}</td>
      <td>${fmtN(r.demand)}</td>
      <td>${fmtN(r.collection)}</td>
      <td class="${pctClass(r.collection_pct, true)}">${fmt(r.collection_pct, 2)}%</td>
    </tr>
  `).join('');
}

// ── Render: Bar Chart ────────────────────────────────────────────

function renderBarChart(data) {
  const canvas = document.getElementById('bar-chart');
  if (barChart) barChart.destroy();

  const rows = (data && data.all) || [];
  if (!rows.length) return;

  const labels = rows.map(r => r.name);
  const values = rows.map(r => r.collection_pct || 0);
  const colors = values.map(v =>
    v >= 95 ? 'rgba(16,185,129,0.7)' :
    v >= 80 ? 'rgba(245,158,11,0.7)' :
              'rgba(239,68,68,0.7)'
  );

  barChart = new Chart(canvas, {
    type: 'bar',
    data: {
      labels,
      datasets: [{
        label: 'Collection %',
        data: values,
        backgroundColor: colors,
        borderRadius: 4,
        barThickness: rows.length > 30 ? 10 : 18,
      }],
    },
    options: {
      indexAxis: 'y',
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            label: ctx => ` ${ctx.raw.toFixed(2)}%`,
          },
        },
      },
      scales: {
        x: {
          min: 0, max: 100,
          grid: { color: 'rgba(255,255,255,0.04)' },
          ticks: { color: 'rgba(255,255,255,0.5)', font: { size: 10, family: 'JetBrains Mono' } },
        },
        y: {
          grid: { display: false },
          ticks: { color: 'rgba(255,255,255,0.6)', font: { size: 11, family: 'DM Sans' } },
        },
      },
    },
  });

  // Resize canvas height based on row count
  canvas.parentElement.style.minHeight = Math.max(300, rows.length * 28 + 60) + 'px';
}

// ── Render: DPD Donut ────────────────────────────────────────────

function renderDPDChart(data) {
  const canvas = document.getElementById('dpd-chart');
  if (dpdChart) dpdChart.destroy();

  if (!data || !data.length) {
    canvas.parentElement.innerHTML = emptyState('No DPD data') + '<canvas id="dpd-chart"></canvas>';
    return;
  }

  const labels = data.map(d => d.bucket);
  const values = data.map(d => d.count);
  const palette = [
    '#10b981', '#3b82f6', '#f59e0b', '#f97316',
    '#ef4444', '#dc2626', '#991b1b', '#7f1d1d',
  ];

  dpdChart = new Chart(canvas, {
    type: 'doughnut',
    data: {
      labels,
      datasets: [{
        data: values,
        backgroundColor: palette.slice(0, labels.length),
        borderWidth: 0,
        hoverOffset: 6,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      cutout: '58%',
      plugins: {
        legend: {
          position: 'bottom',
          labels: {
            color: 'rgba(255,255,255,0.6)',
            font: { size: 10, family: 'JetBrains Mono' },
            padding: 12,
            boxWidth: 12, boxHeight: 12,
          },
        },
        tooltip: {
          callbacks: {
            label: ctx => {
              const total = ctx.dataset.data.reduce((a, b) => a + b, 0);
              const pct = total ? ((ctx.raw / total) * 100).toFixed(1) : 0;
              return ` ${fmtIndian(ctx.raw)} (${pct}%)`;
            },
          },
        },
      },
    },
  });
}

const TREND_PALETTE = [
  '#f59e0b', '#3b82f6', '#10b981', '#ef4444',
  '#8b5cf6', '#ec4899', '#06b6d4', '#f97316',
  '#84cc16', '#6366f1', '#14b8a6', '#e11d48',
];

/** Treat 0/null/undefined as missing data → null so Chart.js skips them with spanGaps */
function sanitizeSeries(series) {
  return (series || []).map(v => (v == null || v === 0) ? null : v);
}

/** Merge duplicate region names (e.g. KALABURAGI / KALBURGI) by picking the non-null value per date */
function dedupeRegions(data) {
  if (!data || !data.entities) return data;

  // Normalize: strip spaces, uppercase
  const norm = s => s.trim().toUpperCase();

  // Map of known aliases → canonical name
  const ALIASES = {
    'KALBURGI': 'KALABURAGI',
  };

  const merged = {};       // canonical → { name, values[] }
  const orderSeen = [];    // preserve first-seen order

  data.entities.forEach((name, i) => {
    const key = ALIASES[norm(name)] || norm(name);
    if (!merged[key]) {
      merged[key] = { name: name, values: (data.values[i] || []).slice() };
      orderSeen.push(key);
    } else {
      // Merge: for each date, prefer non-null / non-zero value
      const existing = merged[key].values;
      const incoming = data.values[i] || [];
      for (let j = 0; j < Math.max(existing.length, incoming.length); j++) {
        const a = existing[j], b = incoming[j];
        const aValid = a != null && a !== 0;
        const bValid = b != null && b !== 0;
        existing[j] = aValid ? a : bValid ? b : null;
      }
    }
  });

  return {
    dates: data.dates,
    entities: orderSeen.map(k => merged[k].name),
    values: orderSeen.map(k => merged[k].values),
    averages: data.averages, // pass through as-is
  };
}

// ── Render: Trends — Option B (Small Multiples) ─────────────────

function renderTrendsB(data) {
  smCharts.forEach(c => c.destroy());
  smCharts = [];

  const grid = document.getElementById('small-multiples-grid');
  if (!data || !data.entities || !data.entities.length) {
    grid.innerHTML = emptyState('No trend data');
    return;
  }

  const { entities, dates, values } = data;

  grid.innerHTML = entities.map((name, i) => {
    const series = sanitizeSeries(values[i]);
    const validValues = series.filter(v => v != null);
    const current = validValues.length ? validValues[validValues.length - 1] : null;
    const pctCls = current >= 95 ? 'pct-green' : current >= 80 ? 'pct-yellow' : 'pct-red';
    return `
      <div class="sm-card">
        <div class="sm-header">
          <span class="sm-name">${name}</span>
          <span class="sm-pct ${pctCls}">${current != null ? current.toFixed(2) + '%' : '\u2014'}</span>
        </div>
        <canvas id="sm-chart-${i}"></canvas>
      </div>`;
  }).join('');

  entities.forEach((name, i) => {
    const canvas = document.getElementById(`sm-chart-${i}`);
    if (!canvas) return;
    const color = TREND_PALETTE[i % TREND_PALETTE.length];
    const sanitized = sanitizeSeries(values[i]);
    const chart = new Chart(canvas, {
      type: 'line',
      data: {
        labels: dates.map(d => d.slice(5)),
        datasets: [{
          data: sanitized,
          borderColor: color,
          backgroundColor: hexToRGBA(color, 0.08),
          borderWidth: 2,
          pointRadius: 1,
          pointHoverRadius: 4,
          tension: 0.3,
          fill: true,
          spanGaps: true,
        }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: {
              title: ctx => ctx[0].label,
              label: ctx => ` ${ctx.raw != null ? ctx.raw.toFixed(2) : '\u2014'}%`,
            },
          },
        },
        scales: {
          x: {
            display: true,
            grid: { display: false },
            ticks: { color: 'rgba(255,255,255,0.3)', font: { size: 9, family: 'JetBrains Mono' }, maxTicksLimit: 5 },
          },
          y: {
            grid: { color: 'rgba(255,255,255,0.04)' },
            ticks: { color: 'rgba(255,255,255,0.3)', font: { size: 9, family: 'JetBrains Mono' }, maxTicksLimit: 4 },
          },
        },
        animation: { duration: 500 },
      },
    });
    smCharts.push(chart);
  });
}

function hexToRGBA(hex, alpha) {
  const r = parseInt(hex.slice(1, 3), 16);
  const g = parseInt(hex.slice(3, 5), 16);
  const b = parseInt(hex.slice(5, 7), 16);
  return `rgba(${r},${g},${b},${alpha})`;
}

// ── Render: Trends — Option D (All Regions Multi-Line) ──────────

function renderTrendsD(data) {
  const canvas = document.getElementById('trend-chart-d');
  if (trendChartD) trendChartD.destroy();

  if (!data || !data.entities || !data.entities.length) return;

  const { entities, dates, values } = data;
  const labels = dates.map(d => d.slice(5)); // MM-DD

  const datasets = entities.map((name, i) => ({
    label: name,
    data: sanitizeSeries(values[i]),
    borderColor: TREND_PALETTE[i % TREND_PALETTE.length],
    backgroundColor: 'transparent',
    tension: 0.3,
    pointRadius: 2,
    pointHoverRadius: 6,
    borderWidth: 2,
    fill: false,
    spanGaps: true,
  }));

  trendChartD = new Chart(canvas, {
    type: 'line',
    data: { labels, datasets },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { intersect: false, mode: 'index' },
      plugins: {
        legend: {
          display: true,
          position: 'bottom',
          labels: {
            color: 'rgba(255,255,255,0.7)',
            font: { size: 11, family: 'JetBrains Mono' },
            padding: 16,
            boxWidth: 14, boxHeight: 3,
          },
        },
        tooltip: {
          callbacks: {
            label: ctx => ` ${ctx.dataset.label}: ${ctx.raw != null ? ctx.raw.toFixed(2) : '\u2014'}%`,
          },
        },
      },
      scales: {
        x: {
          grid: { color: 'rgba(255,255,255,0.04)' },
          ticks: { color: 'rgba(255,255,255,0.5)', font: { size: 10, family: 'JetBrains Mono' } },
        },
        y: {
          grid: { color: 'rgba(255,255,255,0.04)' },
          ticks: { color: 'rgba(255,255,255,0.5)', font: { size: 10, family: 'JetBrains Mono' } },
        },
      },
    },
  });
}

// ── Render: Projection ───────────────────────────────────────────

function renderProjection(data) {
  const el = document.getElementById('projection-content');
  if (!data || data.error) { el.innerHTML = emptyState('Projection unavailable'); return; }

  const cur = data.current_pct || 0;
  const proj = data.projected_pct || 0;
  const barColor = proj >= 95 ? 'var(--accent-green)' :
                   proj >= 80 ? 'var(--accent-orange)' : 'var(--accent-red)';

  el.innerHTML = `
    <div class="projection-bar-wrap">
      <div class="projection-bar-fill" style="width:${cur}%;background:${barColor};opacity:0.7;"></div>
      ${proj > cur ? `<div class="projection-marker" style="left:${Math.min(proj, 100)}%;" data-label="Projected ${fmt(proj,1)}%"></div>` : ''}
    </div>
    <div class="projection-stats">
      <div class="projection-stat">Current: <strong>${fmt(cur,2)}%</strong></div>
      <div class="projection-stat">Projected: <strong>${fmt(proj,2)}%</strong></div>
      <div class="projection-stat">Remaining Days: <strong>${data.remaining_days}</strong></div>
      <div class="projection-stat">Daily Avg Gain: <strong>${data.daily_avg >= 0 ? '+' : ''}${fmt(data.daily_avg,2)}%</strong></div>
    </div>
    ${proj < 95 ? `<div class="projection-warning">Projected month-end collection (${fmt(proj,1)}%) is below 95% target.</div>` : ''}
  `;
}

// ── Render: Slippage ─────────────────────────────────────────────

function renderSlippage(data) {
  const el = document.getElementById('slippage-content');
  if (!data || data.error || data.message) {
    el.innerHTML = emptyState(data?.message || 'Slippage data unavailable');
    return;
  }

  const rateClass = data.rate > 5 ? 'slip-highlight' : 'slip-safe';

  let breakdownHTML = '';
  if (data.breakdown && data.breakdown.length) {
    breakdownHTML = `
      <div class="data-table-wrap" style="margin-top:16px;">
        <table class="data-table">
          <thead><tr><th>Slipped To Bucket</th><th>Count</th></tr></thead>
          <tbody>${data.breakdown.map(b => `
            <tr><td>${b.bucket}</td><td>${fmtN(b.count)}</td></tr>
          `).join('')}</tbody>
        </table>
      </div>
    `;
  }

  el.innerHTML = `
    <div style="font-size:11px;color:var(--text-muted);margin-bottom:12px;">
      Comparing ${data.previous_date || '—'} &rarr; ${data.current_date || S.selectedDate}
    </div>
    <div class="slippage-stats">
      <div class="slip-stat">
        <div class="slip-num ${rateClass}">${fmtN(data.slipped)}</div>
        <div class="slip-label">Slipped (0 → 1-30)</div>
      </div>
      <div class="slip-stat">
        <div class="slip-num">${fmtN(data.stayed_current || 0)}</div>
        <div class="slip-label">Stayed Current</div>
      </div>
      <div class="slip-stat">
        <div class="slip-num">${fmtN(data.total_current_prev)}</div>
        <div class="slip-label">Total Current (Prev)</div>
      </div>
      <div class="slip-stat">
        <div class="slip-num ${rateClass}">${fmt(data.rate,2)}%</div>
        <div class="slip-label">Slippage Rate</div>
      </div>
    </div>
    ${breakdownHTML}
  `;
}


// ── Search ───────────────────────────────────────────────────────

function applySearch() {
  const term = S.searchTerm;
  document.querySelectorAll('.searchable').forEach(row => {
    const name = row.dataset.name || '';
    row.style.display = (!term || name.includes(term)) ? '' : 'none';
  });
}

// ── Column Sort ──────────────────────────────────────────────────

function attachSort(tableId) {
  const table = document.getElementById(tableId);
  if (!table) return;
  const headers = table.querySelectorAll('th[data-key]');
  headers.forEach(th => {
    th.addEventListener('click', () => {
      const key = th.dataset.key;
      const tbody = table.querySelector('tbody');
      const rows = Array.from(tbody.querySelectorAll('tr'));

      // Toggle direction
      const isAsc = th.classList.contains('sorted-asc');
      headers.forEach(h => h.classList.remove('sorted-asc', 'sorted-desc'));

      const dir = isAsc ? 'desc' : 'asc';
      th.classList.add(dir === 'asc' ? 'sorted-asc' : 'sorted-desc');

      const colIdx = Array.from(th.parentElement.children).indexOf(th);

      rows.sort((a, b) => {
        const av = parseFloat(a.children[colIdx]?.textContent.replace(/[,%₹\s]/g, '')) || 0;
        const bv = parseFloat(b.children[colIdx]?.textContent.replace(/[,%₹\s]/g, '')) || 0;
        return dir === 'asc' ? av - bv : bv - av;
      });

      rows.forEach(r => tbody.appendChild(r));
    });
  });
}

// ── Utilities ────────────────────────────────────────────────────

async function fetchJSON(url) {
  try {
    const res = await fetch(url);
    if (!res.ok) return { error: `HTTP ${res.status}` };
    return await res.json();
  } catch (e) {
    console.error('Fetch error:', url, e);
    return { error: e.message };
  }
}

function fmt(val, decimals) {
  if (val == null || isNaN(val)) return '\u2014';
  return Number(val).toFixed(decimals);
}

function fmtN(val) {
  if (val == null || isNaN(val)) return '\u2014';
  return Number(val).toLocaleString('en-IN');
}

function fmtIndian(num) {
  if (num == null || isNaN(num)) return '\u2014';
  const n = Math.round(num);
  const s = Math.abs(n).toString();
  if (s.length <= 3) return (n < 0 ? '-' : '') + s;
  const last3 = s.slice(-3);
  const rest = s.slice(0, -3);
  const grouped = rest.replace(/\B(?=(\d{2})+(?!\d))/g, ',');
  return (n < 0 ? '-' : '') + grouped + ',' + last3;
}

function pctClass(val, prefix) {
  const p = prefix ? 'pct-' : 'kpi-';
  if (val >= 95) return p + 'green';
  if (val >= 80) return p + 'yellow';
  return p + 'red';
}

function emptyState(msg) {
  return `<div class="empty-state">${msg}</div>`;
}
