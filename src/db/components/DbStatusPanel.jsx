import { Database, RefreshCw, Server, Trash2 } from "lucide-react";
import { Button } from "../../components/ui.jsx";

/**
 * Database Status section: DuckDB file + per-table status, plus the
 * Sync DuckDB and Clear DB actions.
 *
 * Props:
 *   summary  — output of summarizeDb()
 *   db       — raw db-status payload (for dbAvailable flag)
 *   busy     — current busy key ("sync" | "clear" | "")
 *   onSync   — Sync DuckDB handler
 *   onClear  — Clear DB handler
 */
export default function DbStatusPanel({ summary, db, busy, onSync, onClear }) {
  const dbAvailable = db?.dbAvailable !== false;

  return (
    <div className="panel db-status-panel">
      <div className="panel-header" style={{ marginBottom: 14 }}>
        <div>
          <p className="eyebrow">Database</p>
          <h2>DuckDB Status</h2>
          <p className="sub">Central store shared by EOD and Hourly.</p>
        </div>
        <Server size={18} className="muted" />
      </div>

      <div className="db-status-rows">
        <div className="mini-status">
          <div className="row">
            <span className={`dot ${dbAvailable ? "ok" : "bad"}`} />
            <span className="db-status-label">DuckDB file</span>
          </div>
          <strong>{dbAvailable ? "Connected" : "Unavailable"}</strong>
        </div>

        {summary.files.map((f) => (
          <div key={f.id} className="mini-status">
            <div className="row">
              <span className={`dot ${f.loaded ? "ok" : ""}`} />
              <span className="db-status-label">{f.table}</span>
            </div>
            <strong>
              {f.loaded ? `${f.rowCount?.toLocaleString?.() ?? f.rowCount} rows` : "Not loaded"}
            </strong>
          </div>
        ))}
      </div>

      <div className="actions db-status-actions">
        <Button
          variant="success"
          icon={RefreshCw}
          className="grow"
          disabled={!summary.anySaved || Boolean(busy)}
          loading={busy === "sync"}
          onClick={onSync}
        >
          Sync DuckDB
        </Button>
        <Button
          variant="danger-soft"
          icon={Trash2}
          disabled={!summary.allLoaded && !summary.files.some((f) => f.loaded) || Boolean(busy)}
          loading={busy === "clear"}
          onClick={onClear}
        >
          Clear DB
        </Button>
      </div>

      {!summary.anySaved && (
        <p className="db-status-hint">
          <Database size={13} /> Upload Demand Master and Last Month PAR above, then Sync DuckDB.
        </p>
      )}
    </div>
  );
}
