import { useMemo, useState } from "react";

// Ordered row-object keys per table type, aligned to that type's `headers`.
// The backend returns row objects whose key order is NOT the header order
// (Flask's jsonify sorts keys alphabetically), so we map values by key, not by
// Object.values() order. Covers every type compute_instant_report emits.
const KEYS_BY_TYPE = {
  regular: ["name", "demand", "collection", "ftod", "collection_pct"],
  bucket: ["name", "demand", "collection", "balance", "collection_pct"],
  npa: ["name", "demand", "activation_account", "activation_amount", "closure_account", "closure_amount"],
};

function columnKeys(table) {
  const keys = KEYS_BY_TYPE[table.type];
  if (keys && keys.length === (table.headers || []).length) return keys;
  return null; // unknown type — fall back to insertion order
}

function rowCells(table, row) {
  const keys = columnKeys(table);
  if (keys) return keys.map((k) => row[k]);
  // Fallback: name first, then remaining values as-is.
  const rest = Object.keys(row).filter((k) => k !== "name");
  return [row.name, ...rest.map((k) => row[k])];
}

function totalCells(table) {
  const gt = table.grand_total || {};
  const keys = columnKeys(table);
  if (keys) return keys.slice(1).map((k) => gt[k]); // drop the name column
  return Object.values(gt);
}

/**
 * Render an Instant report_data structure: section tabs + a level filter, with
 * each table's headers, rows and grand total. The pivot numbers are exactly what
 * the backend computed — this only displays them.
 */
export default function ReportView({ report }) {
  const sections = report?.sections || [];
  const [activeSection, setActiveSection] = useState(0);

  const levels = useMemo(() => {
    const set = [];
    for (const s of sections)
      for (const t of s.tables || []) if (t.level && !set.includes(t.level)) set.push(t.level);
    return set;
  }, [sections]);

  const [level, setLevel] = useState(null);
  const effLevel = level || levels[0] || null;

  if (!sections.length) {
    return (
      <div className="empty" style={{ padding: 24 }}>
        <p className="muted">No sections in this report.</p>
      </div>
    );
  }

  const section = sections[Math.min(activeSection, sections.length - 1)];
  const tables = (section.tables || []).filter((t) => !effLevel || t.level === effLevel);

  return (
    <div className="col" style={{ gap: 14 }}>
      <div className="row" style={{ gap: 8, flexWrap: "wrap" }}>
        {sections.map((s, i) => (
          <button
            key={i}
            className={`chip ${i === activeSection ? "on" : ""}`}
            onClick={() => setActiveSection(i)}
          >
            {s.title}
          </button>
        ))}
      </div>

      {levels.length > 0 && (
        <div className="row" style={{ gap: 8, alignItems: "center" }}>
          <span className="muted" style={{ fontSize: 12 }}>Level:</span>
          <select className="input" style={{ maxWidth: 200 }} value={effLevel || ""} onChange={(e) => setLevel(e.target.value)}>
            {levels.map((l) => (
              <option key={l} value={l}>{l}</option>
            ))}
          </select>
        </div>
      )}

      {tables.map((t, ti) => (
        <div key={ti} className="panel" style={{ padding: 0, overflow: "hidden" }}>
          <div style={{ padding: "10px 14px", borderBottom: "1px solid var(--border)" }}>
            <strong style={{ fontSize: 13 }}>{section.title} · {t.level}</strong>
          </div>
          <div style={{ overflowX: "auto" }}>
            <table className="data-table">
              <thead>
                <tr>
                  {(t.headers || []).map((h, hi) => (
                    <th key={hi}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {(t.rows || []).map((r, ri) => (
                  <tr key={ri}>
                    {rowCells(t, r).map((v, vi) => (
                      <td key={vi} style={vi === 0 ? undefined : { textAlign: "right" }}>{v == null ? "" : String(v)}</td>
                    ))}
                  </tr>
                ))}
                {t.grand_total && (
                  <tr className="grand-total">
                    <td><strong>Grand Total</strong></td>
                    {totalCells(t).map((v, vi) => (
                      <td key={vi} style={{ textAlign: "right" }}><strong>{v == null ? "" : String(v)}</strong></td>
                    ))}
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </div>
      ))}
    </div>
  );
}
