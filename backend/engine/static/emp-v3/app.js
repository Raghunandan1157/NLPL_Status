/* Employee Module V3 — Main Entry Point */

const STATE = {
  empId: '',
  empName: '',
  branch: '',
  dates: [],
  selectedDate: '',
  fyMode: 'overall',
  performance: null,
  accounts: [],
  allEmployees: [],
  dropdownIdx: -1
};

// Utility: fetch JSON with error handling
async function fetchJSON(url) {
  const r = await fetch(url);
  if (!r.ok) {
    const e = await r.json().catch(() => ({}));
    throw new Error(e.error || r.statusText);
  }
  return r.json();
}

// Utility: POST JSON
async function postJSON(url, body) {
  const r = await fetch(url, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(body)
  });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.error || r.statusText);
  return data;
}

// Initialize on DOM ready
document.addEventListener('DOMContentLoaded', () => {
  initLogin(STATE);        // from login.js
  setupDashboardListeners(STATE);  // from dashboard.js
  loadEmployeeList(STATE); // from login.js (async, loads in background)
});
