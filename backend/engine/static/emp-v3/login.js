/* ================================================================
   Login + Autocomplete — emp-v3
   ================================================================ */

(function () {

  // ── Internal helpers (closure-scoped) ──────────────────────────

  function doLogin(state, empId) {
    fetch('/emp-v3/api/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ emp_id: empId }),
    })
      .then(function (res) { return res.json(); })
      .then(function (data) {
        if (data.error) {
          showToast(data.error);
          return;
        }

        if (data.role === 'ceo') {
          window.location.href = '/employee/';
          return;
        }

        // Employee login success
        state.empId = data.emp_id || empId;

        var match = state.allEmployees.find(function (e) {
          return String(e.emp_id) === String(state.empId);
        });

        state.empName = match ? match.emp_name : '';
        state.branch  = match ? match.branch  : '';

        enterDashboard(state);
      })
      .catch(function (err) {
        console.error('Login error:', err);
        showToast(err.message || 'Login failed');
      });
  }

  function showDropdown(items, state) {
    var dropdown = document.getElementById('emp-dropdown');

    if (!items.length) {
      dropdown.innerHTML = '<div class="emp-dropdown-empty">No employees found</div>';
      dropdown.classList.add('visible');
      return;
    }

    dropdown.innerHTML = items.map(function (e) {
      return '<div class="emp-dropdown-item" data-emp-id="' + e.emp_id + '">' +
        '<span class="emp-dd-id">' + e.emp_id + '</span>' +
        '<span class="emp-dd-name">' + (e.emp_name || '') + '</span>' +
        '<span class="emp-dd-branch">' + (e.branch || '') + '</span>' +
      '</div>';
    }).join('');

    dropdown.classList.add('visible');

    dropdown.querySelectorAll('.emp-dropdown-item').forEach(function (el) {
      el.addEventListener('click', function () {
        selectItem(el.dataset.empId, state);
      });
    });
  }

  function filterEmployees(term, allEmployees) {
    var lower = (term || '').toLowerCase();
    if (!lower) return allEmployees.slice(0, 15);

    return allEmployees.filter(function (e) {
      return String(e.emp_id).toLowerCase().indexOf(lower) !== -1 ||
             (e.emp_name && e.emp_name.toLowerCase().indexOf(lower) !== -1);
    }).slice(0, 15);
  }

  function hideDropdown(state) {
    document.getElementById('emp-dropdown').classList.remove('visible');
    state.dropdownIdx = -1;
  }

  function selectItem(empId, state) {
    document.getElementById('emp-id-input').value = empId;
    hideDropdown(state);
    doLogin(state, empId);
  }

  function highlightItem(items, idx) {
    items.forEach(function (it, i) {
      it.classList.toggle('active', i === idx);
    });
    if (items[idx]) {
      items[idx].scrollIntoView({ block: 'nearest' });
    }
  }

  // ── Global: initLogin ──────────────────────────────────────────

  window.initLogin = function initLogin(state) {
    var form    = document.getElementById('login-form');
    var input   = document.getElementById('emp-id-input');
    var ceoBtn  = document.getElementById('ceo-btn');

    // Form submit
    form.addEventListener('submit', function (e) {
      e.preventDefault();
      var empId = input.value.trim();
      if (!empId) return;
      doLogin(state, empId);
    });

    // Autocomplete: filter on each keystroke
    input.addEventListener('input', function () {
      var term = input.value.trim();
      state.dropdownIdx = -1;
      if (!term) { hideDropdown(state); return; }
      var matches = filterEmployees(term, state.allEmployees);
      showDropdown(matches, state);
    });

    // Keyboard navigation
    input.addEventListener('keydown', function (e) {
      var dropdown = document.getElementById('emp-dropdown');
      if (!dropdown.classList.contains('visible')) return;
      var items = dropdown.querySelectorAll('.emp-dropdown-item');
      if (!items.length) return;

      if (e.key === 'ArrowDown') {
        e.preventDefault();
        state.dropdownIdx = Math.min(state.dropdownIdx + 1, items.length - 1);
        highlightItem(items, state.dropdownIdx);
      } else if (e.key === 'ArrowUp') {
        e.preventDefault();
        state.dropdownIdx = Math.max(state.dropdownIdx - 1, 0);
        highlightItem(items, state.dropdownIdx);
      } else if (e.key === 'Enter' && state.dropdownIdx >= 0) {
        e.preventDefault();
        var selected = items[state.dropdownIdx];
        if (selected) selectItem(selected.dataset.empId, state);
      } else if (e.key === 'Escape') {
        hideDropdown(state);
      }
    });

    // Close dropdown on outside click
    document.addEventListener('click', function (e) {
      if (!e.target.closest('#input-wrap')) hideDropdown(state);
    });

    // CEO button
    ceoBtn.addEventListener('click', function () {
      window.location.href = '/employee/';
    });
  };

  // ── Global: loadEmployeeList ───────────────────────────────────

  window.loadEmployeeList = function loadEmployeeList(state) {
    return fetch('/emp-v3/api/employees')
      .then(function (res) { return res.json(); })
      .then(function (data) {
        state.allEmployees = (data && data.employees) || [];
      })
      .catch(function (err) {
        console.error('Failed to load employee list:', err);
        state.allEmployees = [];
      });
  };

})();
