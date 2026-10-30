// Starts the service for end-to-end tests on a scratch copy of the examples and the validation
// workspace (sb-* definitions in instruments/, their benches) and the e2e fixtures, because the tests edit bench files
// (and node drags are saved to the file).
import { spawn } from "node:child_process";
import { cpSync, mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";

const port = process.argv[2] ?? "18765";
const root = resolve(import.meta.dirname, "..", "..");
const workspace = mkdtempSync(join(tmpdir(), "simubench-e2e-"));
cpSync(join(root, "examples"), workspace, { recursive: true });
cpSync(join(root, "validation"), workspace, { recursive: true });
// Definitions and benches only the end-to-end tests use (a definition with a declared panel).
cpSync(join(root, "web", "e2e", "fixtures"), workspace, { recursive: true });

const child = spawn(
  "uv",
  ["run", "simubench", "serve", "--workspace", workspace, "--token", "e2e-token", "--port", port, "--scpi-base-port", "0", "--record-dir", join(workspace, "runs")],
  { cwd: root, stdio: "inherit", shell: process.platform === "win32" },
);
for (const sig of ["SIGINT", "SIGTERM"]) process.on(sig, () => child.kill(sig));
child.on("exit", (code) => process.exit(code ?? 0));
