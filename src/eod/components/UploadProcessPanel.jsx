import { useEffect, useMemo, useRef, useState } from "react";
import {
  Archive,
  BarChart3,
  CheckCircle2,
  CloudUpload,
  Code,
  Database,
  FileCheck2,
  FileOutput,
  Mail,
  MessageCircle,
  Merge,
  Play,
  Sparkles,
  UserCheck,
  Users,
} from "lucide-react";
import { Button, FileDrop, ProgressBar, Spinner, Switch, useToast } from "../../components/ui.jsx";
import { todayDMY } from "../../lib/format.js";
import {
  cacheFile,
  eventsUrl,
  generateDailyHourlyReport,
  generateEmployeeReport,
  processEod,
  snapshotReports,
  syncToDashboard,
} from "../api.js";
import DbStatusSummary from "../../db/components/DbStatusSummary.jsx";

const PIPELINE = [
  { n: 1, label: "Register Files", icon: Database },
  { n: 2, label: "SQL Join", icon: Merge },
  { n: 3, label: "Process Data", icon: Sparkles },
  { n: 4, label: "Write Excel", icon: FileOutput },
  { n: 5, label: "Archive & Sync", icon: Archive },
  { n: 6, label: "Employee Report", icon: UserCheck },
  { n: 7, label: "Finish", icon: CheckCircle2 },
];

