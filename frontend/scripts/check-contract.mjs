import { execFileSync } from "node:child_process";
import { readFileSync, unlinkSync } from "node:fs";
execFileSync("uv", ["run", "python", "../scripts/export_openapi.py"], {
  stdio: "inherit",
});
const target = ".cache/openapi-check.ts";
execFileSync(
  process.execPath,
  [
    "node_modules/openapi-typescript/bin/cli.js",
    ".cache/openapi.json",
    "-o",
    target,
  ],
  { stdio: "inherit" },
);
const same =
  readFileSync(target, "utf8") === readFileSync("src/types/openapi.ts", "utf8");
unlinkSync(target);
if (!same) {
  console.error("API types differ: run npm run api:generate and review.");
  process.exitCode = 1;
}
