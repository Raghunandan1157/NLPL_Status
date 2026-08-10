import { Component, createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  FileSpreadsheet,
  Info,
  Loader2,
  RotateCcw,
  X,
  XCircle,
} from "lucide-react";
import { fileSizeMB } from "../lib/format.js";

/* ----------------------------------------------------------- ErrorBoundary */
/** Catches render errors in a subtree so a single broken page shows a message
 *  instead of turning the whole app into a blank white screen. */
export class ErrorBoundary extends Component {
  constructor(props) {
    super(props);
    this.state = { error: null };
  }

  static getDerivedStateFromError(error) {
    return { error };
  }

  componentDidCatch(error, info) {
    // eslint-disable-next-line no-console
    console.error("UI error caught by ErrorBoundary:", error, info);
  }

  reset = () => this.setState({ error: null });

  render() {
    if (this.state.error) {
      return (
        <div className="empty" style={{ padding: 40 }}>
          <AlertTriangle size={28} />
          <h3>Something went wrong on this page</h3>
          <p className="muted" style={{ maxWidth: 420 }}>
            {String(this.state.error?.message || this.state.error)}
          </p>
          <button className="btn btn-primary btn-sm" style={{ marginTop: 12 }} onClick={this.reset}>
            <RotateCcw size={15} /> Try again
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}

/* ------------------------------------------------------------------ Toasts */
const ToastContext = createContext(null);

// Sticky toasts never expire on their own, so the stack needs a ceiling.
const MAX_TOASTS = 6;

export function ToastProvider({ children }) {
  const [toasts, setToasts] = useState([]);
  const idRef = useRef(0);

  const dismiss = useCallback((id) => {
    setToasts((list) => list.filter((t) => t.id !== id));
  }, []);

  const clear = useCallback(() => setToasts([]), []);

  // Toasts DO NOT auto-dismiss. A sync result is often the only record of what
  // happened — "323 rows · Customer details FAILED (413)" is exactly the kind of
  // message that used to vanish after 4.5s while the user was still reading it.
  // They stay until the user closes one, switches page, or refreshes.
  // Pass an explicit `duration` (ms) to opt a single toast back into auto-hide.
  const push = useCallback(
    (toast) => {
      const id = ++idRef.current;
      const item = { id, type: "info", duration: null, ...toast };
      setToasts((list) => {
        const next = [...list, item];
        // Nothing expires on its own now, so cap the stack — otherwise a long
        // session grows past the viewport and buries the newest message.
        // Oldest go first; the newest is always the one on screen.
        return next.length > MAX_TOASTS ? next.slice(next.length - MAX_TOASTS) : next;
      });
      if (item.duration) setTimeout(() => dismiss(id), item.duration);
      return id;
    },
    [dismiss]
  );

  const api = {
    push,
    dismiss,
    clear,
    success: (message, title) => push({ type: "success", message, title }),
    error: (message, title) => push({ type: "error", message, title }),
    info: (message, title) => push({ type: "info", message, title }),
    warn: (message, title) => push({ type: "warn", message, title }),
  };

  const icons = {
    success: <CheckCircle2 size={18} className="toast-icon" />,
    error: <XCircle size={18} className="toast-icon" />,
    warn: <AlertTriangle size={18} className="toast-icon" />,
    info: <Info size={18} className="toast-icon" />,
  };

  return (
    <ToastContext.Provider value={api}>
      {children}
      <div className="toast-wrap">
        {toasts.map((t) => (
          <div key={t.id} className={`toast ${t.type}`}>
            {icons[t.type]}
            <div className="grow">
              {t.title && <div className="toast-title">{t.title}</div>}
              <div className="toast-msg">{t.message}</div>
            </div>
            <button className="toast-close" onClick={() => dismiss(t.id)} aria-label="Dismiss">
              <X size={15} />
            </button>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

export function useToast() {
  const ctx = useContext(ToastContext);
  if (!ctx) throw new Error("useToast must be used within ToastProvider");
  return ctx;
}

/* ------------------------------------------------------------------ Button */
export function Button({ variant = "ghost", size, icon: Icon, loading, children, className = "", ...rest }) {
  const cls = ["btn", `btn-${variant}`, size === "sm" ? "btn-sm" : "", className].filter(Boolean).join(" ");
  return (
    <button className={cls} disabled={loading || rest.disabled} {...rest}>
      {loading ? <Loader2 size={16} className="spin" /> : Icon ? <Icon size={16} /> : null}
      {children}
    </button>
  );
}

/* ------------------------------------------------------------------- Modal */
export function Modal({ title, onClose, children, footer, wide }) {
  useEffect(() => {
    const onKey = (e) => e.key === "Escape" && onClose?.();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div className="modal-overlay" onMouseDown={(e) => e.target === e.currentTarget && onClose?.()}>
      <div className="modal" style={wide ? { maxWidth: 720 } : undefined} role="dialog" aria-modal="true">
        <div className="modal-head">
          <h3>{title}</h3>
          <button className="toast-close" onClick={onClose} aria-label="Close">
            <X size={18} />
          </button>
        </div>
        <div className="modal-body">{children}</div>
        {footer && <div className="modal-foot">{footer}</div>}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ Switch */
export function Switch({ checked, onChange, label }) {
  return (
    <label className="switch">
      <input type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} />
      {label}
    </label>
  );
}

/* -------------------------------------------------------------- ProgressBar */
export function ProgressBar({ value, done }) {
  return (
    <div className="progress">
      <div className={`progress-fill ${done ? "done" : ""}`} style={{ width: `${Math.min(100, Math.max(0, value))}%` }} />
    </div>
  );
}

/* ---------------------------------------------------------------- FileDrop */
/* ------------------------------------------------------------ RemoveButton */
/**
 * Delete control for a file the backend is already holding: an X that turns
 * into a "Remove / Cancel" strip in place. Deleting a stored file is not
 * undoable, so it always asks first — but on the card itself, so there is no
 * dialog to lose behind a window and Cancel is a single click.
 *
 * Used by FileDrop, and directly wherever a stored file is shown as something
 * other than a drop zone (status pills and the like).
 */
export function RemoveButton({ onRemove, removing, label = "Remove this file" }) {
  const [confirming, setConfirming] = useState(false);

  if (!confirming) {
    return (
      <button
        type="button"
        className="filedrop-clear"
        title={label}
        aria-label={label}
        disabled={removing}
        onClick={() => setConfirming(true)}
      >
        {removing ? <Loader2 size={13} className="spin" /> : <X size={15} />}
      </button>
    );
  }

  return (
    <div className="filedrop-confirm">
      <span>Remove?</span>
      <button
        type="button"
        className="filedrop-confirm-yes"
        disabled={removing}
        onClick={async () => {
          try {
            await onRemove();
          } finally {
            setConfirming(false);
          }
        }}
      >
        {removing ? <Loader2 size={13} className="spin" /> : "Remove"}
      </button>
      <button
        type="button"
        className="filedrop-confirm-no"
        disabled={removing}
        onClick={() => setConfirming(false)}
      >
        Cancel
      </button>
    </div>
  );
}

/**
 * File picker with a built-in remove control.
 *
 * Two kinds of file, two behaviours — a file the user has only *selected* is
 * cleared instantly (nothing has happened yet), while a file already saved on
 * the backend is deleted only after an in-place confirm, because that is a real
 * deletion other runs depend on.
 *
 * @param onFile    receives the picked File, and `null` when the user clears a
 *                  staged pick. Handlers must accept null.
 * @param onRemove  optional async delete of the file already on the backend.
 *                  Supplying it puts a remove control on the card — including
 *                  the `locked` variant, which otherwise has no way back.
 * @param removing  true while that delete is in flight (shows a spinner).
 */
export function FileDrop({
  label,
  hint,
  file,
  onFile,
  accept = ".xlsx,.xls,.xlsm",
  disabled,
  locked,
  lockedText,
  onRemove,
  removing,
  removeLabel = "Remove this file",
}) {
  const inputRef = useRef(null);
  const [drag, setDrag] = useState(false);

  const removeControl = onRemove ? (
    <RemoveButton onRemove={onRemove} removing={removing} label={removeLabel} />
  ) : null;

  if (locked) {
    return (
      <div className="filedrop-wrap">
        <div
          className={`filedrop locked ${removeControl ? "ready" : ""}`}
          title={hint || lockedText || "Using backend data"}
        >
          <span className="filedrop-icon ok">
            <CheckCircle2 size={22} />
          </span>
          <div className="filedrop-text">
            <strong title={lockedText || "Using backend data"}>{lockedText || "Using backend data"}</strong>
            <small title={hint}>{hint}</small>
          </div>
        </div>
        {removeControl}
      </div>
    );
  }

  // The clear control is a SIBLING of the drop button, not a child — a <button>
  // inside a <button> is invalid HTML and browsers drop the inner one.
  return (
    <div className="filedrop-wrap">
      <button
        type="button"
        className={`filedrop ${file ? "ready" : ""} ${drag ? "drag" : ""}`}
        disabled={disabled}
        onClick={() => inputRef.current?.click()}
        onDragOver={(e) => {
          e.preventDefault();
          setDrag(true);
        }}
        onDragLeave={() => setDrag(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDrag(false);
          const f = e.dataTransfer.files?.[0];
          if (f) onFile(f);
        }}
        title={file ? file.name : undefined}
      >
        <input
          ref={inputRef}
          type="file"
          accept={accept}
          hidden
          // Reset the input's value on every pick so choosing the SAME file again
          // after clearing still fires onChange (the browser suppresses it when
          // the value is unchanged).
          onChange={(e) => {
            const f = e.target.files?.[0] || null;
            e.target.value = "";
            onFile(f);
          }}
        />
        <span className="filedrop-icon">
          <FileSpreadsheet size={22} />
        </span>
        <div className="filedrop-text">
          <strong title={file ? file.name : label}>{file ? file.name : label}</strong>
          <small>{file ? `${fileSizeMB(file.size)} · ready` : hint}</small>
        </div>
      </button>

      {/* A staged pick is cleared straight away — nothing has been saved yet.
          A backend file goes through onRemove's confirm instead. */}
      {removeControl}
      {!removeControl && file && !disabled ? (
        <button
          type="button"
          className="filedrop-clear"
          title="Remove this file"
          aria-label={`Remove ${file.name}`}
          onClick={() => onFile(null)}
        >
          <X size={15} />
        </button>
      ) : null}
    </div>
  );
}

/* ------------------------------------------------------------------- misc */
export function Spinner({ size = 16 }) {
  return <Loader2 size={size} className="spin" />;
}
