// Client-side Disbursement Report processing — ported verbatim from the source
// module's static/db/app.js so the output (Product Name, VLOOKUP enrichment,
// Employee ID, single/2-sheet workbook) is byte-for-byte equivalent. The only
// changes are turning DOM side-effects into return values.
import * as XLSX from "xlsx";

// ── Product ID → Name Mapping ────────────────
export const PRODUCT_MAP = {
  604001: "FIG",
  104207: "IGL & JLG",
  204207: "IGL & JLG",
  234008: "IGL & JLG",
  274203: "IGL & JLG",
  264203: "IGL & JLG",
  81402: "VVY",
};

function formatDateDDMMYYYY(d) {
  const dd = String(d.getDate()).padStart(2, "0");
  const mm = String(d.getMonth() + 1).padStart(2, "0");
  const yyyy = d.getFullYear();
  return `${dd}-${mm}-${yyyy}`;
}

function parseDDMMYYYY(str) {
  const parts = str.split("-");
  return new Date(Number(parts[2]), Number(parts[1]) - 1, Number(parts[0]));
}

const dateStrPattern = /^\d{1,2}-[A-Za-z]{3}-\d{4}$/;

function formatDatesInAOA(aoa) {
  for (let r = 0; r < aoa.length; r++) {
    for (let c = 0; c < aoa[r].length; c++) {
      const cell = aoa[r][c];
      if (cell instanceof Date) {
        aoa[r][c] = formatDateDDMMYYYY(cell);
      } else if (typeof cell === "string" && dateStrPattern.test(cell.trim())) {
        const parsed = new Date(cell);
        if (!isNaN(parsed.getTime())) aoa[r][c] = formatDateDDMMYYYY(parsed);
      }
    }
  }
  return aoa;
}

function deepCopyAOA(aoa) {
  return aoa.map((row) => row.map((cell) => (cell instanceof Date ? new Date(cell.getTime()) : cell)));
}

