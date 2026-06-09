/* ================================================================
   Employee Login — Frontend Logic
   ================================================================ */

const S = {
  empId: null,
  dates: [],
  selectedDate: null,
  fyMode: 'overall',
  cachedPerf: null,
  allEmployees: [],
  dropdownIdx: -1,
};

// ── Bootstrap ────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', init);

async function init() {
  setupLoginListeners();
  loadEmployeeList();
}

// ── Login ────────────────────────────────────────────────────────

function setupLoginListeners() {
  const form = document.getElementById('login-form');
  const input = document.getElementById('emp-id-input');
  const ceoBtn = document.getElementById('ceo-btn');
  const dropdown = document.getElementById('emp-dropdown');

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    hideDropdown();
    const empId = input.value.trim();
    if (!empId) return;
    await doLogin(empId);
  });

  ceoBtn.addEventListener('click', () => {
    window.location.href = '/employee/';
  });

  // Autocomplete: filter on input
  input.addEventListener('input', () => {
    const term = input.value.trim();
    S.dropdownIdx = -1;
    if (!term) { hideDropdown(); return; }
    showFilteredDropdown(term);
  });

  // Show full list on focus if input is empty
  input.addEventListener('focus', () => {
    const term = input.value.trim();
    if (S.allEmployees.length) {
      showFilteredDropdown(term);
    }
  });

  // Keyboard navigation
  input.addEventListener('keydown', (e) => {
    if (!dropdown.classList.contains('visible')) return;
    const items = dropdown.querySelectorAll('.emp-dropdown-item');
    if (!items.length) return;

    if (e.key === 'ArrowDown') {
      e.preventDefault();
      S.dropdownIdx = Math.min(S.dropdownIdx + 1, items.length - 1);
      highlightItem(items);
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      S.dropdownIdx = Math.max(S.dropdownIdx - 1, 0);
      highlightItem(items);
    } else if (e.key === 'Enter' && S.dropdownIdx >= 0) {
      e.preventDefault();
      const selected = items[S.dropdownIdx];
      if (selected) selectDropdownItem(selected.dataset.empId);
    } else if (e.key === 'Escape') {
      hideDropdown();
    }
  });

  // Close dropdown on outside click
  document.addEventListener('click', (e) => {
    if (!e.target.closest('#input-wrap')) hideDropdown();
  });

  input.focus();
}

// ── Employee List (for autocomplete) ─────────────────────────────

async function loadEmployeeList() {
  const dropdown = document.getElementById('emp-dropdown');
  dropdown.innerHTML = '<div class="emp-dropdown-loading"><span class="loading-spinner"></span> Loading employees...</div>';

  const data = await fetchJSON('/emp-login/api/employees');
  S.allEmployees = (data && data.employees) || [];
}

function showFilteredDropdown(term) {
  const dropdown = document.getElementById('emp-dropdown');

  if (!S.allEmployees.length) {
    dropdown.innerHTML = '<div class="emp-dropdown-loading"><span class="loading-spinner"></span> Loading...</div>';
    dropdown.classList.add('visible');
    return;
  }

  const lower = (term || '').toLowerCase();
  const matches = lower
    ? S.allEmployees.filter(e =>
        String(e.emp_id).toLowerCase().includes(lower) ||
        (e.emp_name && e.emp_name.toLowerCase().includes(lower))
      ).slice(0, 15)
    : S.allEmployees.slice(0, 15);

  if (!matches.length) {
    dropdown.innerHTML = '<div class="emp-dropdown-empty">No employees found</div>';
    dropdown.classList.add('visible');
    return;
  }

  dropdown.innerHTML = matches.map(e => `
    <div class="emp-dropdown-item" data-emp-id="${e.emp_id}">
      <div>
        <span class="emp-dropdown-id">${e.emp_id}</span>
        <span class="emp-dropdown-name">${e.emp_name || ''}</span>
      </div>
      <span class="emp-dropdown-meta">${e.branch || ''}</span>
    </div>
  `).join('');

  dropdown.classList.add('visible');

  // Click handler for each item
  dropdown.querySelectorAll('.emp-dropdown-item').forEach(el => {
    el.addEventListener('click', () => {
      selectDropdownItem(el.dataset.empId);
    });
  });
}

function selectDropdownItem(empId) {
  document.getElementById('emp-id-input').value = empId;
  hideDropdown();
  doLogin(empId);
}

function hideDropdown() {
  document.getElementById('emp-dropdown').classList.remove('visible');
  S.dropdownIdx = -1;
}

