import { useEffect, useRef, useState } from "react";
import { CheckCircle2, FileCheck2, Play, Sparkles } from "lucide-react";
import { Button, FileDrop, ProgressBar, Spinner, useToast } from "../components/ui.jsx";
import { checkIns, checkOd, processOd, uploadIns, uploadOd } from "./odApi.js";
import "../eod/eod.css";

export default function OdReportModule() {
  const toast = useToast();
  const [par, setPar] = useState(null);
  const [odStaged, setOdStaged] = useState(null); // {filename}
  const [insStaged, setInsStaged] = useState(null);
  const [busy, setBusy] = useState("");
  const [steps, setSteps] = useState([]);
  const [done, setDone] = useState(false);
  const [savedFile, setSavedFile] = useState("");
  const logRef = useRef(null);

  useEffect(() => {
    checkOd().then((r) => r.exists && setOdStaged({ filename: r.filename })).catch(() => {});
    checkIns().then((r) => r.exists && setInsStaged({ filename: r.filename })).catch(() => {});
  }, []);

  useEffect(() => {
    if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight;
  }, [steps]);

  async function handleOd(file) {
    if (!file) return;
    setBusy("od");
    try {
      const res = await uploadOd(file);
      setOdStaged({ filename: res.filename });
      toast.success(`Month-end file ready: ${res.filename}`, "Uploaded");
    } catch (e) {
      toast.error(e.message, "Upload failed");
    } finally {
      setBusy("");
    }
  }

  async function handleIns(file) {
    if (!file) return;
    setBusy("ins");
    try {
      const res = await uploadIns(file);
      setInsStaged({ filename: res.filename });
      toast.success(`Insurance file ready: ${res.filename}`, "Uploaded");
    } catch (e) {
      toast.error(e.message, "Upload failed");
    } finally {
      setBusy("");
    }
  }

  async function handleProcess() {
    if (!par || busy) return;
    setBusy("process");
    setSteps([]);
    setDone(false);
    setSavedFile("");
    try {
      await processOd(par, (ev) => {
        if (ev.error) {
          setSteps((s) => [...s, { error: true, title: "Error", detail: ev.error }]);
          toast.error(ev.error, "Processing failed");
          return;
        }
        if (ev.done) {
          setDone(true);
          setSavedFile(ev.filename || "OD Report.xlsx");
          toast.success(`Saved ${ev.filename} to your Downloads folder.`, "OD Report ready");
          return;
        }
        if (ev.step) {
          setSteps((s) => [...s, ev]);
        }
      });
    } catch (e) {
      toast.error(e.message, "Processing failed");
    } finally {
      setBusy("");
    }
  }

  const pct = done ? 100 : Math.min(95, steps.length * 16);

  return (
    <div className="eod">
      <div className="eod-head">
        <div>
          <p className="eyebrow">OD Report</p>
          <h1 className="eod-title">Overdue (OD) Report</h1>
          <p className="muted eod-subtitle">
            FTOD and Insurance-OD analysis from PAR + month-end + insurance files. Output saves to your Downloads folder.
          </p>
        </div>
      </div>

      <div className="eod-grid">
        <div className="col" style={{ gap: 18 }}>
          <div className="panel">
            <div className="panel-header">
              <div>
                <p className="eyebrow">Step 1 · Inputs</p>
                <h2>Upload & Process</h2>
                <p className="sub">PAR is required. Month-end (.xlsb) and Insurance are optional but enrich the report.</p>
              </div>
              {busy === "process" && (
                <span className="badge badge-info">
                  <Spinner size={13} /> Processing
                </span>
              )}
            </div>

            <div className="file-grid" style={{ gridTemplateColumns: "1fr" }}>
              <FileDrop
                label="PAR Report"
                hint="Required · .xlsx"
                file={par}
                onFile={setPar}
                disabled={Boolean(busy)}
              />
            </div>

            <div className="file-grid" style={{ gridTemplateColumns: "repeat(2, 1fr)", marginTop: 4 }}>
              {odStaged ? (
                <FileDrop locked lockedText="Month-End OD file ready" hint={odStaged.filename} />
              ) : (
                <FileDrop
                  label="Month-End OD (.xlsb)"
                  hint="Optional · .xlsb"
                  accept=".xlsb"
                  file={null}
                  onFile={handleOd}
                  disabled={busy === "od"}
                />
              )}
              {insStaged ? (
                <FileDrop locked lockedText="Insurance file ready" hint={insStaged.filename} />
              ) : (
                <FileDrop
                  label="Insurance file"
                  hint="Optional · .xlsx/.csv"
                  accept=".xlsx,.xls,.csv"
                  file={null}
                  onFile={handleIns}
                  disabled={busy === "ins"}
                />
              )}
            </div>

            <div className="actions">
              <Button
                variant="success"
                icon={Play}
                className="grow"
                disabled={!par || Boolean(busy)}
                loading={busy === "process"}
                onClick={handleProcess}
              >
                Process & Save to Downloads
              </Button>
            </div>
          </div>

          {(steps.length > 0 || done) && (
            <div className="panel">
              <div className="panel-header" style={{ marginBottom: 14 }}>
                <div>
                  <p className="eyebrow">Step 2 · Pipeline</p>
                  <h2>{done ? "Completed" : "Processing…"}</h2>
                </div>
                {done && (
                  <span className="badge badge-success">
                    <CheckCircle2 size={13} /> 100%
                  </span>
                )}
              </div>
              <ProgressBar value={pct} done={done} />
              <div className="log-stream" ref={logRef} style={{ marginTop: 12 }}>
                {steps.map((s, i) => (
                  <div key={i} style={{ marginBottom: 8 }}>
                    <p className={`log-line ${s.error ? "error" : s.status === "skipped" ? "warn" : "success"}`}>
                      <span className="log-dot" />
                      <b>{s.step ? `Step ${s.step}: ` : ""}{s.title}</b>
                      {s.status ? ` — ${s.status}` : ""}
                    </p>
                    {s.detail && (
                      <p className="muted" style={{ margin: "0 0 0 18px", fontSize: 12.5 }}>{s.detail}</p>
                    )}
                    {Array.isArray(s.sub) &&
                      s.sub.map((sub, j) => (
                        <p key={j} className="muted" style={{ margin: "0 0 0 18px", fontSize: 12 }}>• {sub}</p>
                      ))}
                  </div>
                ))}
                {done && savedFile && (
                  <p className="log-line success">
                    <span className="log-dot" />
                    Saved <b>{savedFile}</b> to your Downloads folder.
                  </p>
                )}
              </div>
            </div>
          )}
        </div>

        <div className="col" style={{ gap: 18 }}>
          <div className="panel hint-panel">
            <div className="row" style={{ alignItems: "flex-start", gap: 12 }}>
              <Sparkles size={18} className="muted" />
              <div>
                <strong style={{ fontSize: 13.5 }}>How OD Report works</strong>
                <p className="muted" style={{ margin: "4px 0 0", fontSize: 12.5 }}>
                  Filters out 0-day accounts, flags FY disbursements, runs FTOD logic against the
                  month-end OD file, and matches insurance-OD / nominee records. The finished
                  <b> OD Report.xlsx</b> is written to your Downloads folder.
                </p>
                <p className="muted" style={{ margin: "8px 0 0", fontSize: 12 }}>
                  Stage the month-end <b>.xlsb</b> and Insurance files once; they persist for
                  subsequent runs until replaced.
                </p>
              </div>
            </div>
          </div>

          {(odStaged || insStaged) && (
            <div className="panel">
              <div className="panel-header" style={{ marginBottom: 10 }}>
                <div>
                  <p className="eyebrow">Staged files</p>
                  <h2>Backend inputs</h2>
                </div>
                <FileCheck2 size={18} className="muted" />
              </div>
              {odStaged && <p className="muted" style={{ fontSize: 12.5 }}>Month-End: {odStaged.filename}</p>}
              {insStaged && <p className="muted" style={{ fontSize: 12.5 }}>Insurance: {insStaged.filename}</p>}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
