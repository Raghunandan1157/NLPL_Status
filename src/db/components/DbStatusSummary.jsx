import { Database, ExternalLink } from "lucide-react";
import { Button } from "../../components/ui.jsx";
import { openDbModule, summarizeDb } from "../dbApi.js";

/**
 * Small read-only DB status summary for EOD / Hourly process pages.
 * Shows whether the master files are loaded and links to the DB Module.
 * Replaces the full DB-management panels that used to live in those pages.
 *
 * Props:
 *   status   — { backend, db } object the host page already fetches
 *   compact  — when true, renders inside the side column as a panel
 */
export default function DbStatusSummary({ status, compact = false }) {
  const { files, ready } = summarizeDb(status);

  const body = (
    <>
      <div className="db-summary-head">
        <Database size={16} className="muted" />
        <strong>Database Status</strong>
      </div>

      <div className="db-summary-rows">
        {files.map((f) => (
          <div key={f.id} className="db-summary-row">
            <span className={`dot ${f.loaded ? "ok" : ""}`} />
            <span className="db-summary-name">{f.label}</span>
            <span className={`db-summary-state ${f.loaded ? "ok" : "bad"}`}>
              {f.loaded ? "Loaded" : f.saved ? "Not synced" : "Missing"}
            </span>
          </div>
        ))}
      </div>

      {!ready && (
        <p className="db-summary-warn">
          DB setup incomplete. Please go to the DB Module and upload/sync Demand
          Master and Last Month PAR.
        </p>
      )}

      <Button variant="outline" icon={ExternalLink} className="grow" onClick={openDbModule}>
        Open DB Module
      </Button>
    </>
  );

  return (
    <div className={`panel db-summary ${ready ? "ready" : "warn"}`}>
      {body}
    </div>
  );
}