function highlightItem(items) {
  items.forEach((it, i) => it.classList.toggle('active', i === S.dropdownIdx));
  if (items[S.dropdownIdx]) {
    items[S.dropdownIdx].scrollIntoView({ block: 'nearest' });
  }
}

async function doLogin(empId) {
  hideError();

  const res = await postJSON('/emp-login/api/login', { emp_id: empId });

  if (res.error) {
    showError(res.error);
    return;
  }

  if (res.role === 'ceo') {
    window.location.href = '/employee/';
    return;
  }

  // Employee login success
  S.empId = res.emp_id;
  await enterDashboard();
}

function showError(msg) {
  const el = document.getElementById('login-error');
  el.textContent = msg;
  el.classList.add('visible');
}

function hideError() {
  document.getElementById('login-error').classList.remove('visible');
}

// ── Dashboard Entry ──────────────────────────────────────────────

async function enterDashboard() {
  // Hide login, show dashboard
  document.getElementById('login-screen').style.display = 'none';
  document.getElementById('dashboard-screen').classList.add('active');

  // Load status / dates
  const status = await fetchJSON('/emp-login/api/status');
  if (!status || !status.available) {
    showToast(status?.message || 'No data available');
    return;
  }

  S.dates = status.dates || [];
  populateDateSelect();
  initMonthNav();

  if (S.dates.length) {
    S.selectedDate = S.dates[S.dates.length - 1].date_iso;
    document.getElementById('dash-date-select').value = S.selectedDate;
  }

  setupDashboardListeners();
  loadPerformance();
}

function populateDateSelect() {
  const sel = document.getElementById('dash-date-select');
  sel.innerHTML = S.dates.length
    ? S.dates.map(d => `<option value="${d.date_iso}">${d.date_display}</option>`).join('')
    : '<option value="">No dates</option>';
}

// ── Month Navigation ────────────────────────────────────────────

function getMonthsFromDates(dates) {
  var months = [];
  var seen = {};
  dates.forEach(function(d) {
    var ym = d.date_iso.substring(0, 7);
    if (!seen[ym]) {
      seen[ym] = true;
      var parts = ym.split('-');
      var monthNames = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
      months.push({ key: ym, label: monthNames[parseInt(parts[1], 10) - 1] + ' ' + parts[0] });
    }
  });
  return months;
}

function initMonthNav() {
  var months = getMonthsFromDates(S.dates);
  if (!months.length) return;

  S._months = months;
  S._monthIdx = months.length - 1;
  updateMonthLabel();
  filterDatesByMonth();

  document.getElementById('month-prev').addEventListener('click', function() {
    if (S._monthIdx > 0) {
      S._monthIdx--;
      updateMonthLabel();
      filterDatesByMonth();
    }
  });

  document.getElementById('month-next').addEventListener('click', function() {
    if (S._monthIdx < S._months.length - 1) {
      S._monthIdx++;
      updateMonthLabel();
      filterDatesByMonth();
    }
  });
}

function updateMonthLabel() {
  var m = S._months[S._monthIdx];
  document.getElementById('month-label').textContent = m ? m.label : '—';
  document.getElementById('month-prev').disabled = S._monthIdx <= 0;
  document.getElementById('month-next').disabled = S._monthIdx >= S._months.length - 1;
}

function filterDatesByMonth() {
  var m = S._months[S._monthIdx];
  if (!m) return;
  var filtered = S.dates.filter(function(d) { return d.date_iso.startsWith(m.key); });
  var sel = document.getElementById('dash-date-select');
  sel.innerHTML = filtered.length
    ? filtered.map(function(d) { return '<option value="' + d.date_iso + '">' + d.date_display + '</option>'; }).join('')
    : '<option value="">No dates</option>';
  if (filtered.length) {
    var latest = filtered[filtered.length - 1].date_iso;
    sel.value = latest;
    S.selectedDate = latest;
    loadPerformance();
  }
}

function setupDashboardListeners() {
  document.getElementById('dash-date-select').addEventListener('change', (e) => {
    S.selectedDate = e.target.value;
    loadPerformance();
  });

  document.getElementById('dash-back-btn').addEventListener('click', () => {
    document.getElementById('dashboard-screen').classList.remove('active');
    document.getElementById('login-screen').style.display = '';
    S.empId = null;
    S.cachedPerf = null;
    document.getElementById('emp-id-input').value = '';
    hideDropdown();
    document.getElementById('emp-id-input').focus();
  });

  // FY toggle
  document.querySelectorAll('.toggle-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      S.fyMode = btn.dataset.fy;
      document.querySelectorAll('.toggle-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      applyFYFilter();
    });
  });
}

