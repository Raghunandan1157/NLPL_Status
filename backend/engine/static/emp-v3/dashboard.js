/* ================================================================
   Employee Module V3 — Dashboard
   ================================================================
   Globals used from components.js:
     fmt, fmtN, fmtCompact, pctClass, ratingLabel, ratingColor,
     renderCircularProgress, renderProgressBar, renderKPICard,
     renderSectionCard, showToast
   Globals used from app.js:
     fetchJSON
   ================================================================
   API data shapes:
     performance: {
       employee: {emp_id, emp_name, region, area, branch},
       kpis: {demand, collection, collection_pct, ftod, npa_count},
       sections: [ {title, overall, fy, products}, ... ]   // LIST
     }
     accounts: {accounts: [{account_id, product, demand, collection,
       installment_amount, dpd_group, loan_status}], total: int}
   ================================================================ */

// ── Enter Dashboard ─────────────────────────────────────────────

async function enterDashboard(state) {
  document.getElementById('login-screen').style.display = 'none';
  document.getElementById('dashboard-screen').classList.add('active');

  try {
    const status = await fetchJSON('/emp-v3/api/status');

    if (!status || !status.available) {
      showToast(status?.message || 'No data available');
      return;
    }

    const sel = document.getElementById('dash-date-select');
    state.dates = status.dates || [];

    sel.innerHTML = state.dates.length
      ? state.dates.map(d =>
          '<option value="' + d.date_iso + '">' + d.date_display + '</option>'
        ).join('')
      : '<option value="">No dates</option>';

    initMonthNav(state);

    if (state.dates.length) {
      state.selectedDate = state.dates[state.dates.length - 1].date_iso;
      sel.value = state.selectedDate;
    }

    document.getElementById('dash-subtitle').textContent =
      state.empId + ' \u2022 ' + state.empName;

    await loadPerformance(state);
  } catch (err) {
    showToast(err.message || 'Failed to load dashboard');
  }
}

// ── Load Performance ────────────────────────────────────────────

async function loadPerformance(state) {
  if (!state.empId || !state.selectedDate) return;

  document.getElementById('dash-loading').style.display = '';
  document.getElementById('dash-content').style.display = 'none';

  try {
    const data = await fetchJSON(
      '/emp-v3/api/my-performance?emp_id=' +
      encodeURIComponent(state.empId) +
      '&date=' + encodeURIComponent(state.selectedDate)
    );

    state.performance = data.performance || {};
    state.accounts = data.accounts || {};

    renderDashboard(state);

    document.getElementById('dash-loading').style.display = 'none';
    document.getElementById('dash-content').style.display = '';
  } catch (err) {
    document.getElementById('dash-loading').style.display = 'none';
    showToast(err.message || 'Failed to load performance data');
  }
}

// ── Setup Dashboard Listeners ───────────────────────────────────

function setupDashboardListeners(state) {
  document.getElementById('dash-back-btn').addEventListener('click', function () {
    document.getElementById('dashboard-screen').classList.remove('active');
    document.getElementById('login-screen').style.display = '';
    state.empId = '';
    state.empName = '';
    state.branch = '';
    state.selectedDate = '';
    state.fyMode = 'overall';
    state.performance = null;
    state.accounts = {};
  });

  document.getElementById('dash-date-select').addEventListener('change', function (e) {
    state.selectedDate = e.target.value;
    loadPerformance(state);
  });

  document.querySelectorAll('.toggle-btn').forEach(function (btn) {
    btn.addEventListener('click', function () {
      document.querySelectorAll('.toggle-btn').forEach(function (b) {
        b.classList.remove('active');
      });
      btn.classList.add('active');
      state.fyMode = btn.dataset.fy;
      if (state.performance) {
        renderSections(state);
      }
    });
  });

  // Detail modal close handlers
  document.getElementById('detail-modal-close').addEventListener('click', closeDetailModal);
  document.querySelector('.detail-modal-backdrop').addEventListener('click', closeDetailModal);
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') closeDetailModal();
  });
}

