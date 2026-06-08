import { useState } from "react";
import { Database, History, Workflow } from "lucide-react";
import InstantProcess from "./InstantProcess.jsx";
import InstantHistory from "./InstantHistory.jsx";
import InstantBackend from "./InstantBackend.jsx";
import "../eod/eod.css";
import "./instant.css";

const TABS = [
  { id: "process", label: "Process", icon: Workflow },
  { id: "history", label: "Report History", icon: History },
  { id: "backend", label: "Monthly Backend", icon: Database },
];

export default function InstantModule() {
  const [tab, setTab] = useState("process");
  const [reloadKey, setReloadKey] = useState(0);

  return (
    <div className="eod">
      <div className="eod-head">
        <div>
          <p className="eyebrow">Instant Report</p>
          <h1 className="eod-title">Instant Pivot Report</h1>
          <p className="muted eod-subtitle">
            PAR + Collection → instant pivot summaries by Region / Area / Branch, cached per date.
          </p>
        </div>
      </div>

      <div className="tabs eod-tabs">
        {TABS.map((t) => {
          const Icon = t.icon;
          return (
            <button key={t.id} className={tab === t.id ? "active" : ""} onClick={() => setTab(t.id)}>
              <Icon size={15} /> {t.label}
            </button>
          );
        })}
      </div>

      {tab === "process" && <InstantProcess onCached={() => setReloadKey((k) => k + 1)} />}
      {tab === "history" && <InstantHistory reloadKey={reloadKey} />}
      {tab === "backend" && <InstantBackend onChange={() => setReloadKey((k) => k + 1)} />}
    </div>
  );
}
