import { CheckCircle2, FileSpreadsheet, Loader2, Octagon, RotateCcw, XCircle } from "lucide-react";
import ProcessingStepTimeline from "./ProcessingStepTimeline.jsx";
import LiveLogDrawer from "./LiveLogDrawer.jsx";

/**
 * Reusable processing panel: status header (with live elapsed timer), step
 * timeline, success/cancelled/error cards, an inline Stop button while running,
 * and the collapsible Live Log. Driven entirely by a useProcessingJob() instance.
 *
 * Props:
 *   job        — the object returned by useProcessingJob()
 *   title      — optional heading override
 *   eyebrow    — small label above the heading
 *   reportCard — optional node rendered inside the success area (e.g. download)
 *   onStop     — optional override for the Stop button (defaults to job.cancel)
 *   onRetry    — optional handler; shows a Retry button on cancelled/error
 */
export default function ProcessingPanel({ job, title, eyebrow = "Pipeline", reportCard, onStop, onRetry }) {
  const { status, steps, stepStates, logs, elapsed } = job;
  const heading =
    title ||
    (status === "completed"
      ? "Completed"
      : status === "cancelling"
      ? "Stopping…"
      : status === "cancelled"
      ? "Cancelled"
      : status === "error"
      ? "Failed"
      : status === "running"
      ? "Processing…"
      : "Last run");

  return (
    <div className="panel proc-panel">
      <div className="panel-header proc-panel-head">
        <div>
          <p className="eyebrow">{eyebrow}</p>
          <h2>{heading}</h2>
        </div>
        <div className="row">
          <span className="badge badge-muted">{elapsed ? `${elapsed.toFixed(1)}s` : "0.0s"}</span>
          {status === "completed" && (
            <span className="badge badge-success">
              <CheckCircle2 size={13} /> Done
            </span>
          )}
          {status === "error" && (
            <span className="badge badge-danger">
              <XCircle size={13} /> Error
            </span>
          )}
          {status === "cancelled" && <span className="badge badge-warn">Cancelled</span>}
          {status === "cancelling" && (
            <span className="badge badge-warn">
              <Loader2 size={13} className="spin" /> Stopping
            </span>
          )}
          {status === "running" && (
            <span className="badge badge-info">
              <Loader2 size={13} className="spin" /> Running
            </span>
          )}
        </div>
      </div>

      <ProcessingStepTimeline steps={steps} states={stepStates} />

      {status === "running" && (
        <button type="button" className="btn btn-danger-soft proc-stop-btn" onClick={onStop || job.cancel}>
          <Octagon size={15} /> Stop processing
        </button>
      )}

      {status === "cancelling" && (
        <button type="button" className="btn btn-danger-soft proc-stop-btn" disabled>
          <Loader2 size={15} className="spin" /> Stopping process…
        </button>
      )}

      {status === "completed" && (
        <div className="proc-success-card">
          <span className="proc-success-tick">
            <CheckCircle2 size={20} />
          </span>
          <div className="proc-success-text">
            <strong>Report generated successfully</strong>
            <span>All steps completed · saved to Reports &amp; Downloads</span>
          </div>
          <FileSpreadsheet size={20} className="text-muted" />
        </div>
      )}

      {status === "completed" && reportCard}

      {status === "cancelled" && (
        <div className="proc-result-card cancelled">
          <div className="proc-result-text">
            <strong>Process stopped safely</strong>
            <span>No report was generated. You can start again.</span>
          </div>
          {onRetry && (
            <button type="button" className="btn btn-soft btn-sm" onClick={onRetry}>
              <RotateCcw size={15} /> Start again
            </button>
          )}
        </div>
      )}

      {status === "error" && (
        <div className="proc-result-card failed">
          <div className="proc-result-text">
            <strong>Processing failed</strong>
            <span>No report was generated. Check the Live Log, then retry.</span>
          </div>
          {onRetry && (
            <button type="button" className="btn btn-soft btn-sm" onClick={onRetry}>
              <RotateCcw size={15} /> Retry
            </button>
          )}
        </div>
      )}

      <LiveLogDrawer logs={logs} />
    </div>
  );
}
