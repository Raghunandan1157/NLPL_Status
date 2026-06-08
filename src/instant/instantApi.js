// Instant Report backend calls. Served by the migrated `blueprints.instant`
// blueprint mounted at /instant. Reports are pivot summaries (JSON) cached per
// date; monthly Demand/Last-Month-PAR backends are required to process.
import { requestJson } from "../lib/apiClient.js";

/* --------------------------------------------------------------- status */
export const backendStatus = () => requestJson("/instant/backend-status");
export const monthlyStatus = () => requestJson("/instant/monthly-backend-status");
export const historyDates = () => requestJson("/instant/history-dates");

/* ----------------------------------------------------------- processing */
export function cacheFile(type, file) {
  const fd = new FormData();
  fd.append("type", type);
  fd.append("file", file);
  return requestJson("/instant/cache-file", { method: "POST", body: fd });
}

export function processInstant({ par, collection, targetDate }) {
  const fd = new FormData();
  fd.append("par", par);
  fd.append("collection", collection);
  if (targetDate) fd.append("targetDate", targetDate);
  return requestJson("/instant/process", { method: "POST", body: fd });
}

export function generateFromCache(date) {
  return requestJson("/instant/generate-from-cache", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ date }),
  });
}

/* ------------------------------------------------------------- history */
export const deleteCache = (date) =>
  requestJson(`/instant/delete-cache/${encodeURIComponent(date)}`, { method: "DELETE" });
export const deleteAllCache = () =>
  requestJson("/instant/delete-all-cache", { method: "DELETE" });

/* ------------------------------------------------- monthly backend data */
export function monthlyUpload(month, type, file) {
  const fd = new FormData();
  fd.append("month", month);
  fd.append("type", type);
  fd.append("file", file);
  return requestJson("/instant/monthly-backend-upload", { method: "POST", body: fd });
}
export const monthlyDelete = (month, type) =>
  requestJson(
    `/instant/monthly-backend-delete?month=${encodeURIComponent(month)}&type=${encodeURIComponent(type)}`,
    { method: "DELETE" }
  );
