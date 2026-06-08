import { useCallback, useEffect, useState } from "react";
import { CheckCircle2, Database, RefreshCw, Trash2 } from "lucide-react";
import { Button, FileDrop, Spinner, useToast } from "../components/ui.jsx";
import { monthlyDelete, monthlyStatus, monthlyUpload } from "./instantApi.js";

function currentMonth() {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
}

export default function InstantBackend({ onChange }) {
  const toast = useToast();
  const [month, setMonth] = useState(currentMonth());
  const [months, setMonths] = useState(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await monthlyStatus();
      setMonths(res.months || []);
    } catch {
      setMonths([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  async function upload(type, file) {
    if (!file) return;
    if (!/^\d{4}-\d{2}$/.test(month)) {
      toast.error("Pick a valid month (YYYY-MM) first.", "Month required");
      return;
    }
    setBusy(`${type}`);
    try {
      const res = await monthlyUpload(month, type, file);
      toast.success(`${type === "demand" ? "Demand" : "Last Month PAR"} saved for ${month} (${res.cache_rows} rows).`, "Saved");
      load();
      onChange?.();
    } catch (e) {
      toast.error(e.message, "Upload failed");
    } finally {
      setBusy("");
    }
  }

  async function remove(m, type) {
    if (!window.confirm(`Delete ${type === "demand" ? "Demand" : "Last Month PAR"} for ${m}?`)) return;
    setBusy(`del-${m}-${type}`);
    try {
      await monthlyDelete(m, type);
      toast.success(`Deleted ${type} for ${m}.`, "Removed");
      load();
      onChange?.();
    } catch (e) {
      toast.error(e.message, "Delete failed");
    } finally {
      setBusy("");
    }
  }

  return (
    <div className="eod-grid">
      <div className="col" style={{ gap: 18 }}>
        <div className="panel">
          <div className="panel-header">
            <div>
              <p className="eyebrow">Monthly backend</p>
              <h2>Demand & Last Month PAR</h2>
              <p className="sub">Each month has its own Demand Sheet + Last Month PAR. Required before processing that month.</p>
            </div>
          </div>

          <label className="field">
            <span>Month</span>
            <input className="input" type="month" value={month} onChange={(e) => setMonth(e.target.value)} />
          </label>

          <div className="file-grid" style={{ gridTemplateColumns: "repeat(2, 1fr)", marginTop: 12 }}>
            <FileDrop
              label="Demand Sheet Master"
              hint=".xlsx"
              file={null}
              onFile={(f) => upload("demand", f)}
              disabled={busy === "demand"}
            />
            <FileDrop
              label="Last Month PAR"
              hint=".xlsx"
              file={null}
              onFile={(f) => upload("last_month", f)}
              disabled={busy === "last_month"}
            />
          </div>
        </div>
      </div>

      <div className="col" style={{ gap: 18 }}>
        <div className="panel">
          <div className="panel-header">
            <div>
              <p className="eyebrow">Loaded</p>
              <h2>Months on file</h2>
            </div>
            <Button size="sm" variant="ghost" icon={RefreshCw} onClick={load}>Refresh</Button>
          </div>
          {loading ? (
            <div className="row" style={{ gap: 10, padding: 16 }}>
              <Spinner size={16} /> Loading…
            </div>
          ) : !months || months.length === 0 ? (
            <div className="empty" style={{ padding: 22 }}>
              <Database size={24} />
              <p className="muted">No monthly backend data yet.</p>
            </div>
          ) : (
            <div className="col" style={{ gap: 10 }}>
              {months.map((m) => (
                <div key={m.month} className="instant-month-card">
                  <Database size={18} className="muted" />
                  <div className="grow">
                    <strong style={{ fontSize: 13 }}>{m.month}</strong>
                    <div className="muted" style={{ fontSize: 12 }}>
                      {m.demand_sheet ? (
                        <span><CheckCircle2 size={11} /> Demand</span>
                      ) : (
                        <span>Demand missing</span>
                      )}
                      {"  ·  "}
                      {m.last_month_par ? "Last Month PAR ✓" : "Last Month PAR —"}
                    </div>
                  </div>
                  {m.demand_sheet && (
                    <Button size="sm" variant="ghost" icon={Trash2} loading={busy === `del-${m.month}-demand`} onClick={() => remove(m.month, "demand")} />
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
