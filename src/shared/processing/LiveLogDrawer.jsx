import { useEffect, useRef, useState } from "react";
import { ChevronDown, ScrollText, X } from "lucide-react";

/**
 * "Live Log" drawer — CLOSED by default. The user clicks "Open Live Log" to
 * reveal the stream and can close it anytime; processing continues regardless.
 * Supports auto-scroll, per-line timestamp, optional stage label, and error
 * highlighting. (This replaces the old always-on "Console Log".)
 *
 * logs: [{ ts, text, tone, stage }] — newest first.
 */
export default function LiveLogDrawer({ logs = [], defaultOpen = false }) {
  const [open, setOpen] = useState(defaultOpen);
  const [autoScroll, setAutoScroll] = useState(true);
  const boxRef = useRef(null);

  // Newest line is at the top of the list; "auto-scroll" keeps it in view.
  useEffect(() => {
    if (open && autoScroll && boxRef.current) boxRef.current.scrollTop = 0;
  }, [logs, open, autoScroll]);

  if (!open) {
    return (
      <button type="button" className="livelog-open-btn" onClick={() => setOpen(true)}>
        <ScrollText size={15} /> Open Live Log
        {logs.length > 0 && <span className="livelog-count">{logs.length}</span>}
      </button>
    );
  }

  return (
    <div className="livelog">
      <div className="livelog-head">
        <span className="livelog-title">
          <ScrollText size={15} /> Live Log
        </span>
        <div className="livelog-actions">
          <label className="livelog-autoscroll">
            <input
              type="checkbox"
              checked={autoScroll}
              onChange={(e) => setAutoScroll(e.target.checked)}
            />
            Auto-scroll
          </label>
          <button type="button" className="livelog-close" onClick={() => setOpen(false)} title="Close Live Log">
            <ChevronDown size={15} /> Close
            <X size={13} />
          </button>
        </div>
      </div>
      <div className="livelog-body" ref={boxRef}>
        {logs.length === 0 ? (
          <p className="livelog-empty">Waiting for log output…</p>
        ) : (
          logs.map((l, i) => (
            <p key={i} className={`livelog-line ${l.tone || "info"}`}>
              <span className="livelog-ts">{l.ts}</span>
              {l.stage && <span className="livelog-stage">{l.stage}</span>}
              <span className="livelog-text">{l.text}</span>
            </p>
          ))
        )}
      </div>
    </div>
  );
}
