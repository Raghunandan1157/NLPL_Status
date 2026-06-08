import { useEffect, useState } from "react";
import { CheckCircle2, KeyRound, LogOut } from "lucide-react";
import { Button, Modal, useToast } from "../components/ui.jsx";
import { emailGetConfig, emailLogin, emailLogout } from "./centralApi.js";

/**
 * Centralized Gmail connect bar. Uses the app-level /api/email/* endpoints, so
 * connecting once here works for EVERY module that sends mail — there is no
 * per-module Gmail login. Drop it at the top of any module's email panel.
 */
export default function MailConnect({ onHealthChange, onChange }) {
  const toast = useToast();
  const [cfg, setCfg] = useState({ configured: false, sender: "" });
  const [open, setOpen] = useState(false);
  const [user, setUser] = useState("");
  const [pwd, setPwd] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    emailGetConfig()
      .then((c) => setCfg({ configured: !!c.configured, sender: c.sender || "" }))
      .catch(() => {});
  }, []);

  function apply(next) {
    setCfg(next);
    onChange?.(next);
    onHealthChange?.();
  }

  async function submit() {
    setBusy(true);
    try {
      const res = await emailLogin({ user, appPassword: pwd });
      if (res.success) {
        toast.success(`Signed in as ${res.sender}`, "Gmail connected");
        setOpen(false);
        setPwd("");
        apply({ configured: true, sender: res.sender });
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
      apply({ configured: false, sender: "" });
      toast.info("Signed out of Gmail.");
    } catch (e) {
      toast.error(e.message);
    }
  }

  return (
    <>
      {cfg.configured ? (
        <div className="gmail-bar ok">
          <CheckCircle2 size={18} />
          <div className="grow">
            <strong>Gmail connected</strong>
            <div className="muted" style={{ fontSize: 12.5 }}>
              Sending as {cfg.sender} · shared across all modules
            </div>
          </div>
          <Button size="sm" variant="ghost" icon={LogOut} onClick={signOut}>
            Sign out
          </Button>
        </div>
      ) : (
        <div className="gmail-bar">
          <KeyRound size={18} className="muted" />
          <div className="grow">
            <strong>Connect Gmail to send</strong>
            <div className="muted" style={{ fontSize: 12.5 }}>
              Sign in once with a Gmail <b>App Password</b> — shared by every module.
            </div>
          </div>
          <Button size="sm" variant="primary" onClick={() => setOpen(true)}>
            Connect Gmail
          </Button>
        </div>
      )}

      {open && (
        <Modal
          title="Connect Gmail"
          onClose={() => setOpen(false)}
          footer={
            <>
              <Button variant="ghost" onClick={() => setOpen(false)}>
                Cancel
              </Button>
              <Button variant="primary" loading={busy} disabled={!user || !pwd} onClick={submit}>
                Connect
              </Button>
            </>
          }
        >
          <p className="muted" style={{ marginTop: 0, fontSize: 12.5 }}>
            Use a Gmail <b>App Password</b> (Google Account → Security → App passwords), not your
            normal password.
          </p>
          <label className="field">
            <span>Gmail address</span>
            <input
              className="input"
              type="email"
              value={user}
              onChange={(e) => setUser(e.target.value)}
              placeholder="you@gmail.com"
            />
          </label>
          <label className="field" style={{ marginTop: 10 }}>
            <span>App password</span>
            <input
              className="input"
              type="password"
              value={pwd}
              onChange={(e) => setPwd(e.target.value)}
              placeholder="16-character app password"
            />
          </label>
        </Modal>
      )}
    </>
  );
}
