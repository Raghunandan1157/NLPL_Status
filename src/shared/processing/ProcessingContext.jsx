import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import { cancelProcessBeacon } from "./processingApi.js";

/**
 * App-wide processing coordinator.
 *
 * A processing module registers its active job via `beginJob(...)`. While a job
 * is registered:
 *   - Any in-app navigation routed through `requestNavigation(fn)` is intercepted
 *     and a Stop-confirm modal is shown instead of navigating immediately.
 *   - A `beforeunload` handler warns on refresh / tab-close and fires a
 *     best-effort backend cancel beacon so the server stops at its next phase.
 *
 * The Shell renders <StopProcessConfirmModal> driven by this context and routes
 * its changeView through `requestNavigation`.
 */
const ProcessingContext = createContext(null);

export function ProcessingProvider({ children }) {
  // The single active job (the app processes one heavy job at a time).
  const [activeJob, setActiveJob] = useState(null); // { module, processId, cancel, label }
  const activeRef = useRef(null);

  // Pending navigation intercepted while a job is active.
  const [pendingNav, setPendingNav] = useState(null); // () => void
  const [stopping, setStopping] = useState(false);

  const beginJob = useCallback((job) => {
    activeRef.current = job;
    setActiveJob(job);
  }, []);

  const endJob = useCallback((processId) => {
    // Only clear if it matches (avoid a stale endJob wiping a newer job).
    if (!processId || activeRef.current?.processId === processId) {
      activeRef.current = null;
      setActiveJob(null);
    }
  }, []);

  const isActive = useCallback(() => Boolean(activeRef.current), []);

  // Route in-app navigation through here. Returns true if it ran immediately.
  const requestNavigation = useCallback((navFn) => {
    if (activeRef.current) {
      setPendingNav(() => navFn);
      return false;
    }
    navFn();
    return true;
  }, []);

  const confirmStay = useCallback(() => setPendingNav(null), []);

  const confirmStop = useCallback(async () => {
    const job = activeRef.current;
    const nav = pendingNav;
    setStopping(true);
    try {
      if (job?.cancel) {
        try {
          await job.cancel();
        } catch {
          /* even if cancel errors, let the user move on */
        }
      }
    } finally {
      setStopping(false);
      activeRef.current = null;
      setActiveJob(null);
      setPendingNav(null);
      if (nav) nav();
    }
  }, [pendingNav]);

  // Warn + best-effort backend cancel on refresh / close while processing.
  useEffect(() => {
    const onBeforeUnload = (e) => {
      if (!activeRef.current) return;
      const job = activeRef.current;
      cancelProcessBeacon(job.module, job.processId);
      e.preventDefault();
      e.returnValue = "";
      return "";
    };
    window.addEventListener("beforeunload", onBeforeUnload);
    return () => window.removeEventListener("beforeunload", onBeforeUnload);
  }, []);

  const value = {
    activeJob,
    beginJob,
    endJob,
    isActive,
    requestNavigation,
    pendingNav,
    stopping,
    confirmStay,
    confirmStop,
  };

  return <ProcessingContext.Provider value={value}>{children}</ProcessingContext.Provider>;
}

export function useProcessing() {
  const ctx = useContext(ProcessingContext);
  if (!ctx) throw new Error("useProcessing must be used within ProcessingProvider");
  return ctx;
}

/** Safe variant for components that may render outside the provider (returns a
 *  no-op shim so modules don't crash if used standalone). */
export function useProcessingOptional() {
  return (
    useContext(ProcessingContext) || {
      activeJob: null,
      beginJob: () => {},
      endJob: () => {},
      isActive: () => false,
      requestNavigation: (fn) => {
        fn();
        return true;
      },
      pendingNav: null,
      stopping: false,
      confirmStay: () => {},
      confirmStop: () => {},
    }
  );
}