/* ── Officer ID → Employee ID helpers ── */
function sanitizeOfficerRaw(v) {
  let s = String(v == null ? "" : v).trim();
  s = s.replace(/\)/g, "");
  s = s.replace(/^\(/, "");
  s = s.replace(/^["']+|["']+$/g, "");
  return s;
}
function digitsOnly(v) {
  return String(v == null ? "" : v).replace(/\D/g, "");
}
function extractLast5(s) {
  return s ? s.slice(-5) : "";
}
function prefixed(last5) {
  if (!last5) return "";
  const n = last5.length;
  if (n === 4) return "NM" + last5;
  if (n === 5) {
    if (last5[0] === "1") return "NL" + last5;
    return "NM" + last5.slice(1);
  }
  return "";
}

/**
 * Parse a file's bytes into an AOA, fix CSV misalignment, and extract the
 * unique LoanDisbDate values. Returns { aoa, dateKeys:[{date,count}],
 * loanDisbColIdx, totalRows }.
 */
export function analyzeWorkbook(arrayBuffer) {
  const data = new Uint8Array(arrayBuffer);
  const wb = XLSX.read(data, { type: "array", cellDates: true });
  const ws = wb.Sheets[wb.SheetNames[0]];
  const aoa = XLSX.utils.sheet_to_json(ws, { header: 1, defval: "" });

  // ── CSV cleanup: fix misaligned rows from unescaped commas ──
  const rawHeaders = aoa[0] || [];
  let headerCount = rawHeaders.length;
  while (headerCount > 0 && String(rawHeaders[headerCount - 1]).trim() === "") headerCount--;
  let loanPurposeCol = -1;
  for (let lpc = 0; lpc < headerCount; lpc++) {
    if (String(rawHeaders[lpc]).trim() === "LoanPurpose") {
      loanPurposeCol = lpc;
      break;
    }
  }
  if (aoa[0] && aoa[0].length > headerCount) aoa[0] = aoa[0].slice(0, headerCount);
  for (let ri = 1; ri < aoa.length; ri++) {
    if (aoa[ri].length > headerCount) {
      const extra = aoa[ri].length - headerCount;
      if (loanPurposeCol !== -1) {
        const parts = [];
        for (let pi = loanPurposeCol; pi <= loanPurposeCol + extra; pi++) parts.push(String(aoa[ri][pi]));
        const merged = parts.join(", ");
        let fixed = aoa[ri].slice(0, loanPurposeCol);
        fixed.push(merged);
        fixed = fixed.concat(aoa[ri].slice(loanPurposeCol + extra + 1));
        aoa[ri] = fixed;
      } else {
        aoa[ri] = aoa[ri].slice(0, headerCount);
      }
    }
  }

  let loanDisbColIdx = -1;
  const dateKeys = [];
  if (aoa.length >= 2) {
    const headers = aoa[0];
    for (let c = 0; c < headers.length; c++) {
      if (String(headers[c]).trim() === "LoanDisbDate") {
        loanDisbColIdx = c;
        break;
      }
    }
    if (loanDisbColIdx !== -1) {
      const dateMap = {};
      for (let r = 1; r < aoa.length; r++) {
        const val = aoa[r][loanDisbColIdx];
        if (!val) continue;
        const dt = val instanceof Date ? val : new Date(val);
        if (isNaN(dt.getTime())) continue;
        const key = formatDateDDMMYYYY(dt);
        dateMap[key] = (dateMap[key] || 0) + 1;
      }
      Object.keys(dateMap)
        .sort((a, b) => parseDDMMYYYY(a) - parseDDMMYYYY(b))
        .forEach((dk) => dateKeys.push({ date: dk, count: dateMap[dk] }));
    }
  }

  return { aoa, dateKeys, loanDisbColIdx, totalRows: Math.max(0, aoa.length - 1) };
}

/**
 * Build the enriched workbook. Returns { blob, outName, rowCount, counts,
 * filteredCount } or { error }.
 */
export function processData(srcAOA, { backendLookup, filterDate, originalName }) {
  const aoa = deepCopyAOA(srcAOA);
  if (aoa.length < 2) return { error: "File appears empty or has no data rows" };

  let headers = aoa[0];

  // ── VLOOKUP Enrichment: Region/RM Name/Area/AM Name via BranchID ──
  if (backendLookup) {
    let branchIdCol = -1;
    for (let bc = 0; bc < headers.length; bc++) {
      const hdr = String(headers[bc]).trim();
      if (hdr === "BranchID" || hdr === "Branch ID" || hdr === "BRANCH ID") {
        branchIdCol = bc;
        break;
      }
    }
    if (branchIdCol !== -1) {
      const enrichCols = [
        { header: "Region", key: "region" },
        { header: "RM Name", key: "rmName" },
        { header: "Area", key: "area" },
        { header: "AM Name", key: "amName" },
      ];
      const existingHeaders = {};
      for (let ec = 0; ec < headers.length; ec++) existingHeaders[String(headers[ec]).trim()] = ec;
      const colsToAdd = [];
      const colsExisting = [];
      for (let ei = 0; ei < enrichCols.length; ei++) {
        if (existingHeaders[enrichCols[ei].header] !== undefined)
          colsExisting.push({ idx: existingHeaders[enrichCols[ei].header], key: enrichCols[ei].key });
        else colsToAdd.push(enrichCols[ei]);
      }
      if (colsToAdd.length > 0) {
        for (let ar = 0; ar < aoa.length; ar++) {
          if (ar === 0) {
            for (let ac = 0; ac < colsToAdd.length; ac++) aoa[0].push(colsToAdd[ac].header);
          } else {
            const bId = aoa[ar][branchIdCol];
            const bKey =
              bId !== null && bId !== undefined && bId !== ""
                ? String(typeof bId === "number" ? Math.round(bId) : bId).trim()
                : "";
            const match = bKey ? backendLookup[bKey] : null;
            for (let ac2 = 0; ac2 < colsToAdd.length; ac2++)
              aoa[ar].push(match ? match[colsToAdd[ac2].key] || "" : "");
          }
        }
      }
      for (let oe = 0; oe < colsExisting.length; oe++) {
        const colIdx = colsExisting[oe].idx;
        const colKey = colsExisting[oe].key;
        for (let or2 = 1; or2 < aoa.length; or2++) {
          const bId2 = aoa[or2][branchIdCol];
          const bKey2 =
            bId2 !== null && bId2 !== undefined && bId2 !== ""
              ? String(typeof bId2 === "number" ? Math.round(bId2) : bId2).trim()
              : "";
          const match2 = bKey2 ? backendLookup[bKey2] : null;
          if (match2 && (!aoa[or2][colIdx] || String(aoa[or2][colIdx]).trim() === ""))
            aoa[or2][colIdx] = match2[colKey] || "";
        }
      }
      headers = aoa[0];
    }
  }

  // Find SchemeID/ProductID column
  let schemeCol = -1;
  for (let c = 0; c < headers.length; c++) {
    if (String(headers[c]).trim() === "SchemeID/ProductID") {
      schemeCol = c;
      break;
    }
  }
  if (schemeCol === -1) return { error: 'Column "SchemeID/ProductID" not found!' };

  formatDatesInAOA(aoa);

  // Insert "Product Name" right after SchemeID/ProductID
  const insertCol = schemeCol + 1;
  for (let r = 0; r < aoa.length; r++) {
    if (r === 0) aoa[r].splice(insertCol, 0, "Product Name");
    else {
      const schemeVal = Number(aoa[r][schemeCol]);
      const prodName = PRODUCT_MAP[schemeVal] || "OTHER";
      aoa[r].splice(insertCol, 0, prodName);
    }
  }

  // Employee ID from CreditOffcierName
  let creditCol = -1;
  const hdrAfterProduct = aoa[0];
  for (let ci = 0; ci < hdrAfterProduct.length; ci++) {
    if (String(hdrAfterProduct[ci]).trim().toLowerCase() === "creditoffciername") {
      creditCol = ci;
      break;
    }
  }
  if (creditCol !== -1) {
    const empInsertCol = creditCol + 1;
    for (let ei2 = 0; ei2 < aoa.length; ei2++) {
      if (ei2 === 0) aoa[ei2].splice(empInsertCol, 0, "Employee ID");
      else {
        const empId = prefixed(extractLast5(digitsOnly(sanitizeOfficerRaw(aoa[ei2][creditCol]))));
        aoa[ei2].splice(empInsertCol, 0, empId);
      }
    }
  }

  // Workbook — Sheet 1: All Data
  const wb = XLSX.utils.book_new();
  const ws1 = XLSX.utils.aoa_to_sheet(aoa);
  XLSX.utils.book_append_sheet(wb, ws1, "All Data");

  // Sheet 2: Filtered by selected date
  let filteredCount = 0;
  if (filterDate) {
    let disbColAfterInsert = -1;
    for (let dci = 0; dci < aoa[0].length; dci++) {
      if (String(aoa[0][dci]).trim() === "LoanDisbDate") {
        disbColAfterInsert = dci;
        break;
      }
    }
    const filteredAOA = [aoa[0]];
    if (disbColAfterInsert !== -1) {
      for (let r = 1; r < aoa.length; r++) {
        const cellVal = String(aoa[r][disbColAfterInsert] || "").trim();
        if (cellVal === filterDate) filteredAOA.push(aoa[r]);
      }
    }
    filteredCount = filteredAOA.length - 1;
    const ws2 = XLSX.utils.aoa_to_sheet(filteredAOA);
    XLSX.utils.book_append_sheet(wb, ws2, filterDate);
  }

  const wbout = XLSX.write(wb, { bookType: "xlsx", type: "array" });
  const blob = new Blob([wbout], {
    type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  });

  const baseName = String(originalName || "report").replace(/\.(xlsx|csv)$/i, "");
  const outName = `${baseName} (with Product Name).xlsx`;

  const counts = {};
  for (let r = 1; r < aoa.length; r++) {
    const pn = aoa[r][insertCol];
    counts[pn] = (counts[pn] || 0) + 1;
  }

  return { blob, outName, rowCount: aoa.length - 1, counts, filteredCount };
}
