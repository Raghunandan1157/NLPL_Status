// Quick Report backend calls. Served by the migrated `blueprints.quick`
// blueprint mounted at /quick. Live progress streams on the shared SSE channel
// (/eod/events) because the engine logs every module's steps to the root logger.
import { API_BASE, requestJson } from "../lib/apiClient.js";
import {
  moduleReportArchiveFileUrl,
  moduleReportArchiveList,
  moduleSnapshotReports,
} from "../shared/centralApi.js";

export const MODULE = "quick";

/* --------------------------------------------------------------- status */
export const getBackendFilesStatus = () => requestJson("/quick/backend-files-status");

/* ------------------------------------------------------------ processing */
/**
 * POST the 3 files + date/time. The endpoint streams the finished xlsx back as
 * an attachment, so we resolve to { blob, filename } for a client-side download.
 * On error the backend returns JSON, which we surface as an Error.
 */
export async function processQuick({ files, date, hour, minute, ampm }) {
  const fd = new FormData();
  fd.append("par", files.par);
  fd.append("collection", files.collection);
  fd.append("collectionReport", files.collectionReport);
  if (date) fd.append("date", date);
  if (hour) fd.append("hour", hour);
  if (minute) fd.append("minute", minute);
  if (ampm) fd.append("ampm", ampm);

  let res;
  try {
    res = await fetch(`${API_BASE}/quick/process`, { method: "POST", body: fd });
  } catch {
    throw new Error("Cannot reach the backend. Make sure it is running (npm run dev).");
  }
  if (!res.ok) {
    const payload = await res.json().catch(() => ({}));
    const msg = payload.error || payload.message || `Request failed (${res.status})`;
    throw new Error(payload.suggestion ? `${msg} — ${payload.suggestion}` : msg);
  }
  const blob = await res.blob();
  const cd = res.headers.get("Content-Disposition") || "";
  const m = /filename\*?=(?:UTF-8'')?"?([^";]+)"?/i.exec(cd);
  const filename = m ? decodeURIComponent(m[1]) : "Quick Report.xlsx";
  return { blob, filename };
}

export const syncToDashboard = () =>
  requestJson("/quick/sync-to-dashboard", { method: "POST" });

export const saveToDownloads = () =>
  requestJson("/quick/save-to-downloads", { method: "POST" });

/* ----------------------------------------------------- report archive */
export const snapshotReports = (date, time) => moduleSnapshotReports(MODULE, date, time);
export const reportArchiveList = () => moduleReportArchiveList(MODULE);
export const reportArchiveFileUrl = (date, run, type) =>
  moduleReportArchiveFileUrl(MODULE, date, run, type);
