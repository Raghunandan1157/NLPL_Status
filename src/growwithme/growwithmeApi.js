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

export const syncDaily = (date, file) => {
  if (file) {
    const fd = new FormData();
    fd.append("file", file);
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
