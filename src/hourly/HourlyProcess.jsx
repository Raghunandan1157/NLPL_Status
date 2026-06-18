import { useEffect, useState, useMemo, useCallback } from "react";
import {
  Archive,
  CalendarDays,
  CheckCircle2,
  Database,
  Download,
  FileCheck2,
  FileSpreadsheet,
  FolderOpen,
  Loader2,
  Play,
  Zap,
} from "lucide-react";
import { Button, FileDrop, useToast, Modal } from "../components/ui.jsx";
import { syncHourly } from "../growwithme/growwithmeApi.js";
import {
  saveBackendFile,
  cacheEodOutput,
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
  snapshotReports,
} from "./hourlyApi.js";
import { useProcessingJob } from "../shared/processing/useProcessingJob.js";
import ProcessingPanel from "../shared/processing/ProcessingPanel.jsx";

// The six visible stages of an hourly run, in order.
const STEPS = [
  { key: "upload", label: "File uploaded" },
  { key: "validate", label: "Validating columns" },
  { key: "match", label: "Matching data" },
  { key: "generate", label: "Generating report" },
  { key: "save", label: "Saving to reports" },
  { key: "download", label: "Download ready" },
];

const HOURS = Array.from({ length: 12 }, (_, i) => String(i + 1));
const MINUTES = ["00", "10", "20", "30", "40", "50"];

