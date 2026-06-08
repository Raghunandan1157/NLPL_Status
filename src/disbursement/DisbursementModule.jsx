import { useState } from "react";
import { FileSpreadsheet, Mail, Workflow } from "lucide-react";
import DisbProcess from "./DisbProcess.jsx";
import DisbEmail from "./DisbEmail.jsx";
import ReportsPanel from "./components/ReportsPanel.jsx";
import "../eod/eod.css";

const TABS = [
  { id: "process", label: "Process", icon: Workflow },
  { id: "reports", label: "Reports & Downloads", icon: FileSpreadsheet },
  { id: "email", label: "Email", icon: Mail },
];

export default function DisbursementModule({ onHealthChange }) {
  const [tab, setTab] = useState("process");
  const [reloadKey, setReloadKey] = useState(0);

  return (
    <div className="eod">
      <div className="eod-head">
        <div>
          <p className="eyebrow">Disbursement Report</p>
          <h1 className="eod-title">Daily Disbursement Report</h1>
          <p className="muted eod-subtitle">
            Enrich the disbursement export with Product Name, Region/Area and Employee ID, then email or run VBA.
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

      {tab === "process" && <DisbProcess onProcessed={() => setReloadKey((k) => k + 1)} />}
      {tab === "reports" && <ReportsPanel key={reloadKey} />}
      {tab === "email" && <DisbEmail onHealthChange={onHealthChange} />}
    </div>
  );
}
