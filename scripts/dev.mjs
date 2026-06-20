/**
 * One-command dev launcher for NLPL Status.
 *
 *   npm run dev
 *
 * Starts the Flask backend AND the Vite frontend, waits until both are
 * healthy, then opens the UI in the default browser. Ctrl+C stops both.
 *
 * Dependency-free: uses only Node built-ins (Node 18+ for global fetch).
 */
import { spawn, exec } from "node:child_process";
import { existsSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join, resolve } from "node:path";
import process from "node:process";

const __dirname = dirname(fileURLToPath(import.meta.url));
const ROOT = resolve(__dirname, "..");
const IS_WIN = process.platform === "win32";

const BACKEND_PORT = process.env.NLPL_PORT || "5055";
const FRONTEND_PORT = "5174";
const BACKEND_HEALTH = `http://127.0.0.1:${BACKEND_PORT}/api/health`;
const FRONTEND_URL = `http://127.0.0.1:${FRONTEND_PORT}`;

const c = {
  dim: (s) => `\x1b[2m${s}\x1b[0m`,
  cyan: (s) => `\x1b[36m${s}\x1b[0m`,
  green: (s) => `\x1b[32m${s}\x1b[0m`,
  yellow: (s) => `\x1b[33m${s}\x1b[0m`,
  red: (s) => `\x1b[31m${s}\x1b[0m`,
};

/** Resolve the best Python interpreter. Preference order:
 *    1. $PYTHON override
 *    2. THIS project's own venv  (self-contained — created by `npm run setup`)
 *    3. a sibling unified-collection-report venv (legacy fallback)
 *    4. system python
 *  So once `nlpl_status/venv` exists, the app no longer needs any other project. */
function resolvePython() {
  if (process.env.PYTHON) return process.env.PYTHON;
  const venvPy = (dir, name = "venv") =>
    IS_WIN ? join(dir, name, "Scripts", "python.exe") : join(dir, name, "bin", "python");
  const unified = process.env.UNIFIED_COLLECTION_DIR
    ? resolve(process.env.UNIFIED_COLLECTION_DIR)
    : resolve(ROOT, "..", "unified-collection-report");
  const candidates = [
    venvPy(ROOT),          // own venv (preferred — self-contained)
    venvPy(ROOT, ".venv"),
    venvPy(unified),       // fallback: sibling unified-collection-report venv
  ];
  for (const p of candidates) if (existsSync(p)) return p;
  return IS_WIN ? "python" : "python3";
}

function prefix(tag, color, stream, line) {
  for (const raw of line.toString().split(/\r?\n/)) {
    if (raw.trim() === "") continue;
    stream.write(`${color(`[${tag}]`)} ${raw}\n`);
  }
}

function startBackend() {
  const python = resolvePython();
  console.log(c.dim(`Backend python: ${python}`));
  const child = spawn(python, [join(ROOT, "backend", "server.py")], {
    cwd: ROOT,
    env: { ...process.env, PYTHONUNBUFFERED: "1" },
  });
  child.stdout.on("data", (d) => prefix("backend", c.cyan, process.stdout, d));
  child.stderr.on("data", (d) => prefix("backend", c.cyan, process.stderr, d));
  return child;
}

function startFrontend() {
  const viteJs = join(ROOT, "node_modules", "vite", "bin", "vite.js");
  const child = spawn(process.execPath, [viteJs], { cwd: ROOT, env: { ...process.env } });
  child.stdout.on("data", (d) => prefix("web", c.green, process.stdout, d));
  child.stderr.on("data", (d) => prefix("web", c.green, process.stderr, d));
  return child;
}

async function waitFor(url, label, timeoutMs) {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    try {
      const res = await fetch(url, { signal: AbortSignal.timeout(2000) });
      if (res.ok || res.status === 404) return true;
    } catch {
      /* not up yet */
    }
    await new Promise((r) => setTimeout(r, 700));
  }
  console.log(c.yellow(`! ${label} did not respond within ${timeoutMs / 1000}s (continuing anyway)`));
  return false;
}

function openBrowser(url) {
  // Force Google Chrome, falling back to the OS default browser if Chrome
  // isn't installed/registered (so the launcher still works everywhere).
  if (IS_WIN) {
    // `start "" chrome <url>` resolves chrome.exe via the registry App Paths.
    exec(`start "" chrome "${url}"`, { shell: "cmd.exe" }, (err) => {
      if (err) exec(`start "" "${url}"`, { shell: "cmd.exe" });
    });
  } else if (process.platform === "darwin") {
    exec(`open -a "Google Chrome" "${url}"`, (err) => {
      if (err) exec(`open "${url}"`);
    });
  } else {
    exec(`google-chrome "${url}"`, (err) => {
      if (err) exec(`xdg-open "${url}"`);
    });
  }
}

const children = [];
function shutdown() {
  for (const ch of children) {
    if (ch && !ch.killed) {
      try {
        if (IS_WIN) spawn("taskkill", ["/pid", String(ch.pid), "/T", "/F"]);
        else ch.kill("SIGTERM");
      } catch {
        /* ignore */
      }
    }
  }
}

process.on("SIGINT", () => {
  console.log(c.yellow("\nStopping services..."));
  shutdown();
  process.exit(0);
});
process.on("SIGTERM", () => {
  shutdown();
  process.exit(0);
});

(async () => {
  console.log(c.cyan("\n  NLPL Status — starting backend + frontend...\n"));
  const backend = startBackend();
  children.push(backend);
  backend.on("exit", (code) => {
    console.log(c.red(`Backend exited (code ${code}). Stopping.`));
    shutdown();
    process.exit(code || 1);
  });

  await waitFor(BACKEND_HEALTH, "Backend", 60000);
  console.log(c.green("  ✓ Backend ready"));

  const frontend = startFrontend();
  children.push(frontend);
  frontend.on("exit", (code) => {
    console.log(c.red(`Frontend exited (code ${code}). Stopping.`));
    shutdown();
    process.exit(code || 1);
  });

  await waitFor(FRONTEND_URL, "Frontend", 40000);
  console.log(c.green(`  ✓ Frontend ready → ${FRONTEND_URL}\n`));
  openBrowser(FRONTEND_URL);
  console.log(c.dim("  Press Ctrl+C to stop both services.\n"));
})();
