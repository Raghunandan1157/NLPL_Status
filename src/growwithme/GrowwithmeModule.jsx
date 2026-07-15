import { useEffect, useState } from "react";
import { CalendarDays, CloudUpload, Database, PieChart, Plug, RefreshCw, UserCog, Users, Workflow } from "lucide-react";
import { Button, FileDrop, useToast } from "../components/ui.jsx";
import { fetchEmployee, ping, saveEmployee, scopeOptions, syncDaily, syncDisbursement, syncHourly, syncPortfolio, syncStaff } from "./growwithmeApi.js";
import "../eod/eod.css";

const TABS = [
  { id: "daily", label: "Daily", icon: CalendarDays },
  { id: "hourly", label: "Hourly", icon: Workflow },
  { id: "disbursement", label: "Disbursement", icon: CloudUpload },
  { id: "portfolio", label: "Portfolio", icon: PieChart },
  { id: "staff", label: "Staff", icon: Users },
  { id: "employee", label: "Employee", icon: UserCog },
];

const SCOPES = ["full", "region", "division", "area", "branch", "self"];

// Module-level (stable identity) so editing a field doesn't remount the input
// and lose focus between keystrokes.
function EditField({ label, value, onChange, type = "text" }) {
  return (
    <label className="field">
      <span>{label}</span>
      <input className="input" type={type} value={value || ""} onChange={onChange} />
    </label>
  );
}

