import { useCallback, useEffect, useMemo, useState } from "react";
import {
  CalendarDays,
  CheckCircle2,
  Clock,
  Download,
  FileSpreadsheet,
  Inbox,
  Layers,
  RefreshCw,
  Search,
} from "lucide-react";
import { Button } from "../../components/ui.jsx";

function formatBytes(n) {
  if (!n || n < 0) return "";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

/**
 * Reusable Reports & Downloads section with a date-first flow:
 *   (module is chosen upstream) → pick a DATE → see that date's generated reports.
 *
 * Left: a date rail (newest first, with run/report counts). Right: the selected
 * date's runs as premium report cards (name, module, time, size, status,
 * download). Includes a text filter, loading skeleton and empty state.
 *
 * Props: listFn() -> { dates: [...] }, fileUrlFn(date, run, type) -> url,
 *        moduleLabel, eyebrow, title, subtitle, emptyHint.
 */
export default function ReportsDownloadSection({
  listFn,
  fileUrlFn,
  moduleLabel = "",
  eyebrow = "Reports & Downloads",
  title = "Download by date & run",
  subtitle = "Pick a date to see the reports generated that day. Reports from the last 3 days are downloadable.",
  emptyHint = "Run a process to see generated reports here.",
}) {
  const [loading, setLoading] = useState(true);
  const [dates, setDates] = useState([]);
  const [query, setQuery] = useState("");
  const [error, setError] = useState("");
  const [selectedDate, setSelectedDate] = useState(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const res = await listFn();
      setDates(Array.isArray(res?.dates) ? res.dates : []);
    } catch (e) {
      setError(e?.message || "Could not load reports.");
      setDates([]);
    } finally {
      setLoading(false);
    }
  }, [listFn]);

  useEffect(() => {
    load();
  }, [load]);

  // Normalize + sort newest-first, applying the text filter to date/report names.
  const groups = useMemo(() => {
    const q = query.trim().toLowerCase();
    const norm = (dates || []).map((g) => ({
      date: g.date,
      label: g.label || g.date,
      savedAt: g.savedAt || "",
      runs: (g.runs || []).map((r) => ({
        runId: r.runId || r.id || r.savedAt || "",
        time: r.time || r.hour || "",
        savedAt: r.savedAt || "",
        reports: (r.reports || []).filter((rep) => rep.available !== false),
      })),
    }));
    if (!q) return norm;
    return norm
      .map((g) => ({
        ...g,
        runs: g.runs.filter(
          (r) =>
            g.label.toLowerCase().includes(q) ||
            r.time.toLowerCase().includes(q) ||
            r.reports.some((rep) => (rep.label || rep.name || "").toLowerCase().includes(q))
        ),
      }))
      .filter((g) => g.runs.length > 0);
  }, [dates, query]);

  // Default-select the newest date once data arrives / filter changes.
  useEffect(() => {
    if (groups.length === 0) {
      setSelectedDate(null);
      return;
    }
    if (!selectedDate || !groups.some((g) => g.date === selectedDate)) {
      setSelectedDate(groups[0].date);
    }
  }, [groups, selectedDate]);

  const current = useMemo(
    () => groups.find((g) => g.date === selectedDate) || null,
    [groups, selectedDate]
  );

  const countReports = (g) => g.runs.reduce((m, r) => m + r.reports.length, 0);
  const totalReports = useMemo(() => groups.reduce((n, g) => n + countReports(g), 0), [groups]);

  return (
    <div className="panel reports-section">
      <div className="panel-header reports-head">
        <div>
          <p className="eyebrow">{eyebrow}</p>
          <h2>{title}</h2>
          <p className="sub">{subtitle}</p>
        </div>
        <Button variant="ghost" icon={RefreshCw} onClick={load} disabled={loading}>
          Refresh
        </Button>
      </div>

      <div className="reports-search">
        <Search size={15} className="text-muted" />
        <input
          type="text"
          placeholder="Filter by date, time or report name…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
      </div>

      {loading ? (
        <div className="reports-skeleton">
          {[0, 1, 2].map((i) => (
            <div key={i} className="skeleton-card">
              <div className="skeleton-line w40" />
              <div className="skeleton-line w70" />
              <div className="skeleton-line w55" />
            </div>
          ))}
        </div>
      ) : error ? (
        <div className="reports-empty">
          <span className="reports-empty-icon">
            <Inbox size={28} />
          </span>
          <strong>Couldn’t load reports</strong>
          <p className="muted">{error}</p>
        </div>
      ) : totalReports === 0 ? (
        <div className="reports-empty">
          <span className="reports-empty-icon">
            <Inbox size={28} />
          </span>
          <strong>No reports yet</strong>
          <p className="muted">{emptyHint}</p>
        </div>
      ) : (
        <div className="reports-datelayout">
          {/* Step 1 → pick a date */}
          <div className="reports-daterail">
            <div className="reports-daterail-label">
              <CalendarDays size={13} /> Select a date
            </div>
            {groups.map((g) => {
              const n = countReports(g);
              return (
                <button
                  key={g.date}
                  type="button"
                  className={`reports-date-btn ${selectedDate === g.date ? "on" : ""}`}
                  onClick={() => setSelectedDate(g.date)}
                >
                  <span className="reports-date-btn-main">{g.label}</span>
                  <span className="reports-date-btn-sub">
                    {g.runs.length} run{g.runs.length !== 1 ? "s" : ""} · {n} report{n !== 1 ? "s" : ""}
                  </span>
                </button>
              );
            })}
          </div>

          {/* Step 2 → that date's generated reports */}
          <div className="reports-dateview">
            {current && (
              <>
                <div className="reports-dateview-head">
                  <div className="reports-dateview-title">
                    <CalendarDays size={16} /> {current.label}
                  </div>
                  <div className="reports-dateview-meta">
                    <span>
                      <Layers size={12} /> {current.runs.length} run
                      {current.runs.length !== 1 ? "s" : ""}
                    </span>
                    <span>
                      <FileSpreadsheet size={12} /> {countReports(current)} report
                      {countReports(current) !== 1 ? "s" : ""}
                    </span>
                  </div>
                </div>

                {current.runs.map((r) => (
                  <div key={r.runId} className="reports-run">
                    <div className="reports-run-time">
                      <Clock size={13} /> {r.time || r.runId}
                    </div>
                    <div className="reports-cards">
                      {r.reports.map((rep) => (
                        <div key={rep.type} className="report-card">
                          <span className="report-card-icon">
                            <FileSpreadsheet size={18} />
                          </span>
                          <div className="report-card-meta">
                            <strong className="report-card-name" title={rep.name || rep.label}>
                              {rep.label || rep.name}
                            </strong>
                            <div className="report-card-sub">
                              {moduleLabel && <span className="report-card-module">{moduleLabel}</span>}
                              {r.time && <span>{r.time}</span>}
                              {rep.size ? <span>{formatBytes(rep.size)}</span> : null}
                              <span className="report-card-status">
                                <CheckCircle2 size={12} /> Ready
                              </span>
                            </div>
                          </div>
                          <a
                            className="btn btn-soft btn-sm report-card-dl"
                            href={fileUrlFn(current.date, r.runId, rep.type)}
                          >
                            <Download size={15} /> Download
                          </a>
                        </div>
                      ))}
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
