import path from "node:path";

// The backend's own venv Python, shelled out to by global-setup.ts and
// seed.ts. Was hardcoded to the Windows venv layout (.venv/Scripts/
// python.exe) until Day 44 - harmless while e2e only ever ran on this
// Windows dev machine, but a hard failure on a Linux CI runner, whose
// venvs use .venv/bin/python instead.
export const BACKEND_DIR = path.resolve(__dirname, "../../backend");

export const PYTHON_BIN = path.join(
  BACKEND_DIR,
  ".venv",
  process.platform === "win32" ? "Scripts" : "bin",
  process.platform === "win32" ? "python.exe" : "python"
);
