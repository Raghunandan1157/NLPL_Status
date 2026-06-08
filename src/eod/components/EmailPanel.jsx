import { useEffect, useMemo, useRef, useState } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  Eye,
  KeyRound,
  Loader2,
  LogOut,
  Mail,
  Plus,
  RefreshCw,
  Save,
  Search,
  Send,
  Trash2,
  Wand2,
  X,
  XCircle,
} from "lucide-react";
import { Button, Modal, useToast } from "../../components/ui.jsx";
import { isValidEmail } from "../../lib/format.js";
import {
  autoAssignBranches,
  emailConfigGet,
  emailConfigSave,
  emailGetConfig,
  emailLogin,
  emailLogout,
  precomputeEmailBody,
  reportSheetData,
  reportSheetNames,
  sendBatchEmail,
} from "../api.js";

const GROUPS = [
  ["summary", "Summary"],
  ["regions", "Regions"],
  ["divisions", "Divisions"],
  ["areas", "Areas"],
  ["branches", "Branches"],
];

function recipientsFromConfig(cfg) {
  const byCard = {};
  (cfg.conns || []).forEach((cn) => {
    (byCard[cn.cardId] ||= []).push(cn.sheet);
  });
  return (cfg.cards || []).map((c) => ({
    id: c.id,
    email: c.email || "",
    mode: c.mode || "combined",
    sheets: byCard[c.id] || [],
  }));
}

function toConfig(recipients) {
  const cards = recipients.map((r) => ({ id: r.id, email: r.email.trim(), mode: r.mode }));
  const conns = recipients.flatMap((r) =>
    r.sheets.map((s, i) => ({ id: `x_${r.id}_${i}`, sheet: s, cardId: r.id }))
  );
  return { cards, conns };
}

