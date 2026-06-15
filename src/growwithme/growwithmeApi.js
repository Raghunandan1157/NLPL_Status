// GrowwithmeDB sync calls. Served by the `growwithme_sync` blueprint mounted at
// /growwithme. Pushes EOD daily / Quick hourly / disbursement / portfolio data
// into the GrowwithmeDB API (AWS EC2, MariaDB Growwithme_NEWDB),
// with whole-scope delete-then-insert override semantics.
import { requestJson } from "../lib/apiClient.js";

export const ping = () => requestJson("/growwithme/ping");

export const syncDaily = (date) =>
  requestJson("/growwithme/sync-daily", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ date }),
  });

export const syncHourly = (date, periodHour) =>
  requestJson("/growwithme/sync-hourly", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ date, period_hour: periodHour }),
  });

export function syncDisbursement(file, dates) {
  const fd = new FormData();
  fd.append("file", file);
  if (dates && dates.length) fd.append("dates", dates.join(","));
  return requestJson("/growwithme/sync-disbursement", { method: "POST", body: fd });
}

// Pushes the latest Month-End Employee Report's POS sheet into
// GrowwithmeDB.portfolio_* (branch+product+month). periodMonth = "YYYY-MM".
export const syncPortfolio = (periodMonth) =>
  requestJson("/growwithme/sync-portfolio", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ period_month: periodMonth }),
  });
