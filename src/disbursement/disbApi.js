// Disbursement Report backend calls. Served by the migrated `blueprints.db`
// blueprint mounted at /db. Core processing runs client-side (dbProcessing.js);
// the backend stores the reference lookup, receives the finished file, and
// handles email / VBA / bundle / archive.
import { apiUrl, requestJson } from "../lib/apiClient.js";
import {
  moduleReportArchiveFileUrl,
  moduleReportArchiveList,
  moduleSnapshotReports,
} from "../shared/centralApi.js";

export const MODULE = "db";

/* ------------------------------------------------ backend reference file */
export const backendStatus = () => requestJson("/db/backend-status");
export const backendData = () => requestJson("/db/backend-data");
export function uploadBackend(file) {
  const fd = new FormData();
  fd.append("file", file);
  return requestJson("/db/upload-backend", { method: "POST", body: fd });
}

/* ----------------------------------------------------- processed output */
export function uploadProcessed(blob, name, targetDate) {
  const fd = new FormData();
  fd.append("file", new File([blob], name, { type: blob.type }));
  if (targetDate) fd.append("target_date", targetDate);
  return requestJson("/db/upload-processed", { method: "POST", body: fd });
}
export const downloadOutputUrl = () => apiUrl("/db/download-output");

/* ------------------------------------------------------------- delivery */
export function saveBundle(action) {
  return requestJson("/db/save-bundle-to-server", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(action ? { action } : {}),
  });
}
export const vbaBundles = () => requestJson("/db/vba-runner/bundles");
export function runVba(bundlePath) {
  return requestJson("/db/vba-runner/run", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ bundle_path: bundlePath, script: "daily" }),
  });
}
export function sendEmail(recipients, subject) {
  return requestJson("/db/send-email", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ recipients, subject }),
  });
}
export const syncToDashboard = () => requestJson("/db/sync-to-dashboard", { method: "POST" });

/* ----------------------------------------------------- report archive */
export const snapshotReports = (date, time) => moduleSnapshotReports(MODULE, date, time);
export const reportArchiveList = () => moduleReportArchiveList(MODULE);
export const reportArchiveFileUrl = (date, run, type) =>
  moduleReportArchiveFileUrl(MODULE, date, run, type);
