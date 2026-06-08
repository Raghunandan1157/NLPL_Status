import { useCallback, useEffect, useState } from "react";
import { FileSpreadsheet, Workflow } from "lucide-react";
import { getBackendFilesStatus } from "./quickApi.js";
import QuickProcess from "./QuickProcess.jsx";
import ReportsPanel from "./components/ReportsPanel.jsx";
import "../eod/eod.css";

const TABS = [
  { id: "process", label: "Process", icon: Workflow },
  { id: "reports", label: "Reports & Downloads", icon: FileSpreadsheet },
];

export default function QuickModule() {
  const [tab, setTab] = useState("process");
  const [status, setStatus] = useState(null);

  const refreshStatus = useCallback(async () => {
    try {
      setStatus(await getBackendFilesStatus());
    } catch {
      setStatus(null);
    }
  }, []);

  useEffect(() => {
    refreshStatus();
  }, [refreshStatus]);

  return (
    <div className="eod">
      <div className="eod-head">
        <div>
          <p className="eyebrow">Quick Report</p>
          <h1 className="eod-title">Quick Hourly Report</h1>
          <p className="muted eod-subtitle">
            PAR + Collection + hourly Collection Report → final hourly fast report in one pass.
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

      {tab === "process" && <QuickProcess status={status} refreshStatus={refreshStatus} />}
      {tab === "reports" && <ReportsPanel />}
    </div>
  );
}
