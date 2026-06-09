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
  Loader2,
  Mail,
  MessageCircle,
  Merge,
  Play,
  Sparkles,
  UserCheck,
  Users,
} from "lucide-react";
import { Button, FileDrop, Switch, useToast } from "../../components/ui.jsx";
import { todayDMY } from "../../lib/format.js";
import {
  cacheFile,
  generateDailyHourlyReport,
  generateEmployeeReport,
  processEod,
  snapshotReports,
  syncToDashboard,
} from "../api.js";
import DbStatusSummary from "../../db/components/DbStatusSummary.jsx";
import { useProcessingJob } from "../../shared/processing/useProcessingJob.js";
import ProcessingPanel from "../../shared/processing/ProcessingPanel.jsx";

// Step labels for the shared timeline (mirrors the backend's 7-step pipeline).
const EOD_STEPS = [
  { key: "register", label: "Register files" },
  { key: "join", label: "SQL join" },
  { key: "process", label: "Process data" },
  { key: "excel", label: "Write Excel" },
  { key: "archive", label: "Archive & sync" },
  { key: "employee", label: "Employee report" },
  { key: "finish", label: "Finish" },
];

// Map the backend SSE step number (1-based) onto the shared step states.
function eodSseStep(data, { setSteps }) {
  if (data.done) return; // the hook marks everything done on completion
  const n = typeof data.step === "number" ? data.step : 0;
  if (n < 1) return;
  const updates = {};
  for (let i = 0; i < EOD_STEPS.length; i++) {
    updates[i] = i < n - 1 ? "done" : i === n - 1 ? "active" : "pending";
  }
  setSteps(updates);
}

export default function UploadProcessPanel({ status, refreshStatus, onSwitchTab }) {
  const toast = useToast();
  const job = useProcessingJob({ module: "eod", steps: EOD_STEPS, onSseEvent: eodSseStep });
  const [files, setFiles] = useState({ par: null, collection: null });
  const [useCache, setUseCache] = useState(false);
  const [busy, setBusy] = useState("");

  const isMasterDemandMissing = !status?.backend?.masterDemand;
  const isLastMonthParMissing = !status?.backend?.lastMonthPar;
  const isMasterDemandNotLoaded = !status?.db?.demandMaster?.loaded;
  const isLastMonthParNotLoaded = !status?.db?.lastMonthPar?.loaded;
  const needsSync = isMasterDemandNotLoaded || isLastMonthParNotLoaded;
  const masterMissing = isMasterDemandMissing || isLastMonthParMissing;

  const canProcess = useMemo(() => {
    if (busy || job.busy) return false;
    if (masterMissing || needsSync) return false;
    if (useCache) return true;
    return Boolean(files.par && files.collection);
  }, [files, useCache, busy, job.busy, masterMissing, needsSync]);

  const pushLog = (text, tone = "info") => job.log(text, tone);

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
    try {
      const out = await job.run(async ({ processId, signal }) => {
        const requestOptions = {
          targetDate: todayDMY(),
          useBackendDemand: true,
          useLastCache: useCache,
          cachePar: true,
          cacheCollection: true,
          autoFixSheets: false,
          processId,
          signal,
        };
        const payload = await processEod({ files, options: requestOptions });
        if (payload?.cancelled) {
          const err = new Error(payload.message || "Processing cancelled.");
          err.cancelled = true;
          throw err;
        }
        toast.success(payload.message || "Processing complete.", "EOD complete");
        // Archive both reports under this date for the Reports & Downloads page.
        snapshotReports(requestOptions.targetDate).catch(() => {});
        refreshStatus();
        return payload;
      });
      // Only a SUCCESSFUL run moves to Reports (cancelled returns null; failed throws).
      if (!out) return;
      // Focus the Reports & Downloads tab after a successful run.
      if (onSwitchTab) setTimeout(() => onSwitchTab("reports"), 600);
    } catch (e) {
      if (!e?.cancelled) toast.error(e.message, "Processing failed");
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

  const showProgress = job.status !== "idle";

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
            {job.running && (
              <span className="badge badge-info">
                <Loader2 size={13} className="spin" /> Processing
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
              loading={job.running}
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

        {/* Shared processing panel: status, step timeline, live log, stop. */}
        {showProgress && (
          <ProcessingPanel
            job={job}
            eyebrow="Step 2 · Pipeline"
            onRetry={canProcess ? handleProcess : undefined}
          />
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
