// Starts the service for end-to-end tests on a scratch copy of the examples and the validation
// workspace (sb-* definitions in instruments/, their benches) and the e2e fixtures, because the tests edit bench files
// (and node drags are saved to the file). The workspace is a fixed directory under the OS temp
// folder so a test can edit a definition the way an agent would (instrument.spec.ts).
import { spawn } from "node:child_process";
import { cpSync, mkdirSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";

const port = process.argv[2] ?? "18765";
const root = resolve(import.meta.dirname, "..", "..");
const workspace = join(tmpdir(), "simubench-e2e-workspace");
rmSync(workspace, { recursive: true, force: true });
mkdirSync(workspace, { recursive: true });
cpSync(join(root, "examples"), workspace, { recursive: true });
cpSync(join(root, "validation"), workspace, { recursive: true });
// Definitions and benches only the end-to-end tests use (a definition with a declared panel).
cpSync(join(root, "web", "e2e", "fixtures"), workspace, { recursive: true });
// The 34465A acceptance page edits a workspace copy of the built-in definition, so that what an
// agent changes shows in its version history.
const library = join(root, "src", "simubench", "library", "instruments");
mkdirSync(join(workspace, "instruments"), { recursive: true });
for (const name of ["keysight-34465a.yaml", "keysight-34465a.coverage.yaml"]) {
  cpSync(join(library, name), join(workspace, "instruments", name));
}

const child = spawn(
  "uv",
  ["run", "simubench", "serve", "--workspace", workspace, "--token", "e2e-token", "--port", port, "--scpi-base-port", "0", "--record-dir", join(workspace, "runs")],
  { cwd: root, stdio: "inherit", shell: process.platform === "win32" },
);
for (const sig of ["SIGINT", "SIGTERM"]) process.on(sig, () => child.kill(sig));
child.on("exit", (code) => process.exit(code ?? 0));