// ── Master Render ───────────────────────────────────────────────

function renderDashboard(state) {
  var perf = state.performance;
  if (!perf) return;

  renderEmployeeInfo(perf.employee);
  renderPerformanceSummary(perf.kpis, state);
  renderKPIs(perf.kpis);
  renderSections(state);
  renderAccounts(state.accounts);
}

// ── Render: Employee Info ───────────────────────────────────────

function renderEmployeeInfo(emp) {
  if (!emp) return;

  var name = emp.emp_name || emp.emp_id || '?';
  var initials = name
    .split(/\s+/)
    .filter(Boolean)
    .map(function (w) { return w[0]; })
    .join('')
    .slice(0, 2)
    .toUpperCase();

  document.getElementById('emp-avatar').textContent = initials;
  document.getElementById('emp-name-display').textContent = emp.emp_name || '\u2014';
  document.getElementById('emp-id-detail').textContent = 'ID: ' + (emp.emp_id || '\u2014');

  var parts = [emp.region, emp.area, emp.branch].filter(Boolean);
  document.getElementById('emp-location').textContent = parts.join(' \u203A ') || '\u2014';
}

// ── Render: Performance Summary ─────────────────────────────────

function renderPerformanceSummary(kpis, state) {
  var pct = (kpis && kpis.collection_pct) || 0;

  var badge = document.getElementById('perf-rating-badge');
  var label = ratingLabel(pct);
  var cls = pctClass(pct);

  badge.textContent = label + ' (' + Math.round(pct) + '%)';
  badge.className = 'perf-rating-badge badge--' + cls;

  // Fetch collection trend for all dates and render line chart
  var ringWrap = document.getElementById('perf-ring-wrap');
  ringWrap.innerHTML = '<span class="loading-spinner loading-spinner--sm"></span>';

  if (state && state.empId) {
    fetchJSON('/emp-v3/api/collection-trend?emp_id=' + encodeURIComponent(state.empId))
      .then(function (data) {
        var trend = (data && data.trend) || [];
        if (trend.length) {
          ringWrap.innerHTML = renderLineChart(trend);
        } else {
          ringWrap.innerHTML = '<div class="empty-state" style="padding:20px">No trend data available</div>';
        }
      })
      .catch(function () {
        ringWrap.innerHTML = '<div class="empty-state" style="padding:20px">Failed to load trend</div>';
      });
  }
}

// ── Render: KPI Strip ───────────────────────────────────────────

function renderKPIs(kpis) {
  var strip = document.getElementById('kpi-strip');
  if (!kpis) { strip.innerHTML = ''; return; }

  var briefcaseIcon = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="7" width="20" height="14" rx="2" ry="2"/><path d="M16 7V5a2 2 0 0 0-2-2h-4a2 2 0 0 0-2 2v2"/></svg>';
  var currencyIcon = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="1" x2="12" y2="23"/><path d="M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6"/></svg>';
  var checkCircleIcon = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg>';
  var trendingUpIcon = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="23 6 13.5 15.5 8.5 10.5 1 18"/><polyline points="17 6 23 6 23 12"/></svg>';

  var pct = kpis.collection_pct || 0;

  strip.innerHTML = [
    renderKPICard({
      icon: briefcaseIcon,
      value: fmtN(kpis.demand),
      label: 'Regular Demand',
      colorClass: 'accounts'
    }),
    renderKPICard({
      icon: currencyIcon,
      value: fmtN(kpis.collection),
      label: 'Collection',
      colorClass: 'demand'
    }),
    renderKPICard({
      icon: checkCircleIcon,
      value: Math.round(pct) + '%',
      label: 'Collection %',
      colorClass: 'collected',
      pct: pct
    }),
    renderKPICard({
      icon: trendingUpIcon,
      value: fmtN(kpis.ftod),
      label: 'FTOD',
      colorClass: 'rate'
    })
  ].join('');
}

