import { apiUrl, requestJson } from "../lib/apiClient.js";

export { apiUrl };

/* ------------------------------------------------------------- file checks */
export const getBackendFilesStatus = () => requestJson("/hourly/backend-files-status");
export const checkEodDuplicate = () => requestJson("/hourly/check-eod-duplicate");
export const getEodMeetingDates = () => requestJson("/hourly/get-eod-meeting-dates");
export const deleteHourlyDaily = () => requestJson("/hourly/delete-hourly-daily", { method: "POST" });

/* ------------------------------------------------------------------ upload */
export function saveHourlyDaily(file) {
  const fd = new FormData();
  fd.append("file", file);
  return requestJson("/hourly/save-hourly-daily", { method: "POST", body: fd });
}

export function saveBackendFile(type, file) {
  const fd = new FormData();
  fd.append("type", type);
  fd.append("file", file);
  return requestJson("/hourly/save-backend-file", { method: "POST", body: fd });
}

export const cacheEodOutput = () => requestJson("/hourly/cache-eod-output", { method: "POST" });

export function cacheCollection(file) {
  const fd = new FormData();
  fd.append("file", file);
  return requestJson("/hourly/cache-collection", { method: "POST", body: fd });
}

export const cacheCollectionGdrive = () => requestJson("/hourly/cache-collection-gdrive", { method: "POST" });

/* -------------------------------------------------------------- processing */
/**
 * Run the hourly merge. The /hourly/process endpoint generates the reports
 * server-side (saving the Hourly Fast Report + detailed report as *_Latest) and
 * returns the Fast Report as a binary attachment. There is NO auto-download:
 * the user downloads from Reports & Downloads (or the "Save a copy" button). We
 * only read the response headers (filename for display, AccountID field for the
 * VBA bundle) and discard the body.
 *
 * @returns {Promise<{filename: string, accountIdField: string}>}
 */
export async function processHourly({ files, options }) {
  const fd = new FormData();
  fd.append("date", options.date);
  if (options.hour) fd.append("hour", options.hour);
  if (options.minute) fd.append("minute", options.minute);
  if (options.ampm) fd.append("ampm", options.ampm);

  if (files.eodOutput) fd.append("eodOutput", files.eodOutput);
  if (files.collection) fd.append("file", files.collection);
  if (files.hourlyDaily) fd.append("hourlyDaily", files.hourlyDaily);

  if (options.useGDriveCollection) fd.append("useGDriveCollection", "true");
  if (options.processId) fd.append("processId", options.processId);

  let response;
  try {
    response = await fetch(apiUrl("/hourly/process"), {
      method: "POST",
      body: fd,
      signal: options.signal,
    });
  } catch (e) {
    if (e?.name === "AbortError") throw e;
    throw new Error("Cannot reach the backend. Make sure it is running (npm run dev).");
  }

  // Errors come back as JSON, not a file.
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    const msg = payload.error || payload.message || `Request failed (${response.status})`;
    throw new Error(payload.suggestion ? `${msg} — ${payload.suggestion}` : msg);
  }

  // A cancelled run returns JSON (200) instead of a file — surface it as abort.
  const ctype = response.headers.get("Content-Type") || "";
  if (ctype.includes("application/json")) {
    const payload = await response.json().catch(() => ({}));
    if (payload.cancelled) {
      const err = new Error(payload.message || "Processing cancelled.");
      err.cancelled = true;
      throw err;
    }
    throw new Error(payload.error || payload.message || "Unexpected response.");
  }

  const accountIdField = response.headers.get("X-Account-ID-Field") || "";

  // Resolve a clean download filename from Content-Disposition, falling back to
  // a date-stamped name so it is clear and never overflows the UI.
  let filename = "";
  const disp = response.headers.get("Content-Disposition") || "";
  const star = /filename\*=UTF-8''([^;]+)/i.exec(disp);
  const plain = /filename="?([^";]+)"?/i.exec(disp);
  if (star) filename = decodeURIComponent(star[1]);
  else if (plain) filename = plain[1];
  if (!filename) {
    // Backend normally provides the name via Content-Disposition; this is only a
    // safety fallback. Mirror the backend rule:
    // "Hourly Report as on {date} {time}.xlsx".
    const t = (options.hour && options.minute && options.ampm)
      ? `${options.hour}-${String(options.minute).padStart(2, "0")} ${options.ampm}`
      : "";
    const d = options.date || "";
    filename = t
      ? `Hourly Report as on ${d ? `${d} ` : ""}${t}.xlsx`
      : "Hourly Report.xlsx";
  }

  // No auto-download: we only needed the headers. Discard the body so the
  // (possibly several-MB) report isn't streamed/held for nothing.
  try {
    await response.body?.cancel();
  } catch {
    /* ignore */
  }

  return { filename, accountIdField };
}

