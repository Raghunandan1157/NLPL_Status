// On-Date Report backend calls. Served by the migrated `blueprints.ondate`
// blueprint mounted at /ondate. Reports accumulate per month under the engine's
// REPORTS_DIR, so this module uses its own native listing/download endpoints.
import { apiUrl, requestJson } from "../lib/apiClient.js";

const MONTHS = [
  "January", "February", "March", "April", "May", "June",
  "July", "August", "September", "October", "November", "December",
];

/** Split a YYYY-MM-DD string into the {month, year} the backend expects. */
export function monthYearFromDate(dateIso) {
  const [y, m] = String(dateIso || "").split("-");
  const idx = parseInt(m, 10) - 1;
  return { month: MONTHS[idx] || "", year: y || "" };
}

export async function extractReport({ file, date }) {
  const { month, year } = monthYearFromDate(date);
  const fd = new FormData();
  fd.append("file", file);
  fd.append("date", date);
  fd.append("month", month);
  fd.append("year", year);
  return requestJson("/ondate/extract-ondate-report", { method: "POST", body: fd });
}

export function checkReport(date) {
  return requestJson("/ondate/check-step2-report", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ date }),
  });
}

export const listReports = () => requestJson("/ondate/get-step2-reports");

export const downloadReportUrl = (relPath) =>
  apiUrl(`/ondate/download-step2-report/${String(relPath).split("/").map(encodeURIComponent).join("/")}`);
