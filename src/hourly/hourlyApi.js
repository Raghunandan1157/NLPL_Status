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
export function processHourly({ files, options }) {
  const fd = new FormData();
  fd.append("date", options.date);
  if (options.hour) fd.append("hour", options.hour);
  if (options.minute) fd.append("minute", options.minute);
  if (options.ampm) fd.append("ampm", options.ampm);
  
  if (files.eodOutput) fd.append("eodOutput", files.eodOutput);
  if (files.collection) fd.append("file", files.collection);
  if (files.hourlyDaily) fd.append("hourlyDaily", files.hourlyDaily);
  
  if (options.useGDriveCollection) fd.append("useGDriveCollection", "true");
  
  return requestJson("/hourly/process", { method: "POST", body: fd });
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
