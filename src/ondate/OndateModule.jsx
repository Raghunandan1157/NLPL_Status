import { useState } from "react";
import { FileSpreadsheet, Workflow } from "lucide-react";
import OndateProcess from "./OndateProcess.jsx";
import OndateReports from "./OndateReports.jsx";
import "../eod/eod.css";

const TABS = [
  { id: "process", label: "Process", icon: Workflow },
  { id: "reports", label: "Reports & Downloads", icon: FileSpreadsheet },
];

export default function OndateModule() {
  const [tab, setTab] = useState("process");
  const [reloadKey, setReloadKey] = useState(0);

  return (
    <div className="eod">
      <div className="eod-head">
        <div>
          <p className="eyebrow">On-Date Report</p>
          <h1 className="eod-title">Monthly On-Date Report</h1>
          <p className="muted eod-subtitle">
            Extract per-date On-Date sheets into a monthly master workbook, with full formatting preserved.
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

      {tab === "process" && <OndateProcess onExtracted={() => setReloadKey((k) => k + 1)} />}
      {tab === "reports" && <OndateReports reloadKey={reloadKey} />}
    </div>
  );
}
