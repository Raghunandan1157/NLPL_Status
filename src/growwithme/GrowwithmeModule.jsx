import { useEffect, useState } from "react";
import { CalendarDays, CloudUpload, Database, PieChart, Plug, RefreshCw, UserCog, Users, Workflow } from "lucide-react";
import { Button, FileDrop, useToast } from "../components/ui.jsx";
import { autosyncStatus, fetchEmployee, ping, saveEmployee, scopeOptions, syncDaily, syncDisbursement, syncHourly, syncPortfolio, syncStaff } from "./growwithmeApi.js";
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

// ── Mode of Collection: trim the workbook in the BROWSER before uploading ──
// The "Collection report as on <date>" file is ~5 MB and 34 columns wide, but the
// sync needs only 7 of those columns and only the rows for the selected date
// (the file normally spans several days). Trimming here cuts it to a few hundred
// KB, so the upload can't run into a proxy body limit on the way in.
//
// The column names below MUST match what _parse_collection_channels() reads in
// backend/blueprints/growwithme_sync.py.
const CHANNEL_COLUMNS = [
  "Trxdate", "OfficerID", "OfficerName", "AccountID",
  "CollectionTotal", "ReverseTotal", "ProductID", "CollectionChannel",
];

/** Excel serial (46238) or a real date -> "YYYY-MM-DD". Mirrors _excel_date(). */
function excelToIso(v) {
  if (v == null || v === "") return null;
  if (v instanceof Date) {
    return `${v.getFullYear()}-${String(v.getMonth() + 1).padStart(2, "0")}-${String(v.getDate()).padStart(2, "0")}`;
  }
  const s = String(v).trim();
  if (/^\d+$/.test(s)) {
    // Excel's 1900 epoch with its leap-year quirk: serial 1 == 1900-01-01.
    const d = new Date(Date.UTC(1899, 11, 30) + Number(s) * 86400000);
    return d.toISOString().slice(0, 10);
  }
  return /^\d{4}-\d{2}-\d{2}/.test(s) ? s.slice(0, 10) : null;
}

/** Officer digits, from the name's parentheses. Mirrors _officer_employee_code(). */
function officerDigits(officerId, officerName) {
  const m = /\((\d+)\)/.exec(String(officerName || ""));
  const d = m ? m[1] : String(officerId || "").replace(/\D/g, "");
  return d.replace(/^0+/, "") || null;
}

/**
 * Read `file` and AGGREGATE it to officer × channel totals for `date`, entirely in
 * the browser. Only the aggregate is uploaded — a few hundred KB of JSON instead
 * of a 5 MB workbook, which is what "only the needed rows and columns" means in
 * practice and removes any chance of a proxy body limit rejecting the upload.
 *
 * This mirrors _parse_collection_channels() in growwithme_sync.py exactly (same
 * date filter, same DISTINCT AccountID count, same grouping key). That parser
 * stays as the fallback for a raw workbook upload.
 *
 * Throws with a readable message if the file is the wrong report or holds nothing
 * for the date — better to fail here, in front of the user, than after an upload.
 */
async function aggregateChannelFile(file, date) {
  const XLSX = await import("xlsx");
  const wb = XLSX.read(await file.arrayBuffer(), { type: "array" });
  const sheet = wb.SheetNames.includes("Sheet1") ? "Sheet1" : wb.SheetNames[0];
  const rows = XLSX.utils.sheet_to_json(wb.Sheets[sheet], { defval: null });
  if (!rows.length) throw new Error("The Mode of Collection file is empty.");
  if (!("CollectionChannel" in rows[0])) {
    throw new Error('No "CollectionChannel" column — is this the Collection report?');
  }

  // Aggregated per DATE, not just for the selected one. The report spans several
  // days, and receipts for an earlier date keep appearing in later exports (late
  // postings) — so every date this file carries is worth (re)pushing. The server
  // push is a whole-date replace, which makes that self-correcting.
  const seen = new Set();
  const byDate = new Map(); // 'YYYY-MM-DD' -> Map(key -> slot)
  let matched = 0;
  for (const r of rows) {
    const iso = excelToIso(r.Trxdate);
    if (iso) seen.add(iso);
    // Rows with no usable date belong to the SELECTED date: the file is already
    // date-scoped by its name.
    const day = iso || date;
    const channel = String(r.CollectionChannel ?? "").trim().toUpperCase();
    if (!channel) continue;
    if (day === date) matched++;
    let agg = byDate.get(day);
    if (!agg) {
      agg = new Map();
      byDate.set(day, agg);
    }
    const officerCode = String(r.OfficerID ?? "").trim();
    const productId = r.ProductID == null ? null : String(r.ProductID).trim() || null;
    const key = `${officerCode}|${channel}|${productId}`;
    let slot = agg.get(key);
    if (!slot) {
      slot = {
        officer_code: officerCode || null,
        officer_digits: officerDigits(officerCode, r.OfficerName),
        channel,
        product_id: productId,
        _accounts: new Set(),
        amount: 0,
        reversed: 0,
      };
      agg.set(key, slot);
    }
    if (r.AccountID != null) slot._accounts.add(String(r.AccountID));
    slot.amount += Number(r.CollectionTotal) || 0;
    slot.reversed += Number(r.ReverseTotal) || 0;
  }
  if (!byDate.get(date)?.size) {
    const span = [...seen].sort().join(", ") || "no recognisable dates";
    throw new Error(`No rows dated ${date}. This file covers: ${span}.`);
  }

  const finish = (agg) => [...agg.values()].map(({ _accounts, amount, reversed, ...rest }) => ({
    ...rest,
    accounts: _accounts.size,
    amount: Math.round(amount * 100) / 100,
    reversed: Math.round(reversed * 100) / 100,
  }));
  const out = finish(byDate.get(date));
  byDate.delete(date);
  const extras = {};
  for (const [d, agg] of byDate) extras[d] = finish(agg);
  return { rows: out, extras, matched, total: rows.length, dates: [...seen].sort() };
}