export default function UploadProcessPanel({ status, refreshStatus, onSwitchTab }) {
  const toast = useToast();
  const [files, setFiles] = useState({ par: null, collection: null });
  const [useCache, setUseCache] = useState(false);
  const [logs, setLogs] = useState([
    { text: "Ready. Upload PAR + Collection, then run EOD processing.", tone: "info" },
  ]);
  const [busy, setBusy] = useState("");
  const [step, setStep] = useState(0); // 0 = not started
  const [done, setDone] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const [result, setResult] = useState(null);
  const esRef = useRef(null);
  const timerRef = useRef(null);
  const logBoxRef = useRef(null);

  const isMasterDemandMissing = !status?.backend?.masterDemand;
  const isLastMonthParMissing = !status?.backend?.lastMonthPar;
  const isMasterDemandNotLoaded = !status?.db?.demandMaster?.loaded;
  const isLastMonthParNotLoaded = !status?.db?.lastMonthPar?.loaded;
  const needsSync = isMasterDemandNotLoaded || isLastMonthParNotLoaded;
  const masterMissing = isMasterDemandMissing || isLastMonthParMissing;

  const canProcess = useMemo(() => {
    if (busy) return false;
    if (masterMissing || needsSync) return false;
    if (useCache) return true;
    return Boolean(files.par && files.collection);
  }, [files, useCache, busy, masterMissing, needsSync]);

  useEffect(() => () => {
    esRef.current?.close();
    clearInterval(timerRef.current);
  }, []);

  useEffect(() => {
    if (logBoxRef.current) logBoxRef.current.scrollTop = 0;
  }, [logs]);

  function pushLog(text, tone = "info") {
    setLogs((items) => [{ text, tone }, ...items].slice(0, 120));
  }

  function toneFor(text) {
    const t = text.toLowerCase();
    if (t.includes("error") || t.includes("failed") || t.includes("traceback")) return "error";
    if (t.includes("completed") || t.includes("success") || t.includes("saved") || t.includes("done")) return "success";
    if (t.includes("warn")) return "warn";
    return "info";
  }

  function connectLogs() {
    esRef.current?.close();
    const source = new EventSource(eventsUrl());
    source.onmessage = (event) => {
      if (!event.data) return;
      try {
        const data = JSON.parse(event.data);
        if (data.log) {
          pushLog(data.log, toneFor(data.log));
          if (typeof data.step === "number" && data.step >= 1) setStep(data.step);
          if (data.done) {
            setStep(7);
            setDone(true);
          }
        }
      } catch {
        pushLog(event.data);
      }
    };
    source.onerror = () => {
      /* keep-alive hiccups are normal; ignore */
    };
    esRef.current = source;
  }

  function startTimer() {
    setElapsed(0);
    const t0 = Date.now();
    clearInterval(timerRef.current);
    timerRef.current = setInterval(() => setElapsed((Date.now() - t0) / 1000), 250);
  }

  async function handleCache(type) {
    const file = files[type];
    if (!file) return;
    setBusy(`cache-${type}`);
    try {
      const res = await cacheFile(type, file);
      toast.success(res.message || `${type} cached (${res.time ?? "?"} ms)`, "Cached");
      pushLog(`${type.toUpperCase()} cached.`, "success");
      refreshStatus();
    } catch (e) {
      toast.error(e.message, "Cache failed");
    } finally {
      setBusy("");
    }
  }

  async function handleProcess() {
    if (!canProcess) return;
    setBusy("process");
    setResult(null);
    setDone(false);
    setStep(0);
    setLogs([{ text: "Processing started…", tone: "info" }]);
    connectLogs();
    startTimer();
    try {
      const requestOptions = {
        targetDate: todayDMY(),
        useBackendDemand: true,
        useLastCache: useCache,
        cachePar: true,
        cacheCollection: true,
        autoFixSheets: false,
      };
      const payload = await processEod({ files, options: requestOptions });
      setResult(payload);
      setDone(true);
      setStep(7);
      toast.success(payload.message || "Processing complete.", "EOD complete");
      // Archive both reports under this date for the Reports & Downloads page.
      snapshotReports(requestOptions.targetDate).catch(() => {});
      refreshStatus();
    } catch (e) {
      pushLog(e.message, "error");
      toast.error(e.message, "Processing failed");
    } finally {
      clearInterval(timerRef.current);
      setBusy("");
      setTimeout(() => esRef.current?.close(), 1500);
    }
  }

  async function handleGenerateEmployeeReport() {
    setBusy("employee-report");
    try {
      const res = await generateEmployeeReport();
      toast.success(res.message || "Employee reports generated.", "Employees done");
      pushLog("Employee Report + Accounts Report generated.", "success");
    } catch (e) {
      toast.error(e.message, "Employee report failed");
      pushLog(e.message, "error");
    } finally {
      setBusy("");
    }
  }

  async function handleGenerateDailyHourlyReport() {
    setBusy("daily-hourly-report");
    try {
      const res = await generateDailyHourlyReport(todayDMY());
      toast.success(res.message || "Daily + Hourly reports generated.", "Reports done");
      pushLog("Daily Report + Hourly Report generated.", "success");
    } catch (e) {
      toast.error(e.message, "Daily/Hourly report failed");
      pushLog(e.message, "error");
    } finally {
      setBusy("");
    }
  }

  async function handleSyncToDashboard() {
    setBusy("sync-dashboard");
    try {
      const res = await syncToDashboard();
      toast.success(res.message || "Synced to dashboard.", "Dashboard synced");
      pushLog(res.message || "Dashboard sync complete.", "success");
    } catch (e) {
      toast.error(e.message, "Dashboard sync failed");
      pushLog(e.message, "error");
    } finally {
      setBusy("");
    }
  }

  function handleRunVba() {
    window.open("/eod/vba-runner", "_blank", "noopener,noreferrer");
  }

  function handleSendEmail() {
    onSwitchTab?.("email");
  }

  function handleSendWhatsApp() {
    onSwitchTab?.("whatsapp");
  }

  const showProgress = busy === "process" || step > 0 || done;
  const pct = done ? 100 : Math.min(95, Math.round((Math.max(0, step - 1) / 7) * 100) + (busy === "process" ? 6 : 0));

  return (
    <div className="eod-grid">
      {/* Main column */}
      <div className="col" style={{ gap: 18 }}>
        <div className="panel">
          <div className="panel-header">
            <div>
              <p className="eyebrow">Step 1 · Inputs</p>
              <h2>Upload & Process</h2>
              <p className="sub">PAR and Collection are required unless you reuse the last cache.</p>
            </div>
            {busy === "process" && (
              <span className="badge badge-info">
                <Spinner size={13} /> Processing
              </span>
            )}
          </div>

          <div className="file-grid" style={{ gridTemplateColumns: "repeat(2, 1fr)" }}>
            {useCache ? (
              <FileDrop
                locked
                lockedText="Using cached PAR Report"
                hint={status?.lastCache?.par?.name || status?.lastCache?.par?.originalName || "Cached PAR"}
              />
            ) : (
              <FileDrop
                label="Today's PAR Report"
                hint="Required · .xlsx"
                file={files.par}
                onFile={(f) => setFiles({ ...files, par: f })}
                disabled={Boolean(busy)}
              />
            )}
            {useCache ? (
              <FileDrop
                locked
                lockedText="Using cached Collection Report"
                hint={status?.lastCache?.collection?.name || status?.lastCache?.collection?.originalName || "Cached Collection"}
              />
            ) : (
              <FileDrop
                label="Today's Collection Report"
                hint="Required · .xlsx"
                file={files.collection}
                onFile={(f) => setFiles({ ...files, collection: f })}
                disabled={Boolean(busy)}
              />
            )}
          </div>

          <div className="control-grid" style={{ gridTemplateColumns: "1fr" }}>
            <Switch
              checked={useCache}
              onChange={(v) => setUseCache(v)}
              label="Use cached PAR + Collection data"
            />
          </div>

          {(masterMissing || needsSync) && (
            <div className="banner warn" style={{ marginBottom: 16 }}>
              <strong>Required Action:</strong> {masterMissing
                ? "Master files (Demand Master, Last Month PAR) are missing. Upload and sync them in the DB Module."
                : "Master files need to be synced into DuckDB. Open the DB Module and click Sync DuckDB."}
            </div>
          )}

          <div className="actions">
            <Button
              variant="success"
              icon={Play}
              disabled={!canProcess}
              loading={busy === "process"}
              onClick={handleProcess}
              className="grow"
            >
              Run EOD Processing
            </Button>
          </div>
        </div>

        {/* Additional Reports */}
        <div className="panel">
          <div className="panel-header" style={{ marginBottom: 14 }}>
            <div>
              <p className="eyebrow">Additional Reports</p>
              <h2>Generate Extra Reports</h2>
              <p className="sub">Employee and Daily/Hourly reports from the latest EOD output.</p>
            </div>
          </div>
          <div className="actions">
            <Button
              variant="primary"
              icon={Users}
              className="grow"
              disabled={Boolean(busy)}
              loading={busy === "employee-report"}
              onClick={handleGenerateEmployeeReport}
            >
              RUN Employees
            </Button>
            <Button
              variant="primary"
              icon={BarChart3}
              className="grow"
              disabled={Boolean(busy)}
              loading={busy === "daily-hourly-report"}
              onClick={handleGenerateDailyHourlyReport}
            >
              RUN Daily+Hourly
            </Button>
          </div>
        </div>

        {/* Sync / Send Actions */}
        <div className="panel">
          <div className="panel-header" style={{ marginBottom: 14 }}>
            <div>
              <p className="eyebrow">Sync / Send</p>
              <h2>Deliver &amp; Sync</h2>
              <p className="sub">Push reports to the dashboard or open delivery tabs.</p>
            </div>
          </div>
          <div className="actions">
            <Button
              variant="success"
              icon={CloudUpload}
              className="grow"
              disabled={Boolean(busy)}
              loading={busy === "sync-dashboard"}
              onClick={handleSyncToDashboard}
            >
              SYNC to Database
            </Button>
            <Button
              variant="primary"
              icon={Mail}
              className="grow"
              disabled={Boolean(busy)}
              onClick={handleSendEmail}
            >
              SEND Email
            </Button>
            <Button
              variant="primary"
              icon={MessageCircle}
              className="grow"
              disabled={Boolean(busy)}
              onClick={handleSendWhatsApp}
            >
              SEND WhatsApp
            </Button>
            <Button
              variant="ghost"
              icon={Code}
              className="grow"
              disabled={Boolean(busy)}
              onClick={handleRunVba}
            >
              RUN VBA
            </Button>
          </div>
        </div>

        {/* Progress + live log */}
        {showProgress && (
          <div className="panel">
            <div className="panel-header" style={{ marginBottom: 14 }}>
              <div>
                <p className="eyebrow">Step 2 · Pipeline</p>
                <h2>{done ? "Completed" : busy === "process" ? "Processing…" : "Last run"}</h2>
              </div>
              <div className="row">
                <span className="badge badge-muted">{elapsed ? `${elapsed.toFixed(1)}s` : "0.0s"}</span>
                {done && (
                  <span className="badge badge-success">
                    <CheckCircle2 size={13} /> {pct}%
                  </span>
                )}
              </div>
            </div>

            <ProgressBar value={pct} done={done} />

            <div className="pipeline">
              {PIPELINE.map((p) => {
                const state = done || step > p.n ? "done" : step === p.n ? "active" : "pending";
                const Icon = state === "done" ? CheckCircle2 : p.icon;
                return (
                  <div key={p.n} className={`pipe ${state}`}>
                    <span className="pipe-ic">{state === "active" ? <Spinner size={15} /> : <Icon size={15} />}</span>
                    <span className="pipe-label">{p.label}</span>
                  </div>
                );
              })}
            </div>

            <div className="log-stream" ref={logBoxRef}>
              {logs.map((l, i) => (
                <p key={i} className={`log-line ${l.tone}`}>
                  <span className="log-dot" />
                  {l.text}
                </p>
              ))}
            </div>
          </div>
        )}
      </div>

      {/* Side column: read-only DB status (full management lives in the DB Module) */}
      <div className="col" style={{ gap: 18 }}>
        <DbStatusSummary status={status} />

        <div className="panel hint-panel">
          <div className="row" style={{ alignItems: "flex-start", gap: 12 }}>
            <FileCheck2 size={18} className="muted" />
            <div>
              <strong style={{ fontSize: 13.5 }}>Tip</strong>
              <p className="muted" style={{ margin: "4px 0 0", fontSize: 12.5 }}>
                Large PAR/Collection files cache automatically for ~80% faster re-runs. After processing, head to the{" "}
                <b>Email</b> and <b>WhatsApp</b> tabs to deliver the report.
              </p>
              <p className="muted" style={{ margin: "8px 0 0", fontSize: 12 }}>
                🧹 Uploaded files, generated reports, archives and caches are{" "}
                <b>automatically deleted after 3 days</b>. The Demand Master, Last Month PAR database tables and your config are
                kept.
              </p>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
