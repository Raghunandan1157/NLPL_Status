// All EOD backend calls live here. Endpoints are served by the reused
// `blueprints.eod` blueprint mounted at /eod on the Flask backend.
import { apiUrl, postSseStream, requestJson } from "../lib/apiClient.js";

export { apiUrl };

/* ----------------------------------------------------------------- status */
export const getHealth = () => requestJson("/api/health");
export const getBackendFilesStatus = () => requestJson("/eod/backend-files-status");
export const getDbStatus = () => requestJson("/eod/db-status");
export const getLastCache = () => requestJson("/eod/last-cache");
export const getCacheHistory = () => requestJson("/eod/cache-history");

/* --------------------------------------------------------- upload / cache */
export function cacheFile(type, file) {
  const fd = new FormData();
  fd.append("type", type);
  fd.append("file", file);
  return requestJson("/eod/cache-file", { method: "POST", body: fd });
}

export function saveBackendFile(type, file, { ingest = true } = {}) {
  // Raw multipart File upload (no base64, no client-side read). When ingest is
  // false the backend only saves the file — DuckDB ingestion is deferred to an
  // explicit Save to DB / Sync (keeps the upload fast).
  const fd = new FormData();
  fd.append("type", type);
  fd.append("file", file);
  if (!ingest) fd.append("ingest", "false");
  return requestJson("/eod/save-backend-file", { method: "POST", body: fd });
}

export function ingestSingleToDb(type) {
  return requestJson("/eod/ingest-single-to-db", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ type }),
  });
}

export function ingestToDb() {
  return requestJson("/eod/ingest-to-db", { method: "POST" });
}

export const clearDb = () => requestJson("/eod/clear-db", { method: "POST" });

/* ------------------------------------------------------------- processing */
export function processEod({ files, options }) {
  const fd = new FormData();
  if (files.par) fd.append("par", files.par);
  if (files.collection) fd.append("collection", files.collection);
  if (files.demand) fd.append("demand", files.demand);
  fd.append("targetDate", options.targetDate);
  fd.append("useBackendDemand", String(options.useBackendDemand));
  fd.append("useLastCache", String(options.useLastCache));
  fd.append("cachePar", String(options.cachePar));
  fd.append("cacheCollection", String(options.cacheCollection));
  fd.append("autoFixSheets", String(options.autoFixSheets));
  return requestJson("/eod/process", { method: "POST", body: fd });
}

/** SSE log stream emitted by the backend during processing. */
export const eventsUrl = () => apiUrl("/eod/events");

/* ------------------------------------------------------------ extra reports */
export const generateEmployeeReport = () =>
  requestJson("/eod/generate-employee-report", { method: "POST" });

export function generateDailyHourlyReport(targetDate) {
  return requestJson("/eod/generate-daily-hourly-report", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ targetDate }),
  });
}

export function syncToDashboard() {
  return requestJson("/eod/sync-to-dashboard", { method: "POST" });
}

/* --------------------------------------------------------------- downloads */
export const DOWNLOADS = [
  { key: "output", label: "Regular Demand vs Collection", path: "/eod/download-output" },
  { key: "report", label: "EOD Report", path: "/eod/download-report" },
  { key: "employee", label: "Employee Report", path: "/eod/download-employee-report" },
  { key: "accounts", label: "Account Report", path: "/eod/download-employee-report-accounts" },
  { key: "daily", label: "Daily Report", path: "/eod/download-daily-report" },
  { key: "hourly", label: "Hourly Report", path: "/eod/download-hourly-report" },
];

/* ------------------------------------------------------------------- email */
export const reportSheetNames = () => requestJson("/eod/report-sheet-names");
export const reportSheetData = (sheet) =>
  requestJson(`/eod/report-sheet-data?sheet=${encodeURIComponent(sheet)}`);
export const emailConfigGet = () => requestJson("/eod/email-config");
export function emailConfigSave({ cards, conns }) {
  return requestJson("/eod/email-config", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ cards, conns }),
  });
}
export const autoAssignBranches = () => requestJson("/eod/auto-assign-branches", { method: "POST" });
export const precomputeEmailBody = () => requestJson("/eod/precompute-email-body", { method: "POST" });

/** Stream a batch email send. recipients: [{email, sheets[], mode}]. */
export function sendBatchEmail(recipients, onEvent, signal) {
  return postSseStream("/eod/send-batch-email", { recipients }, onEvent, { signal });
}

export function sendSheetEmail(sheets, recipient) {
  return requestJson("/eod/send-sheet-email", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ sheets, recipient }),
  });
}

/* ------------------------------------------------------- gmail login (app) */
export const emailGetConfig = () => requestJson("/api/email/config");
export function emailLogin({ user, appPassword, host, port }) {
  return requestJson("/api/email/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ user, appPassword, host, port }),
  });
}
export const emailLogout = () => requestJson("/api/email/logout", { method: "POST" });

/* ------------------------------------------------- per-date report archive */
export function snapshotReports(date) {
  return requestJson("/api/eod/snapshot-reports", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ date }),
  });
}
export const reportArchiveList = () => requestJson("/api/eod/report-archive");
export const reportArchiveFileUrl = (date, run, type) =>
  apiUrl(
    `/api/eod/report-archive/file?date=${encodeURIComponent(date)}` +
      `&run=${encodeURIComponent(run)}&type=${encodeURIComponent(type)}`
  );

/* ---------------------------------------------------------------- whatsapp */
export const whatsappContactsGet = () => requestJson("/eod/whatsapp-contacts");
export function whatsappContactsSave(contacts) {
  return requestJson("/eod/whatsapp-contacts", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ contacts }),
  });
}
export const whatsappOpen = () => requestJson("/eod/whatsapp-open", { method: "POST" });
export function whatsappSend(bundlePath, filename) {
  return requestJson("/eod/whatsapp-send", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ bundle_path: bundlePath, filename }),
  });
}
