// Month-End Report backend calls. Served by the migrated
// `blueprints.quick_month_end` blueprint mounted at /quick-month-end.
import { apiUrl, requestJson } from "../lib/apiClient.js";
import {
  moduleReportArchiveFileUrl,
  moduleReportArchiveList,
  moduleSnapshotReports,
} from "../shared/centralApi.js";

export const MODULE = "quick_month_end";

const FILE_KEYS = ["demand", "lastMonthPar", "par", "collection"];

function buildForm(files, extra = {}) {
  const fd = new FormData();
  FILE_KEYS.forEach((k) => {
    if (files[k]) fd.append(k, files[k]);
  });
  Object.entries(extra).forEach(([k, v]) => fd.append(k, v));
  return fd;
}

/* ------------------------------------------------------------ processing */
export function checkColumns(files) {
  return requestJson("/quick-month-end/check-columns", {
    method: "POST",
    body: buildForm(files),
  });
}

export function processQme({ files, uploadToDatabase }) {
  return requestJson("/quick-month-end/process", {
    method: "POST",
    body: buildForm(files, { uploadToDatabase: String(!!uploadToDatabase) }),
  });
}

export const saveToDownloads = () =>
  requestJson("/quick-month-end/save-to-downloads", { method: "POST" });

export const getDashboardMonths = () => requestJson("/quick-month-end/dashboard-months");

export function syncToDashboard(month) {
  return requestJson("/quick-month-end/sync-to-dashboard", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ month }),
  });
}

/* -------------------------------------------------------------- downloads */
export const DOWNLOADS = [
  { key: "eod", label: "Regular Demand vs Collection", path: "/quick-month-end/download-output" },
  { key: "report", label: "EOD Report", path: "/quick-month-end/download-report" },
  { key: "employee", label: "Month-End Employee Report", path: "/quick-month-end/download-employee-report" },
];
export const downloadUrl = (path) => apiUrl(path);

/* ----------------------------------------------------- report archive */
export const snapshotReports = (date, time) => moduleSnapshotReports(MODULE, date, time);
export const reportArchiveList = () => moduleReportArchiveList(MODULE);
export const reportArchiveFileUrl = (date, run, type) =>
  moduleReportArchiveFileUrl(MODULE, date, run, type);
