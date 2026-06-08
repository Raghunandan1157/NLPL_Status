import { useEffect, useMemo, useState } from "react";
import { CalendarDays, Play, Sparkles } from "lucide-react";
import { Button, FileDrop, Spinner, useToast } from "../components/ui.jsx";
import { useProcessLog } from "../shared/useProcessLog.js";
import LogStream from "../shared/LogStream.jsx";
import { checkReport, extractReport, monthYearFromDate } from "./ondateApi.js";

function todayIso() {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(
    d.getDate()
  ).padStart(2, "0")}`;
}

export default function OndateProcess({ onExtracted }) {
  const toast = useToast();
  const [file, setFile] = useState(null);
  const [date, setDate] = useState(todayIso());
  const [busy, setBusy] = useState(false);
  const [exists, setExists] = useState(null);
  const [result, setResult] = useState(null);
  const log = useProcessLog("Ready. Upload the On-Date source file and pick a date to extract.");

  const { month, year } = useMemo(() => monthYearFromDate(date), [date]);

  useEffect(() => {
    if (!date) return;
    let active = true;
    checkReport(date)
      .then((r) => active && setExists(r.exists ? r : null))
      .catch(() => active && setExists(null));
    return () => {
      active = false;
    };
  }, [date]);

  async function handleExtract() {
    if (!file || !date || busy) return;
    setBusy(true);
    setResult(null);
    log.reset("Extraction started…");
    log.connectLogs();
    log.startTimer();
    try {
      const res = await extractReport({ file, date });
      setResult(res);
      log.setDone(true);
      log.setStep(7);
      log.pushLog(res.message || "Report updated.", "success");
      toast.success(res.message || "On-Date report updated.", "Extracted");
      onExtracted?.();
    } catch (e) {
      log.pushLog(e.message, "error");
      toast.error(e.message, "Extraction failed");
    } finally {
      log.stopTimer();
      setBusy(false);
      log.closeLater();
    }
  }

  const showProgress = busy || log.step > 0 || log.done;
  const pct = log.done ? 100 : busy ? 55 : 0;

  return (
    <div className="eod-grid">
      <div className="col" style={{ gap: 18 }}>
        <div className="panel">
          <div className="panel-header">
            <div>
              <p className="eyebrow">Step 1 · Source</p>
              <h2>Upload & Extract</h2>
              <p className="sub">
                Upload the On-Date workbook and pick the date to extract into that month's master report.
              </p>
            </div>
            {busy && (
              <span className="badge badge-info">
                <Spinner size={13} /> Extracting
              </span>
            )}
          </div>

          <div className="file-grid" style={{ gridTemplateColumns: "1fr" }}>
            <FileDrop
              label="On-Date Source File"
              hint="Required · .xlsx"
              file={file}
              onFile={setFile}
              disabled={busy}
            />
          </div>

          <div className="control-grid" style={{ gridTemplateColumns: "1fr 1fr" }}>
            <label className="field">
              <span>Date to extract</span>
              <input className="input" type="date" value={date} onChange={(e) => setDate(e.target.value)} />
            </label>
            <label className="field">
              <span>Target month</span>
              <input className="input" value={`${month} ${year}`} readOnly />
            </label>
          </div>

          {exists && (
            <div className="banner" style={{ marginBottom: 16 }}>
              <CalendarDays size={15} /> A report already exists for {month} {year}. Extracting will
              add or update this date's column in it.
            </div>
          )}

          <div className="actions">
            <Button
              variant="success"
              icon={Play}
              className="grow"
              disabled={!file || !date || busy}
              loading={busy}
              onClick={handleExtract}
            >
              Extract On-Date Report
            </Button>
          </div>
        </div>

        {result?.success && (
          <div className="panel">
            <div className="panel-header" style={{ marginBottom: 6 }}>
              <div>
                <p className="eyebrow">Result</p>
                <h2>Updated</h2>
              </div>
            </div>
            <p className="muted" style={{ fontSize: 12.5 }}>
              {result.sheets_processed} sheet(s), {(result.rows || 0).toLocaleString()} rows,{" "}
              {result.merged_cells || 0} merged regions. Download it from the{" "}
              <b>Reports &amp; Downloads</b> tab.
            </p>
          </div>
        )}

        {showProgress && (
          <LogStream
            logs={log.logs}
            elapsed={log.elapsed}
            done={log.done}
            pct={pct}
            eyebrow="Step 2 · Extraction"
            title={log.done ? "Completed" : "Extracting…"}
          />
        )}
      </div>

      <div className="col" style={{ gap: 18 }}>
        <div className="panel hint-panel">
          <div className="row" style={{ alignItems: "flex-start", gap: 12 }}>
            <Sparkles size={18} className="muted" />
            <div>
              <strong style={{ fontSize: 13.5 }}>How On-Date works</strong>
              <p className="muted" style={{ margin: "4px 0 0", fontSize: 12.5 }}>
                Each month has one master workbook with a column block per calendar day. Extracting a
                date copies that day's On-Date sheets (with full formatting) into the right column.
                Run it once per day; the month report builds up over time.
              </p>
              <p className="muted" style={{ margin: "8px 0 0", fontSize: 12 }}>
                Monthly reports are kept (not auto-deleted) so the full month accumulates.
              </p>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