function todayIso() {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

// "YYYY-MM-DD" for YESTERDAY — the day a daily report actually covers. Defaulting
// the Date field to TODAY meant a report generated overnight for the 5th, uploaded
// on the 6th, silently landed under the 6th: the sync is a whole-date override and
// never questions the date it is handed, so nothing warned about it.
function yesterdayIso() {
  const d = new Date();
  d.setDate(d.getDate() - 1);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

/**
 * Pull the as-on date out of a report filename, e.g.
 *   "Daily Collection Report as on 05-08-2026.xlsx" -> "2026-08-05"
 *   "Collection report as on 04-08-2026.xlsx"       -> "2026-08-04"
 * The file states which day it covers, so it is a far better source than a date
 * box the user has to remember to change. Returns null when there's no match.
 */
function dateFromFilename(name) {
  const m = /(\d{2})-(\d{2})-(\d{4})/.exec(String(name || ""));
  if (!m) return null;
  const [, dd, mm, yyyy] = m;
  const d = Number(dd), mo = Number(mm);
  if (d < 1 || d > 31 || mo < 1 || mo > 12) return null;
  return `${yyyy}-${mm}-${dd}`;
}

function nowHour() {
  return `${String(new Date().getHours()).padStart(2, "0")}:00`;
}

function thisMonth() {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
}

// "2026-07" — the last COMPLETED month. Portfolio is a month-end snapshot, so
// this is the month you normally upload. It is the Portfolio tab's default:
// defaulting to the current (still-running) month meant a July file uploaded on
// 6 Aug silently landed under August, because /portfolio/sync upserts on
// (branch, product, month) and never complains about the month it was handed.
function lastCompletedMonth() {
  const d = new Date();
  d.setDate(1);
  d.setMonth(d.getMonth() - 1);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
}

const MONTH_LABELS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** "2026-07" -> "Jul 2026", for unambiguous display. */
function monthLabel(value) {
  const [y, m] = String(value || "").split("-");
  const i = Number(m) - 1;
  return MONTH_LABELS[i] ? `${MONTH_LABELS[i]} ${y}` : value || "—";
}

// Explicit month list for the picker, newest first: the current (in-progress)
// month, then `count` completed months back. A closed list beats a bare
// <input type="month"> here — the value being uploaded into is always visible
// and can't be left on a stale or mistyped month.
function monthOptions(count = 24) {
  const out = [];
  const d = new Date();
  d.setDate(1);
  for (let i = 0; i <= count; i++) {
    const value = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
    out.push({ value, label: monthLabel(value), current: value === thisMonth() });
    d.setMonth(d.getMonth() - 1);
  }
  return out;
}

function DailyTab() {
  const toast = useToast();
  // Yesterday, not today — see yesterdayIso(). A daily report covers a COMPLETED
  // day, so today's date is almost never the right answer.
  const [date, setDate] = useState(yesterdayIso());
  const [file, setFile] = useState(null);
  // Second, independent workbook: only its CollectionChannel column is read, to
  // drive the Collection module's Mode of Collection panel. Both files upload in
  // one request; either may be omitted.
  const [channelFile, setChannelFile] = useState(null);
  // Third, independent workbook: the EOD "Regular Demand vs Collection" output.
  // Only its `_precomp` sheet is read, for the RUPEE AMOUNTS — the Daily
  // Collection Report has no amount columns at all, so without this (or an EOD
  // run archived for the date) the MIS Amount view has nothing to show.
  const [amountFile, setAmountFile] = useState(null);
  const [trimNote, setTrimNote] = useState("");
  const [dateNote, setDateNote] = useState("");
  const [busy, setBusy] = useState(false);

  // Take the date from the FILENAME whenever it states one — the file knows which
  // day it covers, the date box only knows what it was last set to.
  function adoptFileDate(f, which) {
    if (!f) return;
    const d = dateFromFilename(f.name);
    if (!d) return;
    setDate((cur) => {
      if (cur !== d) setDateNote(`Date set to ${d} from "${f.name}".`);
      return d;
    });
    void which;
  }

  async function sync() {
    setBusy(true);
    try {
      // Aggregate the channel workbook in the browser and upload only the result.
      // A 5 MB / 34-column / 30k-row file becomes ~440 KB of JSON, so the upload
      // cannot hit a proxy body limit.
      let channelRows = null;
      let channelExtras = null;
      if (channelFile) {
        try {
          const t = await aggregateChannelFile(channelFile, date);
          channelRows = t.rows;
          channelExtras = t.extras;
          const extraDays = Object.keys(t.extras).length;
          const kb = (JSON.stringify(t.rows).length / 1024).toFixed(0);
          setTrimNote(`Mode of Collection: ${t.matched.toLocaleString()} of ${t.total.toLocaleString()} rows used for ${date}, sent as ${t.rows.length.toLocaleString()} officer×channel totals (${kb} KB)${extraDays ? ` · ${extraDays} other date(s) in the file will be refreshed too` : ""}.`);
        } catch (e) {
          setTrimNote("");
          toast.error(e.message, "Mode of Collection file");
          setBusy(false);
          return;
        }
      }
      const r = await syncDaily(date, file, channelRows, amountFile, channelExtras);
      // The channel push is reported separately so a channel problem is visible
      // even when the collection sync itself succeeded.
      if (r.success && r.channels_ok === false) {
        toast.warn(r.message, "Daily synced — Mode of Collection failed");
      } else if (r.success && !r.amount_rows) {
        // Counts landed but no rupees — the MIS Amount toggle will stay hidden
        // for this date. Silent success here is what made the missing amounts so
        // hard to spot in the first place.
        toast.warn(r.message, "Daily synced — no rupee amounts");
      } else if (r.success && r.amount_warning) {
        toast.warn(r.message, "Daily synced — check the amount source");
      } else if (r.success) {
        toast.success(r.message || "Synced.", "Daily synced to local DB");
      } else {
        toast.error(r.message, "Sync failed");
      }
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
          <p className="sub">Pushes an EOD Employee Report — or a raw Daily Collection Report (its Employee Data sheet) — into GrowwithmeDB (collection grain 2). Whole-date override. All three boxes below are optional: each falls back to the report already generated for this date, and attaching one only overrides that. File 1 gives accounts, file 2 Mode of Collection, file 3 the rupee amounts.</p>
        </div>
      </div>
      {/* Three independent uploads, sent together in one request. Every box is
          optional and each carries an example filename, because all three are
          ".xlsx from the same daily pack" and are otherwise easy to mix up.
          FileDrop shows an X to remove a file once attached. */}
      <div className="file-grid" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(320px, 1fr))", marginBottom: 12 }}>
        <FileDrop
          label="1 · Collection report — accounts (optional)"
          hint='e.g. "Daily Collection Report as on 04-08-2026.xlsx" — an EOD Employee Report or a raw Daily Collection Report. Gives demand/collection ACCOUNT COUNTS. Leave empty to use the latest generated report.'
          accept=".xlsx,.xls"
          file={file}
          onFile={(f) => { setFile(f); adoptFileDate(f, "collection"); }}
          disabled={busy}
        />
        <FileDrop
          label="2 · Mode of Collection (optional)"
          hint='e.g. "Collection report as on 04-08-2026.xlsx" — only its CollectionChannel column is read. May span several days; EVERY date it carries is pushed (selected date + a refresh of the rest, so late-posted receipts are picked up).'
          accept=".xlsx,.xls"
          file={channelFile}
          // Clearing (or replacing) the file must drop the row-count note too —
          // otherwise it keeps describing a file that is no longer attached.
          onFile={(f) => { setChannelFile(f); setTrimNote(""); adoptFileDate(f, "channel"); }}
          disabled={busy}
        />
        <FileDrop
          label="3 · Regular Demand vs Collection — amounts (optional)"
          hint='e.g. "Regular Demand vs Collection.xlsx" (the EOD output workbook) — the ONLY source of rupee amounts. Leave empty to use the EOD run archived for this date.'
          accept=".xlsx,.xls"
          file={amountFile}
          onFile={(f) => { setAmountFile(f); adoptFileDate(f, "amount"); }}
          disabled={busy}
        />
      </div>
      <div className="control-grid" style={{ gridTemplateColumns: "1fr auto" }}>
        <label className="field">
          <span>Date</span>
          <input className="input" type="date" value={date} onChange={(e) => setDate(e.target.value)} />
        </label>
        <Button variant="success" icon={CloudUpload} loading={busy} onClick={sync} style={{ alignSelf: "end" }}>
          {file || channelFile || amountFile ? "Upload & sync" : "Sync latest"}
        </Button>
      </div>
      <p className="sub" style={{ marginTop: 10 }}>
        Syncing <b>{date || "—"}</b>
        {dateNote ? <><br /><span style={{ color: "#b45309", fontWeight: 600 }}>{dateNote}</span></> : null}
        {channelFile ? " · Mode of Collection will be refreshed for this date." : " · no Mode of Collection file attached."}
        {amountFile ? " · rupee amounts from the attached workbook." : " · amounts from this date's EOD run, if one exists."}
        {trimNote ? <><br />{trimNote}</> : null}
      </p>
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
  // Defaults to the last COMPLETED month — the one a month-end POS file belongs
  // to. See lastCompletedMonth() for why the current month is the wrong default.
  const [month, setMonth] = useState(lastCompletedMonth());
  const [file, setFile] = useState(null);
  const [busy, setBusy] = useState(false);
  const options = monthOptions();
  const isCurrentMonth = month === thisMonth();

  async function sync() {
    setBusy(true);
    try {
      const r = await syncPortfolio(month, file);
      // Report the month the SERVER resolved, not the one we sent — if they ever
      // disagree, the toast is where that needs to be visible.
      const landed = monthLabel(r.period_month ? String(r.period_month).slice(0, 7) : month);
      if (r.success) toast.success(r.message || "Synced.", `Portfolio synced — ${landed}`);
      else toast.error(r.message, `Sync failed — ${landed}`);
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
          <span>Month to upload into</span>
          <select className="input" value={month} onChange={(e) => setMonth(e.target.value)} disabled={busy}>
            {options.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
                {o.current ? " — current month (in progress)" : ""}
              </option>
            ))}
          </select>
        </label>
        <Button variant="success" icon={CloudUpload} loading={busy} onClick={sync} style={{ alignSelf: "end" }}>
          {file ? "Upload & sync" : "Sync latest"}
        </Button>
      </div>
      {/* The target month restated in words, right where the click happens —
          "2026-07" in a picker is easy to misread; "July 2026" is not. */}
      <p className="sub" style={{ marginTop: 10 }}>
        This will write POS into <b>{monthLabel(month)}</b>, replacing whatever that month already holds.
        {isCurrentMonth ? (
          <b style={{ color: "#b45309" }}>
            {" "}You've picked the current month, which hasn't ended yet — a month-end file usually belongs to{" "}
            {monthLabel(lastCompletedMonth())}.
          </b>
        ) : null}
      </p>
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

// Read-only banner: outcome of the automatic pushes the backend fires after
// every EOD / Hourly report generation. Polls while the page is open so a sync
// that finishes in the background updates without a reload.
function AutoSyncStatus() {
  const [st, setSt] = useState(null);
  useEffect(() => {
    let alive = true;
    const load = () => autosyncStatus().then((r) => { if (alive) setSt(r); }).catch(() => {});
    load();
    const id = setInterval(load, 15000);
    return () => { alive = false; clearInterval(id); };
  }, []);
  const entries = ["daily", "hourly"].map((k) => [k, st?.[k]]).filter(([, v]) => v);
  if (!entries.length) return null;
  return (
    <div className="banner" style={{ marginBottom: 16 }}>
      <RefreshCw size={15} />
      <div>
        <b>Auto-sync</b> (runs by itself after every EOD / Hourly generation):{" "}
        {entries.map(([k, v]) => (
          <span key={k} style={{ marginRight: 14 }} title={v.message || ""}>
            {k === "daily" ? "Daily" : "Hourly"} {v.date}
            {v.period_hour != null ? ` @${v.period_hour}h` : ""} —{" "}
            {v.state === "running" ? (
              <b>running…</b>
            ) : v.success ? (
              <b style={{ color: "var(--ok, #059669)" }}>synced</b>
            ) : (
              <b style={{ color: "var(--danger, #dc2626)" }}>FAILED</b>
            )}
            {v.finished ? ` at ${String(v.finished).slice(11, 16)}` : ""}
          </span>
        ))}
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

      <AutoSyncStatus />

      {tab === "daily" && <DailyTab />}
      {tab === "hourly" && <HourlyTab />}
      {tab === "disbursement" && <DisbTab />}
      {tab === "portfolio" && <PortfolioTab />}
      {tab === "staff" && <StaffTab />}
      {tab === "employee" && <EmployeeTab />}
    </div>
  );
}