// ── Render: Sections (LIST of section objects) ──────────────────

function renderSections(state) {
  var container = document.getElementById('sections-container');
  var perf = state.performance;
  var sections = perf ? perf.sections : null;

  if (!sections || !sections.length) {
    container.innerHTML = '<div class="empty-state">No section data</div>';
    return;
  }

  container.innerHTML = sections.map(function (section, idx) {
    return renderSectionCard(section, idx, state.fyMode);
  }).join('');

  // Make section cards clickable for detail view
  container.querySelectorAll('.vba-section').forEach(function (el, idx) {
    el.style.cursor = 'pointer';
    el.addEventListener('click', function () {
      renderDetailModal(sections[idx], idx);
    });
  });
}

// ── Render: Accounts Table ──────────────────────────────────────

function renderAccounts(accountsData) {
  var countEl = document.getElementById('accounts-count');
  var tbody = document.getElementById('accounts-tbody');

  var accounts = (accountsData && accountsData.accounts) || [];
  var total = (accountsData && accountsData.total) || accounts.length;

  if (!accounts.length) {
    countEl.textContent = '(0)';
    tbody.innerHTML = '<tr><td colspan="7" class="empty-state">No accounts</td></tr>';
    return;
  }

  countEl.textContent = '(' + total + ')';

  tbody.innerHTML = accounts.map(function (a) {
    var statusClass = (a.loan_status || '').toLowerCase().replace(/\s+/g, '-');
    return '<tr>' +
      '<td class="mono">' + (a.account_id || '\u2014') + '</td>' +
      '<td>' + (a.product || '\u2014') + '</td>' +
      '<td class="mono">' + fmtN(a.demand) + '</td>' +
      '<td class="mono">' + fmtN(a.collection) + '</td>' +
      '<td class="mono">' + fmtN(a.installment_amount) + '</td>' +
      '<td>' + (a.dpd_group || '\u2014') + '</td>' +
      '<td><span class="status-badge status-badge--' + statusClass + '">' +
        (a.loan_status || '\u2014') + '</span></td>' +
      '</tr>';
  }).join('');
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

function initMonthNav(state) {
  var months = getMonthsFromDates(state.dates);
  if (!months.length) return;

  state._months = months;
  state._monthIdx = months.length - 1;
  updateMonthLabel(state);
  filterDatesByMonth(state);

  document.getElementById('month-prev').addEventListener('click', function() {
    if (state._monthIdx > 0) {
      state._monthIdx--;
      updateMonthLabel(state);
      filterDatesByMonth(state);
    }
  });

  document.getElementById('month-next').addEventListener('click', function() {
    if (state._monthIdx < state._months.length - 1) {
      state._monthIdx++;
      updateMonthLabel(state);
      filterDatesByMonth(state);
    }
  });
}

function updateMonthLabel(state) {
  var m = state._months[state._monthIdx];
  document.getElementById('month-label').textContent = m ? m.label : '—';
  document.getElementById('month-prev').disabled = state._monthIdx <= 0;
  document.getElementById('month-next').disabled = state._monthIdx >= state._months.length - 1;
}

function filterDatesByMonth(state) {
  var m = state._months[state._monthIdx];
  if (!m) return;
  var filtered = state.dates.filter(function(d) { return d.date_iso.startsWith(m.key); });
  var sel = document.getElementById('dash-date-select');
  sel.innerHTML = filtered.length
    ? filtered.map(function(d) { return '<option value="' + d.date_iso + '">' + d.date_display + '</option>'; }).join('')
    : '<option value="">No dates</option>';
  if (filtered.length) {
    var latest = filtered[filtered.length - 1].date_iso;
    sel.value = latest;
    state.selectedDate = latest;
    loadPerformance(state);
  }
}
