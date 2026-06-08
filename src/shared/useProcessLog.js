import { useCallback, useEffect, useRef, useState } from "react";
import { eventsUrl } from "../eod/api.js";

// Infer a log line's tone from its text (matches the EOD/Hourly convention).
export function toneFor(text) {
  const t = String(text || "").toLowerCase();
  if (t.includes("error") || t.includes("failed") || t.includes("traceback")) return "error";
  if (t.includes("completed") || t.includes("success") || t.includes("saved") || t.includes("done"))
    return "success";
  if (t.includes("warn")) return "warn";
  return "info";
}

/**
 * Shared live-progress engine for module process panels.
 *
 * Wraps the global SSE log channel (`/eod/events` — the backend streams every
 * module's processing logs through the root logger) plus an elapsed timer and a
 * newest-first capped log buffer. Identical behaviour to the EOD/Hourly panels,
 * so every migrated module gets the same loading/progress experience.
 */
export function useProcessLog(initial = "Ready.") {
  const [logs, setLogs] = useState([{ text: initial, tone: "info" }]);
  const [step, setStep] = useState(0);
  const [done, setDone] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const esRef = useRef(null);
  const timerRef = useRef(null);

  useEffect(
    () => () => {
      esRef.current?.close();
      clearInterval(timerRef.current);
    },
    []
  );

  const pushLog = useCallback((text, tone) => {
    setLogs((items) => [{ text, tone: tone || toneFor(text) }, ...items].slice(0, 120));
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
          if (typeof data.step === "number" && data.step >= 1) setStep(data.step);
          if (data.done) setDone(true);
        }
      } catch {
        pushLog(event.data);
      }
    };
    source.onerror = () => {
      /* keep-alive hiccups are normal; ignore */
    };
    esRef.current = source;
  }, [pushLog]);

  const startTimer = useCallback(() => {
    setElapsed(0);
    const t0 = Date.now();
    clearInterval(timerRef.current);
    timerRef.current = setInterval(() => setElapsed((Date.now() - t0) / 1000), 250);
  }, []);

  const stopTimer = useCallback(() => clearInterval(timerRef.current), []);
  const closeLater = useCallback((ms = 1500) => {
    setTimeout(() => esRef.current?.close(), ms);
  }, []);

  const reset = useCallback(
    (msg) => {
      setLogs(msg ? [{ text: msg, tone: "info" }] : []);
      setStep(0);
      setDone(false);
      setElapsed(0);
    },
    []
  );

  return {
    logs, step, done, elapsed,
    setStep, setDone,
    pushLog, connectLogs, startTimer, stopTimer, closeLater, reset,
  };
}
