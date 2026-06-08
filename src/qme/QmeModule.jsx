import { useState } from "react";
import { FileSpreadsheet, Workflow } from "lucide-react";
import QmeProcess from "./QmeProcess.jsx";
import ReportsPanel from "./components/ReportsPanel.jsx";
import "../eod/eod.css";

const TABS = [
  { id: "process", label: "Process", icon: Workflow },
  { id: "reports", label: "Reports & Downloads", icon: FileSpreadsheet },
];

export default function QmeModule() {
  const [tab, setTab] = useState("process");

  return (
    <div className="eod">
      <div className="eod-head">
        <div>
          <p className="eyebrow">Month-End Report</p>
          <h1 className="eod-title">Quick Month-End Employee Report</h1>
          <p className="muted eod-subtitle">
            Demand + Last Month PAR + PAR + Collection → month-end Employee report in one pass.
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

      {tab === "process" && <QmeProcess />}
      {tab === "reports" && <ReportsPanel />}
    </div>
  );
}
