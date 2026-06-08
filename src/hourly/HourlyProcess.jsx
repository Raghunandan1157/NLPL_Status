import { useEffect, useState, useMemo, useRef, useCallback } from "react";
import {
  Archive,
  CalendarDays,
  Clock,
  Database,
  Download,
  FileCheck2,
  FolderOpen,
  Merge,
  Play,
  RefreshCw,
  Zap,
  CheckCircle2,
} from "lucide-react";
import { Button, FileDrop, Switch, useToast, Modal, Spinner } from "../components/ui.jsx";
import {
  getBackendFilesStatus,
  checkEodDuplicate,
  getEodMeetingDates,
  deleteHourlyDaily,
  saveHourlyDaily,
  saveBackendFile,
  cacheEodOutput,
  cacheCollection,
  cacheCollectionGdrive,
  processHourly,
  saveToDownloads,
  generateFastReport,
  downloadFastReportUrl,
  getGDriveConfig,
  scanGDriveCollection,
  downloadGDriveFile,
  getBundles,
  useBundle,
  saveBundleToServer,
  runVbaScript,
  snapshotReports,
} from "./hourlyApi.js";
import { eventsUrl } from "../eod/api.js";

export default function HourlyProcess({ status, refreshStatus }) {
  const toast = useToast();
  
  // File inputs
  const [files, setFiles] = useState({ eodOutput: null, collection: null, hourlyDaily: null });
  const [useGDriveCollection, setUseGDriveCollection] = useState(false);
  
  // Date and Time selection
  const [targetDate, setTargetDate] = useState(() => {
    const today = new Date();
    const yyyy = today.getFullYear();
    const mm = String(today.getMonth() + 1).padStart(2, '0');
    const dd = String(today.getDate()).padStart(2, '0');
    return `${yyyy}-${mm}-${dd}`;
  });
  
  const [timeState, setTimeState] = useState(() => {
    const now = new Date();
    let hours = now.getHours();
    const ampm = hours >= 12 ? "PM" : "AM";
    hours = hours % 12;
    hours = hours ? hours : 12; // 0 should be 12
    const mins = Math.floor(now.getMinutes() / 10) * 10;
    return {
      hour: String(hours),
      minute: String(mins).padStart(2, '0'),
      ampm,
    };
  });
  
  // Modals & UI states
  const [gdriveModal, setGdriveModal] = useState(false);
  const [gdriveUrl, setGdriveUrl] = useState("");
  const [gdriveFiles, setGdriveFiles] = useState([]);
  const [selectedGdriveFile, setSelectedGdriveFile] = useState(null);
  
  const [bundleModal, setBundleModal] = useState(false);
  const [bundles, setBundles] = useState([]);
  const [selectedBundle, setSelectedBundle] = useState(null);
  
  const [busy, setBusy] = useState("");
  const [countdown, setCountdown] = useState("");
  const [showHourlyDaily, setShowHourlyDaily] = useState(false);
  
  // SSE log stream state
  const [logs, setLogs] = useState([
    { text: "Ready. Upload files, then run Hourly processing.", tone: "info" },
  ]);
  const [done, setDone] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const esRef = useRef(null);
  const timerRef = useRef(null);
  const logBoxRef = useRef(null);
  
  // Refs
  const countdownInterval = useRef(null);
  
  // Date representation helpers
  const targetDateDMY = useMemo(() => {
    if (!targetDate) return "";
    const parts = targetDate.split("-");
    return `${parts[2]}-${parts[1]}-${parts[0]}`;
  }, [targetDate]);

  const loadGDriveUrl = useCallback(async () => {
    try {
      const res = await getGDriveConfig();
      if (res.success && res.folder_url) {
        setGdriveUrl(res.folder_url);
      }
    } catch {}
  }, []);

  const loadBundles = useCallback(async () => {
    try {
      const res = await getBundles();
      if (res.success) {
        setBundles(res.bundles || []);
      }
    } catch {}
  }, []);

  // Set default values from backend status on load
  useEffect(() => {
    loadGDriveUrl();
    loadBundles();
  }, [loadGDriveUrl, loadBundles]);

  // SSE Logger Helpers
  useEffect(() => () => {
    esRef.current?.close();
    clearInterval(timerRef.current);
  }, []);

  useEffect(() => {
    if (logBoxRef.current) logBoxRef.current.scrollTop = 0;
  }, [logs]);

  const pushLog = useCallback((text, tone = "info") => {
    setLogs((items) => [{ text, tone }, ...items].slice(0, 120));
  }, []);

  const toneFor = useCallback((text) => {
    const t = text.toLowerCase();
    if (t.includes("error") || t.includes("failed") || t.includes("traceback")) return "error";
    if (t.includes("completed") || t.includes("success") || t.includes("saved") || t.includes("done")) return "success";
    if (t.includes("warn")) return "warn";
    return "info";
  }, []);

  const connectLogs = useCallback(() => {
    esRef.current?.close();
    const source = new EventSource(eventsUrl());
    source.onmessage = (event) => {
      if (!event.data) return;
      try {
        const data = JSON.parse(event.data);
        if (data.log) {
          pushLog(data.log, toneFor(data.log));
          if (data.done) {
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
  }, [pushLog, toneFor]);

  const startTimer = useCallback(() => {
    setElapsed(0);
    const t0 = Date.now();
    clearInterval(timerRef.current);
    timerRef.current = setInterval(() => setElapsed((Date.now() - t0) / 1000), 250);
  }, []);

  // Countdown timer for HourlyDaily file
  const startCountdown = useCallback((timestampStr) => {
    if (countdownInterval.current) clearInterval(countdownInterval.current);
    
    function update() {
      const now = new Date();
      const midnight = new Date(now);
      midnight.setHours(24, 0, 0, 0);
      const diff = midnight - now;
      
      if (diff <= 0) {
        clearInterval(countdownInterval.current);
        setCountdown("Expired");
        (async () => {
          try {
            await deleteHourlyDaily();
            toast.warn("Hourly Daily file expired at midnight, removed.");
            refreshStatus();
          } catch {}
        })();
        return;
      }
      
      const hrs = Math.floor(diff / 3600000);
      const mins = Math.floor((diff % 3600000) / 60000);
      const secs = Math.floor((diff % 60000) / 1000);
      const pad = (n) => String(n).padStart(2, "0");
      setCountdown(`${pad(hrs)}:${pad(mins)}:${pad(secs)}`);
    }
    
    update();
    countdownInterval.current = setInterval(update, 1000);
  }, [refreshStatus, toast]);

  useEffect(() => {
    if (status?.backend?.hourlyDailyFile && !status?.backend?.hourlyDailyExpired) {
      startCountdown(status.backend.hourlyDailyTimestamp);
    } else {
      setCountdown("");
      if (countdownInterval.current) clearInterval(countdownInterval.current);
    }
    
    return () => {
      if (countdownInterval.current) clearInterval(countdownInterval.current);
    };
  }, [status, startCountdown]);

  // Handle Manual Uploads
  async function handleEodOutputUpload(file) {
    if (!file) return;
    setBusy("upload-eod");
    try {
      const res = await saveBackendFile("eodOutput", file);
      toast.success(res.message || "EOD Output uploaded.", "Uploaded");
      setFiles((f) => ({ ...f, eodOutput: null }));
      
      // Auto cache immediately
      try {
        await cacheEodOutput();
      } catch {}
      
      refreshStatus();
    } catch (e) {
      toast.error(e.message, "Upload failed");
    } finally {
      setBusy("");
    }
  }

  function handleCollectionUpload(file) {
    if (!file) return;
    // Match the original: hold the Collection file in memory and send it to
    // /process (the backend caches it by hash). Do NOT pre-cache + clear — that
    // left nothing to send and the backend rejected the run.
    setFiles((f) => ({ ...f, collection: file }));
    setUseGDriveCollection(false);
    toast.success(`Collection Report ready: ${file.name}`, "Loaded");
  }

  async function handleHourlyDailyUpload(file) {
    if (!file) return;
    setBusy("upload-hourlydaily");
    try {
      const res = await saveHourlyDaily(file);
      toast.success("Hourly Daily Collection saved successfully.", "Uploaded");
      setFiles((f) => ({ ...f, hourlyDaily: null }));
      refreshStatus();
    } catch (e) {
      toast.error(e.message, "Upload failed");
    } finally {
      setBusy("");
    }
  }

  // Google Drive Handlers
  async function handleScanGDrive() {
    if (!gdriveUrl) {
      toast.warn("Please paste a Google Drive folder URL.");
      return;
    }
    setBusy("gdrive-scan");
    try {
      const res = await scanGDriveCollection(gdriveUrl);
      if (res.success) {
        setGdriveFiles(res.collection_files || []);
        if (res.collection_files?.length > 0) {
          setSelectedGdriveFile(res.collection_files[0]);
        }
        toast.success(`Found ${res.collection_files?.length || 0} collection reports.`, "GDrive scanned");
      } else {
        toast.error(res.message || "GDrive scan failed.");
      }
    } catch (e) {
      toast.error(e.message, "GDrive scan failed");
    } finally {
      setBusy("");
    }
  }

  async function handleDownloadGDrive() {
    if (!selectedGdriveFile) return;
    setBusy("gdrive-download");
    try {
      const res = await downloadGDriveFile(selectedGdriveFile.id, selectedGdriveFile.name, "collection");
      if (res.success) {
        toast.success("Google Drive Collection downloaded & cached.", "GDrive Success");
        setUseGDriveCollection(true);
        setGdriveModal(false);
        refreshStatus();
      } else {
        toast.error(res.message || "GDrive download failed.");
      }
    } catch (e) {
      toast.error(e.message, "GDrive download failed");
    } finally {
      setBusy("");
    }
  }

  // Bundle handlers
  async function handleUseBundle() {
    if (!selectedBundle) return;
    setBusy("use-bundle");
    try {
      const res = await useBundle({
        bundlePath: selectedBundle.path,
        useEodOutput: Boolean(selectedBundle.eod_output_file),
        useHourlyDaily: Boolean(selectedBundle.hourly_daily_file),
        eodOutputName: selectedBundle.eod_output_file,
        hourlyDailyName: selectedBundle.hourly_daily_file,
      });
      
      if (res.success) {
        toast.success("Files from EOD Bundle imported successfully.", "Bundle loaded");
        setBundleModal(false);
        refreshStatus();
      } else {
        toast.error(res.message || "Failed to load bundle.");
      }
    } catch (e) {
      toast.error(e.message, "Bundle load failed");
    } finally {
      setBusy("");
    }
  }

  // Run Hourly Process
  async function handleRunProcess() {
    setBusy("process");
    setDone(false);
    setLogs([{ text: "Hourly merging started…", tone: "info" }]);
    connectLogs();
    startTimer();
    try {
      const options = {
        date: targetDateDMY,
        hour: timeState.hour,
        minute: timeState.minute,
        ampm: timeState.ampm,
        useGDriveCollection,
      };
      
      toast.info("Merging Collection onto EOD output...", "Processing");
      
      // Run process — send the Collection (and optional Hourly Daily) the user
      // loaded, exactly like the original. EOD Output is taken from the backend
      // (auto-flow or a prior upload).
      const blobRes = await processHourly({
        files: { collection: files.collection, hourlyDaily: files.hourlyDaily || null },
        options,
      });
      
      // Save bundle metadata on the server automatically
      try {
        const timeFormatted = `${targetDateDMY} @ ${timeState.hour}:${timeState.minute} ${timeState.ampm}`;
        await saveBundleToServer({
          action: "replace",
          formattedDatetime: timeFormatted,
          dateOnly: targetDateDMY,
          accountIdField: "",
        });
      } catch {}
      
      setDone(true);
      toast.success("Hourly Collection Report processed successfully.", "Finished");
      
      // Auto snapshot for the Reports & Downloads tab
      try {
        const timeFormatted = `${timeState.hour}:${timeState.minute} ${timeState.ampm}`;
        await snapshotReports(targetDateDMY, timeFormatted);
      } catch {}
      
      // Since process returns Excel attachment
      const latestUrl = downloadFastReportUrl(targetDateDMY);
      window.location.href = latestUrl;
      
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

  // Fast Report Execution
  async function handleFastReport() {
    setBusy("fast-report");
    try {
      const res = await generateFastReport({
        targetDate: targetDateDMY,
        hour: timeState.hour,
        minute: timeState.minute,
        ampm: timeState.ampm,
      });
      
      if (res.success) {
        toast.success("Fast hourly report generated.", "Success");
        
        // Auto snapshot for the Reports & Downloads tab
        try {
          const timeFormatted = `${timeState.hour}:${timeState.minute} ${timeState.ampm}`;
          await snapshotReports(targetDateDMY, timeFormatted);
        } catch {}
        
        // Trigger download of fast report
        const dlUrl = downloadFastReportUrl(targetDateDMY);
        window.location.href = dlUrl;
      }
    } catch (e) {
      toast.error(e.message, "Fast Report failed");
    } finally {
      setBusy("");
    }
  }

  // Hourly only needs its own inputs (EOD Output + a Collection report). It does
  // NOT depend on the DuckDB master files — that's an EOD-only requirement.
  const hasEod = Boolean(status?.backend?.eodOutput);
  const hasCollection = useGDriveCollection || Boolean(files.collection);
  const hasHourlyDaily =
    Boolean(files.hourlyDaily) ||
    Boolean(status?.backend?.hourlyDailyFile && !status?.backend?.hourlyDailyExpired);
  const inputsMissing = !hasEod || !hasCollection;

  const canProcess = useMemo(() => {
    if (busy) return false;
    return hasEod && hasCollection;
  }, [busy, hasEod, hasCollection]);

  const showProgress = busy === "process" || elapsed > 0 || done;

  return (
    <div className="eod-grid">
      <div className="col" style={{ gap: 18 }}>
        <div className="panel">
          <div className="panel-header">
            <div>
              <p className="eyebrow">Hourly Collection Processing</p>
              <h2>Upload & Configure</h2>
              <p className="sub">Merge hourly collection reports with today's EOD Output instantly.</p>
            </div>
            {busy === "process" && (
              <span className="badge badge-info">
                <Spinner size={13} /> Merging data
              </span>
            )}
          </div>

          {/* Quick-access bar */}
          <div className="hourly-quick-actions">
            <Button
              variant="outline"
              icon={Zap}
              className="gdrive-quick grow"
              onClick={() => setGdriveModal(true)}
            >
              Google Drive Sync
            </Button>
            <Button
              variant="outline"
              icon={FolderOpen}
              className="bundle-quick grow"
              onClick={() => setBundleModal(true)}
            >
              Load EOD Bundle
            </Button>
          </div>

          {/* EOD Output auto-flows from your latest EOD run — you only upload it
              if it is missing. The Collection Report is the one file you upload
              each time to run the hourly merge. Hourly Daily is optional (VBA). */}
          {hasEod ? (
            <div className="file-pill" style={{ display: "flex", alignItems: "center", gap: 8, padding: "10px 12px", marginBottom: 14 }}>
              <CheckCircle2 size={15} style={{ color: "var(--success)" }} />
              <span style={{ fontSize: 12.5 }}>
                <b>EOD Output ready</b>
                {status?.backend?.eodOutputSource === "eod-auto" ? " — from your EOD run" : " — uploaded"}
                {status?.backend?.eodOutput ? ` · ${status.backend.eodOutput}` : ""}
              </span>
            </div>
          ) : (
            <div style={{ display: "flex", flexDirection: "column", gap: 6, marginBottom: 14 }}>
              <strong style={{ fontSize: 12.5, color: "var(--text-soft)" }}>
                EOD Output · Required — run EOD first, or upload it here
              </strong>
              <FileDrop
                label="Upload EOD Output Excel"
                hint="Required · .xlsx"
                file={files.eodOutput}
                onFile={handleEodOutputUpload}
                disabled={busy === "upload-eod"}
              />
            </div>
          )}

          {/* The one file you upload each run */}
          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            <strong style={{ fontSize: 12.5, color: "var(--text-soft)" }}>
              Collection Report · Required — upload this to run
            </strong>
            <FileDrop
              label={hasCollection ? "Replace Collection Report" : "Upload the Collection Report Excel"}
              hint={
                useGDriveCollection
                  ? "Loaded from Google Drive"
                  : files.collection
                  ? `Ready: ${files.collection.name}`
                  : "Required · .xlsx (or use Google Drive Sync above)"
              }
              file={files.collection}
              onFile={handleCollectionUpload}
              disabled={Boolean(busy)}
            />
          </div>

          {/* Optional Hourly Daily — only for the VBA Merge step, hidden by default */}
          {showHourlyDaily || hasHourlyDaily ? (
            <div style={{ display: "flex", flexDirection: "column", gap: 6, marginTop: 12 }}>
              <strong style={{ fontSize: 12.5, color: "var(--text-soft)" }}>Hourly Daily · Optional (VBA Merge)</strong>
              <FileDrop
                label={hasHourlyDaily ? "Replace Hourly Daily" : "Upload Hourly Daily Report"}
                hint={hasHourlyDaily ? `Loaded: ${status.backend.hourlyDailyFile}` : "Optional · For VBA Merge"}
                file={files.hourlyDaily}
                onFile={handleHourlyDailyUpload}
                disabled={busy === "upload-hourlydaily"}
              />
              {hasHourlyDaily && (
                <div className="file-pill" style={{ fontSize: 11, padding: "3px 8px", background: "var(--warn-soft)", color: "#a16207" }}>
                  Expires in: <span className="countdown-box" style={{ color: "#a16207" }}>{countdown}</span>
                </div>
              )}
            </div>
          ) : (
            <button
              type="button"
              onClick={() => setShowHourlyDaily(true)}
              style={{ marginTop: 10, fontSize: 12, background: "none", border: "none", cursor: "pointer", color: "var(--text-muted)", padding: 0, textAlign: "left" }}
            >
              + Add optional Hourly Daily file (only needed for VBA Merge)
            </button>
          )}

          {/* Date Picker + Time Select Grid */}
          <div className="control-grid" style={{ marginTop: 12 }}>
            <div className="field">
              <label style={{ fontSize: 12, fontWeight: 600, color: "var(--text-muted)", display: "block", marginBottom: 6 }}>Target Date</label>
              <div style={{ display: "flex", alignItems: "center", gap: 10, background: "var(--surface-3)", border: "1px solid var(--border)", borderRadius: "8px", padding: "6px 12px" }}>
                <CalendarDays size={16} className="text-muted" />
                <input
                  type="date"
                  value={targetDate}
                  onChange={(e) => setTargetDate(e.target.value)}
                  style={{ background: "transparent", border: "none", color: "var(--text)", outline: "none", fontSize: 13, width: "100%" }}
                />
              </div>
            </div>

            <div className="field">
              <label style={{ fontSize: 12, fontWeight: 600, color: "var(--text-muted)", display: "block", marginBottom: 6 }}>Report Run Time</label>
              <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                <select
                  value={timeState.hour}
                  onChange={(e) => setTimeState({ ...timeState, hour: e.target.value })}
                  style={{ background: "var(--surface-3)", border: "1px solid var(--border)", borderRadius: "8px", padding: "6px 10px", color: "var(--text)", outline: "none", fontSize: 13 }}
                >
                  {Array.from({ length: 12 }, (_, i) => String(i + 1)).map((h) => (
                    <option key={h} value={h}>{h}</option>
                  ))}
                </select>
                <select
                  value={timeState.minute}
                  onChange={(e) => setTimeState({ ...timeState, minute: e.target.value })}
                  style={{ background: "var(--surface-3)", border: "1px solid var(--border)", borderRadius: "8px", padding: "6px 10px", color: "var(--text)", outline: "none", fontSize: 13 }}
                >
                  {["00", "10", "20", "30", "40", "50"].map((m) => (
                    <option key={m} value={m}>{m}</option>
                  ))}
                </select>
                <select
                  value={timeState.ampm}
                  onChange={(e) => setTimeState({ ...timeState, ampm: e.target.value })}
                  style={{ background: "var(--surface-3)", border: "1px solid var(--border)", borderRadius: "8px", padding: "6px 10px", color: "var(--text)", outline: "none", fontSize: 13 }}
                >
                  <option value="AM">AM</option>
                  <option value="PM">PM</option>
                </select>
              </div>
            </div>
          </div>

          {inputsMissing ? (
            <div className="banner warn" style={{ marginTop: 16, marginBottom: 8 }}>
              <strong>Not ready yet:</strong>{" "}
              {!hasEod && !hasCollection
                ? "Run the EOD module first (for the EOD Output), then upload a Collection Report here."
                : !hasEod
                ? "No EOD Output found — run the EOD module first, or upload it above."
                : "Upload a Collection Report above (or use Google Drive Sync) to run."}
            </div>
          ) : (
            <div
              className="banner"
              style={{ marginTop: 16, marginBottom: 8, background: "var(--success-soft)", color: "#15803d", display: "flex", alignItems: "center", gap: 8 }}
            >
              <CheckCircle2 size={15} /> Ready to process — click <b>Run Hourly Processing</b>.
            </div>
          )}

          <div className="actions" style={{ marginTop: 18 }}>
            <Button
              variant="success"
              icon={Play}
              disabled={!canProcess}
              loading={busy === "process"}
              onClick={handleRunProcess}
              style={{ flex: 2 }}
            >
              Run Hourly Processing
            </Button>
            <Button
              variant="primary"
              icon={Zap}
              disabled={!status?.backend?.fastReportAvailable || busy === "process"}
              loading={busy === "fast-report"}
              onClick={handleFastReport}
              style={{ flex: 1 }}
            >
              Fast Report
            </Button>
          </div>
        </div>

        {/* Progress + live log */}
        {showProgress && (
          <div className="panel" style={{ marginTop: 18 }}>
            <div className="panel-header" style={{ marginBottom: 14 }}>
              <div>
                <p className="eyebrow">Pipeline</p>
                <h2>{done ? "Completed" : busy === "process" ? "Processing…" : "Last run"}</h2>
              </div>
              <div className="row">
                <span className="badge badge-muted">{elapsed ? `${elapsed.toFixed(1)}s` : "0.0s"}</span>
                {done && (
                  <span className="badge badge-success">
                    <CheckCircle2 size={13} /> Done
                  </span>
                )}
              </div>
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

      {/* Side column: which inputs are currently loaded */}
      <div className="col" style={{ gap: 18 }}>
        <div className="panel">
          <div className="panel-header" style={{ marginBottom: 12 }}>
            <div>
              <p className="eyebrow">Inputs</p>
              <h2>Loaded Files</h2>
              <p className="sub">Upload all files yourself. Replace any of them anytime.</p>
            </div>
            <Archive size={18} className="muted" />
          </div>
          <div className="db-summary-rows">
            <div className="db-summary-row">
              <span className={`dot ${hasEod ? "ok" : ""}`} />
              <span className="db-summary-name">EOD Output</span>
              <span className={`db-summary-state ${hasEod ? "ok" : "bad"}`}>{hasEod ? "Ready (auto)" : "Run EOD first"}</span>
            </div>
            <div className="db-summary-row">
              <span className={`dot ${hasCollection ? "ok" : ""}`} />
              <span className="db-summary-name">Collection Report</span>
              <span className={`db-summary-state ${hasCollection ? "ok" : "bad"}`}>{hasCollection ? "Loaded" : "Upload to run"}</span>
            </div>
          </div>
        </div>

        <div className="panel hint-panel">
          <div className="row" style={{ alignItems: "flex-start", gap: 12 }}>
            <FileCheck2 size={18} className="muted" />
            <div>
              <strong style={{ fontSize: 13.5 }}>Hourly Operations</strong>
              <p className="muted" style={{ margin: "4px 0 0", fontSize: 12.5 }}>
                1. <b>Upload the Collection Report</b> (or use Google Drive Sync) — this is the only file you provide each run.
              </p>
              <p className="muted" style={{ margin: "4px 0 0", fontSize: 12.5 }}>
                2. The <b>EOD Output</b> is taken automatically from your latest EOD run (upload it only if none exists).
              </p>
              <p className="muted" style={{ margin: "4px 0 0", fontSize: 12.5 }}>
                3. Click <b>Run Hourly Processing</b> to merge them into a clean Excel report.
              </p>
              <p className="muted" style={{ margin: "8px 0 0", fontSize: 12 }}>
                🧹 Stale Hourly reports are deleted after 3 days. Config and contacts remain permanent.
              </p>
            </div>
          </div>
        </div>
      </div>

      {/* Google Drive sync Modal */}
      {gdriveModal && (
        <Modal
          title="Google Drive — Collection Report"
          onClose={() => setGdriveModal(false)}
          footer={
            <>
              <Button variant="ghost" onClick={() => setGdriveModal(false)}>
                Cancel
              </Button>
              <Button
                variant="primary"
                disabled={!selectedGdriveFile}
                loading={busy === "gdrive-download"}
                onClick={handleDownloadGDrive}
              >
                Download &amp; Use
              </Button>
            </>
          }
        >
          <div style={{ display: "flex", gap: 10, marginBottom: 16 }}>
            <input
              type="text"
              className="input"
              value={gdriveUrl}
              onChange={(e) => setGdriveUrl(e.target.value)}
              placeholder="Paste Google Drive folder URL"
              style={{ flex: 1 }}
            />
            <Button
              variant="outline"
              loading={busy === "gdrive-scan"}
              onClick={handleScanGDrive}
            >
              Scan
            </Button>
          </div>

          {gdriveFiles.length > 0 && (
            <div style={{ maxHeight: 200, overflowY: "auto", border: "1px solid var(--border)", borderRadius: 8, padding: 8 }}>
              {gdriveFiles.map((file) => {
                const isSelected = selectedGdriveFile?.id === file.id;
                return (
                  <div
                    key={file.id}
                    className={`bundle-item-row ${isSelected ? "selected" : ""}`}
                    onClick={() => setSelectedGdriveFile(file)}
                    style={{ padding: "8px 12px", borderRadius: 6, cursor: "pointer", marginBottom: 4 }}
                  >
                    <span style={{ fontSize: 12.5 }}>{file.name}</span>
                  </div>
                );
              })}
            </div>
          )}
        </Modal>
      )}

      {/* EOD Bundle Modal */}
      {bundleModal && (
        <Modal
          title="EOD Bundle Selection"
          onClose={() => setBundleModal(false)}
          footer={
            <>
              <Button variant="ghost" onClick={() => setBundleModal(false)}>
                Cancel
              </Button>
              <Button
                variant="primary"
                disabled={!selectedBundle}
                loading={busy === "use-bundle"}
                onClick={handleUseBundle}
              >
                Confirm &amp; Use
              </Button>
            </>
          }
        >
          {bundles.length === 0 ? (
            <p className="muted" style={{ padding: 20, textAlign: "center" }}>No EOD Bundle folders found.</p>
          ) : (
            <div style={{ display: "flex", flexDirection: "column", gap: 8, maxHeight: 300, overflowY: "auto" }}>
              {bundles.map((bundle) => {
                const isSelected = selectedBundle?.name === bundle.name;
                return (
                  <div
                    key={bundle.name}
                    className={`bundle-item-row ${isSelected ? "selected" : ""}`}
                    onClick={() => setSelectedBundle(bundle)}
                  >
                    <div>
                      <strong>{bundle.name}</strong>
                      <div className="bundle-item-files">
                         Files: {bundle.files?.join(", ")}
                      </div>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </Modal>
      )}
    </div>
  );
}
