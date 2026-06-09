/* ================================================================
   Employee Performance — Frontend Logic
   Vanilla JS, follows analytics/app.js patterns.
   ================================================================ */

// ── State ────────────────────────────────────────────────────────
const S = {
  dates: [],
  selectedDate: null,
  currentView: 'leaderboard',
  empAvailable: false,
  allEmployees: [],
  // Leaderboard
  lb: {
    page: 1,
    perPage: 50,
    sort: 'collection_pct',
    order: 'desc',
    search: '',
    region: '',
    district: '',
    branch: '',
    totalPages: 1,
  },
  // Individual
  currentEmpId: null,
  fyMode: 'overall',
  cachedSections: null,
};

// ── Bootstrap ────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', init);

async function init() {
  const status = await fetchJSON('/employee/api/status');
  if (!status || status.error || !status.available) {
    document.getElementById('status-banner').style.display = 'flex';
    if (status && status.message) {
      document.getElementById('status-message').textContent = status.message;
    }
    // Still populate dates if available
    if (status && status.dates && status.dates.length) {
      S.dates = status.dates;
      populateDateSelect();
    }
    return;
  }
  S.empAvailable = true;
  S.dates = status.dates || [];
  populateDateSelect();
  initMonthNav();
  setupListeners();
  if (S.dates.length) {
    S.selectedDate = S.dates[S.dates.length - 1].date_iso;
    document.getElementById('date-select').value = S.selectedDate;
    loadLeaderboard();
    loadEmployeeList();
  }
}

// ── Populate Date Dropdown ───────────────────────────────────────

function populateDateSelect() {
  const sel = document.getElementById('date-select');
  sel.innerHTML = S.dates.length
    ? S.dates.map(d => `<option value="${d.date_iso}">${d.date_display}</option>`).join('')
    : '<option value="">No cached dates</option>';
}

// ── Month Navigation ────────────────────────────────────────────

