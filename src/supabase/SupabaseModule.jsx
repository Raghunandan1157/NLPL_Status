import { useState } from "react";
import { CalendarDays, CloudUpload, Database, RefreshCw, Workflow } from "lucide-react";
import { Button, FileDrop, useToast } from "../components/ui.jsx";
import { todayDMY } from "../lib/format.js";
import {
  checkDate,
  checkDisbursement,
  checkHourly,
  syncDaily,
  syncDisbursement,
  syncHourly,
} from "./supabaseApi.js";
import "../eod/eod.css";

const TABS = [
  { id: "daily", label: "Daily", icon: CalendarDays },
  { id: "hourly", label: "Hourly", icon: Workflow },
  { id: "disbursement", label: "Disbursement", icon: CloudUpload },
];

function todayIso() {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

function DailyTab() {
  const toast = useToast();
  const [date, setDate] = useState(todayIso());
  const [busy, setBusy] = useState("");
  const [status, setStatus] = useState(null);

  async function check() {
    setBusy("check");
    try {
      const r = await checkDate(date);
      if (r.success) {
        setStatus(r);
        toast.info(r.exists ? `${r.count} rows already on ${date}.` : `No rows on ${date}.`);
      } else toast.error(r.message);
    } catch (e) {
      toast.error(e.message);
    } finally {
      setBusy("");
    }
  }
  async function sync() {
    setBusy("sync");
    try {
      const r = await syncDaily(date);
      if (r.success) toast.success(r.message || "Synced.", "Daily synced");
      else toast.error(r.message, "Sync failed");
    } catch (e) {
      toast.error(e.message, "Sync failed");
    } finally {
      setBusy("");
    }
  }

  return (
    <div className="panel">
      <div className="panel-header">
        <div>
          <p className="eyebrow">EOD daily</p>
          <h2>Sync daily performance</h2>
          <p className="sub">Pushes the latest EOD Employee Report into Grow_With_Me._stage_daily_performance for a date.</p>
        </div>
      </div>
      <div className="control-grid" style={{ gridTemplateColumns: "1fr auto auto" }}>
        <label className="field">
          <span>Date</span>
          <input className="input" type="date" value={date} onChange={(e) => setDate(e.target.value)} />
        </label>
        <Button variant="ghost" icon={RefreshCw} loading={busy === "check"} onClick={check} style={{ alignSelf: "end" }}>
          Check
        </Button>
        <Button variant="success" icon={CloudUpload} loading={busy === "sync"} onClick={sync} style={{ alignSelf: "end" }}>
          Sync (override)
        </Button>
      </div>
      {status && (
        <p className="muted" style={{ fontSize: 12.5, marginTop: 4 }}>
          {status.exists ? `${status.count} rows currently on ${status.date} — syncing will overwrite.` : `No rows yet on ${status.date}.`}
        </p>
      )}
    </div>
  );
}

function HourlyTab() {
  const toast = useToast();
  const [busy, setBusy] = useState("");
  const [count, setCount] = useState(null);

  async function check() {
    setBusy("check");
    try {
      const r = await checkHourly();
      if (r.success) {
        setCount(r.count);
        toast.info(`${r.count} rows currently staged.`);
      } else toast.error(r.message);
    } catch (e) {
      toast.error(e.message);
    } finally {
      setBusy("");
    }
  }
  async function sync() {
    setBusy("sync");
    try {
      const r = await syncHourly();
      if (r.success) toast.success(r.message || "Synced.", "Hourly synced");
      else toast.error(r.message, "Sync failed");
    } catch (e) {
      toast.error(e.message, "Sync failed");
    } finally {
      setBusy("");
    }
  }

  return (
    <div className="panel">
      <div className="panel-header">
        <div>
          <p className="eyebrow">Quick hourly</p>
          <h2>Sync hourly performance</h2>
          <p className="sub">Pushes the latest Quick Report into _stage_hourly_performance (full override, no date).</p>
        </div>
      </div>
      <div className="actions">
        <Button variant="ghost" icon={RefreshCw} loading={busy === "check"} onClick={check}>
          Check staged rows{count != null ? ` (${count})` : ""}
        </Button>
        <Button variant="success" icon={CloudUpload} className="grow" loading={busy === "sync"} onClick={sync}>
          Sync Quick Report (override all)
        </Button>
      </div>
    </div>
  );
}

function DisbTab() {
  const toast = useToast();
  const [file, setFile] = useState(null);
  const [busy, setBusy] = useState("");

  async function sync() {
    if (!file) {
      toast.warn("Upload a disbursement file first.");
      return;
    }
    setBusy("sync");
    try {
      const r = await syncDisbursement(file, []); // all dates
      if (r.success) toast.success(r.message || "Synced.", "Disbursement synced");
      else toast.error(r.message, "Sync failed");
    } catch (e) {
      toast.error(e.message, "Sync failed");
    } finally {
      setBusy("");
    }
  }

  return (
    <div className="panel">
      <div className="panel-header">
        <div>
          <p className="eyebrow">Disbursement</p>
          <h2>Sync disbursement</h2>
          <p className="sub">Aggregates a disbursement export and pushes it into Grow_With_Me.disbursement_daily.</p>
        </div>
      </div>
      <div className="file-grid" style={{ gridTemplateColumns: "1fr" }}>
        <FileDrop
          label="Disbursement export"
          hint=".csv / .xlsx"
          accept=".csv,.xlsx,.xls"
          file={file}
          onFile={setFile}
          disabled={Boolean(busy)}
        />
      </div>
      <div className="actions">
        <Button variant="success" icon={CloudUpload} className="grow" disabled={!file} loading={busy === "sync"} onClick={sync}>
          Sync all dates to Supabase
        </Button>
      </div>
    </div>
  );
}

export default function SupabaseModule() {
  const [tab, setTab] = useState("daily");

  return (
    <div className="eod">
      <div className="eod-head">
        <div>
          <p className="eyebrow">Supabase Sync</p>
          <h1 className="eod-title">Supabase Grow_With_Me Sync</h1>
          <p className="muted eod-subtitle">
            Mirror EOD daily, Quick hourly and disbursement data into the Supabase staging tables.
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

      <div className="banner" style={{ marginBottom: 16 }}>
        <Database size={15} /> Requires <b>SUPABASE_SERVICE_KEY</b> in the backend .env. Without it, sync returns a clear "not configured" message.
      </div>

      {tab === "daily" && <DailyTab />}
      {tab === "hourly" && <HourlyTab />}
      {tab === "disbursement" && <DisbTab />}
    </div>
  );
}
