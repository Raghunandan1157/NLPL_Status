import { useEffect, useState } from "react";
import { Activity, ArrowRight, Database, Mail, Sparkles } from "lucide-react";
import { MODULES } from "../modules/registry.js";
import { getDbStatus } from "../eod/api.js";
import "./home.css";

function StatusChip({ icon: Icon, label, value, tone }) {
  return (
    <div className="status-chip">
      <span className={`status-chip-icon ${tone || ""}`}>
        <Icon size={18} />
      </span>
      <div>
        <small>{label}</small>
        <strong>{value}</strong>
      </div>
    </div>
  );
}

export default function HomePage({ health, onOpen }) {
  const [db, setDb] = useState(null);
  const online = Boolean(health);

  useEffect(() => {
    let alive = true;
    getDbStatus()
      .then((d) => alive && setDb(d))
      .catch(() => alive && setDb(null));
    return () => {
      alive = false;
    };
  }, [health]);

  return (
    <div className="home">
      <section className="hero">
        <div className="hero-content">
          <span className="hero-eyebrow">
            <Sparkles size={14} /> Operations Console
          </span>
          <h1>Run your daily collection workflows from one clean place.</h1>
          <p>
            Upload the day's files, generate the EOD report, and deliver it over email and WhatsApp —
            all backed by the unified collection engine.
          </p>
          <div className="hero-actions">
            <button className="btn btn-primary" onClick={() => onOpen("eod")}>
              Open EOD Module <ArrowRight size={16} />
            </button>
          </div>
        </div>
        <div className="hero-status">
          <StatusChip
            icon={Activity}
            label="Backend"
            value={online ? "Online" : "Offline"}
            tone={online ? "ok" : "bad"}
          />
          <StatusChip
            icon={Database}
            label="Demand DB"
            value={db?.demandMaster?.loaded ? "Ready" : "Not loaded"}
            tone={db?.demandMaster?.loaded ? "ok" : "muted"}
          />
          <StatusChip
            icon={Mail}
            label="Email"
            value={health?.email?.configured ? "Configured" : "Add credentials"}
            tone={health?.email?.configured ? "ok" : "muted"}
          />
        </div>
      </section>

      <div className="section-head">
        <h2>Modules</h2>
        <p className="muted">Pick a module to get started. More are on the way.</p>
      </div>

      <div className="module-grid">
        {MODULES.map((m) => {
          const Icon = m.icon;
          const live = m.status === "live";
          return (
            <div
              key={m.id}
              className={`module-card accent-${m.accent} ${live ? "live" : "soon"}`}
              role={live ? "button" : undefined}
              tabIndex={live ? 0 : undefined}
              onClick={() => live && onOpen(m.id)}
              onKeyDown={(e) => live && (e.key === "Enter" || e.key === " ") && onOpen(m.id)}
            >
              <div className="module-top">
                <span className="module-icon">
                  <Icon size={22} />
                </span>
                {live ? (
                  <span className="badge badge-success">Live</span>
                ) : (
                  <span className="badge badge-muted">Coming soon</span>
                )}
              </div>
              <h3>{m.name}</h3>
              <div className="module-tagline">{m.tagline}</div>
              <p className="module-desc">{m.description}</p>
              {m.features && (
                <div className="module-tags">
                  {m.features.map((f) => (
                    <span key={f} className="module-tag">
                      {f}
                    </span>
                  ))}
                </div>
              )}
              {live && (
                <div className="module-open">
                  Open module <ArrowRight size={15} />
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