/* ------------------------------------------------------------- Gmail login */
function GmailLogin({ cfg, onChange, onHealthChange }) {
  const toast = useToast();
  const [open, setOpen] = useState(false);
  const [user, setUser] = useState("");
  const [pwd, setPwd] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit() {
    setBusy(true);
    try {
      const res = await emailLogin({ user, appPassword: pwd, host: "smtp.gmail.com", port: 587 });
      if (res.success) {
        toast.success(`Signed in as ${res.sender}`, "Gmail connected");
        setOpen(false);
        setPwd("");
        onChange({ configured: true, sender: res.sender });
        onHealthChange?.();
      } else {
        toast.error(res.error || "Login failed", "Gmail");
      }
    } catch (e) {
      toast.error(e.message, "Gmail");
    } finally {
      setBusy(false);
    }
  }

  async function signOut() {
    try {
      await emailLogout();
      onChange({ configured: false, sender: "" });
      onHealthChange?.();
      toast.info("Signed out of Gmail.");
    } catch (e) {
      toast.error(e.message);
    }
  }

  if (cfg.configured) {
    return (
      <div className="gmail-bar ok">
        <CheckCircle2 size={18} />
        <div className="grow">
          <strong>Gmail connected</strong>
          <div className="muted" style={{ fontSize: 12.5 }}>Sending as {cfg.sender}</div>
        </div>
        <Button size="sm" variant="ghost" icon={LogOut} onClick={signOut}>
          Sign out
        </Button>
      </div>
    );
  }

  return (
    <div className="gmail-bar">
      <KeyRound size={18} className="muted" />
      <div className="grow">
        <strong>Connect Gmail to send</strong>
        <div className="muted" style={{ fontSize: 12.5 }}>
          Sign in with a Gmail <b>App Password</b> (not your normal password).
        </div>
      </div>
      <Button size="sm" variant="primary" onClick={() => setOpen(true)}>
        Connect Gmail
      </Button>

      {open && (
        <Modal
          title="Connect Gmail"
          onClose={() => setOpen(false)}
          footer={
            <>
              <Button variant="ghost" onClick={() => setOpen(false)}>
                Cancel
              </Button>
              <Button variant="primary" loading={busy} onClick={submit} disabled={!user || !pwd}>
                Sign in
              </Button>
            </>
          }
        >
          <div className="field" style={{ marginBottom: 12 }}>
            <span>Gmail address</span>
            <input className="input" value={user} onChange={(e) => setUser(e.target.value)} placeholder="you@gmail.com" autoFocus />
          </div>
          <div className="field" style={{ marginBottom: 12 }}>
            <span>App password</span>
            <input
              className="input"
              type="password"
              value={pwd}
              onChange={(e) => setPwd(e.target.value)}
              placeholder="16-character app password"
            />
          </div>
          <p className="muted" style={{ fontSize: 12 }}>
            Create one at{" "}
            <a href="https://myaccount.google.com/apppasswords" target="_blank" rel="noreferrer" style={{ color: "var(--primary-600)" }}>
              myaccount.google.com/apppasswords
            </a>{" "}
            (requires 2-Step Verification). Credentials are validated against Gmail and stored locally in <code>.env</code>.
          </p>
        </Modal>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ panel */
export default function EmailPanel({ health, onHealthChange }) {
  const toast = useToast();
  const [groups, setGroups] = useState(null);
  const [recipients, setRecipients] = useState([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [busy, setBusy] = useState("");
  const [pickerFor, setPickerFor] = useState(null);
  const [send, setSend] = useState(null);
  const [mailCfg, setMailCfg] = useState({ configured: !!health?.email?.configured, sender: health?.email?.sender || "" });
  const idRef = useRef(1000);
  const abortRef = useRef(null);

  async function load() {
    setLoading(true);
    setLoadError("");
    emailGetConfig().then(setMailCfg).catch(() => {});
    try {
      setGroups(await reportSheetNames());
    } catch (e) {
      setLoadError(e.message);
      setGroups(null);
    }
    try {
      setRecipients(recipientsFromConfig(await emailConfigGet()));
    } catch {
      /* no saved config yet */
    }
    setLoading(false);
  }

  useEffect(() => {
    load();
    return () => abortRef.current?.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const allSheets = useMemo(() => (groups ? GROUPS.flatMap(([k]) => groups[k] || []) : []), [groups]);

  function addRecipient() {
    setRecipients((r) => [...r, { id: `c${++idRef.current}`, email: "", mode: "combined", sheets: [] }]);
  }
  const update = (id, patch) => setRecipients((r) => r.map((x) => (x.id === id ? { ...x, ...patch } : x)));
  const remove = (id) => setRecipients((r) => r.filter((x) => x.id !== id));

  async function handleSave() {
    setBusy("save");
    try {
      await emailConfigSave(toConfig(recipients));
      toast.success("Recipient configuration saved.", "Saved");
    } catch (e) {
      toast.error(e.message, "Save failed");
    } finally {
      setBusy("");
    }
  }

  async function handleAutoAssign() {
    setBusy("auto");
    try {
      const res = await autoAssignBranches();
      setRecipients(recipientsFromConfig(res));
      const un = res.unallocated?.length ? ` · ${res.unallocated.length} unallocated` : "";
      toast.success((res.message || "Assignments loaded") + un, "Auto-assigned");
    } catch (e) {
      toast.error(e.message, "Auto-assign failed");
    } finally {
      setBusy("");
    }
  }

  async function handleSend() {
    if (!mailCfg.configured) {
      toast.warn("Connect Gmail first (top of this tab) to send emails.", "Not connected");
      return;
    }
    const valid = recipients
      .map((r) => ({ email: r.email.trim(), sheets: r.sheets, mode: r.mode }))
      .filter((r) => isValidEmail(r.email) && r.sheets.length > 0);

    if (valid.length === 0) {
      toast.warn("Add at least one recipient with a valid email and one or more sheets.", "Nothing to send");
      return;
    }
    if (valid.length !== recipients.length) {
      toast.warn(`${recipients.length - valid.length} recipient(s) skipped (invalid email or no sheets).`);
    }

    const statuses = {};
    valid.forEach((r) => (statuses[r.email] = { phase: "queued" }));
    setSend({ running: true, statuses, sent: 0, failed: 0, total: valid.length });

    abortRef.current = new AbortController();
    try {
      await precomputeEmailBody().catch(() => {});
      await sendBatchEmail(
        valid,
        (ev) => {
          setSend((prev) => {
            if (!prev) return prev;
            const next = { ...prev, statuses: { ...prev.statuses } };
            if (ev.email && next.statuses[ev.email]) {
              next.statuses[ev.email] = { phase: ev.phase || next.statuses[ev.email].phase, error: ev.error };
            }
            if (ev.phase === "complete" || ev.done) {
              next.running = false;
              if (typeof ev.sent === "number") next.sent = ev.sent;
              if (typeof ev.failed === "number") next.failed = ev.failed;
            }
            if (ev.phase === "auth_error" || ev.phase === "error") {
              next.running = false;
              next.fatal = ev.message;
            }
            return next;
          });
        },
        abortRef.current.signal
      );
      setSend((prev) => (prev ? { ...prev, running: false } : prev));
    } catch (e) {
      setSend((prev) => (prev ? { ...prev, running: false, fatal: e.message } : prev));
    }
  }

  const closeSend = () => {
    abortRef.current?.abort();
    setSend(null);
  };

  if (loading) {
    return (
      <div className="panel">
        <div className="row" style={{ gap: 10 }}>
          <Loader2 className="spin" size={18} /> Loading report sheets…
        </div>
      </div>
    );
  }

  const picker = pickerFor ? recipients.find((r) => r.id === pickerFor) : null;

  return (
    <>
      <GmailLogin cfg={mailCfg} onChange={setMailCfg} onHealthChange={onHealthChange} />

      {loadError ? (
        <div className="panel">
          <div className="empty">
            <AlertTriangle size={28} />
            <h3>No EOD report available</h3>
            <p className="muted">{loadError}</p>
            <p className="muted">Run EOD processing first, then come back to email it.</p>
            <Button variant="ghost" icon={RefreshCw} onClick={load} style={{ marginTop: 12 }}>
              Retry
            </Button>
          </div>
        </div>
      ) : (
        <div className="panel">
          <div className="panel-header">
            <div>
              <p className="eyebrow">Deliver</p>
              <h2>Email the EOD Report</h2>
              <p className="sub">
                {groups?.total ?? allSheets.length} sheets available · assign sheets to recipients, then send.
              </p>
            </div>
            <div className="row wrap" style={{ gap: 8 }}>
              <Button size="sm" variant="ghost" icon={Wand2} loading={busy === "auto"} onClick={handleAutoAssign}>
                Auto-assign
              </Button>
              <Button size="sm" variant="ghost" icon={Save} loading={busy === "save"} onClick={handleSave}>
                Save
              </Button>
              <Button size="sm" variant="primary" icon={Send} onClick={handleSend} disabled={recipients.length === 0}>
                Send all
              </Button>
            </div>
          </div>

          <div className="recipient-list">
            {recipients.length === 0 && (
              <div className="empty">
                <Mail size={26} />
                <p className="muted">No recipients yet. Add one or use auto-assign from your config file.</p>
              </div>
            )}

            {recipients.map((r) => {
              const bad = r.email && !isValidEmail(r.email);
              return (
                <div key={r.id} className="recipient">
                  <div className="recipient-top">
                    <div className="grow">
                      <input
                        className={`input ${bad ? "input-bad" : ""}`}
                        placeholder="name@example.com"
                        value={r.email}
                        onChange={(e) => update(r.id, { email: e.target.value })}
                      />
                    </div>
                    <select className="input mode-select" value={r.mode} onChange={(e) => update(r.id, { mode: e.target.value })}>
                      <option value="combined">Combined</option>
                      <option value="separate">Separate</option>
                    </select>
                    <button className="icon-btn" onClick={() => remove(r.id)} title="Remove recipient">
                      <Trash2 size={15} />
                    </button>
                  </div>
                  <div className="sheet-chips">
                    {r.sheets.length === 0 && <span className="muted" style={{ fontSize: 12 }}>No sheets assigned</span>}
                    {r.sheets.map((s) => (
                      <span key={s} className="chip">
                        {s}
                        <button onClick={() => update(r.id, { sheets: r.sheets.filter((x) => x !== s) })}>
                          <X size={12} />
                        </button>
                      </span>
                    ))}
                    <button className="chip add" onClick={() => setPickerFor(r.id)}>
                      <Plus size={13} /> Sheets
                    </button>
                  </div>
                </div>
              );
            })}
          </div>

          <Button variant="ghost" icon={Plus} onClick={addRecipient} style={{ marginTop: 14 }}>
            Add recipient
          </Button>
        </div>
      )}

      {picker && (
        <SheetPicker
          groups={groups}
          selected={picker.sheets}
          onClose={() => setPickerFor(null)}
          onApply={(sheets) => {
            update(picker.id, { sheets });
            setPickerFor(null);
          }}
        />
      )}

      {send && <SendProgress send={send} onClose={closeSend} />}
    </>
  );
}

/* ---------------------------------------------------------------- picker */
function SheetPicker({ groups, selected, onClose, onApply }) {
  const [chosen, setChosen] = useState(new Set(selected));
  const [query, setQuery] = useState("");
  const [preview, setPreview] = useState({ sheet: null, rows: null, loading: false });

  function toggle(s) {
    setChosen((prev) => {
      const next = new Set(prev);
      next.has(s) ? next.delete(s) : next.add(s);
      return next;
    });
  }
  function toggleGroup(items, on) {
    setChosen((prev) => {
      const next = new Set(prev);
      items.forEach((s) => (on ? next.add(s) : next.delete(s)));
      return next;
    });
  }
  async function showPreview(s) {
    setPreview({ sheet: s, rows: null, loading: true });
    try {
      const data = await reportSheetData(s);
      setPreview({ sheet: s, rows: (data.rows || []).slice(0, 14), loading: false });
    } catch {
      setPreview({ sheet: s, rows: [], loading: false });
    }
  }
  const cellVal = (c) => (c && typeof c === "object" ? c.v : c);

  const q = query.trim().toLowerCase();

  return (
    <Modal
      wide
      title="Select sheets"
      onClose={onClose}
      footer={
        <>
          <span className="muted grow" style={{ fontSize: 12.5 }}>{chosen.size} selected</span>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="primary" onClick={() => onApply([...chosen])}>
            Apply
          </Button>
        </>
      }
    >
      <div className="picker-search">
        <Search size={15} className="muted" />
        <input className="input" placeholder="Search sheets…" value={query} onChange={(e) => setQuery(e.target.value)} autoFocus />
      </div>
      <div className="picker-groups">
        {GROUPS.map(([key, label]) => {
          const items = (groups[key] || []).filter((s) => !q || s.toLowerCase().includes(q));
          if (items.length === 0) return null;
          const all = items.every((s) => chosen.has(s));
          return (
            <div key={key} className="picker-group">
              <div className="picker-group-head">
                <span>
                  {label} <span className="muted">({items.length})</span>
                </span>
                <button className="link" onClick={() => toggleGroup(items, !all)}>
                  {all ? "Clear" : "Select all"}
                </button>
              </div>
              <div className="picker-items">
                {items.map((s) => (
                  <div key={s} className={`picker-item ${chosen.has(s) ? "on" : ""}`}>
                    <input type="checkbox" checked={chosen.has(s)} onChange={() => toggle(s)} />
                    <span className="grow" onClick={() => toggle(s)} style={{ cursor: "pointer" }}>{s}</span>
                    <button className="preview-btn" title="Preview" onClick={() => showPreview(s)}>
                      <Eye size={13} />
                    </button>
                  </div>
                ))}
              </div>
            </div>
          );
        })}
      </div>

      {preview.sheet && (
        <div className="sheet-preview">
          <div className="sheet-preview-head">
            <strong>{preview.sheet}</strong>
            <button className="toast-close" onClick={() => setPreview({ sheet: null, rows: null, loading: false })}>
              <X size={14} />
            </button>
          </div>
          {preview.loading ? (
            <div className="row" style={{ gap: 8, padding: 8 }}>
              <Loader2 size={14} className="spin" /> Loading preview…
            </div>
          ) : preview.rows && preview.rows.length ? (
            <div className="sheet-preview-scroll">
              <table>
                <tbody>
                  {preview.rows.map((row, ri) => (
                    <tr key={ri}>
                      {(row || []).slice(0, 8).map((c, ci) => (
                        <td key={ci}>{cellVal(c) ?? ""}</td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <div className="muted" style={{ padding: 8, fontSize: 12 }}>No preview data.</div>
          )}
        </div>
      )}
    </Modal>
  );
}

/* --------------------------------------------------------- send progress */
function SendProgress({ send, onClose }) {
  const entries = Object.entries(send.statuses);
  const icon = (phase) => {
    if (phase === "sent" || phase === "retry_sent") return <CheckCircle2 size={15} className="ok" />;
    if (phase === "failed" || phase === "retry_failed") return <XCircle size={15} className="bad" />;
    if (phase === "queued") return <span className="dot" />;
    return <Loader2 size={15} className="spin" />;
  };
  const done = !send.running;

  return (
    <Modal
      title={send.running ? "Sending emails…" : "Send complete"}
      onClose={done ? onClose : undefined}
      footer={
        <Button variant={done ? "primary" : "ghost"} onClick={onClose}>
          {done ? "Close" : "Hide"}
        </Button>
      }
    >
      <div className="spread" style={{ marginBottom: 12 }}>
        <span className="badge badge-success">{send.sent} sent</span>
        <span className="badge badge-danger">{send.failed} failed</span>
        <span className="badge badge-muted">{send.total} total</span>
      </div>
      {send.fatal && (
        <div className="banner warn" style={{ marginBottom: 12 }}>
          <AlertTriangle size={16} /> {send.fatal}
        </div>
      )}
      <div className="send-list">
        {entries.map(([email, s]) => (
          <div key={email} className="send-row">
            {icon(s.phase)}
            <span className="grow" style={{ fontSize: 13 }}>{email}</span>
            <span className="muted" style={{ fontSize: 11.5 }}>{s.error ? s.error.slice(0, 40) : s.phase}</span>
          </div>
        ))}
      </div>
    </Modal>
  );
}
