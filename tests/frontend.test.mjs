import { test } from "node:test";
import assert from "node:assert/strict";
import { escapeHTML, sampleCount } from "../src/cip/web/logic.mjs";

test("contract markup and quoted attributes remain literal text", () => {
  assert.equal(
    escapeHTML('<img src=x onerror="alert(1)">'),
    "&lt;img src=x onerror=&quot;alert(1)&quot;&gt;",
  );
  assert.equal(escapeHTML("A & B's"), "A &amp; B&#39;s");
});

test("counts use families, preserve unknown coverage and exclude conflicting versions", () => {
  const family = (name, value, status = "ok", conflicts = []) => ({
    agreement: name,
    versions: [{ facts: { payment_days: { value, status } }, conflicts }],
  });
  const snapshot = {
    families: [
      family("Match", 60),
      family("No match", 30),
      family("Unknown", null, "unknown"),
      family("Conflict", 90, "ok", [{}]),
    ],
    review: [],
    exclusions: {},
  };
  const result = sampleCount(snapshot, "payment_days", "gt", 45);
  assert.equal(result.count, 1);
  assert.deepEqual(result.rows, [{ agreement: "Match" }]);
  assert.equal(result.coverage.evaluated, 2);
  assert.equal(result.coverage.complete, false);
  assert.equal(result.coverage.excluded.unknown_required_fact, 1);
  assert.equal(result.coverage.excluded.conflicted, 1);
});

test("explicitly excluded sources prevent complete coverage", () => {
  const result = sampleCount(
    { families: [], review: [], exclusions: { source: "reviewed omission" } },
    "payment_days",
    "eq",
    30,
  );
  assert.equal(result.count, 0);
  assert.equal(result.coverage.complete, false);
});
