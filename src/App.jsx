import { useCallback, useEffect, useState } from "react";
import { ChevronRight, Database, FileSpreadsheet, Home, LayoutGrid, RefreshCw } from "lucide-react";
import { ErrorBoundary, ToastProvider } from "./components/ui.jsx";
import { getHealth } from "./eod/api.js";
import { MODULES, getModule } from "./modules/registry.js";
import HomePage from "./pages/HomePage.jsx";
import ReportsPage from "./pages/ReportsPage.jsx";
import DbModule from "./db/DbModule.jsx";

function Shell() {
  // view = "home" or a module id or "reports_page"
  const [view, setView] = useState(() => {
    const hash = window.location.hash.replace("#", "");
    return hash || "home";
  });
  const [health, setHealth] = useState(null);
  const [healthChecked, setHealthChecked] = useState(false);

  const refreshHealth = useCallback(async () => {
    try {
      setHealth(await getHealth());
    } catch {
      setHealth(null);
    } finally {
      setHealthChecked(true);
    }
  }, []);

  useEffect(() => {
    refreshHealth();
    const t = setInterval(refreshHealth, 20000);
    return () => clearInterval(t);
  }, [refreshHealth]);

  useEffect(() => {
    const handleHashChange = () => {
      const hash = window.location.hash.replace("#", "");
      setView(hash || "home");
    };
    window.addEventListener("hashchange", handleHashChange);
    return () => window.removeEventListener("hashchange", handleHashChange);
  }, []);

  const changeView = (newView) => {
    setView(newView);
    window.location.hash = newView === "home" ? "" : newView;
  };

  const activeModule = (view === "home" || view === "reports_page" || view === "db") ? null : getModule(view);
  const online = Boolean(health);

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-logo">NS</div>
          <div className="brand-text">
            <strong>NLPL Status</strong>
            <small>OPERATIONS CONSOLE</small>
          </div>
        </div>

        <div>
          <div className="nav-group-label">Workspace</div>
          <nav className="nav">
            <button className={view === "home" ? "active" : ""} onClick={() => changeView("home")}>
              <Home size={17} /> Home
            </button>
            <button className={view === "db" ? "active" : ""} onClick={() => changeView("db")}>
              <Database size={17} /> DB Module
            </button>
            <button className={view === "reports_page" ? "active" : ""} onClick={() => changeView("reports_page")}>
              <FileSpreadsheet size={17} /> Reports and Downloads
            </button>
          </nav>
        </div>

        <div>
          <div className="nav-group-label">Modules</div>
          <nav className="nav">
            {MODULES.map((m) => {
              const Icon = m.icon;
              const soon = m.status !== "live";
              return (
                <button
                  key={m.id}
                  className={`${view === m.id ? "active" : ""} ${soon ? "soon" : ""}`}
                  onClick={() => !soon && changeView(m.id)}
                  disabled={soon}
                  title={soon ? "Coming soon" : m.name}
                >
                  <Icon size={17} /> {m.name}
                  {soon && <span className="nav-badge">soon</span>}
                </button>
              );
            })}
          </nav>
        </div>

        <div className="sidebar-footer">
          Reuses the unified collection engine.
          <br />
          Backend {online ? "online" : "offline"} · {MODULES.filter((m) => m.status === "live").length} live module
        </div>
      </aside>

      <div className="main">
        <header className="topbar">
          <div className="crumbs">
            <button onClick={() => changeView("home")}>
              <LayoutGrid size={15} style={{ verticalAlign: "-2px" }} /> Home
            </button>
            {view === "db" && (
              <>
                <ChevronRight size={15} />
                <span className="here">DB Module</span>
              </>
            )}
            {view === "reports_page" && (
              <>
                <ChevronRight size={15} />
                <span className="here">Reports and Downloads</span>
              </>
            )}
            {activeModule && (
              <>
                <ChevronRight size={15} />
                <span className="here">{activeModule.name}</span>
              </>
            )}
          </div>
          <div className="topbar-actions">
            <span className={`health-pill ${healthChecked ? (online ? "online" : "offline") : ""}`}>
              <span className="health-dot" />
              {online ? "Backend online" : healthChecked ? "Backend offline" : "Checking…"}
            </span>
            <button className="icon-btn" onClick={refreshHealth} title="Refresh status">
              <RefreshCw size={16} />
            </button>
          </div>
        </header>

        <div className="page">
          <ErrorBoundary key={view}>
            {view === "home" && <HomePage health={health} onOpen={changeView} />}
            {view === "db" && <DbModule onHealthChange={refreshHealth} />}
            {view === "reports_page" && <ReportsPage />}
            {activeModule?.Component && <activeModule.Component health={health} onHealthChange={refreshHealth} />}
          </ErrorBoundary>
        </div>
      </div>
    </div>
  );
}

export default function App() {
  return (
    <ToastProvider>
      <Shell />
    </ToastProvider>
  );
}
