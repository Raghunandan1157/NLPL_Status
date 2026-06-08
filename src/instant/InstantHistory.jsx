import { useCallback, useEffect, useState } from "react";
import { CalendarDays, Download, RefreshCw, Trash2 } from "lucide-react";
import { Button, Spinner, useToast } from "../components/ui.jsx";
import ReportView from "./ReportView.jsx";
import { downloadReport } from "./instantExcel.js";
import { deleteAllCache, deleteCache, generateFromCache, historyDates } from "./instantApi.js";

export default function InstantHistory({ reloadKey }) {
  const toast = useToast();
  const [dates, setDates] = useState(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState("");
  const [active, setActive] = useState(null); // {date, report}

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await historyDates();
      setDates(res.dates || []);
    } catch {
      setDates([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load, reloadKey]);

  async function view(d) {
    setBusy(`view-${d}`);
    try {
      const report = await generateFromCache(d);
      setActive({ date: d, report });
    } catch (e) {
      toast.error(e.message, "Load failed");
    } finally {
      setBusy("");
    }
  }

  async function remove(d) {
    if (!window.confirm(`Delete cached report for ${d}?`)) return;
    setBusy(`del-${d}`);
    try {
      await deleteCache(d);
      toast.success(`Deleted ${d}.`, "Removed");
      if (active?.date === d) setActive(null);
      load();
    } catch (e) {
      toast.error(e.message, "Delete failed");
    } finally {
      setBusy("");
    }
  }

  async function removeAll() {
    if (!window.confirm("Delete ALL cached instant reports?")) return;
    setBusy("del-all");
    try {
      const res = await deleteAllCache();
      toast.success(`Deleted ${res.deleted} date(s).`, "Cleared");
      setActive(null);
      load();
    } catch (e) {
      toast.error(e.message, "Delete failed");
    } finally {
      setBusy("");
    }
  }

  const iso = (d) => d.date_iso || d.date || d;
  const display = (d) => d.date_display || d.date_iso || d;

  return (
    <div className="col" style={{ gap: 18 }}>
      <div className="panel">
        <div className="panel-header">
          <div>
            <p className="eyebrow">Report History</p>
            <h2>Cached dates</h2>
            <p className="sub">Every processed date is cached. Re-open instantly, no re-upload.</p>
          </div>
          <div className="row" style={{ gap: 8 }}>
            <Button size="sm" variant="ghost" icon={RefreshCw} onClick={load}>Refresh</Button>
            {dates?.length > 0 && (
              <Button size="sm" variant="danger" icon={Trash2} loading={busy === "del-all"} onClick={removeAll}>
                Delete all
              </Button>
            )}
          </div>
        </div>

        {loading ? (
          <div className="row" style={{ gap: 10, padding: 20 }}>
            <Spinner size={18} /> Loading history…
          </div>
        ) : !dates || dates.length === 0 ? (
          <div className="empty">
            <CalendarDays size={28} />
            <h3>No cached reports yet</h3>
            <p className="muted">Generate an instant report to build the history.</p>
          </div>
        ) : (
          <div className="report-cards">
            {dates.map((d) => (
              <div key={iso(d)} className="report-card">
                <span className="report-ic violet"><CalendarDays size={20} /></span>
                <div className="grow">
                  <strong>{display(d)}</strong>
                  <p className="muted">Cached instant report</p>
                </div>
                <Button size="sm" variant="primary" loading={busy === `view-${iso(d)}`} onClick={() => view(iso(d))}>
                  View
                </Button>
                <Button size="sm" variant="ghost" icon={Trash2} loading={busy === `del-${iso(d)}`} onClick={() => remove(iso(d))} />
              </div>
            ))}
          </div>
        )}
      </div>

      {active && (
        <div className="panel">
          <div className="panel-header" style={{ marginBottom: 12 }}>
            <div>
              <p className="eyebrow">Viewing</p>
              <h2>{active.date}</h2>
            </div>
            <Button
              size="sm"
              variant="primary"
              icon={Download}
              onClick={() => downloadReport(active.report, `Instant Report ${active.date}.xlsx`)}
            >
              Download Excel
            </Button>
          </div>
          <ReportView report={active.report} />
        </div>
      )}
    </div>
  );
}
