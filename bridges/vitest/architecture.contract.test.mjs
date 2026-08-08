// Runs `limen check` as a Vitest test, so the architecture contract fails the
// same suite the unit tests do. Copy into tests/architecture.contract.test.mjs
// (or .ts) and adjust SPEC if needed.
//
// Requires: pip install limen-arch (limen on PATH), architecture.yaml in the
// repository root.
import { execFileSync } from "node:child_process";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const REPO_ROOT = resolve(import.meta.dirname ?? ".", "..");
const SPEC = resolve(REPO_ROOT, "architecture.yaml");

describe("architecture contract", () => {
  it("holds (limen check reports zero errors)", () => {
    let stdout = "";
    let exitCode = 0;
    try {
      stdout = execFileSync(
        "limen",
        ["check", "--spec", SPEC, "--workspace", REPO_ROOT, "--format", "json"],
        { encoding: "utf8" },
      );
    } catch (err) {
      exitCode = err.status ?? 1;
      stdout = err.stdout?.toString() ?? "";
      if (exitCode === 3 || exitCode === 4) {
        throw new Error(
          `limen infrastructure error (exit ${exitCode}):\n${err.stderr}`,
        );
      }
    }

    const report = JSON.parse(stdout);
    const errors = (report.findings ?? []).filter(
      (f) => f.severity === "error",
    );
    const details = errors
      .map(
        (f) =>
          `[${f.rule_id}] ${f.file ?? "?"}${f.line ? ":" + f.line : ""}\n` +
          `    ${f.message}` +
          (f.remedy ? `\n    Remedy: ${f.remedy}` : ""),
      )
      .join("\n");

    expect(report.summary.errors, `Architecture contract violated:\n${details}`)
      .toBe(0);
  });
});
