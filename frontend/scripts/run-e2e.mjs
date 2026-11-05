import { spawn } from "node:child_process";
import { createServer } from "node:net";
import { createInterface } from "node:readline";
import { once } from "node:events";
const children = [];
const launch = (command, args, env = process.env) => {
  const child = spawn(command, args, {
    env,
    windowsHide: true,
    stdio: ["pipe", "pipe", "pipe"],
  });
  children.push(child);
  return child;
};
let harness;
try {
  harness = launch("uv", ["run", "python", "../scripts/browser_harness.py"]);
  const ready = await new Promise((resolve, reject) => {
    const timer = setTimeout(
      () =>
        reject(
          new Error(
            "Browser fixture setup timed out. Check Docker Compose test database.",
          ),
        ),
      90000,
    );
    const lines = createInterface({ input: harness.stdout });
    lines.on("line", (line) => {
      try {
        const value = JSON.parse(line);
        if (value.type === "ready") {
          clearTimeout(timer);
          lines.close();
          resolve(value);
        }
      } catch {
        /* No credential-bearing output is forwarded. */
      }
    });
    harness.once("exit", () => {
      clearTimeout(timer);
      reject(
        new Error(
          "Browser fixture process exited during setup. Start the test database.",
        ),
      );
    });
  });
  const probe = createServer();
  probe.listen(0, "127.0.0.1");
  await once(probe, "listening");
  const port = probe.address().port;
  await new Promise((resolve) => probe.close(resolve));
  const base = `http://127.0.0.1:${port}`;
  const vite = launch(
    process.execPath,
    [
      "node_modules/vite/bin/vite.js",
      "--host",
      "127.0.0.1",
      "--port",
      String(port),
      "--strictPort",
    ],
    { ...process.env, API_PROXY_TARGET: ready.api_url },
  );
  const deadline = Date.now() + 30000;
  while (true) {
    if (vite.exitCode !== null)
      throw new Error("Vite exited before browser verification");
    try {
      if ((await fetch(base)).ok) break;
    } catch {
      /* Server is starting. */
    }
    if (Date.now() > deadline) throw new Error("Frontend did not become ready");
    await new Promise((resolve) => setTimeout(resolve, 150));
  }
  const test = spawn(
    process.execPath,
    ["node_modules/@playwright/test/cli.js", "test", ...process.argv.slice(2)],
    {
      windowsHide: true,
      stdio: "inherit",
      env: {
        ...process.env,
        E2E_BASE_URL: base,
        E2E_FIXTURE_JSON: JSON.stringify(ready),
      },
    },
  );
  process.exitCode = await new Promise((resolve, reject) => {
    test.once("exit", (code) => resolve(code ?? 1));
    test.once("error", reject);
  });
} catch (error) {
  console.error(error.message);
  process.exitCode = 1;
} finally {
  if (harness && harness.exitCode === null) {
    harness.stdin.end("\n");
    await Promise.race([
      once(harness, "exit"),
      new Promise((resolve) => setTimeout(resolve, 15000)),
    ]);
  }
  for (const child of children) if (child.exitCode === null) child.kill();
}