// ── Load Performance ─────────────────────────────────────────────

async function loadPerformance() {
  if (!S.empId || !S.selectedDate) return;

  // Show loading
  document.getElementById('dash-loading').style.display = '';
  document.getElementById('dash-content').style.display = 'none';

  const data = await fetchJSON(
    `/emp-login/api/my-performance?emp_id=${encodeURIComponent(S.empId)}&date=${S.selectedDate}`
  );

  document.getElementById('dash-loading').style.display = 'none';

  if (data.error) {
    showToast(data.error);
    return;
  }

  document.getElementById('dash-content').style.display = '';
  S.cachedPerf = data;

  const perf = data.performance;
  renderEmployeeInfo(perf.employee);
  renderKPIs(perf.kpis);
  renderSections(perf.sections);
  renderAccounts(data.accounts);
}

// ── Render: Employee Info ────────────────────────────────────────

function renderEmployeeInfo(emp) {
  if (!emp) return;
  const initials = (emp.emp_name || emp.emp_id || '?')
    .split(/\s+/).map(w => w[0]).join('').slice(0, 2).toUpperCase();
  document.getElementById('emp-avatar').textContent = initials;
  document.getElementById('emp-name-display').textContent = emp.emp_name || '\u2014';
  document.getElementById('emp-id-detail').textContent = `ID: ${emp.emp_id}`;
  const parts = [emp.region, emp.area, emp.branch].filter(Boolean);
  document.getElementById('emp-location').textContent = parts.join(' \u203A ') || '\u2014';

  // Update header subtitle
  document.getElementById('dash-subtitle').textContent =
    `${emp.emp_name || emp.emp_id} \u2014 ${emp.branch || 'Unknown Branch'}`;
}

// ── Render: KPIs ─────────────────────────────────────────────────

function renderKPIs(kpis) {
  const el = document.getElementById('kpi-strip');
  if (!kpis) { el.innerHTML = ''; return; }
  const cards = [
    { label: 'Regular Demand', value: fmtN(kpis.demand), cls: '' },
    { label: 'Collection', value: fmtN(kpis.collection), cls: '' },
    { label: 'Collection %', value: fmt(kpis.collection_pct, 2) + '%', cls: pctCls(kpis.collection_pct) },
    { label: 'FTOD', value: fmtN(kpis.ftod), cls: '' },
    { label: 'NPA Count', value: fmtN(kpis.npa_count), cls: kpis.npa_count > 0 ? 'kpi-red' : '' },
  ];
  el.innerHTML = cards.map(c => `
    <div class="kpi-card ${c.cls}">
      <div class="kpi-label">${c.label}</div>
      <div class="kpi-value">${c.value}</div>
    </div>
  `).join('');
}

// ── Render: VBA Sections ─────────────────────────────────────────

function renderSections(sections) {
  const container = document.getElementById('sections-container');
  if (!sections || !sections.length) {
    container.innerHTML = '<div class="empty-state">No section data</div>';
    return;
  }

  container.innerHTML = sections.map((s, idx) => {
    const isNPA = s.title === 'NPA';
    return `
    <div class="vba-section" style="animation-delay:${idx * 0.05}s">
      <div class="vba-section-title">${s.title}</div>
      <div class="vba-section-grid">
        ${renderCard('Overall', s.overall, isNPA, 'overall')}
        ${renderCard('FY Disbursement', s.fy, isNPA, 'fy')}
      </div>
      ${s.products && s.products.some(p => p.demand > 0) ? renderProducts(s.products, isNPA) : ''}
    </div>`;
  }).join('');

  applyFYFilter();
}

function renderCard(label, data, isNPA, mode) {
  if (!data) return '';
  const hidden = mode === 'fy' && S.fyMode !== 'fy' ? 'style="display:none"' : '';

  if (isNPA) {
    return `
    <div class="vba-section-card" data-mode="${mode}" ${hidden}>
      <div class="vba-card-label">${label}</div>
      <div class="stat-row"><span class="stat-label">Demand</span><span class="stat-value">${fmtN(data.demand)}</span></div>
      <div class="stat-row"><span class="stat-label">Activation Accts</span><span class="stat-value">${fmtN(data.activation_account)}</span></div>
      <div class="stat-row"><span class="stat-label">Activation Amt</span><span class="stat-value">${fmtN(data.activation_amount)}</span></div>
      <div class="stat-row"><span class="stat-label">Closure Accts</span><span class="stat-value">${fmtN(data.closure_account)}</span></div>
      <div class="stat-row"><span class="stat-label">Closure Amt</span><span class="stat-value">${fmtN(data.closure_amount)}</span></div>
    </div>`;
  }

  const hasBalance = 'balance' in data;
  return `
  <div class="vba-section-card" data-mode="${mode}" ${hidden}>
    <div class="vba-card-label">${label}</div>
    <div class="stat-row"><span class="stat-label">Demand</span><span class="stat-value">${fmtN(data.demand)}</span></div>
    <div class="stat-row"><span class="stat-label">Collection</span><span class="stat-value">${fmtN(data.collection)}</span></div>
    <div class="stat-row"><span class="stat-label">${hasBalance ? 'Balance' : 'FTOD'}</span><span class="stat-value">${fmtN(hasBalance ? data.balance : data.ftod)}</span></div>
    <div class="stat-row"><span class="stat-label">Collection %</span><span class="stat-value ${pctCls(data.collection_pct)}">${fmt(data.collection_pct, 2)}%</span></div>
  </div>`;
}

