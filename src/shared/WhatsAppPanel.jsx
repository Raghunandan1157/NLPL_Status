import { useEffect, useState } from "react";
import { CheckCircle2, Loader2, MessageCircle, Power, Send, Users } from "lucide-react";
import { Button, Modal, useToast } from "../components/ui.jsx";
import {
  whatsappContactsGet,
  whatsappContactsSave,
  whatsappOpen,
  whatsappSend,
} from "./centralApi.js";

/**
 * Centralized WhatsApp delivery panel, reusable by any module.
 *
 * The session (one persistent Chromium profile) and the contact list are shared
 * app-wide via /api/whatsapp/* — connect once, every module can send. Only the
 * file being sent is module-specific (passed in via `sendItems`).
 *
 * Props:
 *   backendDataPath — folder holding the module's report file(s)
 *   sendItems       — [{ file, label }] the user can choose from for this module
 */
export default function WhatsAppPanel({ backendDataPath, sendItems = [] }) {
  const toast = useToast();
  const [contacts, setContacts] = useState([]);
  const [connection, setConnection] = useState("idle"); // idle | connecting | ready | error
  const [busy, setBusy] = useState("");
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [choice, setChoice] = useState(sendItems[0]?.file || "");

  useEffect(() => {
    whatsappContactsGet()
      .then((r) => setContacts(r.contacts || []))
      .catch(() => setContacts([]));
  }, []);

  async function connect() {
    setConnection("connecting");
    setBusy("connect");
    try {
      const res = await whatsappOpen();
      if (res.success) {
        setConnection("ready");
        toast.success(res.message || "WhatsApp Web is ready.", "Connected");
      } else {
        setConnection("error");
        toast.error(res.error || "Could not open WhatsApp Web.", "Connect failed");
      }
    } catch (e) {
      setConnection("error");
      toast.error(e.message, "Connect failed");
    } finally {
      setBusy("");
    }
  }

  async function saveContacts() {
    const list = draft.split("\n").map((s) => s.trim()).filter(Boolean);
    setBusy("contacts");
    try {
      const res = await whatsappContactsSave(list);
      setContacts(res.contacts || list);
      setEditing(false);
      toast.success(`${list.length} contact(s) saved.`, "Saved");
    } catch (e) {
      toast.error(e.message, "Save failed");
    } finally {
      setBusy("");
    }
  }

  async function send() {
    if (!backendDataPath) {
      toast.error("Backend data path unavailable. Is the backend running?", "Cannot send");
      return;
    }
    if (contacts.length === 0) {
      toast.warn("Add at least one WhatsApp contact first.");
      return;
    }
    setBusy("send");
    try {
      const res = await whatsappSend(backendDataPath, choice);
      if (res.success) toast.success(res.message || "Sent via WhatsApp.", "Sent");
      else toast.error(res.error || "Send failed.", "WhatsApp");
    } catch (e) {
      toast.error(e.message, "Send failed");
    } finally {
      setBusy("");
    }
  }

  const connBadge = {
    idle: <span className="badge badge-muted">Not connected</span>,
    connecting: (
      <span className="badge badge-info">
        <Loader2 size={12} className="spin" /> Connecting…
      </span>
    ),
    ready: (
      <span className="badge badge-success">
        <CheckCircle2 size={12} /> Ready
      </span>
    ),
    error: <span className="badge badge-danger">Connection failed</span>,
  }[connection];

  return (
    <div className="eod-grid">
      <div className="col" style={{ gap: 18 }}>
        <div className="panel">
          <div className="panel-header">
            <div>
              <p className="eyebrow">Deliver</p>
              <h2>Send via WhatsApp</h2>
              <p className="sub">
                Shared WhatsApp Web session — scan the QR once and it stays signed in for every
                module.
              </p>
            </div>
            {connBadge}
          </div>

          <div className="wa-connect">
            <span className="wa-ic">
              <MessageCircle size={22} />
            </span>
            <div className="grow">
              <strong style={{ fontSize: 13.5 }}>WhatsApp Web session</strong>
              <p className="muted" style={{ margin: "2px 0 0", fontSize: 12.5 }}>
                {connection === "ready"
                  ? "Connected and ready to send."
                  : "Connect first. A Chromium window opens — scan the QR code if prompted."}
              </p>
            </div>
            <Button variant="ghost" icon={Power} loading={busy === "connect"} onClick={connect}>
              {connection === "ready" ? "Reconnect" : "Connect"}
            </Button>
          </div>

          {sendItems.length > 0 && (
            <div className="field" style={{ marginTop: 18 }}>
              <span>File to send</span>
              <div className="wa-files">
                {sendItems.map((s) => (
                  <label key={s.file} className={`wa-file ${choice === s.file ? "on" : ""}`}>
                    <input
                      type="radio"
                      name="wa-file"
                      checked={choice === s.file}
                      onChange={() => setChoice(s.file)}
                    />
                    <div>
                      <strong>{s.label}</strong>
                      <small className="muted">{s.file}</small>
                    </div>
                  </label>
                ))}
              </div>
            </div>
          )}

          <Button
            variant="success"
            icon={Send}
            className="btn-block"
            style={{ marginTop: 16 }}
            loading={busy === "send"}
            disabled={contacts.length === 0 || !choice}
            onClick={send}
          >
            Send to {contacts.length} contact{contacts.length === 1 ? "" : "s"}
          </Button>
        </div>
      </div>

      <div className="col" style={{ gap: 18 }}>
        <div className="panel">
          <div className="panel-header" style={{ marginBottom: 14 }}>
            <div>
              <p className="eyebrow">Recipients</p>
              <h2>Contacts</h2>
              <p className="sub">Shared across modules. Names exactly as they appear in WhatsApp.</p>
            </div>
            <Users size={18} className="muted" />
          </div>

          {contacts.length === 0 ? (
            <div className="empty" style={{ padding: 22 }}>
              <p className="muted">No contacts yet.</p>
            </div>
          ) : (
            <div className="contact-list">
              {contacts.map((c) => (
                <div key={c} className="contact-row">
                  <span className="contact-avatar">{c.slice(0, 1).toUpperCase()}</span>
                  {c}
                </div>
              ))}
            </div>
          )}

          <Button
            variant="ghost"
            icon={Users}
            className="btn-block"
            style={{ marginTop: 12 }}
            onClick={() => {
              setDraft(contacts.join("\n"));
              setEditing(true);
            }}
          >
            Edit contacts
          </Button>
        </div>
      </div>

      {editing && (
        <Modal
          title="Edit WhatsApp contacts"
          onClose={() => setEditing(false)}
          footer={
            <>
              <Button variant="ghost" onClick={() => setEditing(false)}>
                Cancel
              </Button>
              <Button variant="primary" loading={busy === "contacts"} onClick={saveContacts}>
                Save
              </Button>
            </>
          }
        >
          <p className="muted" style={{ marginTop: 0, fontSize: 12.5 }}>
            One contact name per line. Must match the chat name in WhatsApp exactly. Shared with all
            modules.
          </p>
          <textarea
            className="input"
            style={{ minHeight: 200, resize: "vertical", fontFamily: "var(--mono)", fontSize: 13 }}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            placeholder={"Raghunandan\nManager North\nCFO"}
          />
        </Modal>
      )}
    </div>
  );
}
