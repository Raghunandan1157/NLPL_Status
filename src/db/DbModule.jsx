import { useState } from "react";
import { Database, RefreshCw } from "lucide-react";
import { Button, useToast } from "../components/ui.jsx";
import MasterFileCard from "./components/MasterFileCard.jsx";
import DbStatusPanel from "./components/DbStatusPanel.jsx";
import { clearDuckDb, summarizeDb, syncDuckDb, useDbStatus } from "./dbApi.js";
import "../eod/eod.css"; // reuse shared layout primitives (.eod-head, .actions, .mini-status…)
import "./db.css";

/**
 * DB Module — central management of source-supported master/backend files and
 * DuckDB sync. The only place that uploads/ingests Demand Master + Last Month
 * PAR. EOD and Hourly read this state but no longer manage it.
 */
export default function DbModule({ onHealthChange }) {
  const toast = useToast();
  const { backend, db, loading, refresh } = useDbStatus();
  const [busy, setBusy] = useState("");

  const summary = summarizeDb({ backend, db });

  async function handleChange() {
    await refresh();
    onHealthChange?.();
  }

  async function handleSync() {
    setBusy("sync");
    try {
      const res = await syncDuckDb();
      const failed = ["demandMaster", "lastMonthPar"].filter(
        (k) => res?.[k] && res[k].success === false && summary.files.find((f) => f.dbKey === k)?.saved
      );
      if (failed.length) {
        toast.warn("Some files could not be loaded. Check that both master files are valid.", "Partial sync");
      } else {
        toast.success("DuckDB tables loaded successfully.", "Synced");
      }
      await handleChange();
    } catch (e) {
      toast.error(e.message, "Sync failed");
    } finally {
      setBusy("");
    }
  }

  async function handleClear() {
    if (!window.confirm("Clear the Demand Master and Last Month PAR tables from DuckDB? You'll need to Sync again to reload them.")) {
      return;
    }
    setBusy("clear");
    try {
      const res = await clearDuckDb();
      toast.success(res?.message || "Database cleared.", "Cleared");
      await handleChange();
    } catch (e) {
      toast.error(e.message, "Clear failed");
    } finally {
      setBusy("");
    }
  }

  return (
    <div className="eod db-module">
      <div className="eod-head">
        <div>
          <p className="eyebrow">DB Module</p>
          <h1 className="eod-title">Database &amp; Master Files</h1>
          <p className="muted eod-subtitle">
            Upload and sync the shared master files (Demand Master, Last Month PAR)
            used by EOD and Hourly. Upload saves instantly; click Save to DB or
            Sync DuckDB to load into the database.
          </p>
        </div>
        <Button variant="ghost" icon={RefreshCw} loading={loading} onClick={refresh}>
          Refresh
        </Button>
      </div>

      <div className="db-layout">
        <div className="db-main">
          <div className="db-card-grid">
            {summary.files.map((f) => (
              <MasterFileCard key={f.id} file={f} onChange={handleChange} />
            ))}
          </div>
        </div>

        <div className="db-side">
          <DbStatusPanel
            summary={summary}
            db={db}
            busy={busy}
            onSync={handleSync}
            onClear={handleClear}
          />

          <div className="panel hint-panel">
            <div className="row" style={{ alignItems: "flex-start", gap: 12 }}>
              <Database size={18} className="muted" />
              <div>
                <strong style={{ fontSize: 13.5 }}>How it works</strong>
                <p className="muted" style={{ margin: "4px 0 0", fontSize: 12.5 }}>
                  Uploading a master file only saves it (fast). Click <b>Save to DB</b>
                  {" "}on a card, or <b>Sync DuckDB</b> to load both, then EOD and Hourly
                  can run.
                </p>
                <p className="muted" style={{ margin: "8px 0 0", fontSize: 12 }}>
                  🧹 The Demand Master, Last Month PAR and the DuckDB tables are kept
                  permanently (not affected by 3-day retention).
                </p>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
