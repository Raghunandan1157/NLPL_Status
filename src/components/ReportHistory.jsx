import { useCallback, useEffect, useState } from "react";
import {
  CalendarDays,
  Clock,
  Download,
  FileBarChart,
  FileSpreadsheet,
  RefreshCw,
} from "lucide-react";
import { Button, Spinner } from "./ui.jsx";
import { fileSizeMB } from "../lib/format.js";

// Visuals per report type (the backend supplies type + label per run).
const TYPE_META = {
  output: { icon: FileSpreadsheet, accent: "emerald" },
  report: { icon: FileBarChart, accent: "violet" },
};

// Stable default (module-level so it doesn't re-trigger the load effect).
const DEFAULT_FALLBACK = [
  { type: "output", label: "Report", name: "" },
  { type: "report", label: "Report", name: "" },
];

/**
 * Normalise whatever the archive API returns into the date -> runs[] shape the
 * UI expects. Tolerates the current per-run backend AND any older
 * date-only / flat-run payloads, so the page renders instead of crashing.
 */
function normalizeDates(list, fallbackReports) {
  if (!Array.isArray(list)) return [];
  const byDate = new Map();
  const ensure = (date, label, savedAt) => {
    if (!byDate.has(date)) byDate.set(date, { date, label: label || date, savedAt, runs: [] });
    return byDate.get(date);
  };

  for (const item of list) {
    if (!item) continue;
    if (Array.isArray(item.runs)) {
      // already grouped (current backend) — keep, just sanitise runs/reports
      const group = ensure(item.date, item.label, item.savedAt);
      for (const run of item.runs) {
        group.runs.push({
          runId: run.runId || run.id || run.savedAt || `${group.runs.length}`,
          time: run.time || run.hour || "",
          savedAt: run.savedAt,
          reports: Array.isArray(run.reports) && run.reports.length
            ? run.reports
            : fallbackReports.map((f) => ({ ...f, available: false })),
        });
      }
    } else {
      // legacy flat shape: one record per run (or per date)
      const date = item.date || item.label || "";
      const group = ensure(date, item.label, item.savedAt);
      group.runs.push({
        runId: item.id || item.runId || item.savedAt || `${group.runs.length}`,
        time: item.hour || item.time || "",
        savedAt: item.savedAt,
        reports: fallbackReports.map((f) => ({
          ...f,
          available: f.type === "output" ? !!item.hasOutput : !!item.hasReport,
        })),
      });
    }
  }

  return Array.from(byDate.values()).map((d) => ({ ...d, runCount: d.runs.length }));
}

/**
 * Shared "Reports & Downloads" history view for a module (EOD or Hourly).
 *
 * Renders a date rail (last-3-days dates that have archived runs) and, for the
 * selected date, every run that day — newest first, the newest tagged "Latest"
 * and the rest "Previous Run". Each run shows its own archived report files so
 * older runs stay downloadable until retention deletes them.
 *
 * Props:
 *   eyebrow, title, subtitle — header text
 *   listFn      — () => Promise<{ dates: [{date,label,savedAt,runs:[...]}] }>
 *   fileUrlFn   — (date, runId, type) => download URL for that exact run's file
 *   emptyHint   — text shown when there is no history yet
 */
export default function ReportHistory({
  eyebrow,
  title,
  subtitle,
  listFn,
  fileUrlFn,
  emptyHint,
  fallbackReports = DEFAULT_FALLBACK,
}) {
  const [dates, setDates] = useState(null);
  const [selected, setSelected] = useState(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await listFn();
      const list = normalizeDates(res?.dates, fallbackReports);
      setDates(list);
      setSelected((cur) => (cur && list.some((d) => d.date === cur) ? cur : list[0]?.date || null));
    } catch {
      setDates([]);
    } finally {
      setLoading(false);
    }
  }, [listFn, fallbackReports]);

  useEffect(() => {
    load();
  }, [load]);

  const current = dates?.find((d) => d.date === selected) || null;
  const runs = current?.runs || [];

  return (
    <div className="panel">
      <div className="panel-header">
        <div>
          <p className="eyebrow">{eyebrow}</p>
          <h2>{title}</h2>
          <p className="sub">{subtitle}</p>
        </div>
        <Button size="sm" variant="ghost" icon={RefreshCw} onClick={load}>
          Refresh
        </Button>
      </div>

      {loading ? (
        <div className="row" style={{ gap: 10, padding: 20 }}>
          <Spinner size={18} /> Loading archived reports…
        </div>
      ) : !dates || dates.length === 0 ? (
        <div className="empty">
          <FileSpreadsheet size={28} />
          <h3>No archived reports yet</h3>
          <p className="muted">{emptyHint}</p>
        </div>
      ) : (
        <div className="reports-layout">
          {/* Date rail (last 3 days with runs) */}
          <div className="date-rail">
            <div className="date-rail-label">
              <CalendarDays size={14} /> Processed dates
            </div>
            {dates.map((d, i) => (
              <button
                key={d.date}
                className={`date-item ${selected === d.date ? "on" : ""}`}
                onClick={() => setSelected(d.date)}
              >
                <span className="date-main">{d.label || d.date}</span>
                <span className="date-sub">
                  {d.runCount || d.runs?.length || 0} run{(d.runCount || d.runs?.length || 0) === 1 ? "" : "s"}
                  {i === 0 ? " · Latest" : ""}
                </span>
              </button>
            ))}
          </div>

          {/* Runs for the selected date */}
          <div className="reports-panel">
            {current && (
              <>
                <div className="reports-panel-head">
                  <CalendarDays size={16} className="muted" />
                  <strong>{current.label || current.date}</strong>
                  <span className="muted" style={{ fontSize: 12, marginLeft: "auto" }}>
                    {runs.length} run{runs.length === 1 ? "" : "s"} in the last 3 days
                  </span>
                </div>

                {runs.map((run, i) => (
                  <div key={run.runId} className={`run-group ${i === 0 ? "latest" : ""}`}>
                    <div className="run-group-head">
                      <span className={`run-tag ${i === 0 ? "latest" : ""}`}>
                        {i === 0 ? "Latest" : "Previous Run"}
                      </span>
                      <span className="run-time">
                        <Clock size={12} /> {run.time || run.runId}
                      </span>
                      {run.savedAt && (
                        <span className="muted run-saved">generated {run.savedAt}</span>
                      )}
                    </div>

                    <div className="report-cards">
                      {run.reports.map((r) => {
                        const Icon = (TYPE_META[r.type] || {}).icon || FileSpreadsheet;
                        const accent = (TYPE_META[r.type] || {}).accent || "emerald";
                        return (
                          <div key={r.type} className="report-card">
                            <span className={`report-ic ${accent}`}>
                              <Icon size={20} />
                            </span>
                            <div className="grow">
                              <strong>{r.label}</strong>
                              <p className="muted">
                                {r.available
                                  ? `Ready to download (.xlsx)${r.size ? ` · ${fileSizeMB(r.size)}` : ""}`
                                  : "Not generated in this run"}
                              </p>
                            </div>
                            {r.available ? (
                              <a
                                className="btn btn-primary btn-sm"
                                href={fileUrlFn(current.date, run.runId, r.type)}
                              >
                                <Download size={15} /> Download
                              </a>
                            ) : (
                              <Button size="sm" variant="ghost" disabled>
                                Unavailable
                              </Button>
                            )}
                          </div>
                        );
                      })}
                    </div>
                  </div>
                ))}
              </>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
