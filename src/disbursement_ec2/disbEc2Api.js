// Disbursement EC2 sync calls. Served by the migrated `blueprints.disbursement`
// blueprint mounted at /disbursement. Aggregates an ESAF export and pushes to
// the Coll_Db EC2 Postgres `disbursement_daily` table over ssh+psql.
import { requestJson } from "../lib/apiClient.js";

export function preview(file) {
  const fd = new FormData();
  fd.append("file", file);
  return requestJson("/disbursement/preview", { method: "POST", body: fd });
}

export function process(file, dates) {
  const fd = new FormData();
  fd.append("file", file);
  if (dates && dates.length) fd.append("dates", dates.join(","));
  return requestJson("/disbursement/process", { method: "POST", body: fd });
}
