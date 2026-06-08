import { useCallback, useEffect, useState } from "react";
import { Code, MessageCircle, Workflow } from "lucide-react";
import { getBackendFilesStatus } from "./hourlyApi.js";
import { getDbStatus } from "../eod/api.js";
import HourlyProcess from "./HourlyProcess.jsx";
import VbaRunnerPanel from "./components/VbaRunnerPanel.jsx";
import HourlyWhatsApp from "./HourlyWhatsApp.jsx";
import "./hourly.css";

const TABS = [
  { id: "process", label: "Process", icon: Workflow },
  { id: "vba", label: "VBA Runner", icon: Code },
  { id: "whatsapp", label: "WhatsApp", icon: MessageCircle },
];

export default function HourlyModule({ health, onHealthChange }) {
  const [tab, setTab] = useState("process");
  const [status, setStatus] = useState({ backend: null, db: null });

  const refreshStatus = useCallback(async () => {
    try {
      const [backend, db] = await Promise.allSettled([
        getBackendFilesStatus(),
        getDbStatus(),
      ]);
      setStatus({
        backend: backend.status === "fulfilled" ? backend.value : null,
        db: db.status === "fulfilled" ? db.value : null,
      });
    } catch {
      setStatus({ backend: null, db: null });
    }
  }, []);

  useEffect(() => {
    refreshStatus();
  }, [refreshStatus]);

  return (
    <div className="eod">
      <div className="eod-head">
        <div>
          <p className="eyebrow">Hourly Module</p>
          <h1 className="eod-title">Hourly Collection Merging</h1>
          <p className="muted eod-subtitle">
            Merge live collection reports, run VBA macro steps, and dispatch files to teams.
          </p>
        </div>
      </div>

      <div className="tabs eod-tabs">
        {TABS.map((t) => {
          const Icon = t.icon;
          return (
            <button key={t.id} className={tab === t.id ? "active" : ""} onClick={() => setTab(t.id)}>
              <Icon size={15} style={{ marginRight: 6, verticalAlign: "-2px" }} /> {t.label}
            </button>
          );
        })}
      </div>

      {tab === "process" && (
        <HourlyProcess status={status} refreshStatus={refreshStatus} />
      )}
      {tab === "vba" && (
        <VbaRunnerPanel />
      )}
      {tab === "whatsapp" && (
        <HourlyWhatsApp health={health} />
      )}
    </div>
  );
}
