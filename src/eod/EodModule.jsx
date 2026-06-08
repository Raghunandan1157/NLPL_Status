import { useCallback, useEffect, useState } from "react";
import { FileSpreadsheet, Mail, MessageCircle, Workflow } from "lucide-react";
import { getBackendFilesStatus, getDbStatus, getLastCache } from "./api.js";
import UploadProcessPanel from "./components/UploadProcessPanel.jsx";
import ReportsPanel from "./components/ReportsPanel.jsx";
import EmailPanel from "./components/EmailPanel.jsx";
import WhatsAppPanel from "./components/WhatsAppPanel.jsx";
import "./eod.css";

const TABS = [
  { id: "process", label: "Process", icon: Workflow },
  { id: "reports", label: "Reports & Downloads", icon: FileSpreadsheet },
  { id: "email", label: "Email", icon: Mail },
  { id: "whatsapp", label: "WhatsApp", icon: MessageCircle },
];

export default function EodModule({ health, onHealthChange }) {
  const [tab, setTab] = useState("process");
  const [status, setStatus] = useState({ backend: null, db: null, lastCache: null });

  const refreshStatus = useCallback(async () => {
    const [backend, db, lastCache] = await Promise.allSettled([
      getBackendFilesStatus(),
      getDbStatus(),
      getLastCache(),
    ]);
    setStatus({
      backend: backend.status === "fulfilled" ? backend.value : null,
      db: db.status === "fulfilled" ? db.value : null,
      lastCache: lastCache.status === "fulfilled" ? lastCache.value : null,
    });
  }, []);

  useEffect(() => {
    refreshStatus();
  }, [refreshStatus]);

  return (
    <div className="eod">
      <div className="eod-head">
        <div>
          <p className="eyebrow">EOD Module</p>
          <h1 className="eod-title">Regular Demand vs Collection</h1>
          <p className="muted eod-subtitle">
            Process today's files, generate the report, and deliver it to your teams.
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

      {tab === "process" && (
        <UploadProcessPanel
          status={status}
          health={health}
          refreshStatus={refreshStatus}
          onHealthChange={onHealthChange}
          onSwitchTab={(id) => setTab(id)}
        />
      )}
      {tab === "reports" && <ReportsPanel status={status} refreshStatus={refreshStatus} />}
      {tab === "email" && <EmailPanel health={health} onHealthChange={onHealthChange} />}
      {tab === "whatsapp" && <WhatsAppPanel health={health} />}
    </div>
  );
}
