import { AlertCircle, CheckCircle2, Circle, Loader2 } from "lucide-react";

/**
 * Vertical step tracker. `steps` = [{ key, label }], `states` = array of
 * "pending" | "active" | "done" | "error" (same length/order).
 *   - done  → green tick
 *   - active→ spinning loader
 *   - error → red badge
 *   - pending → muted circle
 */
export default function ProcessingStepTimeline({ steps, states }) {
  return (
    <ol className="proc-timeline">
      {steps.map((step, i) => {
        const st = states[i] || "pending";
        return (
          <li key={step.key || i} className={`proc-step ${st}`}>
            <span className="proc-step-icon">
              {st === "done" ? (
                <CheckCircle2 size={17} />
              ) : st === "active" ? (
                <Loader2 size={17} className="spin" />
              ) : st === "error" ? (
                <AlertCircle size={17} />
              ) : (
                <Circle size={17} />
              )}
            </span>
            <span className="proc-step-label">{step.label}</span>
            {st === "active" && <span className="proc-step-tag">in progress</span>}
            {st === "error" && <span className="proc-step-tag err">failed</span>}
          </li>
        );
      })}
    </ol>
  );
}