function renderProducts(products, isNPA) {
  let html = '<div class="product-cards" style="padding:12px 20px 16px;">';
  products.forEach(p => {
    if (p.demand <= 0) return;
    html += `<div class="product-card">
      <div class="product-card-name">${p.name}</div>`;
    if (isNPA) {
      html += `
        <div class="stat-row"><span class="stat-label">Demand</span><span class="stat-value">${fmtN(p.demand)}</span></div>
        <div class="stat-row"><span class="stat-label">Activation Accts</span><span class="stat-value">${fmtN(p.activation_account)}</span></div>
        <div class="stat-row"><span class="stat-label">Activation Amt</span><span class="stat-value">${fmtN(p.activation_amount)}</span></div>
        <div class="stat-row"><span class="stat-label">Closure Accts</span><span class="stat-value">${fmtN(p.closure_account)}</span></div>
        <div class="stat-row"><span class="stat-label">Closure Amt</span><span class="stat-value">${fmtN(p.closure_amount)}</span></div>`;
    } else {
      const hasBalance = 'balance' in p;
      html += `
        <div class="stat-row"><span class="stat-label">Demand</span><span class="stat-value">${fmtN(p.demand)}</span></div>
        <div class="stat-row"><span class="stat-label">Collection</span><span class="stat-value">${fmtN(p.collection)}</span></div>
        <div class="stat-row"><span class="stat-label">${hasBalance ? 'Balance' : 'FTOD'}</span><span class="stat-value">${fmtN(hasBalance ? p.balance : p.ftod)}</span></div>
        <div class="stat-row"><span class="stat-label">Collection %</span><span class="stat-value ${pctCls(p.collection_pct)}">${fmt(p.collection_pct, 2)}%</span></div>`;
    }
    html += '</div>';
  });
  html += '</div>';
  return html;
}

function applyFYFilter() {
  document.querySelectorAll('.vba-section-card[data-mode="fy"]').forEach(card => {
    card.style.display = S.fyMode === 'fy' ? '' : 'none';
  });
}

// ── Render: Accounts Table ───────────────────────────────────────

function renderAccounts(data) {
  const tbody = document.getElementById('accounts-tbody');
  if (!data || !data.accounts || !data.accounts.length) {
    tbody.innerHTML = '<tr><td colspan="7" class="empty-state">No accounts</td></tr>';
    return;
  }
  tbody.innerHTML = data.accounts.map(a => `
    <tr>
      <td class="mono">${a.account_id || '\u2014'}</td>
      <td>${a.product || '\u2014'}</td>
      <td class="mono">${fmtN(a.demand)}</td>
      <td class="mono">${fmtN(a.collection)}</td>
      <td class="mono">${fmtN(a.installment_amount)}</td>
      <td>${a.dpd_group || '\u2014'}</td>
      <td>${a.loan_status || '\u2014'}</td>
    </tr>
  `).join('');
}

// ── Utilities ────────────────────────────────────────────────────

async function fetchJSON(url) {
  try {
    const res = await fetch(url);
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      return { error: data.error || `HTTP ${res.status}` };
    }
    return await res.json();
  } catch (e) {
    console.error('Fetch error:', url, e);
    return { error: e.message };
  }
}

async function postJSON(url, body) {
  try {
    const res = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    return await res.json();
  } catch (e) {
    console.error('Post error:', url, e);
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

function pctCls(val) {
  if (val >= 95) return 'pct-green';
  if (val >= 80) return 'pct-yellow';
  return 'pct-red';
}

function showToast(msg) {
  const el = document.getElementById('toast');
  el.textContent = msg;
  el.classList.add('visible');
  setTimeout(() => el.classList.remove('visible'), 4000);
}
