import { ExternalLink, RefreshCw } from "lucide-react";
import { useRef, useState } from "react";
import { API_BASE } from "../lib/apiClient.js";

/**
 * Renders a migrated unified-collection-report module (Analytics, Employee
 * Performance, …) inside the nlpl_status shell via an iframe that loads the
 * module's own backend-served UI. These modules are read-only dashboards (no
 * file upload / processing job), so they reuse the vendored engine and their
 * original UI exactly — nothing is re-implemented or altered.
 *
 * Props: path (e.g. "/analytics/"), eyebrow, title, subtitle.
 */
export default function EmbeddedModule({ path, eyebrow, title, subtitle }) {
  const src = `${API_BASE}${path}`;
  const frameRef = useRef(null);
  const [reloadKey, setReloadKey] = useState(0);

  return (
    <div className="eod" style={{ display: "flex", flexDirection: "column", height: "100%" }}>
      <div className="eod-head" style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between" }}>
        <div>
          <p className="eyebrow">{eyebrow}</p>
          <h1 className="eod-title">{title}</h1>
          {subtitle && <p className="muted eod-subtitle">{subtitle}</p>}
        </div>
        <div className="row" style={{ gap: 8 }}>
          <button className="btn btn-ghost btn-sm" onClick={() => setReloadKey((k) => k + 1)} title="Reload">
            <RefreshCw size={15} /> Reload
          </button>
          <a className="btn btn-outline btn-sm" href={src} target="_blank" rel="noopener noreferrer">
            <ExternalLink size={15} /> Open in new tab
          </a>
        </div>
      </div>

      <div
        style={{
          flex: 1,
          minHeight: 600,
          marginTop: 14,
          border: "1px solid var(--border)",
          borderRadius: "var(--radius, 14px)",
          overflow: "hidden",
          background: "var(--surface)",
          boxShadow: "var(--shadow-sm)",
        }}
      >
        <iframe
          key={reloadKey}
          ref={frameRef}
          src={src}
          title={title}
          style={{ width: "100%", height: "100%", minHeight: 600, border: "none", display: "block" }}
        />
      </div>
    </div>
  );
}
