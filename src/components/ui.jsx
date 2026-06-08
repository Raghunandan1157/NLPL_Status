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

export function ToastProvider({ children }) {
  const [toasts, setToasts] = useState([]);
  const idRef = useRef(0);

  const dismiss = useCallback((id) => {
    setToasts((list) => list.filter((t) => t.id !== id));
  }, []);

  const push = useCallback(
    (toast) => {
      const id = ++idRef.current;
      const item = { id, type: "info", duration: 4500, ...toast };
      setToasts((list) => [...list, item]);
      if (item.duration) setTimeout(() => dismiss(id), item.duration);
      return id;
    },
    [dismiss]
  );

  const api = {
    push,
    dismiss,
    success: (message, title) => push({ type: "success", message, title }),
    error: (message, title) => push({ type: "error", message, title, duration: 7000 }),
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
export function FileDrop({ label, hint, file, onFile, accept = ".xlsx,.xls,.xlsm", disabled, locked, lockedText }) {
  const inputRef = useRef(null);
  const [drag, setDrag] = useState(false);

  if (locked) {
    return (
      <div className="filedrop locked" title={hint || lockedText || "Using backend data"}>
        <span className="filedrop-icon ok">
          <CheckCircle2 size={22} />
        </span>
        <div className="filedrop-text">
          <strong title={lockedText || "Using backend data"}>{lockedText || "Using backend data"}</strong>
          <small title={hint}>{hint}</small>
        </div>
      </div>
    );
  }

  return (
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
        onChange={(e) => onFile(e.target.files?.[0] || null)}
      />
      <span className="filedrop-icon">
        <FileSpreadsheet size={22} />
      </span>
      <div className="filedrop-text">
        <strong title={file ? file.name : label}>{file ? file.name : label}</strong>
        <small>{file ? `${fileSizeMB(file.size)} · ready` : hint}</small>
      </div>
    </button>
  );
}

/* ------------------------------------------------------------------- misc */
export function Spinner({ size = 16 }) {
  return <Loader2 size={size} className="spin" />;
}
