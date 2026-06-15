// One-time backend setup — make nlpl_status fully self-contained.
// Creates a local Python venv at ./venv and installs backend/requirements.txt,
// so the app no longer depends on the unified-collection-report venv.
//   Usage:  npm run setup        (uses the `py` launcher / python3)
//           PYTHON=/path/python npm run setup   (pin a specific interpreter)
import { spawnSync } from "node:child_process";
import { existsSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const IS_WIN = process.platform === "win32";
const venvPy = IS_WIN ? join(ROOT, "venv", "Scripts", "python.exe") : join(ROOT, "venv", "bin", "python");

function run(cmd, args) {
  console.log(`> ${cmd} ${args.join(" ")}`);
  const r = spawnSync(cmd, args, { stdio: "inherit", cwd: ROOT });
  if (r.status !== 0) {
    console.error("\n✗ setup failed. Ensure Python 3.10–3.12 is installed and on PATH.");
    process.exit(r.status ?? 1);
  }
}

// 1. Create the venv (skip if it already exists).
if (!existsSync(venvPy)) {
  if (process.env.PYTHON) run(process.env.PYTHON, ["-m", "venv", "venv"]);
  else if (IS_WIN) run("py", ["-3", "-m", "venv", "venv"]);
  else run("python3", ["-m", "venv", "venv"]);
}

// 2. Install dependencies into it.
run(venvPy, ["-m", "pip", "install", "--upgrade", "pip"]);
run(venvPy, ["-m", "pip", "install", "-r", join("backend", "requirements.txt")]);

console.log("\n✓ Backend is self-contained. Start with:  npm run dev");
console.log(`  (optional, only for WhatsApp send)  "${venvPy}" -m playwright install chromium`);
