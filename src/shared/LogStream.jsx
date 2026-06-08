import { useEffect, useRef } from "react";
import { CheckCircle2 } from "lucide-react";
import { ProgressBar } from "../components/ui.jsx";

/**
 * Shared progress + live-log panel for module process flows. Mirrors the
 * EOD/Hourly "Pipeline" panel (elapsed badge, progress bar, newest-first log
 * stream) so every migrated module looks and behaves the same.
 */
export default function LogStream({ logs, elapsed, done, pct, title, eyebrow = "Live progress" }) {
  const boxRef = useRef(null);
  useEffect(() => {
    if (boxRef.current) boxRef.current.scrollTop = 0;
  }, [logs]);

  const value = typeof pct === "number" ? pct : done ? 100 : 60;

  return (
    <div className="panel">
      <div className="panel-header" style={{ marginBottom: 14 }}>
        <div>
          <p className="eyebrow">{eyebrow}</p>
          <h2>{title || (done ? "Completed" : "Processing…")}</h2>
        </div>
        <div className="row">
          <span className="badge badge-muted">{elapsed ? `${elapsed.toFixed(1)}s` : "0.0s"}</span>
          {done && (
            <span className="badge badge-success">
              <CheckCircle2 size={13} /> {Math.round(value)}%
            </span>
          )}
        </div>
      </div>

      <ProgressBar value={value} done={done} />

      <div className="log-stream" ref={boxRef}>
        {logs.map((l, i) => (
          <p key={i} className={`log-line ${l.tone}`}>
            <span className="log-dot" />
            {l.text}
          </p>
        ))}
      </div>
    </div>
  );
}
