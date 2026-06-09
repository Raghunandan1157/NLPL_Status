import { useCallback, useEffect, useRef, useState } from "react";
import { apiUrl } from "../../lib/apiClient.js";
import { cancelProcess, getProcessStatus, newProcessId } from "./processingApi.js";
import { useProcessingOptional } from "./ProcessingContext.jsx";

/**
 * Reusable processing-job lifecycle for any module.
 *
 *   const job = useProcessingJob({ module: "hourly", steps: STEPS });
 *   await job.run(async ({ processId, signal, setStep, log }) => {
 *     setStep(0, "done");            // File uploaded
 *     setStep(1, "active");          // Validation started
 *     const res = await processHourly({ ..., processId, signal });
 *     return { report: { filename: res.filename } };
 *   });
 *
 * `steps` is an array of { key, label }. The hook tracks each step's state
 * (pending | active | done | error), a timestamped live-log, elapsed seconds,
 * status, and the final result. It connects the shared SSE log stream and
 * registers the run with ProcessingProvider so navigation is guarded and the
 * backend can be cancelled.
 */
export function useProcessingJob({ module, steps, eventsPath = "/eod/events", onSseEvent }) {
  const ctx = useProcessingOptional();

  const [status, setStatus] = useState("idle"); // idle|running|completed|error|cancelled
  const [stepStates, setStepStates] = useState(() => steps.map(() => "pending"));
  const [logs, setLogs] = useState([]); // { ts, text, tone, stage }
  const [elapsed, setElapsed] = useState(0);
  const [result, setResult] = useState(null);
  const [processId, setProcessId] = useState(null);

  const esRef = useRef(null);
  const timerRef = useRef(null);
  const abortRef = useRef(null);
  const pidRef = useRef(null);

  const nowTs = () =>
    new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });

  const log = useCallback((text, tone = "info", stage = "") => {
    setLogs((items) => [{ ts: nowTs(), text, tone, stage }, ...items].slice(0, 200));
  }, []);

  const toneFor = useCallback((text) => {
    const t = String(text).toLowerCase();
    if (t.includes("error") || t.includes("failed") || t.includes("traceback")) return "error";
    if (t.includes("complete") || t.includes("success") || t.includes("saved") || t.includes("done"))
      return "success";
    if (t.includes("warn")) return "warn";
    return "info";
  }, []);

  const setStep = useCallback((idx, state) => {
    setStepStates((prev) => prev.map((s, i) => (i === idx ? state : s)));
  }, []);

  const setSteps = useCallback((updates) => {
    setStepStates((prev) => prev.map((s, i) => (i in updates ? updates[i] : s)));
  }, []);

  const startTimer = useCallback(() => {
    setElapsed(0);
    const t0 = Date.now();
    clearInterval(timerRef.current);
    timerRef.current = setInterval(() => setElapsed((Date.now() - t0) / 1000), 250);
  }, []);

  const stopTimer = useCallback(() => clearInterval(timerRef.current), []);

  const connectLogs = useCallback(() => {
    esRef.current?.close();
    const source = new EventSource(apiUrl(eventsPath));
    source.onmessage = (event) => {
      if (!event.data) return;
      try {
        const data = JSON.parse(event.data);
        if (data.log) log(data.log, toneFor(data.log));
        onSseEvent?.(data, { setStep, setSteps });
      } catch {
        log(event.data);
      }
    };
    source.onerror = () => {};
    esRef.current = source;
  }, [eventsPath, log, toneFor, onSseEvent, setStep, setSteps]);

  // Cleanup on unmount.
  useEffect(
    () => () => {
      esRef.current?.close();
      clearInterval(timerRef.current);
      ctx.endJob(pidRef.current);
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    []
  );

  // Stop the run AND wait for the backend to actually reach 'cancelled' — which
  // only happens after the /process handler exits its finally and releases the
  // server lock. Resolving only then means a follow-up Start never hits "busy".
  const cancel = useCallback(async () => {
    const pid = pidRef.current;
    if (!pid) return;
    let already = false;
    setStatus((s) => {
      already = s === "cancelling";
      return s === "running" || s === "cancelling" ? "cancelling" : s;
    });
    if (already) return; // ignore repeated clicks

    log("Stopping process… waiting for the server to confirm.", "warn");
    try {
      abortRef.current?.abort();
    } catch {
      /* ignore */
    }
    try {
      await cancelProcess(module, pid);
    } catch {
      /* best effort — the poll below is the real confirmation */
    }

    const t0 = Date.now();
    // Poll until the backend job leaves running/cancelling (i.e. truly stopped).
    // 60s safety cap; the backend stale-reaper frees a genuinely stuck slot.
    for (;;) {
      let st = "unknown";
      try {
        st = (await getProcessStatus(module, pid)).status;
      } catch {
        /* keep polling */
      }
      if (!["running", "cancelling"].includes(st)) break;
      if (Date.now() - t0 > 60000) break;
      await new Promise((r) => setTimeout(r, 600));
    }

    stopTimer();
    setStepStates((prev) => prev.map((s) => (s === "active" ? "error" : s)));
    setStatus("cancelled");
    log("Process stopped safely. You can start again.", "success");
  }, [module, log, stopTimer]);

  const reset = useCallback(() => {
    setStatus("idle");
    setStepStates(steps.map(() => "pending"));
    setLogs([]);
    setElapsed(0);
    setResult(null);
    setProcessId(null);
  }, [steps]);

  const run = useCallback(
    async (runner) => {
      const pid = newProcessId();
      pidRef.current = pid;
      setProcessId(pid);
      setStatus("running");
      setStepStates(steps.map(() => "pending"));
      setLogs([]);
      setResult(null);

      const controller = new AbortController();
      abortRef.current = controller;

      // Register with the app so navigation is guarded + backend is cancellable.
      ctx.beginJob({ module, processId: pid, cancel, label: module });

      connectLogs();
      startTimer();
      log("Processing started.", "info");

      try {
        const out = await runner({
          processId: pid,
          signal: controller.signal,
          setStep,
          setSteps,
          log,
        });
        // Mark every step complete and finish.
        setStepStates((prev) => prev.map(() => "done"));
        setResult(out?.report ? out.report : out || null);
        setStatus("completed");
        log("Completed.", "success");
        return out;
      } catch (e) {
        if (controller.signal.aborted || e?.name === "AbortError" || e?.cancelled) {
          // If cancel() is orchestrating, let it own the final 'cancelled' state
          // (it waits for backend confirmation). Otherwise mark cancelled here.
          setStatus((s) => (s === "cancelling" ? s : "cancelled"));
          return null;
        }
        setStatus("error");
        setStepStates((prev) => prev.map((s) => (s === "active" ? "error" : s)));
        log(e?.message || "Processing failed.", "error");
        throw e;
      } finally {
        stopTimer();
        setTimeout(() => esRef.current?.close(), 1200);
        ctx.endJob(pid);
      }
    },
    [steps, ctx, module, cancel, connectLogs, startTimer, log, setStep, setSteps, stopTimer]
  );

  return {
    module,
    steps,
    status,
    stepStates,
    logs,
    elapsed,
    result,
    processId,
    running: status === "running",
    cancelling: status === "cancelling",
    busy: status === "running" || status === "cancelling",
    done: status === "completed",
    run,
    cancel,
    reset,
    setStep,
    setSteps,
    log,
  };
}