function getMonthsFromDates(dates) {
  var months = [];
  var seen = {};
  dates.forEach(function(d) {
    var ym = d.date_iso.substring(0, 7); // "YYYY-MM"
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
  S._monthIdx = months.length - 1; // default to latest month
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
  var sel = document.getElementById('date-select');
  sel.innerHTML = filtered.length
    ? filtered.map(function(d) { return '<option value="' + d.date_iso + '">' + d.date_display + '</option>'; }).join('')
    : '<option value="">No dates</option>';
  // Auto-select latest date in this month
  if (filtered.length) {
    var latest = filtered[filtered.length - 1].date_iso;
    sel.value = latest;
    S.selectedDate = latest;
    // Trigger change event
    sel.dispatchEvent(new Event('change'));
  }
}

// ── Event Listeners ──────────────────────────────────────────────

function setupListeners() {
  // Date change
  document.getElementById('date-select').addEventListener('change', e => {
    S.selectedDate = e.target.value;
    S.lb.page = 1;
    loadLeaderboard();
    loadEmployeeList();
    // Reset individual view
    if (S.currentEmpId) resetIndividualView();
  });

  // View tabs
  document.getElementById('view-tabs').addEventListener('click', e => {
    const btn = e.target.closest('.view-tab');
    if (!btn) return;
    switchView(btn.dataset.view);
  });

  // Filter bar: debounced search
  document.getElementById('lb-search').addEventListener('input', debounce(e => {
    S.lb.search = e.target.value.trim();
    S.lb.page = 1;
    loadLeaderboard();
  }, 350));

  // Region/Area/Branch cascade
  document.getElementById('lb-region').addEventListener('change', e => {
    S.lb.region = e.target.value;
    S.lb.district = '';
    S.lb.branch = '';
    document.getElementById('lb-district').innerHTML = '<option value="">All Areas</option>';
    document.getElementById('lb-branch').innerHTML = '<option value="">All Branches</option>';
    S.lb.page = 1;
    loadLeaderboard();
  });
  document.getElementById('lb-district').addEventListener('change', e => {
    S.lb.district = e.target.value;
    S.lb.branch = '';
    document.getElementById('lb-branch').innerHTML = '<option value="">All Branches</option>';
    S.lb.page = 1;
    loadLeaderboard();
  });
  document.getElementById('lb-branch').addEventListener('change', e => {
    S.lb.branch = e.target.value;
    S.lb.page = 1;
    loadLeaderboard();
  });

  // Sort
  document.getElementById('lb-sort').addEventListener('change', e => {
    S.lb.sort = e.target.value;
    S.lb.page = 1;
    loadLeaderboard();
  });

  // Order toggle
  document.getElementById('lb-order-toggle').addEventListener('click', () => {
    S.lb.order = S.lb.order === 'desc' ? 'asc' : 'desc';
    document.getElementById('lb-order-toggle').classList.toggle('asc', S.lb.order === 'asc');
    S.lb.page = 1;
    loadLeaderboard();
  });

  // Pagination
  document.getElementById('lb-prev').addEventListener('click', () => {
    if (S.lb.page > 1) { S.lb.page--; loadLeaderboard(); }
  });
  document.getElementById('lb-next').addEventListener('click', () => {
    if (S.lb.page < S.lb.totalPages) { S.lb.page++; loadLeaderboard(); }
  });

  // Back to leaderboard
  document.getElementById('back-to-lb').addEventListener('click', () => switchView('leaderboard'));

  // Employee search autocomplete
  const empInput = document.getElementById('emp-search');
  empInput.addEventListener('input', debounce(e => {
    showSuggestions(e.target.value.trim());
  }, 250));
  empInput.addEventListener('focus', () => {
    if (empInput.value.trim()) showSuggestions(empInput.value.trim());
  });
  document.addEventListener('click', e => {
    if (!e.target.closest('.emp-search-wrap')) {
      document.getElementById('emp-suggestions').classList.remove('visible');
    }
  });

  // FY toggle
  document.getElementById('fy-tabs').addEventListener('click', e => {
    const btn = e.target.closest('.view-tab');
    if (!btn) return;
    S.fyMode = btn.dataset.fy;
    document.querySelectorAll('#fy-tabs .view-tab').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    applyFYFilter();
  });
}

// ── View Switching ───────────────────────────────────────────────

function switchView(viewName) {
  S.currentView = viewName;
  document.querySelectorAll('.view-panel').forEach(p => p.classList.remove('active'));
  document.getElementById(viewName === 'individual' ? 'individual-view' : 'leaderboard-view').classList.add('active');
  document.querySelectorAll('#view-tabs .view-tab').forEach(t =>
    t.classList.toggle('active', t.dataset.view === viewName));
}

// ── Leaderboard ──────────────────────────────────────────────────

async function loadLeaderboard() {
  if (!S.selectedDate) return;
  const params = new URLSearchParams({
    date: S.selectedDate,
    sort: S.lb.sort,
    order: S.lb.order,
    page: S.lb.page,
    per_page: S.lb.perPage,
    search: S.lb.search,
    region: S.lb.region,
    district: S.lb.district,
    branch: S.lb.branch,
  });
  const data = await fetchJSON(`/employee/api/leaderboard?${params}`);
  if (data && data.error) {
    document.getElementById('lb-tbody').innerHTML =
      `<tr><td colspan="12" class="empty-state">${data.error}</td></tr>`;
    return;
  }
  renderLeaderboardKPIs(data.summary);
  renderLeaderboardTable(data.employees);
  updatePagination(data);
  populateFilters(data);
}

function renderLeaderboardKPIs(summary) {
  const el = document.getElementById('lb-kpi-strip');
  if (!summary) { el.innerHTML = ''; return; }
  const cards = [
    { label: 'Total Employees', value: fmtN(summary.total_employees), cls: '' },
    { label: 'Avg Collection %', value: fmt(summary.avg_collection_pct, 2) + '%',
      cls: pctCls(summary.avg_collection_pct) },
    { label: 'Best Employee',
      value: summary.best_employee ? (summary.best_employee.emp_name || summary.best_employee.emp_id) : '\u2014',
      sub: summary.best_employee ? fmt(summary.best_employee.collection_pct, 2) + '%' : '',
      cls: 'kpi-green' },
    { label: 'Needs Attention',
      value: summary.worst_employee ? (summary.worst_employee.emp_name || summary.worst_employee.emp_id) : '\u2014',
      sub: summary.worst_employee ? fmt(summary.worst_employee.collection_pct, 2) + '%' : '',
      cls: 'kpi-red' },
  ];
  el.innerHTML = cards.map(c => `
    <div class="kpi-card ${c.cls}">
      <div class="kpi-label">${c.label}</div>
      <div class="kpi-value">${c.value}</div>
      ${c.sub ? `<div class="kpi-sub">${c.sub}</div>` : ''}
    </div>
  `).join('');
}

function renderLeaderboardTable(employees) {
  const tbody = document.getElementById('lb-tbody');
  if (!employees || !employees.length) {
    tbody.innerHTML = '<tr><td colspan="12" class="empty-state">No employees found</td></tr>';
    return;
  }
  tbody.innerHTML = employees.map(e => {
    const rankCls = e.rank === 1 ? 'gold' : e.rank === 2 ? 'silver' : e.rank === 3 ? 'bronze' : '';
    const empIdSafe = String(e.emp_id).replace(/'/g, "\\'");
    return `
      <tr data-emp-id="${e.emp_id}" onclick="drillDown('${empIdSafe}')">
        <td><span class="rank-badge ${rankCls}">${e.rank}</span></td>
        <td class="emp-id-cell">${e.emp_id}</td>
        <td>${e.emp_name || '\u2014'}</td>
        <td>${e.region || '\u2014'}</td>
        <td>${e.area || '\u2014'}</td>
        <td>${e.branch || '\u2014'}</td>
        <td>${fmtN(e.demand)}</td>
        <td>${fmtN(e.collection)}</td>
        <td>${fmtN(e.ftod)}</td>
        <td class="${pctCls(e.collection_pct)}">${fmt(e.collection_pct, 2)}%</td>
        <td>${fmtN(e.dpd_1_30)}</td>
        <td>${fmtN(e.npa_count)}</td>
      </tr>`;
  }).join('');
}

function updatePagination(data) {
  if (!data) return;
  S.lb.totalPages = data.total_pages || 1;
  document.getElementById('lb-page-info').textContent =
    `Page ${data.page || 1} of ${S.lb.totalPages}`;
  document.getElementById('lb-prev').disabled = (data.page || 1) <= 1;
  document.getElementById('lb-next').disabled = (data.page || 1) >= S.lb.totalPages;
}

function populateFilters(data) {
  // Only populate once per date change (if selects still have only the default option)
  if (!data || !data.employees || !data.employees.length) return;

  const regionSel = document.getElementById('lb-region');
  const districtSel = document.getElementById('lb-district');
  const branchSel = document.getElementById('lb-branch');

  // Collect unique values from all employees (across pages we get from summary)
  // Only populate if not already populated for this date
  if (regionSel.options.length <= 1 && !S.lb.region) {
    const regions = [...new Set(data.employees.map(e => e.region).filter(Boolean))].sort();
    regions.forEach(r => {
      const opt = document.createElement('option');
      opt.value = r; opt.textContent = r;
      regionSel.appendChild(opt);
    });
  }
  if (S.lb.region && districtSel.options.length <= 1 && !S.lb.district) {
    const districts = [...new Set(data.employees.map(e => e.area).filter(Boolean))].sort();
    districts.forEach(d => {
      const opt = document.createElement('option');
      opt.value = d; opt.textContent = d;
      districtSel.appendChild(opt);
    });
  }
  if (S.lb.district && branchSel.options.length <= 1 && !S.lb.branch) {
    const branches = [...new Set(data.employees.map(e => e.branch).filter(Boolean))].sort();
    branches.forEach(b => {
      const opt = document.createElement('option');
      opt.value = b; opt.textContent = b;
      branchSel.appendChild(opt);
    });
  }
}

// ── Drill Down ───────────────────────────────────────────────────

async function drillDown(empId) {
  S.currentEmpId = empId;
  switchView('individual');
  document.getElementById('emp-search').value = empId;
  await loadEmployee(empId);
}

// ── Employee List (for autocomplete) ─────────────────────────────

async function loadEmployeeList() {
  if (!S.selectedDate) return;
  const data = await fetchJSON(`/employee/api/employees?date=${S.selectedDate}`);
  S.allEmployees = (data && data.employees) || [];
}

function showSuggestions(term) {
  const container = document.getElementById('emp-suggestions');
  if (!term || term.length < 1) {
    container.classList.remove('visible');
    return;
  }
  const lower = term.toLowerCase();
  const matches = S.allEmployees.filter(e =>
    String(e.emp_id).toLowerCase().includes(lower) ||
    (e.emp_name && e.emp_name.toLowerCase().includes(lower))
  ).slice(0, 10);

  if (!matches.length) {
    container.classList.remove('visible');
    return;
  }

  container.innerHTML = matches.map(e => `
    <div class="emp-suggestion" data-emp-id="${e.emp_id}">
      <div>
        <span class="emp-sug-id">${e.emp_id}</span>
        <span class="emp-sug-name">${e.emp_name || ''}</span>
      </div>
      <span class="emp-sug-count">${e.account_count || 0} accts</span>
    </div>
  `).join('');
  container.classList.add('visible');

  // Click handler for suggestions
  container.querySelectorAll('.emp-suggestion').forEach(el => {
    el.addEventListener('click', () => {
      const id = el.dataset.empId;
      document.getElementById('emp-search').value = id;
      container.classList.remove('visible');
      drillDown(id);
    });
  });
}

// ── Load Individual Employee ─────────────────────────────────────

async function loadEmployee(empId) {
  // Show loading state
  document.getElementById('emp-empty').style.display = 'none';

  const [perfData, accountsData] = await Promise.all([
    fetchJSON(`/employee/api/employee/${encodeURIComponent(empId)}?date=${S.selectedDate}`),
    fetchJSON(`/employee/api/employee/${encodeURIComponent(empId)}/accounts?date=${S.selectedDate}`),
  ]);

  if (perfData && perfData.error) {
    document.getElementById('emp-empty').style.display = '';
    document.querySelector('#emp-empty .empty-state-text').textContent = perfData.error;
    hideIndividualSections();
    return;
  }

  renderEmployeeInfo(perfData.employee);
  renderEmployeeKPIs(perfData.kpis);
  S.cachedSections = perfData.sections;
  renderEmployeeSections(perfData.sections);
  renderEmployeeAccounts(accountsData);

  // Show sections
  ['emp-info-card', 'emp-kpis', 'emp-toggle-section', 'emp-sections', 'emp-accounts-section'].forEach(id => {
    document.getElementById(id).style.display = '';
  });

  // Product section
  const hasProducts = perfData.sections && perfData.sections.some(s => s.products && s.products.some(p => p.demand > 0));
  document.getElementById('emp-products').style.display = hasProducts ? '' : 'none';
  if (hasProducts) renderProductTables(perfData.sections);
}

function hideIndividualSections() {
  ['emp-info-card', 'emp-kpis', 'emp-toggle-section', 'emp-sections',
   'emp-products', 'emp-accounts-section'].forEach(id => {
    document.getElementById(id).style.display = 'none';
  });
}

function resetIndividualView() {
  S.currentEmpId = null;
  S.fyMode = 'overall';
  S.cachedSections = null;
  document.getElementById('emp-search').value = '';
  hideIndividualSections();
  document.getElementById('emp-empty').style.display = '';
  document.querySelector('#emp-empty .empty-state-text').textContent =
    'Search for an employee or click a row in the Leaderboard';
  // Reset FY toggle
  document.querySelectorAll('#fy-tabs .view-tab').forEach(b => {
    b.classList.toggle('active', b.dataset.fy === 'overall');
  });
}

// ── Render: Employee Info Card ───────────────────────────────────

function renderEmployeeInfo(emp) {
  if (!emp) return;
  const initials = (emp.emp_name || emp.emp_id || '?')
    .split(/\s+/).map(w => w[0]).join('').slice(0, 2).toUpperCase();
  document.getElementById('emp-avatar').textContent = initials;
  document.getElementById('emp-name').textContent = emp.emp_name || '\u2014';
  document.getElementById('emp-id-display').textContent = `ID: ${emp.emp_id}`;
  const parts = [emp.region, emp.area, emp.branch].filter(Boolean);
  document.getElementById('emp-location').textContent = parts.join(' \u203A ') || '\u2014';
}

// ── Render: Employee KPIs ────────────────────────────────────────

function renderEmployeeKPIs(kpis) {
  const el = document.getElementById('emp-kpi-strip');
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

// ── Render: VBA-Style Sections ───────────────────────────────────

function renderEmployeeSections(sections) {
  const container = document.getElementById('emp-sections');
  if (!sections || !sections.length) {
    container.innerHTML = emptyState('No section data');
    return;
  }

  container.innerHTML = sections.map((s, idx) => {
    const isNPA = s.title === 'NPA';
    return `
    <div class="emp-section" style="animation-delay:${idx * 0.05}s">
      <div class="emp-section-title">${s.title}</div>
      <div class="emp-section-grid">
        ${renderSectionCard('Overall', s.overall, isNPA, 'overall')}
        ${renderSectionCard('FY Disbursement', s.fy, isNPA, 'fy')}
      </div>
    </div>`;
  }).join('');

  applyFYFilter();
}

function renderSectionCard(label, data, isNPA, mode) {
  if (!data) return '';
  const hidden = mode === 'fy' && S.fyMode !== 'fy' ? 'style="display:none"' : '';

  if (isNPA) {
    return `
    <div class="emp-section-card" data-mode="${mode}" ${hidden}>
      <div class="emp-section-label">${label}</div>
      <div class="product-stat"><span class="product-stat-label">Demand</span><span class="product-stat-value">${fmtN(data.demand)}</span></div>
      <div class="product-stat"><span class="product-stat-label">Activation Accts</span><span class="product-stat-value">${fmtN(data.activation_account)}</span></div>
      <div class="product-stat"><span class="product-stat-label">Activation Amt</span><span class="product-stat-value">${fmtN(data.activation_amount)}</span></div>
      <div class="product-stat"><span class="product-stat-label">Closure Accts</span><span class="product-stat-value">${fmtN(data.closure_account)}</span></div>
      <div class="product-stat"><span class="product-stat-label">Closure Amt</span><span class="product-stat-value">${fmtN(data.closure_amount)}</span></div>
    </div>`;
  }

  // Regular / Bucket sections
  const hasBalance = 'balance' in data;
  return `
  <div class="emp-section-card" data-mode="${mode}" ${hidden}>
    <div class="emp-section-label">${label}</div>
    <div class="product-stat"><span class="product-stat-label">Demand</span><span class="product-stat-value">${fmtN(data.demand)}</span></div>
    <div class="product-stat"><span class="product-stat-label">Collection</span><span class="product-stat-value">${fmtN(data.collection)}</span></div>
    <div class="product-stat"><span class="product-stat-label">${hasBalance ? 'Balance' : 'FTOD'}</span><span class="product-stat-value">${fmtN(hasBalance ? data.balance : data.ftod)}</span></div>
    <div class="product-stat"><span class="product-stat-label">Collection %</span><span class="product-stat-value ${pctCls(data.collection_pct)}">${fmt(data.collection_pct, 2)}%</span></div>
  </div>`;
}

function applyFYFilter() {
  document.querySelectorAll('.emp-section-card[data-mode="fy"]').forEach(card => {
    card.style.display = S.fyMode === 'fy' ? '' : 'none';
  });
}

// ── Render: Product Tables ───────────────────────────────────────

function renderProductTables(sections) {
  const container = document.getElementById('emp-product-tables');
  if (!sections) { container.innerHTML = ''; return; }

  let html = '';
  sections.forEach(s => {
    if (!s.products || !s.products.length) return;
    const hasAny = s.products.some(p => p.demand > 0);
    if (!hasAny) return;

    const isNPA = s.title === 'NPA';
    html += `<div class="emp-section" style="margin-top:16px;">
      <div class="emp-section-title" style="font-size:13px;">${s.title}</div>
      <div class="product-cards">`;

    s.products.forEach(p => {
      if (isNPA) {
        html += `
        <div class="product-card">
          <div class="product-card-name">${p.name}</div>
          <div class="product-stat"><span class="product-stat-label">Demand</span><span class="product-stat-value">${fmtN(p.demand)}</span></div>
          <div class="product-stat"><span class="product-stat-label">Activation Accts</span><span class="product-stat-value">${fmtN(p.activation_account)}</span></div>
          <div class="product-stat"><span class="product-stat-label">Activation Amt</span><span class="product-stat-value">${fmtN(p.activation_amount)}</span></div>
          <div class="product-stat"><span class="product-stat-label">Closure Accts</span><span class="product-stat-value">${fmtN(p.closure_account)}</span></div>
          <div class="product-stat"><span class="product-stat-label">Closure Amt</span><span class="product-stat-value">${fmtN(p.closure_amount)}</span></div>
        </div>`;
      } else {
        const hasBalance = 'balance' in p;
        html += `
        <div class="product-card">
          <div class="product-card-name">${p.name}</div>
          <div class="product-stat"><span class="product-stat-label">Demand</span><span class="product-stat-value">${fmtN(p.demand)}</span></div>
          <div class="product-stat"><span class="product-stat-label">Collection</span><span class="product-stat-value">${fmtN(p.collection)}</span></div>
          <div class="product-stat"><span class="product-stat-label">${hasBalance ? 'Balance' : 'FTOD'}</span><span class="product-stat-value">${fmtN(hasBalance ? p.balance : p.ftod)}</span></div>
          <div class="product-stat"><span class="product-stat-label">Collection %</span><span class="product-stat-value ${pctCls(p.collection_pct)}">${fmt(p.collection_pct, 2)}%</span></div>
        </div>`;
      }
    });

    html += '</div></div>';
  });

  container.innerHTML = html || emptyState('No product data');
}

// ── Render: Account Detail Table ─────────────────────────────────

function renderEmployeeAccounts(data) {
  const tbody = document.getElementById('emp-accounts-tbody');
  if (!data || !data.accounts || !data.accounts.length) {
    tbody.innerHTML = '<tr><td colspan="6" class="empty-state">No accounts</td></tr>';
    return;
  }
  tbody.innerHTML = data.accounts.map(a => `
    <tr>
      <td class="emp-id-cell">${a.account_id || '\u2014'}</td>
      <td>${a.product || '\u2014'}</td>
      <td>${fmtN(a.demand)}</td>
      <td>${fmtN(a.collection)}</td>
      <td>${a.dpd_group || '\u2014'}</td>
      <td>${a.loan_status || '\u2014'}</td>
    </tr>
  `).join('');
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

function pctCls(val) {
  if (val >= 95) return 'pct-green';
  if (val >= 80) return 'pct-yellow';
  return 'pct-red';
}

function emptyState(msg) {
  return `<div class="empty-state">${msg}</div>`;
}

function debounce(fn, ms) {
  let timer;
  return function (...args) {
    clearTimeout(timer);
    timer = setTimeout(() => fn.apply(this, args), ms);
  };
}
