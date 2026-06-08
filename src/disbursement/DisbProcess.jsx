import { useEffect, useMemo, useState } from "react";
import {
  CheckCircle2,
  CloudUpload,
  Code,
  Database,
  Download,
  Play,
  Save,
  Sparkles,
} from "lucide-react";
import { Button, FileDrop, Spinner, useToast } from "../components/ui.jsx";
import { todayDMY } from "../lib/format.js";
import { analyzeWorkbook, processData } from "./dbProcessing.js";
import {
  backendData,
  backendStatus,
  downloadOutputUrl,
  runVba,
  saveBundle,
  snapshotReports,
  syncToDashboard,
  uploadBackend,
  uploadProcessed,
  vbaBundles,
} from "./disbApi.js";

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

export default function DisbProcess({ onProcessed }) {
  const toast = useToast();
  const [backend, setBackend] = useState({ exists: false, filename: null, rowCount: 0 });
  const [lookup, setLookup] = useState(null);
  const [file, setFile] = useState(null);
  const [analysis, setAnalysis] = useState(null); // {aoa, dateKeys, totalRows}
  const [selectedDate, setSelectedDate] = useState("");
  const [busy, setBusy] = useState("");
  const [result, setResult] = useState(null); // {outName, rowCount, counts, filteredCount}

  useEffect(() => {
    backendStatus()
      .then((s) => {
        setBackend(s);
        if (s.exists) backendData().then((d) => setLookup(d.lookup || null)).catch(() => {});
      })
      .catch(() => {});
  }, []);

  const needsDate = (analysis?.dateKeys?.length || 0) > 0;
  const canProcess = useMemo(
    () => !busy && analysis && (!needsDate || selectedDate),
    [busy, analysis, needsDate, selectedDate]
  );

  async function handleBackend(f) {
    if (!f) return;
    setBusy("backend");
    try {
      const res = await uploadBackend(f);
      setBackend({ exists: true, filename: res.filename, rowCount: res.rowCount });
      setLookup(res.lookup || null);
      toast.success(`Backend reference loaded — ${res.rowCount} branches.`, "Reference ready");
    } catch (e) {
      toast.error(e.message, "Upload failed");
    } finally {
      setBusy("");
    }
  }

  async function handleMainFile(f) {
    setFile(f);
    setAnalysis(null);
    setSelectedDate("");
    setResult(null);
    if (!f) return;
    setBusy("analyze");
    try {
      const buf = await f.arrayBuffer();
      const a = analyzeWorkbook(buf);
      setAnalysis(a);
      if (a.dateKeys.length === 1) setSelectedDate(a.dateKeys[0].date);
      toast.success(
        a.dateKeys.length
          ? `${a.dateKeys.length} disbursement date(s) found · ${a.totalRows} rows.`
          : `${a.totalRows} rows loaded.`,
        "Analyzed"
      );
    } catch (e) {
      toast.error(`Could not analyze file: ${e.message}`, "Analyze failed");
      setFile(null);
    } finally {
      setBusy("");
    }
  }

  async function handleProcess() {
    if (!canProcess) return;
    setBusy("process");
    try {
      const out = processData(analysis.aoa, {
        backendLookup: lookup,
        filterDate: needsDate ? selectedDate : null,
        originalName: file.name,
      });
      if (out.error) {
        toast.error(out.error, "Processing failed");
        return;
      }
      triggerDownload(out.blob, out.outName);
      setResult(out);
      toast.success(`${out.rowCount} rows processed. File downloaded.`, "Done");
      // Upload to server for email/VBA/archive, then snapshot for history.
      try {
        await uploadProcessed(out.blob, out.outName, selectedDate || "");
        snapshotReports(selectedDate || todayDMY(), "").catch(() => {});
        onProcessed?.();
      } catch (up) {
        toast.warn(`Saved locally but server upload failed: ${up.message}`);
      }
    } finally {
      setBusy("");
    }
  }

  async function handleSaveBundle() {
    setBusy("bundle");
    try {
      const res = await saveBundle();
      if (res.already_saved) {
        const replace = window.confirm(
          `This report was already saved to ${res.existing_name}. Save a new copy?`
        );
        const res2 = await saveBundle(replace ? "new" : "replace");
        toast.success(`Bundle saved (${(res2.saved || []).length} file(s)).`, "Saved");
      } else {
        toast.success(`Bundle saved (${(res.saved || []).length} file(s)).`, "Saved");
      }
    } catch (e) {
      toast.error(e.message, "Save failed");
    } finally {
      setBusy("");
    }
  }

  async function handleRunVba() {
    setBusy("vba");
    try {
      // Ensure a bundle exists, then run on the newest one.
      await saveBundle("new").catch(() => {});
      const list = await vbaBundles();
      const newest = (list.bundles || [])[0];
      if (!newest) {
        toast.error("No DB bundle found to run VBA on.", "VBA");
        return;
      }
      const res = await runVba(newest.path);
      if (res.success) toast.success(res.message || "VBA macro completed.", "VBA done");
      else toast.error(res.error || "VBA run failed.", "VBA");
    } catch (e) {
      toast.error(e.message, "VBA failed");
    } finally {
      setBusy("");
    }
  }

  async function handleSync() {
    setBusy("sync");
    try {
      const res = await syncToDashboard();
      toast.success(res.message || "Synced.", "Dashboard");
    } catch (e) {
      toast.error(e.message, "Sync failed");
    } finally {
      setBusy("");
    }
  }

  return (
    <div className="eod-grid">
      <div className="col" style={{ gap: 18 }}>
        <div className="panel">
          <div className="panel-header">
            <div>
              <p className="eyebrow">Reference</p>
              <h2>Backend data for DB</h2>
              <p className="sub">BranchID → Region / RM / Area / AM lookup used to enrich the report.</p>
            </div>
            {backend.exists && (
              <span className="badge badge-success">
                <CheckCircle2 size={12} /> {backend.rowCount} branches
              </span>
            )}
          </div>
          {backend.exists ? (
            <FileDrop locked lockedText="Backend reference loaded" hint={`${backend.filename} · ${backend.rowCount} rows`} />
          ) : (
            <FileDrop
              label="Backend reference (.xlsx/.csv)"
              hint="BranchID mapping"
              accept=".xlsx,.csv"
              file={null}
              onFile={handleBackend}
              disabled={busy === "backend"}
            />
          )}
        </div>

        <div className="panel">
          <div className="panel-header">
            <div>
              <p className="eyebrow">Step 1 · Source</p>
              <h2>Disbursement export</h2>
              <p className="sub">Upload the ClientDisbursementDetail export (.xlsx or .csv).</p>
            </div>
            {busy === "process" && (
              <span className="badge badge-info">
                <Spinner size={13} /> Processing
              </span>
            )}
          </div>

          <div className="file-grid" style={{ gridTemplateColumns: "1fr" }}>
            <FileDrop
              label="Disbursement file"
              hint="Required · .xlsx/.csv"
              accept=".xlsx,.csv"
              file={file}
              onFile={handleMainFile}
              disabled={Boolean(busy)}
            />
          </div>

          {needsDate && (
            <div className="control-grid" style={{ gridTemplateColumns: "1fr" }}>
              <label className="field">
                <span>Disbursement date (adds a filtered sheet)</span>
                <select className="input" value={selectedDate} onChange={(e) => setSelectedDate(e.target.value)}>
                  <option value="">— Select a date —</option>
                  {analysis.dateKeys.map((d) => (
                    <option key={d.date} value={d.date}>
                      {d.date} ({d.count} rows)
                    </option>
                  ))}
                </select>
              </label>
            </div>
          )}

          <div className="actions">
            <Button
              variant="success"
              icon={Play}
              className="grow"
              disabled={!canProcess}
              loading={busy === "process" || busy === "analyze"}
              onClick={handleProcess}
            >
              Run Process
            </Button>
          </div>
        </div>

        {result && (
          <div className="panel">
            <div className="panel-header" style={{ marginBottom: 14 }}>
              <div>
                <p className="eyebrow">Generated</p>
                <h2>Deliver</h2>
                <p className="sub">
                  {result.rowCount} rows · {Object.entries(result.counts).map(([k, v]) => `${k}: ${v}`).join(", ")}
                  {result.filteredCount ? ` · on-date sheet: ${result.filteredCount} rows` : ""}
                </p>
              </div>
            </div>
            <div className="actions">
              <a className="btn btn-primary grow" href={downloadOutputUrl()} style={{ justifyContent: "center" }}>
                <Download size={16} /> Download
              </a>
              <Button variant="ghost" icon={Save} className="grow" loading={busy === "bundle"} onClick={handleSaveBundle}>
                Save Bundle
              </Button>
              <Button variant="ghost" icon={Code} className="grow" loading={busy === "vba"} onClick={handleRunVba}>
                Run VBA
              </Button>
              <Button variant="ghost" icon={CloudUpload} className="grow" loading={busy === "sync"} onClick={handleSync}>
                Sync
              </Button>
            </div>
          </div>
        )}
      </div>

      <div className="col" style={{ gap: 18 }}>
        <div className="panel hint-panel">
          <div className="row" style={{ alignItems: "flex-start", gap: 12 }}>
            <Sparkles size={18} className="muted" />
            <div>
              <strong style={{ fontSize: 13.5 }}>How Disbursement works</strong>
              <p className="muted" style={{ margin: "4px 0 0", fontSize: 12.5 }}>
                Adds a <b>Product Name</b> column (from SchemeID/ProductID), enriches Region / RM /
                Area / AM via the BranchID reference, derives <b>Employee ID</b> from the credit
                officer, and builds the workbook. Pick a date to also get a filtered on-date sheet.
              </p>
              <p className="muted" style={{ margin: "8px 0 0", fontSize: 12 }}>
                Processing runs in your browser (instant, no upload wait); the finished report is then
                saved to the server for Email, VBA and <b>Reports &amp; Downloads</b>.
              </p>
            </div>
          </div>
        </div>

        {backend.exists && (
          <div className="panel">
            <div className="panel-header" style={{ marginBottom: 10 }}>
              <div>
                <p className="eyebrow">Reference</p>
                <h2>Lookup loaded</h2>
              </div>
              <Database size={18} className="muted" />
            </div>
            <p className="muted" style={{ fontSize: 12.5 }}>
              {backend.filename} · {backend.rowCount} branches mapped.
            </p>
          </div>
        )}
      </div>
    </div>
  );
}
