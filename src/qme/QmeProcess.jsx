import { useState } from "react";
import {
  CheckCircle2,
  CloudUpload,
  Download,
  ListChecks,
  Play,
  Save,
  Sparkles,
  XCircle,
} from "lucide-react";
import { Button, FileDrop, Modal, Spinner, Switch, useToast } from "../components/ui.jsx";
import { useProcessLog } from "../shared/useProcessLog.js";
import LogStream from "../shared/LogStream.jsx";
import {
  DOWNLOADS,
  checkColumns,
  downloadUrl,
  processQme,
  saveToDownloads,
  snapshotReports,
} from "./qmeApi.js";

const INPUTS = [
  { key: "demand", label: "Demand Sheet Master" },
  { key: "lastMonthPar", label: "Last Month PAR" },
  { key: "par", label: "PAR File" },
  { key: "collection", label: "Collection File" },
];

export default function QmeProcess({ refreshStatus }) {
  const toast = useToast();
  const [files, setFiles] = useState({ demand: null, lastMonthPar: null, par: null, collection: null });
  const [uploadToDb, setUploadToDb] = useState(false);
  const [busy, setBusy] = useState("");
  const [result, setResult] = useState(null);
  const [columnCheck, setColumnCheck] = useState(null);
  const log = useProcessLog("Ready. Upload all four files, then generate the month-end reports.");

  const allPresent = INPUTS.every((i) => files[i.key]);

  async function handleCheckColumns() {
    if (!allPresent) {
      toast.warn("Upload all four files first.");
      return;
    }
    setBusy("check");
    try {
      const res = await checkColumns(files);
      setColumnCheck(res.results || {});
    } catch (e) {
      toast.error(e.message, "Column check failed");
    } finally {
      setBusy("");
    }
  }

  async function handleProcess() {
    if (!allPresent || busy) return;
    setBusy("process");
    setResult(null);
    log.reset("Processing started…");
    log.connectLogs();
    log.startTimer();
    try {
      const payload = await processQme({ files, uploadToDatabase: uploadToDb });
      setResult(payload);
      log.setDone(true);
      log.setStep(7);
      log.pushLog(payload.message || "Month-end processing complete.", "success");
      toast.success(payload.message || "Month-end reports generated.", "Month-End complete");
      if (payload.databaseUpload) {
        if (payload.databaseUpload.success)
          toast.success(payload.databaseUpload.message || "Database updated.", "Database");
        else toast.warn(payload.databaseUpload.message || "Database upload skipped.");
      }
      snapshotReports(payload.reportDate || "", "").catch(() => {});
      refreshStatus?.();
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
      if (res.success) toast.success(`Saved ${res.saved?.length || 0} file(s) to Downloads.`, "Saved");
      else toast.error(res.message || "Save failed.");
    } catch (e) {
      toast.error(e.message, "Save failed");
    } finally {
      setBusy("");
    }
  }

  const showProgress = busy === "process" || log.step > 0 || log.done;
  const pct = log.done ? 100 : Math.min(95, Math.max(0, log.step - 1) * 14 + (busy === "process" ? 8 : 0));
  const available = result?.available || [];

  return (
    <div className="eod-grid">
      <div className="col" style={{ gap: 18 }}>
        <div className="panel">
          <div className="panel-header">
            <div>
              <p className="eyebrow">Step 1 · Inputs</p>
              <h2>Upload & Generate</h2>
              <p className="sub">All four files are required. The report date is auto-detected from Collection.</p>
            </div>
            {busy === "process" && (
              <span className="badge badge-info">
                <Spinner size={13} /> Processing
              </span>
            )}
          </div>

          <div className="file-grid" style={{ gridTemplateColumns: "repeat(2, 1fr)" }}>
            {INPUTS.map((i) => (
              <FileDrop
                key={i.key}
                label={i.label}
                hint="Required · .xlsx"
                file={files[i.key]}
                onFile={(f) => setFiles((prev) => ({ ...prev, [i.key]: f }))}
                disabled={Boolean(busy)}
              />
            ))}
          </div>

          <div className="control-grid" style={{ gridTemplateColumns: "1fr" }}>
            <Switch
              checked={uploadToDb}
              onChange={setUploadToDb}
              label="Upload employee report to database after generation"
            />
          </div>

          <div className="actions">
            <Button
              variant="success"
              icon={Play}
              className="grow"
              disabled={!allPresent || Boolean(busy)}
              loading={busy === "process"}
              onClick={handleProcess}
            >
              Generate Month-End Reports
            </Button>
            <Button
              variant="ghost"
              icon={ListChecks}
              disabled={!allPresent || Boolean(busy)}
              loading={busy === "check"}
              onClick={handleCheckColumns}
            >
              Check Columns
            </Button>
          </div>
        </div>

        {result && available.length > 0 && (
          <div className="panel">
            <div className="panel-header" style={{ marginBottom: 14 }}>
              <div>
                <p className="eyebrow">Generated</p>
                <h2>Download Reports</h2>
                <p className="sub">Report date: {result.reportDate || "—"}</p>
              </div>
              <Button size="sm" variant="ghost" icon={Save} loading={busy === "save"} onClick={handleSaveToDownloads}>
                Save bundle
              </Button>
            </div>
            <div className="download-list">
              {DOWNLOADS.filter((d) => available.includes(d.key)).map((d) => (
                <a key={d.key} className="btn btn-primary" href={downloadUrl(d.path)} style={{ justifyContent: "flex-start" }}>
                  <Download size={15} /> {d.label}
                </a>
              ))}
            </div>
          </div>
        )}

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
              <strong style={{ fontSize: 13.5 }}>Month-End rules</strong>
              <p className="muted" style={{ margin: "4px 0 0", fontSize: 12.5 }}>
                Runs full EOD processing with regular daily-report rules, then builds the month-end
                Employee report. Produces three downloadable workbooks. Optionally pushes the employee
                report to the Coll_Db daily database.
              </p>
              <p className="muted" style={{ margin: "8px 0 0", fontSize: 12 }}>
                Use <b>Check Columns</b> first to validate file headers. Reports are also kept under{" "}
                <b>Reports &amp; Downloads</b> for 3 days.
              </p>
            </div>
          </div>
        </div>
      </div>

      {columnCheck && (
        <Modal title="Column validation" onClose={() => setColumnCheck(null)} wide>
          <div className="col" style={{ gap: 10 }}>
            {INPUTS.map((i) => {
              const r = columnCheck[i.key] || {};
              const ok = r.status === "ok";
              return (
                <div key={i.key} className="panel" style={{ padding: 12 }}>
                  <div className="row" style={{ gap: 8, alignItems: "center" }}>
                    {ok ? (
                      <CheckCircle2 size={16} style={{ color: "var(--success, #16a34a)" }} />
                    ) : (
                      <XCircle size={16} style={{ color: "var(--danger, #dc2626)" }} />
                    )}
                    <strong>{i.label}</strong>
                    <span className="muted" style={{ marginLeft: "auto", fontSize: 12 }}>
                      {r.filename || "no file"}
                    </span>
                  </div>
                  <p className="muted" style={{ margin: "6px 0 0", fontSize: 12.5 }}>{r.message}</p>
                </div>
              );
            })}
          </div>
        </Modal>
      )}
    </div>
  );
}