function todayIso() {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

function nowHour() {
  return `${String(new Date().getHours()).padStart(2, "0")}:00`;
}

function thisMonth() {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
}

function DailyTab() {
  const toast = useToast();
  const [date, setDate] = useState(todayIso());
  const [file, setFile] = useState(null);
  const [busy, setBusy] = useState(false);

  async function sync() {
    setBusy(true);
    try {
      const r = await syncDaily(date, file);
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
          <p className="sub">Pushes an EOD Employee Report — or a raw Daily Collection Report (its Employee Data sheet) — into GrowwithmeDB (collection grain 2). Whole-date override. Uses the latest generated report, or upload your own below.</p>
        </div>
      </div>
      <div className="file-grid" style={{ gridTemplateColumns: "1fr", marginBottom: 12 }}>
        <FileDrop label="Upload EOD Employee Report or Daily Collection Report (optional)" hint=".xlsx — an EOD Employee Report, or a raw Daily Collection Report. Leave empty to use the latest generated report." accept=".xlsx,.xls" file={file} onFile={setFile} disabled={busy} />
      </div>
      <div className="control-grid" style={{ gridTemplateColumns: "1fr auto" }}>
        <label className="field">
          <span>Date</span>
          <input className="input" type="date" value={date} onChange={(e) => setDate(e.target.value)} />
        </label>
        <Button variant="success" icon={CloudUpload} loading={busy} onClick={sync} style={{ alignSelf: "end" }}>
          {file ? "Upload & sync" : "Sync latest"}
        </Button>
      </div>
    </div>
  );
}

function HourlyTab() {
  const toast = useToast();
  const [file, setFile] = useState(null);
  const [busy, setBusy] = useState(false);
  // The Quick Report has no timestamp of its own, so the user states which
  // date + hour this snapshot is "as of". These flow through /sync-hourly as
  // period_date + period_hour and are what the growwithme report displays.
  const [date, setDate] = useState(todayIso());
  const [time, setTime] = useState(nowHour());

  async function sync() {
    setBusy(true);
    try {
      const hour = Number(String(time).split(":")[0]); // whole-hour grain
      const r = await syncHourly(date, hour, file);
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
          <p className="sub">Pushes a Quick Report into GrowwithmeDB (collection grain 1). Full-snapshot override. Uses the latest generated report, or upload your own below.</p>
        </div>
      </div>
      <div className="file-grid" style={{ gridTemplateColumns: "1fr", marginBottom: 12 }}>
        <FileDrop label="Upload Quick Report (optional)" hint=".xlsx — leave empty to use the latest generated report" accept=".xlsx,.xls" file={file} onFile={setFile} disabled={busy} />
      </div>
      <div className="control-grid" style={{ gridTemplateColumns: "1fr 1fr auto", marginBottom: 12 }}>
        <label className="field">
          <span>As-of date</span>
          <input className="input" type="date" value={date} onChange={(e) => setDate(e.target.value)} disabled={busy} />
        </label>
        <label className="field">
          <span>As-of time (hour)</span>
          <input className="input" type="time" step={3600} value={time} onChange={(e) => setTime(e.target.value)} disabled={busy} />
        </label>
        <Button variant="success" icon={CloudUpload} loading={busy} onClick={sync} style={{ alignSelf: "end" }}>
          {file ? "Upload & sync Quick Report" : "Sync latest Quick Report"}
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
  const [file, setFile] = useState(null);
  const [busy, setBusy] = useState(false);

  async function sync() {
    setBusy(true);
    try {
      const r = await syncPortfolio(month, file);
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
            Reads a Month-End Employee Report's <b>POS</b> sheet (branch + product PrincipalOS) — or a{" "}
            <b>raw PAR file</b> (POS is built from PrincipalOS × DPD) — and pushes it into GrowwithmeDB.portfolio_* for
            the selected month. Whole-month override. Uses the latest generated report, or upload your own below.
          </p>
        </div>
      </div>
      <div className="file-grid" style={{ gridTemplateColumns: "1fr", marginBottom: 12 }}>
        <FileDrop label="Upload Month-End report or raw PAR (optional)" hint=".xlsx — a Month-End report (POS sheet) or a raw PAR. A large PAR can take ~2 min. Leave empty to use the latest generated report." accept=".xlsx,.xls" file={file} onFile={setFile} disabled={busy} />
      </div>
      <div className="control-grid" style={{ gridTemplateColumns: "1fr auto" }}>
        <label className="field">
          <span>Month</span>
          <input className="input" type="month" value={month} onChange={(e) => setMonth(e.target.value)} />
        </label>
        <Button variant="success" icon={CloudUpload} loading={busy} onClick={sync} style={{ alignSelf: "end" }}>
          {file ? "Upload & sync" : "Sync latest"}
        </Button>
      </div>
    </div>
  );
}

function StaffTab() {
  const toast = useToast();
  const [file, setFile] = useState(null);
  const [busy, setBusy] = useState(false);

  async function sync() {
    if (!file) {
      toast.warn("Upload a staff master file first.");
      return;
    }
    setBusy(true);
    try {
      const r = await syncStaff(file);
      if (r.success) toast.success(r.message || "Synced.", "Staff details synced to local DB");
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
          <p className="eyebrow">Staff master</p>
          <h2>Sync staff details to database</h2>
          <p className="sub">
            Reads a staff master's <b>Working</b> sheet and refreshes each employee's name, phone, joining date, DOB
            and reporting manager in GrowwithmeDB. <b>Details-only</b> — never changes branch/role/hierarchy. Upsert
            (never deletes); re-running is safe.
          </p>
        </div>
      </div>
      <div className="file-grid" style={{ gridTemplateColumns: "1fr" }}>
        <FileDrop
          label="Staff master (Working sheet)"
          hint=".xlsx — columns like NMEmpId, Name, PersonalMobile, Date of Joining, ReportingOfficerEMPID"
          accept=".xlsx,.xls"
          file={file}
          onFile={setFile}
          disabled={busy}
        />
      </div>
      <div className="actions">
        <Button variant="success" icon={CloudUpload} className="grow" disabled={!file} loading={busy} onClick={sync}>
          Sync staff details
        </Button>
      </div>
    </div>
  );
}

const TARGETED = ["region", "division", "area", "branch"]; // scopes that take a specific target

const blankEmployee = (code) => ({
  emp_id: code || "", name: "", mobile: "", branch: "", role: "", designation: "",
  scope: "self", scope_target: "", reporting_officer_id: "", gender: "", date_of_joining: "", date_of_birth: "",
  region: "", division: "", area: "",
});

function EmployeeTab() {
  const toast = useToast();
  const [empId, setEmpId] = useState("");
  const [form, setForm] = useState(null);
  const [mode, setMode] = useState(null); // "edit" | "create"
  const [busy, setBusy] = useState(false);
  const [saving, setSaving] = useState(false);
  const [opts, setOpts] = useState({ region: [], division: [], area: [], branch: [] });

  // Load the region/division/area/branch name lists once, for the target picker.
  useEffect(() => {
    scopeOptions()
      .then((r) => r.options && setOpts(r.options))
      .catch(() => {});
  }, []);

  async function load() {
    const code = empId.trim();
    if (!code) {
      toast.warn("Enter an employee code (e.g. NL13071).");
      return;
    }
    setBusy(true);
    try {
      const r = await fetchEmployee(code);
      if (r.found === false) {
        // Genuinely not in the DB → open a blank form to CREATE this employee.
        setForm(blankEmployee(code));
        setMode("create");
        toast.warn(`"${code}" isn't in the database — fill this form and Save to create them.`, "New employee");
      } else {
        setForm({ ...r.employee });
        setMode("edit");
        toast.success("Details loaded.", r.employee.name || code);
      }
    } catch (e) {
      // A real failure (backend not running, network, server error) — NOT "create".
      setForm(null);
      setMode(null);
      toast.error(e.message, "Fetch failed");
    } finally {
      setBusy(false);
    }
  }

  function startNew() {
    setForm(blankEmployee(empId.trim()));
    setMode("create");
  }

  const set = (k, v) => setForm((f) => ({ ...f, [k]: v }));

  async function save() {
    if (!String(form.emp_id || "").trim()) {
      toast.warn("Employee code is required.");
      return;
    }
    setSaving(true);
    try {
      const r = await saveEmployee({ ...form, emp_id: String(form.emp_id).trim() });
      if (r.success) {
        toast.success(r.message || "Saved.", `${form.emp_id} ${mode === "create" ? "created" : "updated"}`);
        setMode("edit"); // after creating, it now exists
      } else toast.error(r.message, "Save failed");
    } catch (e) {
      toast.error(e.message, "Save failed");
    } finally {
      setSaving(false);
    }
  }

  const F = (label, k, type) => (
    <EditField label={label} type={type} value={form[k]} onChange={(e) => set(k, e.target.value)} />
  );

  return (
    <div className="panel">
      <div className="panel-header">
        <div>
          <p className="eyebrow">Employee editor</p>
          <h2>Edit or create an employee</h2>
          <p className="sub">
            <b>Fetch</b> an existing employee to edit their details and data-access <b>scope</b>, or click <b>New</b>{" "}
            to create one.
          </p>
        </div>
      </div>

      <div className="control-grid" style={{ gridTemplateColumns: "1fr auto auto", marginBottom: 12, gap: 8 }}>
        <label className="field">
          <span>Employee code</span>
          <input
            className="input"
            placeholder="e.g. NL13071"
            value={empId}
            onChange={(e) => setEmpId(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && load()}
          />
        </label>
        <Button variant="ghost" icon={RefreshCw} loading={busy} onClick={load} style={{ alignSelf: "end" }}>
          Fetch
        </Button>
        <Button variant="ghost" icon={UserCog} onClick={startNew} style={{ alignSelf: "end" }}>
          New
        </Button>
      </div>

      {form && (
        <>
          <div className="banner" style={{ marginBottom: 12 }}>
            <UserCog size={15} />{" "}
            {mode === "create" ? (
              <span>Creating a <b>new</b> employee.</span>
            ) : (
              <span>Editing <b>{form.emp_id}</b>.</span>
            )}
          </div>

          <div className="control-grid" style={{ gridTemplateColumns: "1fr 1fr", gap: 12 }}>
            {mode === "create" && F("Employee code", "emp_id")}
            {F("Name", "name")}
            {F("Mobile", "mobile")}
            {F("Branch", "branch")}
            {F("Role", "role")}
            {F("Designation", "designation")}
            <label className="field">
              <span>Scope (data access)</span>
              <select
                className="input"
                value={form.scope || "self"}
                onChange={(e) => {
                  const v = e.target.value;
                  setForm((f) => ({ ...f, scope: v, scope_target: TARGETED.includes(v) ? f.scope_target : "" }));
                }}
              >
                {SCOPES.map((s) => (
                  <option key={s} value={s}>
                    {s}
                  </option>
                ))}
              </select>
            </label>
            {form.scope === "region" ? (
              <div className="field" style={{ gridColumn: "1 / -1" }}>
                <span>Which regions? (tick one or more — none = use their own branch&apos;s region)</span>
                <div style={{ display: "flex", flexWrap: "wrap", gap: 12, padding: "8px 2px" }}>
                  {(opts.region || []).map((n) => {
                    const sel = (form.scope_target || "").split(",").map((s) => s.trim()).filter(Boolean);
                    const checked = sel.includes(n);
                    return (
                      <label key={n} style={{ display: "flex", alignItems: "center", gap: 6, fontWeight: 500, cursor: "pointer" }}>
                        <input
                          type="checkbox"
                          checked={checked}
                          onChange={() => set("scope_target", (checked ? sel.filter((x) => x !== n) : [...sel, n]).join(","))}
                        />
                        {n}
                      </label>
                    );
                  })}
                </div>
              </div>
            ) : TARGETED.includes(form.scope) ? (
              <label className="field">
                <span>Which {form.scope}?</span>
                <select className="input" value={form.scope_target || ""} onChange={(e) => set("scope_target", e.target.value)}>
                  <option value="">— use their own branch&apos;s {form.scope} —</option>
                  {(opts[form.scope] || []).map((n) => (
                    <option key={n} value={n}>
                      {n}
                    </option>
                  ))}
                </select>
              </label>
            ) : null}
            {F("Reporting officer (emp code)", "reporting_officer_id")}
            {F("Gender", "gender")}
            {F("Date of joining", "date_of_joining", "date")}
            {F("Date of birth", "date_of_birth", "date")}
          </div>

          <div className="banner" style={{ margin: "12px 0" }}>
            <Database size={15} /> Current location: <b>{form.region || "—"}</b> / {form.division || "—"} /{" "}
            {form.area || "—"}. For scope <b>region/division/area/branch</b>, the boundary comes from the branch above.
          </div>

          <div className="actions">
            <Button variant="success" icon={CloudUpload} className="grow" loading={saving} onClick={save}>
              Save changes
            </Button>
          </div>
        </>
      )}
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
      {tab === "staff" && <StaffTab />}
      {tab === "employee" && <EmployeeTab />}
    </div>
  );
}
