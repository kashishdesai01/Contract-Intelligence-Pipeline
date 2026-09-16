/** Treat every contract string as data when rendering HTML. */
export const escapeHTML = (value) =>
  String(value ?? "").replace(
    /[&<>"']/g,
    (char) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        char
      ],
  );

/** Offline preview only. Live queries always use the backend and persisted receipts. */
export function sampleCount(snapshot, field, op, expected) {
  const rows = [];
  const excluded = { conflicted: 0, unknown_required_fact: 0 };
  let evaluated = 0;
  for (const family of snapshot.families) {
    const version = family.versions.at(-1);
    if (version.conflicts.length) {
      excluded.conflicted++;
      continue;
    }
    const fact = version.facts[field];
    if (!fact || fact.status !== "ok" || typeof fact.value !== "number") {
      excluded.unknown_required_fact++;
      continue;
    }
    evaluated++;
    if (
      op === "gt"
        ? fact.value > expected
        : op === "lt"
          ? fact.value < expected
          : fact.value === expected
    ) {
      rows.push({ agreement: family.agreement });
    }
  }
  return {
    count: rows.length,
    rows,
    coverage: {
      evaluated,
      excluded,
      complete:
        !Object.values(excluded).some(Boolean) &&
        !snapshot.review.length &&
        !Object.keys(snapshot.exclusions).length,
    },
  };
}
