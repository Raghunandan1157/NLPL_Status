import { useEffect, useMemo, useState } from "react";
import { Database, Download, Play, Sparkles } from "lucide-react";
import { Button, FileDrop, Spinner, useToast } from "../components/ui.jsx";
import { useProcessLog } from "../shared/useProcessLog.js";
import LogStream from "../shared/LogStream.jsx";
import ReportView from "./ReportView.jsx";
import { downloadReport } from "./instantExcel.js";
import { backendStatus, processInstant } from "./instantApi.js";

export default function InstantProcess({ onCached }) {
  const toast = useToast();
  const [files, setFiles] = useState({ par: null, collection: null });
  const [targetDate, setTargetDate] = useState("");
  const [busy, setBusy] = useState(false);
  const [report, setReport] = useState(null);
  const [status, setStatus] = useState(null);
  const log = useProcessLog("Ready. Upload PAR + Collection, then generate the instant report.");

  useEffect(() => {
    backendStatus().then(setStatus).catch(() => {});
  }, []);

  const canProcess = useMemo(() => !busy && files.par && files.collection, [busy, files]);
  const hasMonthly = (status?.monthlyData?.length || 0) > 0;

  async function handleProcess() {
    if (!canProcess) return;
    setBusy(true);
    setReport(null);
    log.reset("Processing started…");
    log.connectLogs();
    log.startTimer();
    try {
      const data = await processInstant({ ...files, targetDate });
      setReport(data);
      log.setDone(true);
      log.setStep(7);
      const t = data?.metadata?.processing_time;
      log.pushLog(`Instant report computed${t ? ` in ${t}s` : ""}.`, "success");
      toast.success("Instant report generated.", "Done");
      onCached?.();
    } catch (e) {
      log.pushLog(e.message, "error");
      toast.error(e.message, "Processing failed");
    } finally {
      log.stopTimer();
      setBusy(false);
      log.closeLater();
    }
  }

  const showProgress = busy || log.step > 0 || log.done;
  const pct = log.done ? 100 : busy ? 60 : 0;

  return (
    <div className="col" style={{ gap: 18 }}>
      <div className="eod-grid">
        <div className="col" style={{ gap: 18 }}>
          <div className="panel">
            <div className="panel-header">
              <div>
                <p className="eyebrow">Step 1 · Inputs</p>
                <h2>Upload & Generate</h2>
                <p className="sub">PAR + Collection are required. The month's Demand backend must be uploaded.</p>
              </div>
              {busy && (
                <span className="badge badge-info">
                  <Spinner size={13} /> Processing
                </span>
              )}
            </div>

            <div className="file-grid" style={{ gridTemplateColumns: "repeat(2, 1fr)" }}>
              <FileDrop
                label="PAR Report"
                hint="Required · .xlsx"
                file={files.par}
                onFile={(f) => setFiles({ ...files, par: f })}
                disabled={busy}
              />
              <FileDrop
                label="Collection Report"
                hint="Required · .xlsx"
                file={files.collection}
                onFile={(f) => setFiles({ ...files, collection: f })}
                disabled={busy}
              />
            </div>

            <div className="control-grid" style={{ gridTemplateColumns: "1fr" }}>
              <label className="field">
                <span>Target date (DD-MM-YYYY) — optional, auto-detected from filename</span>
                <input className="input" value={targetDate} onChange={(e) => setTargetDate(e.target.value)} placeholder="DD-MM-YYYY" />
              </label>
            </div>

            {!hasMonthly && (
              <div className="banner warn" style={{ marginBottom: 16 }}>
                <strong>Required Action:</strong> No monthly Demand data found. Upload the month's
                Demand Sheet (and Last Month PAR) in the <b>Monthly Backend</b> tab first.
              </div>
            )}

            <div className="actions">
              <Button variant="success" icon={Play} className="grow" disabled={!canProcess} loading={busy} onClick={handleProcess}>
                Generate Instant Report
              </Button>
            </div>
          </div>

          {showProgress && (
            <LogStream logs={log.logs} elapsed={log.elapsed} done={log.done} pct={pct} eyebrow="Step 2 · Pipeline" />
          )}
        </div>

        <div className="col" style={{ gap: 18 }}>
          <div className="panel hint-panel">
            <div className="row" style={{ alignItems: "flex-start", gap: 12 }}>
              <Sparkles size={18} className="muted" />
              <div>
                <strong style={{ fontSize: 13.5 }}>How Instant works</strong>
                <p className="muted" style={{ margin: "4px 0 0", fontSize: 12.5 }}>
                  Runs the DuckDB merge pipeline and computes pivot summaries (Regular Demand vs
                  Collection, DPD buckets, NPA) by Region / Division / Area / Branch. Each run is
                  cached by date so you can revisit it from <b>Report History</b>.
                </p>
              </div>
            </div>
          </div>
          {status?.monthlyData?.length > 0 && (
            <div className="panel">
              <div className="panel-header" style={{ marginBottom: 8 }}>
                <div>
                  <p className="eyebrow">Monthly backend</p>
                  <h2>Loaded months</h2>
                </div>
                <Database size={18} className="muted" />
              </div>
              <p className="muted" style={{ fontSize: 12.5 }}>
                {status.monthlyData.map((m) => m.month).join(", ")}
              </p>
            </div>
          )}
        </div>
      </div>

      {report && (
        <div className="panel">
          <div className="panel-header" style={{ marginBottom: 12 }}>
            <div>
              <p className="eyebrow">Result</p>
              <h2>Instant Report</h2>
              <p className="sub">
                {report.metadata?.section_count || report.sections?.length || 0} sections
                {report.metadata?.cache_date ? ` · ${report.metadata.cache_date}` : ""}
              </p>
            </div>
            <Button
              size="sm"
              variant="primary"
              icon={Download}
              onClick={() => downloadReport(report, `Instant Report ${report.metadata?.cache_date || ""}.xlsx`.trim())}
            >
              Download Excel
            </Button>
          </div>
          <ReportView report={report} />
        </div>
      )}
    </div>
  );
}