export const saveToDownloads = () => requestJson("/hourly/save-to-downloads", { method: "POST" });

/* ------------------------------------------------------------- fast report */
export function generateFastReport({ targetDate, hour, minute, ampm }) {
  const fd = new FormData();
  fd.append("targetDate", targetDate);
  if (hour) fd.append("hour", hour);
  if (minute) fd.append("minute", minute);
  if (ampm) fd.append("ampm", ampm);
  
  return requestJson("/hourly/generate-fast-report", { method: "POST", body: fd });
}

export const downloadFastReportUrl = (date) =>
  apiUrl(`/hourly/download-fast-report?date=${encodeURIComponent(date)}`);

/* ------------------------------------------------------------- google drive */
export const getGDriveConfig = () => requestJson("/hourly/gdrive-config");

export function scanGDriveCollection(folderUrl) {
  return requestJson("/hourly/gdrive-scan-collection", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ folder_url: folderUrl }),
  });
}

export function downloadGDriveFile(fileId, fileName, target) {
  return requestJson("/hourly/gdrive-download", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ file_id: fileId, file_name: fileName, target }),
  });
}

/* ------------------------------------------------------------- local bundles */
export const getBundles = () => requestJson("/hourly/bundle-list");

export function useBundle({ bundlePath, useEodOutput, useHourlyDaily, eodOutputName, hourlyDailyName }) {
  return requestJson("/hourly/bundle-use", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      bundle_path: bundlePath,
      use_eod_output: useEodOutput,
      use_hourly_daily: useHourlyDaily,
      eod_output_name: eodOutputName,
      hourly_daily_name: hourlyDailyName,
    }),
  });
}

export function saveBundleToServer({ action, formattedDatetime, dateOnly, accountIdField }) {
  return requestJson("/hourly/save-bundle-to-server", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      action,
      formatted_datetime: formattedDatetime,
      date_only: dateOnly,
      account_id_field: accountIdField,
    }),
  });
}

/* ------------------------------------------------------------- vba scripts */
export const getVbaBundles = () => requestJson("/hourly/vba-runner/bundles");

export function runVbaScript(bundlePath, scriptType) {
  return requestJson("/hourly/vba-runner/run", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ bundle_path: bundlePath, script: scriptType }),
  });
}

/* ---------------------------------------------------------------- whatsapp */
export const getWhatsAppContacts = () => requestJson("/hourly/whatsapp-contacts");

export function saveWhatsAppContacts(contacts) {
  return requestJson("/hourly/whatsapp-contacts", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ contacts }),
  });
}

export const openWhatsApp = () => requestJson("/hourly/whatsapp-open", { method: "POST" });

export function sendWhatsApp(bundlePath, filename) {
  return requestJson("/hourly/whatsapp-send", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ bundle_path: bundlePath, filename }),
  });
}

/* ---------------------------------------------------------- report archive */
export function snapshotReports(date, timeVal) {
  return requestJson("/api/hourly/snapshot-reports", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ date, time: timeVal }),
  });
}

export const reportArchiveList = () => requestJson("/api/hourly/report-archive");

export const reportArchiveFileUrl = (date, run, type) =>
  apiUrl(
    `/api/hourly/report-archive/file?date=${encodeURIComponent(date)}` +
      `&run=${encodeURIComponent(run)}&type=${encodeURIComponent(type)}`
  );