export default function HourlyProcess({ status, refreshStatus, goToReports }) {
  const toast = useToast();
  const job = useProcessingJob({ module: "hourly", steps: STEPS });

  // File inputs (Collection is the only file uploaded each run; EOD Output
  // auto-flows from the latest EOD run or a prior upload).
  const [files, setFiles] = useState({ eodOutput: null, collection: null });
  const [useGDriveCollection, setUseGDriveCollection] = useState(false);

  // Date + time selection
  const [targetDate, setTargetDate] = useState(() => {
    const today = new Date();
    const yyyy = today.getFullYear();
    const mm = String(today.getMonth() + 1).padStart(2, "0");
    const dd = String(today.getDate()).padStart(2, "0");
    return `${yyyy}-${mm}-${dd}`;
  });

  const [timeState, setTimeState] = useState(() => {
    const now = new Date();
    let hours = now.getHours();
    const ampm = hours >= 12 ? "PM" : "AM";
    hours = hours % 12;
    hours = hours ? hours : 12;
    const mins = Math.floor(now.getMinutes() / 10) * 10;
    return { hour: String(hours), minute: String(mins).padStart(2, "0"), ampm };
  });

  // Modals
  const [gdriveModal, setGdriveModal] = useState(false);
  const [gdriveUrl, setGdriveUrl] = useState("");
  const [gdriveFiles, setGdriveFiles] = useState([]);
  const [selectedGdriveFile, setSelectedGdriveFile] = useState(null);

  const [bundleModal, setBundleModal] = useState(false);
  const [bundles, setBundles] = useState([]);
  const [selectedBundle, setSelectedBundle] = useState(null);

  const [busy, setBusy] = useState("");
  const [report, setReport] = useState(null); // { filename }

  const targetDateDMY = useMemo(() => {
    if (!targetDate) return "";
    const [y, m, d] = targetDate.split("-");
    return `${d}-${m}-${y}`;
  }, [targetDate]);

  const loadGDriveUrl = useCallback(async () => {
    try {
      const res = await getGDriveConfig();
      if (res.success && res.folder_url) setGdriveUrl(res.folder_url);
    } catch {}
  }, []);

  const loadBundles = useCallback(async () => {
    try {
      const res = await getBundles();
      if (res.success) setBundles(res.bundles || []);
    } catch {}
  }, []);

  useEffect(() => {
    loadGDriveUrl();
    loadBundles();
  }, [loadGDriveUrl, loadBundles]);

  // EOD Output manual upload
  async function handleEodOutputUpload(file) {
    if (!file) return;
    setBusy("upload-eod");
    try {
      const res = await saveBackendFile("eodOutput", file);
      toast.success(res.message || "EOD Output uploaded.", "Uploaded");
      setFiles((f) => ({ ...f, eodOutput: null }));
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
    setFiles((f) => ({ ...f, collection: file }));
    setUseGDriveCollection(false);
    toast.success(`Collection Report ready: ${file.name}`, "Loaded");
  }

  // Google Drive
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
        if (res.collection_files?.length > 0) setSelectedGdriveFile(res.collection_files[0]);
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

  // Bundles
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

  // Main run — driven by the shared processing job (status, steps, live log,
  // navigation guard + cancellation are all handled by the hook).
  async function handleRunProcess() {
    setReport(null);
    toast.info("Merging Collection onto EOD Output…", "Processing");
    try {
      const out = await job.run(async ({ processId, signal, setStep, setSteps, log }) => {
        setStep(0, "done"); // File uploaded
        setSteps({ 1: "active", 2: "active", 3: "active" });

        const options = {
          date: targetDateDMY,
          hour: timeState.hour,
          minute: timeState.minute,
          ampm: timeState.ampm,
          useGDriveCollection,
          processId,
          signal,
        };

        // Returns the DETAILED Hourly Collection Report and downloads it.
        const res = await processHourly({ files: { collection: files.collection }, options });

        setSteps({ 1: "done", 2: "done", 3: "done", 5: "done" });
        setStep(4, "active"); // Saving to reports
        setReport({ filename: res.filename });
        log(`Generated "${res.filename}".`, "success");

        // Persist bundle metadata on the server.
        try {
          const timeFormatted = `${targetDateDMY} @ ${timeState.hour}:${timeState.minute} ${timeState.ampm}`;
          await saveBundleToServer({
            action: "replace",
            formattedDatetime: timeFormatted,
            dateOnly: targetDateDMY,
            accountIdField: res.accountIdField || "",
          });
        } catch {}

        // Archive into Reports & Downloads (date-wise).
        try {
          const timeFormatted = `${timeState.hour}:${timeState.minute} ${timeState.ampm}`;
          await snapshotReports(targetDateDMY, timeFormatted);
        } catch {}

        setStep(4, "done");
        return { report: { filename: res.filename } };
      });

      // Only a SUCCESSFUL run moves to Reports. Cancelled runs return null;
      // failed runs throw (caught below) — neither saves or navigates.
      if (!out) return;

      toast.success("Hourly Report generated and saved to Reports & Downloads.", "Finished");
      refreshStatus();
      // Focus the Reports & Downloads section after success.
      if (goToReports) setTimeout(goToReports, 600);
    } catch (e) {
      if (!e?.cancelled) toast.error(e.message, "Processing failed");
    }
  }

  async function handleSaveCopy() {
    try {
      const res = await saveToDownloads();
      if (res.success) toast.success(`Saved to Downloads: ${res.filename || "report"}`, "Saved");
    } catch (e) {
      toast.error(e.message, "Save failed");
    }
  }

  // Push the latest hourly report's per-employee data into GrowwithmeDB (AWS).
  async function handleSyncDb() {
    setBusy("sync-db");
    try {
      const res = await syncHourly(); // no file → backend uses the latest hourly report
      if (res.success) toast.success(res.message || "Hourly data synced to database.", "Synced to database");
      else toast.error(res.message, "Sync failed");
    } catch (e) {
      toast.error(e.message, "Sync failed");
    } finally {
      setBusy("");
    }
  }

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
        toast.success("Fast summary report generated.", "Success");
        try {
          const timeFormatted = `${timeState.hour}:${timeState.minute} ${timeState.ampm}`;
          await snapshotReports(targetDateDMY, timeFormatted);
        } catch {}
        window.location.href = downloadFastReportUrl(targetDateDMY);
      }
    } catch (e) {
      toast.error(e.message, "Fast Report failed");
    } finally {
      setBusy("");
    }
  }

  // Hourly only needs EOD Output + a Collection report. It does NOT depend on
  // the DuckDB master files — that's an EOD-only requirement.
  const hasEod = Boolean(status?.backend?.eodOutput);
  const hasCollection = useGDriveCollection || Boolean(files.collection);
  const inputsMissing = !hasEod || !hasCollection;

  const canProcess = useMemo(
    () => !busy && !job.busy && hasEod && hasCollection,
    [busy, job.busy, hasEod, hasCollection]
  );
  const showPanel = job.status !== "idle";

  const selectedTimeLabel = `${timeState.hour}:${timeState.minute} ${timeState.ampm}`;

  return (
    <div className="eod-grid">
      <div className="col" style={{ gap: 18 }}>
        <div className="panel hourly-config">
          <div className="panel-header hourly-gradient-head">
            <div>
              <p className="eyebrow">Hourly Collection Processing</p>
              <h2>Upload &amp; Configure</h2>
              <p className="sub">Merge a Collection Report onto today's EOD Output — exact same engine, modern flow.</p>
            </div>
            {job.running && (
              <span className="badge badge-info">
                <Loader2 size={13} className="spin" /> Merging
              </span>
            )}
          </div>

          {/* Quick-access bar */}
          <div className="hourly-quick-actions">
            <Button variant="outline" icon={Zap} className="gdrive-quick grow" onClick={() => setGdriveModal(true)}>
              Google Drive Sync
            </Button>
            <Button variant="outline" icon={FolderOpen} className="bundle-quick grow" onClick={() => setBundleModal(true)}>
              Load EOD Bundle
            </Button>
          </div>

          {/* EOD Output (auto from EOD run; upload only if missing) */}
          {hasEod ? (
            <div className="hourly-eod-pill">
              <CheckCircle2 size={15} style={{ color: "var(--success)" }} />
              <span>
                <b>EOD Output ready</b>
                {status?.backend?.eodOutputSource === "eod-auto" ? " — from your EOD run" : " — uploaded"}
                {status?.backend?.eodOutput ? ` · ${status.backend.eodOutput}` : ""}
              </span>
            </div>
          ) : (
            <div className="hourly-field">
              <strong className="hourly-field-label">EOD Output · Required — run EOD first, or upload it here</strong>
              <FileDrop
                label="Upload EOD Output Excel"
                hint="Required · .xlsx"
                file={files.eodOutput}
                onFile={handleEodOutputUpload}
                disabled={busy === "upload-eod"}
              />
            </div>
          )}

          {/* Collection Report — the one required file uploaded each run */}
          <div className="hourly-field">
            <strong className="hourly-field-label">Collection Report · Required — upload this to run</strong>
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

          {/* Date */}
          <div className="hourly-field">
            <strong className="hourly-field-label">Target Date</strong>
            <div className="hourly-date-input">
              <CalendarDays size={16} className="text-muted" />
              <input type="date" value={targetDate} onChange={(e) => setTargetDate(e.target.value)} />
            </div>
          </div>

          {/* Time — modern chip selector */}
          <div className="hourly-field">
            <div className="hourly-time-head">
              <strong className="hourly-field-label" style={{ margin: 0 }}>Report Run Time</strong>
              <span className="hourly-time-chip">{selectedTimeLabel}</span>
            </div>

            <span className="hourly-time-sub">Hour</span>
            <div className="number-grid hourly-hour-grid">
              {HOURS.map((h) => (
                <button
                  key={h}
                  type="button"
                  className={`number-btn ${timeState.hour === h ? "active" : ""}`}
                  onClick={() => setTimeState((t) => ({ ...t, hour: h }))}
                >
                  {h}
                </button>
              ))}
            </div>

            <span className="hourly-time-sub">Minute</span>
            <div className="number-grid hourly-min-grid">
              {MINUTES.map((m) => (
                <button
                  key={m}
                  type="button"
                  className={`number-btn ${timeState.minute === m ? "active" : ""}`}
                  onClick={() => setTimeState((t) => ({ ...t, minute: m }))}
                >
                  :{m}
                </button>
              ))}
            </div>

            <span className="hourly-time-sub">Meridiem</span>
            <div className="ampm-grid hourly-ampm">
              {["AM", "PM"].map((p) => (
                <button
                  key={p}
                  type="button"
                  className={`demo-ampm-btn ${timeState.ampm === p ? "active" : ""}`}
                  onClick={() => setTimeState((t) => ({ ...t, ampm: p }))}
                >
                  <span className="ampm-label">{p}</span>
                  <span className="ampm-desc">{p === "AM" ? "Morning" : "Afternoon"}</span>
                </button>
              ))}
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
            <div className="banner hourly-ready">
              <CheckCircle2 size={15} /> Ready to process — click <b>Run Hourly Processing</b>.
            </div>
          )}

          <div className="actions" style={{ marginTop: 18 }}>
            <Button
              variant="success"
              icon={Play}
              disabled={!canProcess}
              loading={job.running}
              onClick={handleRunProcess}
              style={{ flex: 2 }}
            >
              Run Hourly Processing
            </Button>
            <Button
              variant="primary"
              icon={Zap}
              disabled={!status?.backend?.fastReportAvailable || job.busy}
              loading={busy === "fast-report"}
              onClick={handleFastReport}
              style={{ flex: 1 }}
            >
              Fast Report
            </Button>
          </div>

          {/* Push the generated hourly report straight into GrowwithmeDB (AWS). */}
          <div className="actions" style={{ marginTop: 10 }}>
            <Button
              variant="outline"
              icon={Database}
              className="grow"
              disabled={Boolean(busy) || job.busy}
              loading={busy === "sync-db"}
              onClick={handleSyncDb}
            >
              Sync hourly to database
            </Button>
          </div>
        </div>

        {/* Shared processing panel: status, step timeline, live log, stop. */}
        {showPanel && (
          <ProcessingPanel
            job={job}
            eyebrow="Pipeline"
            onRetry={canProcess ? handleRunProcess : undefined}
            reportCard={
              report && (
                <div className="hourly-report-card">
                  <FileSpreadsheet size={20} className="text-muted" />
                  <div className="hourly-report-meta">
                    <strong title={report.filename}>{report.filename}</strong>
                    <span>Saved · available in Reports &amp; Downloads</span>
                  </div>
                  <Button variant="outline" icon={Download} onClick={handleSaveCopy}>
                    Download
                  </Button>
                </div>
              )
            }
          />
        )}
      </div>

      {/* Side column */}
      <div className="col" style={{ gap: 18 }}>
        <div className="panel">
          <div className="panel-header" style={{ marginBottom: 12 }}>
            <div>
              <p className="eyebrow">Inputs</p>
              <h2>Loaded Files</h2>
              <p className="sub">EOD Output auto-flows from EOD. Upload only the Collection Report.</p>
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
                1. <b>Upload the Collection Report</b> (or use Google Drive Sync) — the only file you provide each run.
              </p>
              <p className="muted" style={{ margin: "4px 0 0", fontSize: 12.5 }}>
                2. The <b>EOD Output</b> comes automatically from your latest EOD run (upload only if none exists).
              </p>
              <p className="muted" style={{ margin: "4px 0 0", fontSize: 12.5 }}>
                3. Click <b>Run Hourly Processing</b> — the detailed report downloads and is archived under Reports.
              </p>
              <p className="muted" style={{ margin: "8px 0 0", fontSize: 12 }}>
                🧹 Stale Hourly reports are deleted after 3 days. Config and contacts remain permanent.
              </p>
            </div>
          </div>
        </div>
      </div>

      {/* Google Drive modal */}
      {gdriveModal && (
        <Modal
          title="Google Drive — Collection Report"
          onClose={() => setGdriveModal(false)}
          footer={
            <>
              <Button variant="ghost" onClick={() => setGdriveModal(false)}>
                Cancel
              </Button>
              <Button variant="primary" disabled={!selectedGdriveFile} loading={busy === "gdrive-download"} onClick={handleDownloadGDrive}>
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
            <Button variant="outline" loading={busy === "gdrive-scan"} onClick={handleScanGDrive}>
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

      {/* EOD Bundle modal */}
      {bundleModal && (
        <Modal
          title="EOD Bundle Selection"
          onClose={() => setBundleModal(false)}
          footer={
            <>
              <Button variant="ghost" onClick={() => setBundleModal(false)}>
                Cancel
              </Button>
              <Button variant="primary" disabled={!selectedBundle} loading={busy === "use-bundle"} onClick={handleUseBundle}>
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
                      <div className="bundle-item-files">Files: {bundle.files?.join(", ")}</div>
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
