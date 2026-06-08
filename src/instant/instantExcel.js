// Build a downloadable .xlsx from an Instant report_data structure (one sheet
// per section, every level table stacked). Reuses SheetJS (already a dep).
import * as XLSX from "xlsx";

// Row-object key order per table type (see ReportView.jsx) — the backend sorts
// JSON keys alphabetically, so we map values by key, not Object.values() order.
const KEYS_BY_TYPE = {
  regular: ["name", "demand", "collection", "ftod", "collection_pct"],
  bucket: ["name", "demand", "collection", "balance", "collection_pct"],
  npa: ["name", "demand", "activation_account", "activation_amount", "closure_account", "closure_amount"],
};

function tableToAOA(table) {
  const keys = KEYS_BY_TYPE[table.type];
  const aligned = keys && keys.length === (table.headers || []).length;
  const aoa = [];
  aoa.push([table.level || "", ...(table.headers || []).slice(1)]);
  for (const row of table.rows || []) {
    aoa.push(aligned ? keys.map((k) => row[k]) : [row.name, ...Object.keys(row).filter((k) => k !== "name").map((k) => row[k])]);
  }
  if (table.grand_total) {
    const gt = table.grand_total;
    aoa.push(["Grand Total", ...(aligned ? keys.slice(1).map((k) => gt[k]) : Object.values(gt))]);
  }
  aoa.push([]); // spacer
  return aoa;
}

function safeSheetName(name, used) {
  let base = String(name || "Sheet").replace(/[\\/?*[\]:]/g, " ").slice(0, 28).trim() || "Sheet";
  let candidate = base;
  let i = 2;
  while (used.has(candidate)) {
    candidate = `${base} ${i++}`.slice(0, 31);
  }
  used.add(candidate);
  return candidate;
}

export function reportToWorkbookBlob(report) {
  const wb = XLSX.utils.book_new();
  const used = new Set();
  for (const section of report.sections || []) {
    const aoa = [[section.title || "Section"], []];
    for (const table of section.tables || []) aoa.push(...tableToAOA(table));
    const ws = XLSX.utils.aoa_to_sheet(aoa);
    XLSX.utils.book_append_sheet(wb, ws, safeSheetName(section.title, used));
  }
  if (!(report.sections || []).length) {
    XLSX.utils.book_append_sheet(wb, XLSX.utils.aoa_to_sheet([["No data"]]), "Report");
  }
  const out = XLSX.write(wb, { bookType: "xlsx", type: "array" });
  return new Blob([out], {
    type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  });
}

export function downloadReport(report, filename) {
  const blob = reportToWorkbookBlob(report);
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename || "Instant Report.xlsx";
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 4000);
}
