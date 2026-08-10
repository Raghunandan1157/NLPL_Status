// GrowwithmeDB sync calls. Served by the `growwithme_sync` blueprint mounted at
// /growwithme. Pushes EOD daily / Quick hourly / disbursement / portfolio data
// into the GrowwithmeDB API (AWS EC2, MariaDB Growwithme_NEWDB),
// with whole-scope delete-then-insert override semantics.
import { requestJson } from "../lib/apiClient.js";

export const ping = () => requestJson("/growwithme/ping");

// Each sync takes the latest generated report by default, OR an optional `file`
// the user uploads (their own report). With a file we send multipart; without
// one, JSON — the backend then falls back to the latest generated report.
const jsonPost = (path, payload) =>
  requestJson(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });

// `file`        — the EOD / Daily Collection Report (collection figures).
// `channelRows` — Mode of Collection, ALREADY AGGREGATED in the browser to
//                 officer × channel totals (see aggregateChannelFile). Sending the
//                 aggregate rather than the 5 MB workbook keeps the upload ~440 KB.
//                 The backend still accepts a raw `channel_file` workbook as a
//                 fallback, so both paths work.
// `amountFile`  — "Regular Demand vs Collection" (the EOD output workbook). Its
//                 hidden `_precomp` sheet is the ONLY source of rupee amounts:
//                 the Daily Collection Report carries account counts only. Sent
//                 whole, since the sheet is small and read server-side. Omit it
//                 and the backend uses the EOD run archived for `date`.
// Any, all, or none may be supplied. With none, the backend falls back to the
// latest generated report (and simply skips the channel step).
export const syncDaily = (date, file, channelRows, amountFile) => {
  if (file || channelRows || amountFile) {
    const fd = new FormData();
    if (file) fd.append("file", file);
    if (channelRows) fd.append("channel_rows", JSON.stringify(channelRows));
    if (amountFile) fd.append("amount_file", amountFile);
    fd.append("date", date);
    return requestJson("/growwithme/sync-daily", { method: "POST", body: fd });
  }
  return jsonPost("/growwithme/sync-daily", { date });
};

export const syncHourly = (date, periodHour, file) => {
  if (file) {
    const fd = new FormData();
    fd.append("file", file);
    if (date) fd.append("date", date);
    if (periodHour != null) fd.append("period_hour", periodHour);
    return requestJson("/growwithme/sync-hourly", { method: "POST", body: fd });
  }
  return jsonPost("/growwithme/sync-hourly", { date, period_hour: periodHour });
};

export function syncDisbursement(file, dates) {
  const fd = new FormData();
  fd.append("file", file);
  if (dates && dates.length) fd.append("dates", dates.join(","));
  return requestJson("/growwithme/sync-disbursement", { method: "POST", body: fd });
}

// Pushes an HR/staff master ("Working" sheet) into GrowwithmeDB — refreshes name,
// phone, joining date, DOB and reporting manager. DETAILS-ONLY (never touches
// branch/role/hierarchy); upsert, never deletes; re-running is safe. Needs a file.
export function syncStaff(file) {
  const fd = new FormData();
  fd.append("file", file);
  return requestJson("/growwithme/sync-staff", { method: "POST", body: fd });
}

// Scope-target picker options (region/division/area/branch name lists).
export const scopeOptions = () => requestJson("/growwithme/scope-options");

// Employee editor: fetch one employee's details + current scope by emp_id …
export const fetchEmployee = (empId) =>
  requestJson(`/growwithme/employee/${encodeURIComponent(String(empId).trim())}`);

// … and save the edited details + scope (full onboard on a single row).
export const saveEmployee = (row) => jsonPost("/growwithme/employee", row);

// Pushes the Month-End report's POS sheet into GrowwithmeDB.portfolio_*
// (branch+product+month). periodMonth = "YYYY-MM". Optional `file` = a custom
// Month-End report to upload instead of the latest generated one.
export const syncPortfolio = (periodMonth, file) => {
  if (file) {
    const fd = new FormData();
    fd.append("file", file);
    fd.append("period_month", periodMonth);
    return requestJson("/growwithme/sync-portfolio", { method: "POST", body: fd });
  }
  return jsonPost("/growwithme/sync-portfolio", { period_month: periodMonth });
};
