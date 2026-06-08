import { useEffect, useMemo, useState } from "react";
import { CloudUpload, Download, Play, Save, Sparkles } from "lucide-react";
import { Button, FileDrop, Spinner, useToast } from "../components/ui.jsx";
import { todayDMY } from "../lib/format.js";
import { useProcessLog } from "../shared/useProcessLog.js";
import LogStream from "../shared/LogStream.jsx";
import {
  getBackendFilesStatus,
  processQuick,
  saveToDownloads,
  snapshotReports,
  syncToDashboard,
} from "./quickApi.js";

const HOURS = Array.from({ length: 12 }, (_, i) => String(i + 1));
const MINUTES = ["00", "15", "30", "45"];

function triggerDownload(blob, filename) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 4000);
}

export default function QuickProcess({ status, refreshStatus }) {
  const toast = useToast();
  const [files, setFiles] = useState({ par: null, collection: null, collectionReport: null });
  const [date, setDate] = useState(todayDMY());
  const now = new Date();
  const [hour, setHour] = useState(String(((now.getHours() + 11) % 12) + 1));
  const [minute, setMinute] = useState("00");
  const [ampm, setAmpm] = useState(now.getHours() >= 12 ? "PM" : "AM");
  const [busy, setBusy] = useState("");
  const log = useProcessLog("Ready. Upload PAR + Collection + hourly Collection Report, then run.");

  const demandMissing = status?.demandMaster && status.demandMaster.available === false;

  const canProcess = useMemo(
    () => !busy && files.par && files.collection && files.collectionReport && !demandMissing,
    [busy, files, demandMissing]
  );

  useEffect(() => {
    refreshStatus?.();
  }, [refreshStatus]);

  async function runProcess(thenSync) {
    if (!canProcess) return;
    setBusy(thenSync ? "sync" : "process");
    log.reset("Processing started…");
    log.connectLogs();
    log.startTimer();
    try {
      const { blob, filename } = await processQuick({ files, date, hour, minute, ampm });
      triggerDownload(blob, filename);
      log.setDone(true);
      log.setStep(7);
      log.pushLog(`Report generated: ${filename}`, "success");
      toast.success("Quick report generated and downloaded.", "Quick complete");
      snapshotReports(date, `${hour}:${minute} ${ampm}`).catch(() => {});
      refreshStatus?.();
      if (thenSync) {
        log.pushLog("Syncing to dashboard…", "info");
        const res = await syncToDashboard();
        if (res.success) {
          toast.success(res.message || "Synced to dashboard.", "Dashboard synced");
          log.pushLog(res.message || "Dashboard sync complete.", "success");
        } else {
          toast.warn(res.message || "Dashboard sync skipped.");
          log.pushLog(res.message || "Dashboard sync skipped.", "warn");
        }
      }
    } catch (e) {
      log.pushLog(e.message, "error");
      toast.error(e.message, "Processing failed");
    } finally {
      log.stopTimer();
      setBusy("");
      log.closeLater();
    }
  }

  async function handleSaveToDownloads() {
    setBusy("save");
    try {
      const res = await saveToDownloads();
      if (res.success) toast.success(`Saved to ${res.filename}`, "Saved to Downloads");
      else toast.error(res.message || "Save failed.");
    } catch (e) {
      toast.error(e.message, "Save failed");
    } finally {
      setBusy("");
    }
  }

  const showProgress = busy === "process" || busy === "sync" || log.step > 0 || log.done;
  const pct = log.done ? 100 : Math.min(95, Math.max(0, log.step - 1) * 14 + (busy ? 8 : 0));

  return (
    <div className="eod-grid">
      <div className="col" style={{ gap: 18 }}>
        <div className="panel">
          <div className="panel-header">
            <div>
              <p className="eyebrow">Step 1 · Inputs</p>
              <h2>Upload & Generate</h2>
              <p className="sub">PAR, daily Collection and the hourly Collection Report are all required.</p>
            </div>
            {busy && (
              <span className="badge badge-info">
                <Spinner size={13} /> {busy === "save" ? "Saving" : "Processing"}
              </span>
            )}
          </div>

          <div className="file-grid" style={{ gridTemplateColumns: "repeat(3, 1fr)" }}>
            <FileDrop
              label="PAR Report"
              hint="Required · .xlsx"
              file={files.par}
              onFile={(f) => setFiles({ ...files, par: f })}
              disabled={Boolean(busy)}
            />
            <FileDrop
              label="Collection (daily)"
              hint="Required · .xlsx"
              file={files.collection}
              onFile={(f) => setFiles({ ...files, collection: f })}
              disabled={Boolean(busy)}
            />
            <FileDrop
              label="Collection Report (hourly)"
              hint="Required · .xlsx"
              file={files.collectionReport}
              onFile={(f) => setFiles({ ...files, collectionReport: f })}
              disabled={Boolean(busy)}
            />
          </div>

          <div className="control-grid" style={{ gridTemplateColumns: "1.4fr 1fr 1fr 1fr", gap: 10 }}>
            <label className="field">
              <span>Report date (DD-MM-YYYY)</span>
              <input className="input" value={date} onChange={(e) => setDate(e.target.value)} placeholder="DD-MM-YYYY" />
            </label>
            <label className="field">
              <span>Hour</span>
              <select className="input" value={hour} onChange={(e) => setHour(e.target.value)}>
                {HOURS.map((h) => (
                  <option key={h} value={h}>{h}</option>
                ))}
              </select>
            </label>
            <label className="field">
              <span>Minute</span>
              <select className="input" value={minute} onChange={(e) => setMinute(e.target.value)}>
                {MINUTES.map((m) => (
                  <option key={m} value={m}>{m}</option>
                ))}
              </select>
            </label>
            <label className="field">
              <span>AM / PM</span>
              <select className="input" value={ampm} onChange={(e) => setAmpm(e.target.value)}>
                <option value="AM">AM</option>
                <option value="PM">PM</option>
              </select>
            </label>
          </div>

          {demandMissing && (
            <div className="banner warn" style={{ marginBottom: 16 }}>
              <strong>Required Action:</strong> No Demand Master found. Upload one in the EOD / DB
              Module first — Quick Report reuses it for the EOD step.
            </div>
          )}

          <div className="actions">
            <Button
              variant="success"
              icon={Play}
              className="grow"
              disabled={!canProcess}
              loading={busy === "process"}
              onClick={() => runProcess(false)}
            >
              Generate Report
            </Button>
            <Button
              variant="primary"
              icon={CloudUpload}
              className="grow"
              disabled={!canProcess}
              loading={busy === "sync"}
              onClick={() => runProcess(true)}
            >
              Generate & Sync
            </Button>
            <Button
              variant="ghost"
              icon={Save}
              disabled={Boolean(busy)}
              loading={busy === "save"}
              onClick={handleSaveToDownloads}
            >
              Save to Downloads
            </Button>
          </div>
        </div>

        {showProgress && (
          <LogStream
            logs={log.logs}
            elapsed={log.elapsed}
            done={log.done}
            pct={pct}
            eyebrow="Step 2 · Pipeline"
            title={log.done ? "Completed" : "Processing…"}
          />
        )}
      </div>

      <div className="col" style={{ gap: 18 }}>
        <div className="panel hint-panel">
          <div className="row" style={{ alignItems: "flex-start", gap: 12 }}>
            <Sparkles size={18} className="muted" />
            <div>
              <strong style={{ fontSize: 13.5 }}>How Quick works</strong>
              <p className="muted" style={{ margin: "4px 0 0", fontSize: 12.5 }}>
                Runs the EOD step (PAR + Collection vs Demand Master) and merges the hourly
                Collection Report in one pass — producing the hourly fast report directly. The
                report date is taken from the PAR filename when present; the field here is a
                fallback.
              </p>
              <p className="muted" style={{ margin: "8px 0 0", fontSize: 12 }}>
                The generated file downloads automatically and is also saved to{" "}
                <b>Reports &amp; Downloads</b>. Uploads, reports and caches auto-delete after 3 days.
              </p>
            </div>
          </div>
        </div>

        {status?.quickReport?.available && (
          <div className="panel">
            <div className="panel-header" style={{ marginBottom: 10 }}>
              <div>
                <p className="eyebrow">Last report</p>
                <h2>Previous run</h2>
              </div>
              <Download size={18} className="muted" />
            </div>
            <p className="muted" style={{ fontSize: 12.5 }}>
              Generated {status.quickReport.timestamp || "recently"}. See the{" "}
              <b>Reports &amp; Downloads</b> tab for the full history.
            </p>
          </div>
        )}
      </div>
    </div>
  );
}
