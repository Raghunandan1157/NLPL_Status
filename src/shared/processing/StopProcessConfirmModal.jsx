import { AlertTriangle, Loader2 } from "lucide-react";

/**
 * Premium confirmation shown when the user tries to navigate / refresh / leave
 * while a process is still running. Wording is fixed per spec.
 *
 * Props: open, stopping, onStay, onStop.
 */
export default function StopProcessConfirmModal({ open, stopping, onStay, onStop }) {
  if (!open) return null;
  return (
    <div className="modal-overlay" onMouseDown={(e) => e.target === e.currentTarget && !stopping && onStay?.()}>
      <div className="modal stop-modal" role="dialog" aria-modal="true">
        <div className="stop-modal-icon">
          <AlertTriangle size={26} />
        </div>
        <h3 className="stop-modal-title">Processing is still running</h3>
        <p className="stop-modal-msg">
          A report is currently being processed. If you leave now, the current process will be
          stopped from the backend also. Do you want to stop processing and continue?
        </p>
        <div className="stop-modal-actions">
          <button type="button" className="btn btn-ghost" onClick={onStay} disabled={stopping}>
            Stay here
          </button>
          <button type="button" className="btn btn-danger-soft" onClick={onStop} disabled={stopping}>
            {stopping ? (
              <>
                <Loader2 size={15} className="spin" /> Stopping…
              </>
            ) : (
              "Stop processing & continue"
            )}
          </button>
        </div>
      </div>
    </div>
  );
}
