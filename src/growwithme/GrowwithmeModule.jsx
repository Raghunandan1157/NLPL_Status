import { useState } from "react";
import { CalendarDays, CloudUpload, Database, PieChart, Plug, RefreshCw, Workflow } from "lucide-react";
import { Button, FileDrop, useToast } from "../components/ui.jsx";
import { ping, syncDaily, syncDisbursement, syncHourly, syncPortfolio } from "./growwithmeApi.js";
import "../eod/eod.css";

const TABS = [
  { id: "daily", label: "Daily", icon: CalendarDays },
  { id: "hourly", label: "Hourly", icon: Workflow },
  { id: "disbursement", label: "Disbursement", icon: CloudUpload },
  { id: "portfolio", label: "Portfolio", icon: PieChart },
];

function todayIso() {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

function thisMonth() {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
}

function DailyTab() {
  const toast = useToast();
  const [date, setDate] = useState(todayIso());
  const [busy, setBusy] = useState(false);

  async function sync() {
    setBusy(true);
    try {
      const r = await syncDaily(date);
      if (r.success) toast.success(r.message || "Synced.", "Daily synced to local DB");
      else toast.error(r.message, "Sync failed");
    } catch (e) {
      toast.error(e.message, "Sync failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="panel">
      <div className="panel-header">
        <div>
          <p className="eyebrow">EOD daily</p>
          <h2>Sync daily to database</h2>
          <p className="sub">Pushes the latest EOD Employee Report into GrowwithmeDB (collection grain 2). Whole-date override.</p>
        </div>
      </div>
      <div className="control-grid" style={{ gridTemplateColumns: "1fr auto" }}>
        <label className="field">
          <span>Date</span>
          <input className="input" type="date" value={date} onChange={(e) => setDate(e.target.value)} />
        </label>
        <Button variant="success" icon={CloudUpload} loading={busy} onClick={sync} style={{ alignSelf: "end" }}>
          Sync to database
        </Button>
      </div>
    </div>
  );
}

function HourlyTab() {
  const toast = useToast();
  const [busy, setBusy] = useState(false);

  async function sync() {
    setBusy(true);
    try {
      const r = await syncHourly(); // backend defaults date + hour to now
      if (r.success) toast.success(r.message || "Synced.", "Hourly synced to local DB");
      else toast.error(r.message, "Sync failed");
    } catch (e) {
      toast.error(e.message, "Sync failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="panel">
      <div className="panel-header">
        <div>
          <p className="eyebrow">Quick hourly</p>
          <h2>Sync hourly to database</h2>
          <p className="sub">Pushes the latest Quick Report into GrowwithmeDB (collection grain 1). Full-snapshot override.</p>
        </div>
      </div>
      <div className="actions">
        <Button variant="success" icon={CloudUpload} className="grow" loading={busy} onClick={sync}>
          Sync Quick Report to database
        </Button>
      </div>
    </div>
  );
}

function DisbTab() {
  const toast = useToast();
  const [file, setFile] = useState(null);
  const [busy, setBusy] = useState(false);

  async function sync() {
    if (!file) {
      toast.warn("Upload a disbursement file first.");
      return;
    }
    setBusy(true);
    try {
      const r = await syncDisbursement(file, []); // all dates
      if (r.success) toast.success(r.message || "Synced.", "Disbursement synced to local DB");
      else toast.error(r.message, "Sync failed");
    } catch (e) {
      toast.error(e.message, "Sync failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="panel">
      <div className="panel-header">
        <div>
          <p className="eyebrow">Disbursement</p>
          <h2>Sync disbursement to database</h2>
          <p className="sub">Aggregates a disbursement export by month and pushes it into GrowwithmeDB.disbursement. Whole-month override.</p>
        </div>
      </div>
      <div className="file-grid" style={{ gridTemplateColumns: "1fr" }}>
        <FileDrop
          label="Disbursement export"
          hint=".csv / .xlsx"
          accept=".csv,.xlsx,.xls"
          file={file}
          onFile={setFile}
          disabled={busy}
        />
      </div>
      <div className="actions">
        <Button variant="success" icon={CloudUpload} className="grow" disabled={!file} loading={busy} onClick={sync}>
          Sync to database
        </Button>
      </div>
    </div>
  );
}

function PortfolioTab() {
  const toast = useToast();
  const [month, setMonth] = useState(thisMonth());
  const [busy, setBusy] = useState(false);

  async function sync() {
    setBusy(true);
    try {
      const r = await syncPortfolio(month);
      if (r.success) toast.success(r.message || "Synced.", "Portfolio synced to local DB");
      else toast.error(r.message, "Sync failed");
    } catch (e) {
      toast.error(e.message, "Sync failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="panel">
      <div className="panel-header">
        <div>
          <p className="eyebrow">Portfolio (POS)</p>
          <h2>Sync portfolio to database</h2>
          <p className="sub">
            Reads the latest Month-End Employee Report's <b>POS</b> sheet (branch + product PrincipalOS) and pushes it
            into GrowwithmeDB.portfolio_* for the selected month. Whole-month override. Generate a Month-End report first.
          </p>
        </div>
      </div>
      <div className="control-grid" style={{ gridTemplateColumns: "1fr auto" }}>
        <label className="field">
          <span>Month</span>
          <input className="input" type="month" value={month} onChange={(e) => setMonth(e.target.value)} />
        </label>
        <Button variant="success" icon={CloudUpload} loading={busy} onClick={sync} style={{ alignSelf: "end" }}>
          Sync to database
        </Button>
      </div>
    </div>
  );
}

export default function GrowwithmeModule() {
  const toast = useToast();
  const [tab, setTab] = useState("daily");
  const [pinging, setPinging] = useState(false);

  async function test() {
    setPinging(true);
    try {
      const r = await ping();
      if (r.success) toast.success(`Reachable (db: ${r.database || "?"}).`, "GrowwithmeDB API");
      else toast.error(r.message, "Not reachable");
    } catch (e) {
      toast.error(e.message, "Not reachable");
    } finally {
      setPinging(false);
    }
  }

  return (
    <div className="eod">
      <div className="eod-head">
        <div>
          <p className="eyebrow">Local DB Sync</p>
          <h1 className="eod-title">GrowwithmeDB Sync</h1>
          <p className="muted eod-subtitle">
            Push EOD daily, Quick hourly, disbursement and portfolio (POS) data into the local MySQL GrowwithmeDB.
          </p>
        </div>
        <Button variant="ghost" icon={Plug} loading={pinging} onClick={test}>
          Test connection
        </Button>
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
        <Database size={15} /> Targets the GrowwithmeDB API at <b>GROWWITHME_API_URL</b>. Each sync replaces the
        pushed scope (whole-scope delete-then-insert).
      </div>

      {tab === "daily" && <DailyTab />}
      {tab === "hourly" && <HourlyTab />}
      {tab === "disbursement" && <DisbTab />}
      {tab === "portfolio" && <PortfolioTab />}
    </div>
  );
}
