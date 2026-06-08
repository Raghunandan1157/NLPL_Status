// Supabase sync calls. Served by the migrated `blueprints.supabase_sync`
// blueprint mounted at /supabase. Mirrors EOD daily / Quick hourly / disbursement
// data into the Supabase Grow_With_Me staging tables.
import { requestJson } from "../lib/apiClient.js";

export const checkDate = (date) =>
  requestJson(`/supabase/check-date?date=${encodeURIComponent(date)}`);
export const syncDaily = (date) =>
  requestJson("/supabase/sync-daily", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ date }),
  });

export const checkHourly = () => requestJson("/supabase/check-hourly");
export const syncHourly = () => requestJson("/supabase/sync-hourly", { method: "POST" });

export const checkDisbursement = (dates) =>
  requestJson(`/supabase/check-disbursement?dates=${encodeURIComponent(dates.join(","))}`);
export function syncDisbursement(file, dates) {
  const fd = new FormData();
  fd.append("file", file);
  if (dates && dates.length) fd.append("dates", dates.join(","));
  return requestJson("/supabase/sync-disbursement", { method: "POST", body: fd });
}
