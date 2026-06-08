import { useEffect, useState, useCallback } from "react";
import {
  CheckCircle2,
  FolderOpen,
  ChevronDown,
  ChevronUp,
  FileSpreadsheet,
  FileText,
  File,
  Loader2,
  MessageCircle,
  Power,
  Send,
  Users,
} from "lucide-react";
import { Button, Modal, useToast } from "../components/ui.jsx";
import {
  getWhatsAppContacts,
  saveWhatsAppContacts,
  openWhatsApp,
  sendWhatsApp,
  getVbaBundles,
} from "./hourlyApi.js";

export default function HourlyWhatsApp({ health }) {
  const toast = useToast();
  const [contacts, setContacts] = useState([]);
  const [connection, setConnection] = useState("idle"); // idle | connecting | ready | error
  const [bundles, setBundles] = useState([]);
  const [expandedBundle, setExpandedBundle] = useState(null);
  const [selectedFile, setSelectedFile] = useState(null); // { bundlePath, filename, displayName }
  const [busy, setBusy] = useState("");
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");

  const loadInitialData = useCallback(async () => {
    try {
      const cRes = await getWhatsAppContacts();
      setContacts(cRes.contacts || []);
    } catch {
      setContacts([]);
    }

    try {
      const bRes = await getVbaBundles();
      const list = bRes.bundles || [];
      setBundles(list);
      if (list.length > 0) {
        setExpandedBundle(list[0].name);
      }
    } catch {
      setBundles([]);
    }
  }, []);

  useEffect(() => {
    loadInitialData();
  }, [loadInitialData]);

  async function connect() {
    setConnection("connecting");
    setBusy("connect");
    try {
      const res = await openWhatsApp();
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

  function openEditor() {
    setDraft(contacts.join("\n"));
    setEditing(true);
  }

  async function saveContacts() {
    const list = draft
      .split("\n")
      .map((s) => s.trim())
      .filter(Boolean);
    setBusy("contacts");
    try {
      const res = await saveWhatsAppContacts(list);
      setContacts(res.contacts || list);
      setEditing(false);
      toast.success(`${list.length} contact(s) saved.`, "Saved");
    } catch (e) {
      toast.error(e.message, "Save failed");
    } finally {
      setBusy("");
    }
  }

  async function handleSend() {
    if (!selectedFile) {
      toast.warn("Please select a file to send from a bundle first.");
      return;
    }
    if (contacts.length === 0) {
      toast.warn("Add at least one WhatsApp contact first.");
      return;
    }
    setBusy("send");
    try {
      const res = await sendWhatsApp(selectedFile.bundlePath, selectedFile.filename);
      if (res.success) {
        toast.success(res.message || "Sent successfully via WhatsApp.", "Sent");
      } else {
        toast.error(res.error || "Send failed.", "WhatsApp");
      }
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

  const getFileIcon = (filename) => {
    const ext = filename.split(".").pop().toLowerCase();
    if (ext === "xlsx" || ext === "xls") return <FileSpreadsheet size={14} className="text-emerald" />;
    if (ext === "txt") return <FileText size={14} className="text-amber" />;
    return <File size={14} className="text-blue" />;
  };

  return (
    <div className="eod-grid">
      {/* Left side: WhatsApp Connection, Bundles and files selector */}
      <div className="col" style={{ gap: 18 }}>
        <div className="panel">
          <div className="panel-header">
            <div>
              <p className="eyebrow">Deliver</p>
              <h2>WhatsApp Dispatcher</h2>
              <p className="sub">Connect to WhatsApp Web, select an Hourly Bundle file, and send it to your contacts.</p>
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

          <div style={{ marginTop: 20 }}>
            <h4 style={{ marginBottom: 10, fontSize: 13.5 }}>Hourly Bundles (Select file to send)</h4>
            {bundles.length === 0 ? (
              <div className="empty" style={{ padding: 30 }}>
                <FolderOpen size={24} className="muted" style={{ marginBottom: 8 }} />
                <h3>No Hourly Bundles Found</h3>
                <p className="muted">Generate and save an Hourly Collection Report first.</p>
              </div>
            ) : (
              <div className="vba-bundle-list">
                {bundles.map((b) => {
                  const isOpen = expandedBundle === b.name;
                  return (
                    <div key={b.name} className="vba-bundle-card">
                      <div
                        className="vba-bundle-card-header"
                        onClick={() => setExpandedBundle(isOpen ? null : b.name)}
                      >
                        <FolderOpen size={16} className="text-muted" style={{ marginRight: 10 }} />
                        <div className="grow">
                          <span className="vba-bundle-title">{b.name}</span>
                          <div className="vba-bundle-meta">
                            <span>{b.files.length} file(s)</span>
                            {b.target_date && <span>Target Date: {b.target_date}</span>}
                          </div>
                        </div>
                        {isOpen ? <ChevronUp size={16} className="text-muted" /> : <ChevronDown size={16} className="text-muted" />}
                      </div>

                      {isOpen && (
                        <div className="vba-bundle-body">
                          <div style={{ display: "flex", flexDirection: "column", gap: 5 }}>
                            {b.files.map((f) => {
                              const isSelected = selectedFile?.bundlePath === b.path && selectedFile?.filename === f;
                              return (
                                <button
                                  key={f}
                                  className={`bundle-item-row ${isSelected ? "selected" : ""}`}
                                  onClick={() => setSelectedFile({
                                    bundlePath: b.path,
                                    filename: f,
                                    displayName: `${b.name} / ${f}`
                                  })}
                                  style={{ textAlign: "left", width: "100%" }}
                                >
                                  <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                                    {getFileIcon(f)}
                                    <span style={{ fontSize: 12.5, fontFamily: "var(--mono)" }}>{f}</span>
                                  </div>
                                </button>
                              );
                            })}
                          </div>
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            )}
          </div>

          {selectedFile && (
            <div style={{ marginTop: 20, padding: 14, background: "var(--surface-2)", border: "1px solid var(--border)", borderRadius: "var(--radius)" }}>
              <div style={{ fontSize: 12, color: "var(--text-muted)", marginBottom: 4 }}>Selected file for send:</div>
              <strong style={{ fontSize: 13, fontFamily: "var(--mono)" }}>{selectedFile.displayName}</strong>
            </div>
          )}

          <Button
            variant="success"
            icon={Send}
            className="btn-block"
            style={{ marginTop: 16 }}
            loading={busy === "send"}
            disabled={!selectedFile || contacts.length === 0}
            onClick={handleSend}
          >
            Send File to {contacts.length} contact{contacts.length === 1 ? "" : "s"}
          </Button>
        </div>
      </div>

      {/* Right side: Recipient contacts list */}
      <div className="col" style={{ gap: 18 }}>
        <div className="panel">
          <div className="panel-header" style={{ marginBottom: 14 }}>
            <div>
              <p className="eyebrow">Recipients</p>
              <h2>Contacts</h2>
              <p className="sub">Names exactly as they appear in WhatsApp.</p>
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

          <Button variant="ghost" icon={Users} className="btn-block" style={{ marginTop: 12 }} onClick={openEditor}>
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
            One contact name per line. Must match the chat name in WhatsApp exactly.
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
