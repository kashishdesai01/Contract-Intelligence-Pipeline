import { escapeHTML as esc, sampleCount } from "./logic.mjs";
const $ = (s, root = document) => root.querySelector(s);
const icons = {
  grid: '<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/>',
  layers: '<path d="m12 3 9 5-9 5-9-5 9-5Zm-9 9 9 5 9-5M3 16l9 5 9-5"/>',
  search: '<circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 5 5"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  help: '<circle cx="12" cy="12" r="9"/><path d="M9.5 9a2.5 2.5 0 1 1 4 2c-1.5.5-1.5 1-1.5 2M12 17h.01"/>',
  chevrons: '<path d="m8 9 4-4 4 4m-8 6 4 4 4-4"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  arrow: '<path d="M5 12h14m-5-5 5 5-5 5"/>',
  back: '<path d="M19 12H5m5-5-5 5 5 5"/>',
  file: '<path d="M14 3H6a1 1 0 0 0-1 1v16a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1V8l-5-5Zm0 0v6h5M8 13h8m-8 4h5"/>',
  check: '<path d="m5 12 4 4L19 6"/>',
  shield:
    '<path d="m12 3 8 3v6c0 5-8 9-8 9s-8-4-8-9V6l8-3Z"/><path d="m8 12 3 3 5-6"/>',
  close: '<path d="m6 6 12 12M6 18 18 6"/>',
  upload: '<path d="M12 16V3m-5 5 5-5 5 5M4 16v5h16v-5"/>',
  more: '<circle cx="5" cy="12" r="1"/><circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/>',
  spark: '<path d="m12 2 3 7 7 3-7 3-3 7-3-7-7-3 7-3 3-7Z"/>',
};
const icon = (name) =>
  `<svg viewBox="0 0 24 24" aria-hidden="true">${icons[name] || icons.file}</svg>`;
const labels = {
  effective_date: "Effective date",
  payment_days: "Payment terms",
  notice_days: "Termination notice",
  auto_renewal: "Auto-renewal",
  renewal_months: "Renewal term",
  governing_law: "Governing law",
  liability_cap: "Liability cap",
  assignment_consent: "Assignment consent",
};
const pretty = (value, field) =>
  value == null
    ? "Unknown"
    : typeof value === "boolean"
      ? value
        ? "Yes"
        : "No"
      : ["payment_days", "notice_days"].includes(field)
        ? `${value} days`
        : field === "renewal_months"
          ? `${value} months`
          : String(value);
const date = (value) =>
  value
    ? new Date(value.slice(0, 10) + "T12:00:00").toLocaleDateString("en-US", {
        month: "short",
        day: "numeric",
        year: "numeric",
      })
    : "—";
const latest = (f) => f.versions.at(-1);
const state = {
  key: "",
  mode: "live",
  connected: false,
  snapshot: null,
  sid: null,
  sample: null,
  view: "agreements",
  family: null,
  version: null,
  filter: "",
  status: "all",
  jobs: [],
  jobsNext: null,
  resolutions: {},
  exclusions: {},
  busy: false,
  epoch: 0,
};
let toastTimer;
let dialogGeneration = 0;
function toast(message) {
  $("#toast").textContent = message;
  $("#toast").classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => $("#toast").classList.remove("show"), 4500);
}
async function api(path, options = {}) {
  const headers = { ...options.headers };
  if (options.body && !(options.body instanceof FormData))
    headers["Content-Type"] = "application/json";
  const response = await fetch(path, {
    ...options,
    headers,
    signal: options.signal || AbortSignal.timeout(25000),
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(
      {
        invalid_api_key: "That workspace key was not accepted.",
        request_rate_exceeded:
          "Too many requests. Please wait a minute and try again.",
        no_published_snapshot: "No published workspace yet.",
        database_unavailable:
          "The database is temporarily unavailable. Your current workspace is unchanged; please retry.",
        upload_too_large: "Each file must be no larger than 512 KB.",
        unsupported_media_type: "Choose a TXT or supported DOCX file.",
      }[body.title] ||
        (typeof body.title === "string"
          ? body.title.replaceAll("_", " ")
          : "The request could not be completed."),
    );
    error.status = response.status;
    error.title = body.title;
    throw error;
  }
  return body;
}
function conflictCount(s = state.snapshot) {
  return (
    (s?.families || []).reduce((n, f) => n + latest(f).conflicts.length, 0) +
    (s?.review.length || 0)
  );
}
function pill(conflicted) {
  return conflicted
    ? '<span class="pill amber"><i></i>Needs review</span>'
    : '<span class="pill"><i></i>Clear</span>';
}
function heading(eyebrow, title, subtitle, actions = "") {
  return `<div class="eyebrow">${eyebrow}</div><div class="heading-row"><div><h1>${title}</h1><p class="subtitle">${subtitle}</p></div><div class="button-group">${actions}</div></div>`;
}
function mount(html) {
  $("#content").innerHTML = `<div class="reveal">${html}</div>`;
  document
    .querySelectorAll("[data-icon]")
    .forEach((el) => (el.innerHTML = icon(el.dataset.icon)));
}
function render() {
  if (state.mode === "live" && !state.connected) {
    renderWelcome();
    return;
  }
  $("#workspace-name").textContent =
    state.mode === "sample" ? "Sample workspace" : "Connected workspace";
  $("#workspace-mode").textContent =
    state.mode === "sample" ? "Interactive preview" : "Private session";
  $("#mode-indicator").innerHTML =
    `<i></i>${state.mode === "sample" ? "Sample data" : "Live workspace"}`;
  $("#nav-count").textContent = state.snapshot?.families.length ?? "0";
  $("#review-count").textContent = conflictCount() || "";
  $(".workspace-switch .avatar").textContent =
    state.mode === "sample" ? "S" : "W";
  document.querySelectorAll(".nav").forEach((el) => {
    el.classList.toggle(
      "active",
      el.dataset.view === (state.view === "detail" ? "agreements" : state.view),
    );
    el.setAttribute(
      "aria-current",
      el.dataset.view === (state.view === "detail" ? "agreements" : state.view)
        ? "page"
        : "false",
    );
  });
  $("#breadcrumb").textContent = {
    agreements: "Agreements",
    detail: "Agreement details",
    review: "Review queue",
    search: "Explore evidence",
    activity: "Activity",
  }[state.view];
  if (state.view === "agreements") renderAgreements();
  else if (state.view === "detail") renderDetail();
  else if (state.view === "review") renderReview();
  else if (state.view === "search") renderSearch();
  else renderActivity();
}
function renderWelcome() {
  $("#workspace-name").textContent = "Your workspace";
  $("#workspace-mode").textContent = "Connect to get started";
  $("#mode-indicator").innerHTML = "<i></i>Not connected";
  $("#nav-count").textContent = "0";
  $("#review-count").textContent = "";
  mount(
    heading(
      "YOUR PRIVATE WORKSPACE",
      "Clarity starts here.",
      "Connect your workspace to upload agreements, follow amendments, and work from real source evidence.",
    ) +
      `<section class="panel empty">${icon("shield")}<h2>Your contracts. Their complete story.</h2><p>Your documents and published versions are stored in the backend. Sign in once to keep working across page refreshes.</p><button class="button primary" data-action="connect">Connect workspace ${icon("arrow")}</button><p><button class="text-button" data-action="sample">Explore the sample preview instead ${icon("arrow")}</button></p></section>`,
  );
}
function navigate(view) {
  if (state.busy) {
    toast("Finish the current build before switching views.");
    return;
  }
  state.view = view;
  state.epoch++;
  location.hash = view === "detail" ? "agreement/" + state.family : view;
  render();
}
function renderAgreements() {
  const families = state.snapshot?.families || [];
  const amended = families.filter((f) => f.versions.length > 1).length;
  const review =
    families.filter((f) => latest(f).conflicts.length).length +
    (state.snapshot?.review.length || 0);
  const known = families.reduce(
    (n, f) =>
      n +
      Object.values(latest(f).facts).filter((x) => x.status === "ok").length,
    0,
  );
  const actions = `<button class="button subtle" data-action="refresh">${icon("clock")} Refresh</button><button class="button primary" data-action="upload">${icon("plus")} Add agreement</button>`;
  const rows = families.filter(
    (f) =>
      (f.agreement + " " + f.parties.join(" "))
        .toLowerCase()
        .includes(state.filter.toLowerCase()) &&
      (state.status === "all" ||
        (state.status === "review"
          ? latest(f).conflicts.length
          : !latest(f).conflicts.length)),
  );
  mount(
    heading(
      "A LITTLE ORDER. A LOT OF CLARITY.",
      "Your agreements, understood.",
      "One place for your contracts, their changes, and the details that matter.",
      actions,
    ) +
      `
 <section class="stats" aria-label="Workspace summary"><div class="stat"><div class="stat-label">Total agreements ${icon("file")}</div><strong class="stat-value">${families.length.toString().padStart(2, "0")}</strong><span class="stat-foot">Distinct contract families</span></div><div class="stat"><div class="stat-label">Amended agreements ${icon("layers")}</div><strong class="stat-value">${amended.toString().padStart(2, "0")}</strong><span class="stat-foot">Changes kept in context</span></div><div class="stat"><div class="stat-label">Needs your attention ${icon("clock")}</div><strong class="stat-value">${review.toString().padStart(2, "0")}</strong><span class="stat-foot">${review ? "Overlapping or uncertain terms" : "Nothing waiting for review"}</span></div><div class="stat"><div class="stat-label">Source-backed terms ${icon("shield")}</div><strong class="stat-value">${known.toString().padStart(2, "0")}</strong><span class="stat-foot"><em>Traceable to original evidence</em></span></div></section>
 <section class="insight"><span class="insight-symbol">✳</span><div><h3>${review ? "A little attention now. More certainty later." : "A clear view, without the guesswork."}</h3><p>${review ? `${review} agreement or document review needed. Disputed terms are withheld until a new version resolves the uncertainty.` : "Explore an agreement to see its terms and the original passages behind them."}</p></div><button class="text-button" data-view="review">Open review queue ${icon("arrow")}</button></section>
 <section aria-labelledby="agreements-heading"><div class="section-heading"><h2 id="agreements-heading">Agreement library <small>${families.length}</small></h2><span class="tagline">The whole story, in one place.</span></div><div class="toolbar"><div class="input-wrap">${icon("search")}<input id="filter" aria-label="Search agreements" placeholder="Search by agreement or party…" value="${esc(state.filter)}"></div><select id="status-filter" aria-label="Filter by review status"><option value="all">All statuses</option><option value="clear" ${state.status === "clear" ? "selected" : ""}>Clear</option><option value="review" ${state.status === "review" ? "selected" : ""}>Needs review</option></select><span class="right">${state.mode === "sample" ? "Curated synthetic agreements" : "Your published snapshot"}</span></div>
 <div class="table-card">${families.length ? `<div class="table-scroll"><table><thead><tr><th>Agreement</th><th>Status</th><th>Payment terms</th><th>Last effective</th><th>Versions</th><th><span class="muted">Open</span></th></tr></thead><tbody>${rows.map((f) => `<tr><td><div class="agreement-cell"><span class="doc-icon">${icon("file")}</span><div><button class="agreement-name" data-family="${esc(f.id)}">${esc(f.agreement)}</button><div class="agreement-sub">Master Services Agreement</div></div></div></td><td>${pill(latest(f).conflicts.length)}</td><td>${latest(f).facts.payment_days.status === "ok" ? esc(pretty(latest(f).facts.payment_days.value, "payment_days")) : '<span class="muted">Awaiting review</span>'}</td><td class="muted">${date(latest(f).as_of)}</td><td class="muted">${f.versions.length} ${f.versions.length === 1 ? "version" : "versions"}</td><td><button class="icon-button" data-family="${esc(f.id)}" aria-label="Open ${esc(f.agreement)}">${icon("arrow")}</button></td></tr>`).join("")}</tbody></table></div>${!rows.length ? '<div class="empty"><p>No agreements match these filters.</p></div>' : ""}<div class="table-bottom"><span>Showing ${rows.length} of ${families.length} agreements</span><span class="seal">${icon("shield")} Source-backed. Version-aware.</span></div>` : `<div class="empty">${icon("file")}<h2>A fresh start for your agreements.</h2><p>Add a supported contract, or load the sample set to explore terms, amendments, and source evidence.</p><button class="button primary" data-action="upload">${icon("plus")} Add your first agreement</button></div>`}</div></section>
 <div class="lower-grid"><section class="panel"><h3 class="panel-label">WORKSPACE NOTES</h3><div class="note-row"><span class="note-dot amber"></span><div><p>${review ? esc(families.find((f) => latest(f).conflicts.length)?.agreement || "A source document") + " needs a closer look." : "Version history stays with every agreement."}</p><small>${review ? "Disputed terms stay out of trusted results." : "New snapshots never overwrite the original sources."}</small></div></div><div class="note-row"><span class="note-dot"></span><div><p>${state.mode === "sample" ? "You’re exploring a sample workspace." : "You’re connected to a private workspace."}</p><small>${state.mode === "sample" ? "Connect a tenant key to upload and publish." : "Your workspace session survives page refreshes."}</small></div></div></section><section class="panel quote-panel"><h3 class="panel-label">LESS SEARCHING. MORE UNDERSTANDING.</h3><p class="quote">“The detail is only useful<br>when you know where it came from.”</p><span class="quote-source">OUR APPROACH TO CONTRACT INTELLIGENCE</span><span class="quote-mark">”</span></section></div>`,
  );
  $("#filter").addEventListener("input", (e) => {
    state.filter = e.target.value;
    const pos = e.target.selectionStart;
    renderAgreements();
    $("#filter").focus();
    $("#filter").setSelectionRange(pos, pos);
  });
  $("#status-filter").addEventListener("change", (e) => {
    state.status = e.target.value;
    renderAgreements();
  });
}
function renderDetail() {
  const f = state.snapshot?.families.find((x) => x.id === state.family);
  if (!f) {
    state.view = "agreements";
    return render();
  }
  const v = f.versions.find((x) => x.as_of === state.version) || latest(f);
  state.version = v.as_of;
  mount(
    `<button class="back" data-view="agreements">${icon("back")} All agreements</button>` +
      heading(
        "AGREEMENT INTELLIGENCE",
        esc(f.agreement),
        esc(f.parties.join("  ·  ")),
        pill(v.conflicts.length),
      ) +
      `
 <div class="detail-grid"><div><div class="section-heading"><h2>The terms, at a glance.</h2><span class="tagline">Select a term to inspect its evidence</span></div><div class="version-strip" aria-label="Agreement versions">${f.versions.map((x, i) => `<button class="version-button ${x.as_of === v.as_of ? "active" : ""}" data-version="${esc(x.as_of)}" aria-pressed="${x.as_of === v.as_of}">${i ? "Amended" : "Original"} · ${date(x.as_of)}</button>`).join("")}</div>${v.conflicts.length ? '<div class="notice">This version contains conflicting amendments. Terms are withheld from trusted answers. You can inspect earlier versions or open the review queue.</div>' : ""}<div class="facts-grid">${Object.keys(
   labels,
 )
   .map((k) => [k, v.facts[k]])
   .map(
     ([k, fact]) =>
       `<button class="fact ${fact.status !== "ok" ? "disputed" : ""}" data-fact="${esc(k)}"><span class="fact-label">${esc(labels[k])}${icon("arrow")}</span><strong>${fact.status === "ok" ? esc(pretty(fact.value, k)) : fact.status === "disputed" ? "Awaiting review" : "Not established"}</strong><small>${fact.status === "ok" ? "View source evidence" : esc(fact.reason?.replaceAll("_", " ") || "No trusted evidence available")}</small></button>`,
   )
   .join(
     "",
   )}</div><section class="panel spaced"><div class="section-heading"><h2>Agreement text</h2><span class="pill neutral">${v.clauses.length} clauses</span></div>${v.clauses.map((c) => `<article class="clause"><h3>Section ${esc(c.key)} ${c.disputed ? '<span class="pill amber">Disputed</span>' : ""}</h3><p>${esc(c.text)}</p><button class="text-button" data-clause="${esc(c.key)}">${icon("file")} ${c.evidence.length} original ${c.evidence.length === 1 ? "source" : "sources"} ${icon("arrow")}</button></article>`).join("")}</section></div>
 <aside><section class="panel"><h3 class="panel-label">AGREEMENT RECORD</h3><dl class="side-info"><dt>Agreement type</dt><dd>Master Services Agreement</dd><dt>Effective version</dt><dd>${date(v.as_of)}</dd><dt>History</dt><dd>${f.versions.length} retained ${f.versions.length === 1 ? "version" : "versions"}</dd><dt>Review status</dt><dd>${pill(v.conflicts.length)}</dd><dt>Workspace</dt><dd>${state.mode === "sample" ? "Synthetic sample" : "Connected tenant"}</dd></dl></section><section class="panel spaced"><h3 class="panel-label">PLAYBOOK CHECKS</h3>${v.deviations.length ? v.deviations.map((d) => `<div class="note-row"><span class="note-dot amber"></span><div><p>${esc(labels[d.field || d.rule] || d.field || d.rule || "Policy deviation")}</p><small>${esc(d.message || d.reason || (d.value != null ? pretty(d.value, d.field || d.rule) + " · outside the configured playbook" : "Outside the configured playbook"))}</small></div></div>`).join("") : '<p class="subtitle">No accepted terms fall outside the current playbook. Uncertain terms are not assessed.</p>'}<button class="text-button spaced" data-view="review">View review queue ${icon("arrow")}</button></section></aside></div>`,
  );
}
function evidence(title, quote, evidence, reason, origin = "composite") {
  const d = $("#evidence");
  d.innerHTML = `<div class="dialog-heading"><h2 id="evidence-title">${esc(title)}</h2><button class="icon-button" data-close="evidence" aria-label="Close evidence">${icon("close")}</button></div><p class="dialog-sub">A clear line from the answer to the original agreement.</p>${reason ? `<div class="notice">${esc(reason.replaceAll("_", " "))}</div>` : ""}${quote ? `<div class="source-label">${origin === "source_header" ? "SOURCE HEADER" : "COMPOSITE PASSAGE"}</div><blockquote>${esc(quote)}</blockquote>` : ""}${evidence.map((e, i) => `<div class="source-label">ORIGINAL SOURCE ${i + 1} · ${esc(e.source_id.slice(0, 10))}</div><div class="source-code">${esc(e.quote)}</div><details><summary class="text-button">Read full original document</summary><div class="source-code">${esc(state.snapshot.source_texts[e.source_id] || "Source unavailable in this snapshot.")}</div></details>`).join("")}${!evidence.length ? '<p class="dialog-sub">There is no accepted source evidence for this term.</p>' : ""}<div class="notice">Derived text may combine an agreement and an amendment. Original passages are shown separately.</div>`;
  d.showModal();
}
function renderReview() {
  const families = state.snapshot?.families || [];
  const conflicts = families.flatMap((f) =>
    latest(f).conflicts.map((c) => ({ ...c, family: f })),
  );
  const docs = state.snapshot?.review || [];
  mount(
    heading(
      "A CLEARER PATH FORWARD.",
      "A little human judgment.",
      "Uncertainty belongs in the open. Review conflicting changes before trusting the current terms.",
      `<span class="pill ${conflicts.length + docs.length ? "amber" : "neutral"}">${conflicts.length + docs.length} items to review</span>`,
    ) +
      `<section class="panel">${conflicts.length || docs.length ? `${conflicts.map((c) => `<article class="review-item"><span class="doc-icon">${icon("layers")}</span><div><span class="eyebrow">${esc(c.kind.replaceAll("_", " "))}</span><h3>${esc(c.family.agreement)} · Section ${esc(c.target)}</h3><p>${esc(c.detail)}</p><button class="text-button" data-family="${esc(c.family.id)}">Inspect agreement ${icon("arrow")}</button></div><button class="button small" data-resolve="${esc(c.id)}">Review change ${icon("arrow")}</button></article>`).join("")}${docs.map((d) => `<article class="review-item"><span class="doc-icon">${icon("file")}</span><div><h3>Document needs attention</h3><p>${esc(d.reason?.replaceAll("_", " "))}</p><small class="mono">${esc(d.source_id?.slice(0, 16))}</small><p>Correct the input, then rebuild. Unsupported sources are not silently discarded.</p></div></article>`).join("")}` : `<div class="empty">${icon("check")}<h2>Nothing waiting in the wings.</h2><p>Your published workspace has no outstanding conflicts or unlinked documents.</p><button class="button" data-view="agreements">Back to agreements ${icon("arrow")}</button></div>`}</section>`,
  );
}
function renderSearch() {
  mount(
    heading(
      "FOLLOW THE EVIDENCE.",
      "Find the detail that matters.",
      "Search the accepted contract passages, or count agreements using a precise condition.",
    ) +
      `<div class="segmented" id="search-tabs"><button class="active" data-searchmode="passages">Find a passage</button><button data-searchmode="count">Count agreements</button></div><form id="search-form"><div id="search-fields"><div class="search-box"><input aria-label="Search contract passages" id="search-query" required maxlength="500" placeholder="Try “termination notice” or “governing law”…"><button class="button primary">${icon("search")} Search</button></div></div></form><div id="results"><section class="panel empty">${icon("search")}<h2>Every answer starts somewhere.</h2><p>Look for a term or topic. Results include original-source evidence, and disputed versions stay out of trusted results.</p></section></div>`,
  );
  $("#search-tabs").addEventListener("click", (e) => {
    const b = e.target.closest("[data-searchmode]");
    if (!b) return;
    document
      .querySelectorAll("[data-searchmode]")
      .forEach((x) => x.classList.toggle("active", x === b));
    $("#search-fields").innerHTML =
      b.dataset.searchmode === "passages"
        ? '<div class="search-box"><input aria-label="Search contract passages" id="search-query" required maxlength="500" placeholder="Search a term or topic…"><button class="button primary">Search</button></div>'
        : '<div class="search-box"><select id="count-field" aria-label="Contract field"><option value="payment_days">Payment days</option><option value="notice_days">Notice days</option><option value="renewal_months">Renewal months</option></select><select id="count-op" aria-label="Comparison"><option value="gt">more than</option><option value="eq">equal to</option><option value="lt">less than</option></select><input id="count-value" type="number" aria-label="Comparison value" required value="45"><button class="button primary">Count</button></div>';
  });
  $("#search-form").addEventListener("submit", searchSubmit);
}
async function searchSubmit(event) {
  event.preventDefault();
  const epoch = state.epoch;
  const result = $("#results");
  const button =
    $("button[type=submit]", event.target) || $("button", event.target);
  button.disabled = true;
  result.innerHTML = '<div class="loading">Following the evidence…</div>';
  try {
    const count = !!$("#count-field");
    const field = $("#count-field")?.value;
    const op = $("#count-op")?.value;
    const val = Number($("#count-value")?.value);
    let data;
    if (state.mode === "live")
      data = await api(count ? "/v1/query" : "/v1/search", {
        method: "POST",
        body: JSON.stringify(
          count
            ? { predicates: [{ field, op, value: val }] }
            : { query: $("#search-query").value },
        ),
      });
    else if (count) {
      data = sampleCount(state.snapshot, field, op, val);
    } else {
      const words = $("#search-query")
        .value.toLowerCase()
        .split(/\s+/)
        .filter(Boolean);
      data = {
        results: state.snapshot.families
          .filter((f) => !latest(f).conflicts.length)
          .flatMap((f) =>
            latest(f)
              .clauses.filter((c) =>
                words.some((w) => c.text.toLowerCase().includes(w)),
              )
              .map((c) => ({
                ...c,
                family_id: f.id,
                quote: c.text,
                clause_key: c.key,
              })),
          )
          .slice(0, 10),
      };
    }
    if (epoch !== state.epoch) return;
    if (count)
      result.innerHTML = `<section class="panel"><span class="eyebrow">${state.mode === "sample" ? "SAMPLE CALCULATION" : "SNAPSHOT-BOUND RESULT"}</span><h2 class="quote">${data.count} matching agreement ${data.count === 1 ? "family" : "families"}.</h2><p class="subtitle">${data.coverage.evaluated} evaluated · ${data.coverage.complete ? "Complete coverage" : "Partial coverage — uncertain agreements excluded"}</p>${data.receipt_id ? `<button class="text-button spaced" data-receipt="${esc(data.receipt_id)}">Inspect saved query receipt ${icon("arrow")}</button>` : ""}<div class="spaced">${data.rows.map((r) => `<div class="note-row">${icon("file")} ${esc(r.agreement)}</div>`).join("")}</div></section>`;
    else {
      state.searchResults = data.results;
      result.innerHTML = `<section class="panel"><span class="eyebrow">${data.results.length} ACCEPTED PASSAGES ${state.mode === "sample" ? "· SIMPLE SAMPLE MATCHING" : ""}</span>${data.results.length ? data.results.map((r, i) => `<article class="result"><span class="pill neutral">Section ${esc(r.clause_key)}</span><h3>${esc(state.snapshot.families.find((f) => f.id === r.family_id)?.agreement || "Agreement")}</h3><blockquote>${esc(r.quote || r.text)}</blockquote><button class="text-button" data-result="${i}">Inspect original evidence ${icon("arrow")}</button></article>`).join("") : '<div class="empty"><h2>No accepted passages found.</h2><p>Try fewer words. Agreements with disputed current versions are excluded.</p></div>'}</section>`;
    }
  } catch (e) {
    if (epoch === state.epoch)
      result.innerHTML = `<p class="error-banner">${esc(e.message)}</p>`;
  } finally {
    button.disabled = false;
  }
}
function renderActivity() {
  mount(
    heading(
      "THE PAPER TRAIL.",
      "Every step, accounted for.",
      "A record of processing jobs in your workspace. Published versions remain immutable.",
      state.mode === "live"
        ? '<button class="button" data-action="jobs">Refresh activity</button>'
        : "",
    ) +
      `<section class="panel">${state.mode === "sample" ? '<div class="empty"><h2>A preview, without pretend activity.</h2><p>Connect a workspace to see real processing jobs, attempts, and outcomes.</p><button class="button primary" data-action="connect">Connect workspace</button></div>' : state.jobs.length ? state.jobs.map((j) => `<div class="activity-row"><div>${j.kind === "analyze" ? "Analyze document" : "Build snapshot"}<small>${date(j.created_at)} · ${j.attempts} ${j.attempts === 1 ? "attempt" : "attempts"} · <span class="mono">${esc(j.id.slice(0, 8))}</span></small></div><span class="pill ${j.state === "dead" ? "amber" : j.state === "succeeded" ? "" : "neutral"}">${esc(j.state)}</span></div>`).join("") : '<div class="empty"><h2>No processing jobs loaded.</h2><p>Refresh activity to load your workspace’s job history.</p></div>'}${state.jobsNext ? '<button class="button spaced" data-action="more-jobs">Load more jobs</button>' : ""}</section>`,
  );
}
function modal(title, subtitle, body) {
  dialogGeneration++;
  const d = $("#modal");
  d.innerHTML = `<div class="dialog-heading"><h2 id="modal-title">${title}</h2><button class="icon-button" data-close="modal" aria-label="Close dialog">${icon("close")}</button></div><p class="dialog-sub">${subtitle}</p>${body}`;
  d.showModal();
}
function connect() {
  modal(
    "Make it your workspace.",
    "Sign in with a tenant API key. An HttpOnly session keeps this workspace connected for up to eight hours; your API key is not saved in the browser.",
    `<form id="connect-form"><label class="form-label" for="api-key">Workspace API key</label><input id="api-key" type="password" autocomplete="off" spellcheck="false" placeholder="cip_…" required><p class="form-error" id="connect-error" role="alert"></p><div class="dialog-actions">${state.connected ? '<button type="button" class="button" data-action="disconnect">Disconnect</button>' : '<button type="button" class="button" data-action="sample">Preview samples</button>'}<button class="button primary">Connect ${icon("arrow")}</button></div></form><p class="dialog-sub spaced">Create a key with <code>cip create-tenant</code>. This local frontend connects to the API served from the same address.</p>`,
  );
  $("#connect-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const button = $("button.primary", e.target);
    button.disabled = true;
    const attempt = dialogGeneration;
    const headers = { "X-API-Key": $("#api-key").value.trim() };
    try {
      await api("/v1/session", { method: "POST", headers });
      let current;
      try {
        current = await api("/v1/snapshots/current");
      } catch (err) {
        if (err.title !== "no_published_snapshot") throw err;
        await api("/v1/jobs");
        current = null;
      }
      if (attempt !== dialogGeneration || !$("#modal").open) return;
      state.key = "";
      state.connected = true;
      state.mode = "live";
      state.sid = current?.snapshot_id || null;
      state.snapshot = current?.content || null;
      state.resolutions = current?.build_options?.resolutions || {};
      state.exclusions = current?.build_options?.exclusions || {};
      state.jobs = [];
      state.jobsNext = null;
      state.filter = "";
      state.status = "all";
      state.view = "agreements";
      location.hash = "agreements";
      state.epoch++;
      $("#modal").close();
      render();
      toast("Workspace connected.");
    } catch (err) {
      if (attempt === dialogGeneration && $("#connect-error"))
        $("#connect-error").textContent = err.message;
    } finally {
      button.disabled = false;
    }
  });
}
function upload() {
  if (state.mode === "sample") {
    connect();
    return;
  }
  modal(
    "Add a little more context.",
    "Upload supported TXT or DOCX files, or paste agreement text. Each file can be up to 512 KB.",
    `<div class="segmented"><button class="active" data-uploadtab="files">Upload files</button><button data-uploadtab="paste">Paste text</button></div><form id="upload-form"><div id="upload-fields"><label class="upload-zone">${icon("upload")}<strong>A home for your agreements.</strong><p>Choose one or more files<br>UTF-8 TXT · restricted DOCX</p><input type="file" id="files" accept=".txt,.docx" multiple required aria-label="Contract files"></label></div><p class="form-error" id="upload-error" role="alert"></p><div id="build-state"></div><div class="dialog-actions" id="upload-actions"><button type="button" class="button" data-action="load-samples">Load sample set</button><button class="button primary">Upload & build ${icon("arrow")}</button></div></form><p class="dialog-sub spaced">New results are previewed before publication. The current workspace stays unchanged until you publish.</p>`,
  );
  document.querySelectorAll("[data-uploadtab]").forEach((b) =>
    b.addEventListener("click", () => {
      document
        .querySelectorAll("[data-uploadtab]")
        .forEach((x) => x.classList.toggle("active", x === b));
      $("#upload-fields").innerHTML =
        b.dataset.uploadtab === "files"
          ? '<label class="upload-zone"><strong>Choose your agreements.</strong><input type="file" id="files" accept=".txt,.docx" multiple required aria-label="Contract files"></label>'
          : '<label class="form-label" for="paste-name">Document name</label><input id="paste-name" value="agreement.txt" required maxlength="100"><label class="form-label" for="paste-text">Agreement text</label><textarea id="paste-text" placeholder="Master Services Agreement: …" required></textarea>';
    }),
  );
  $("#upload-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const files = $("#files")
      ? Array.from($("#files").files)
      : [
          new File(
            [$("#paste-text").value],
            $("#paste-name").value.replace(/\.txt$/i, "") + ".txt",
            { type: "text/plain" },
          ),
        ];
    await buildDocuments(files);
  });
}
function lockBuild(busy) {
  state.busy = busy;
  $("#modal")
    .querySelectorAll("button,input,textarea")
    .forEach((b) => (b.disabled = busy));
}
async function buildDocuments(files, resolutions = { ...state.resolutions }) {
  if (state.busy) return;
  const old = state.sid;
  let stage;
  try {
    if (files.some((f) => f.size > 512000))
      throw new Error("Each document must be no larger than 512 KB.");
    if (!files.length && !Object.keys(resolutions).length)
      throw new Error("Choose at least one document.");
    lockBuild(true);
    $("#upload-error").textContent = "";
    stage = $("#build-state");
    stage.innerHTML =
      '<div class="progress" role="status">Admitting your documents…</div>';
    for (const file of files) {
      const ext = file.name.split(".").at(-1).toLowerCase();
      if (!["txt", "docx"].includes(ext))
        throw new Error("Choose a TXT or DOCX file.");
      const media =
        ext === "txt"
          ? "text/plain"
          : "application/vnd.openxmlformats-officedocument.wordprocessingml.document";
      const form = new FormData();
      form.append("file", new Blob([file], { type: media }), file.name);
      await api("/v1/documents", { method: "POST", body: form });
    }
    const job = await api("/v1/snapshots", {
      method: "POST",
      body: JSON.stringify({ resolutions, exclusions: state.exclusions }),
    });
    stage.innerHTML =
      '<div class="progress" role="status">Building a versioned snapshot… Your published workspace is unchanged.</div>';
    const until = Date.now() + 120000;
    let sid;
    while (Date.now() < until) {
      await new Promise((r) => setTimeout(r, 1500));
      const status = await api("/v1/jobs/" + job.job_id);
      if (status.state === "dead")
        throw new Error(
          "The build could not finish. Inspect Activity for the processing job. Your current workspace is unchanged.",
        );
      if (status.state === "succeeded") {
        sid = status.result.snapshot_id;
        break;
      }
    }
    if (!sid)
      throw new Error(
        "The build is still processing. Check Activity, then refresh. Your admitted documents are retained; you can rebuild later.",
      );
    const built = await api("/v1/snapshots/" + sid);
    const conflicts = conflictCount(built.content);
    lockBuild(false);
    $("#upload-fields").hidden = true;
    $("#upload-actions").innerHTML =
      `<button type="button" class="button" data-close="modal">Keep current version</button><button type="button" class="button primary" id="publish">Publish snapshot ${icon("arrow")}</button>`;
    stage.innerHTML = `<div class="build-preview"><h3>Your new version is ready.</h3><p>${built.content.families.length} agreement families · ${Object.keys(built.content.source_hashes).length} sources · ${conflicts} review items</p>${conflicts ? '<div class="notice">This snapshot needs review. Publishing will withhold disputed answers and may reduce aggregate coverage.</div>' : "<p>Review checks complete. You choose when this becomes the current workspace.</p>"}</div>`;
    $("#publish").addEventListener("click", async () => {
      lockBuild(true);
      try {
        await api("/v1/snapshots/" + sid + "/promote", {
          method: "POST",
          body: JSON.stringify({ expected_active: old }),
        });
        state.sid = sid;
        state.snapshot = built.content;
        state.resolutions = resolutions;
        state.view = "agreements";
        location.hash = "agreements";
        state.epoch++;
        $("#modal").close();
        render();
        toast("New snapshot published.");
      } catch (e) {
        $("#upload-error").textContent =
          e.status === 409
            ? "The active snapshot changed. Close this preview and refresh before publishing again."
            : e.message;
      } finally {
        lockBuild(false);
      }
    });
  } catch (e) {
    $("#upload-error").textContent = e.message;
    if (stage) stage.innerHTML = "";
    lockBuild(false);
  }
}
async function resolve(id) {
  const conflict = state.snapshot.families
    .flatMap((f) => latest(f).conflicts)
    .find((c) => c.id === id);
  if (!conflict) return;
  modal(
    "Review the original change.",
    "Skipping an instrument’s operations on this section requires an explicit reviewer decision. It creates a new snapshot.",
    `<div class="source-code">${esc(state.snapshot.source_texts[conflict.source_id] || conflict.detail)}</div><div class="notice">All operations by this instrument on Section ${esc(conflict.target)} will be skipped. Other conflicts may remain. Historical snapshots are retained.</div><div class="dialog-actions"><button class="button" data-close="modal">Keep unresolved</button><button class="button primary" id="skip-change">Skip & preview</button></div>`,
  );
  $("#skip-change").addEventListener("click", async () => {
    if (state.mode === "sample") {
      toast("Connect a workspace to make review decisions.");
      return;
    }
    try {
      const review = await api("/v1/review");
      const match = review.conflicts.find((c) => c.id === id);
      if (!match)
        throw new Error("This review item changed. Refresh your workspace.");
      const resolutions = {
        ...state.resolutions,
        [match.skip_resolution_key]: "skip",
      };
      $("#modal").close();
      upload();
      await buildDocuments([], resolutions);
    } catch (e) {
      toast(e.message);
    }
  });
}
async function refresh() {
  if (state.mode === "sample") {
    toast(
      "You’re exploring synthetic sample data. Connect to use your own agreements.",
    );
    return;
  }
  try {
    const current = await api("/v1/snapshots/current");
    state.sid = current.snapshot_id;
    state.snapshot = current.content;
    state.resolutions = current.build_options?.resolutions || {};
    state.exclusions = current.build_options?.exclusions || {};
    state.epoch++;
    render();
    toast("Workspace updated.");
  } catch (e) {
    toast(e.message);
  }
}
async function jobs(more = false) {
  try {
    const result = await api(
      "/v1/jobs" +
        (more && state.jobsNext
          ? "?after=" + encodeURIComponent(state.jobsNext)
          : ""),
    );
    state.jobs = more ? [...state.jobs, ...result.jobs] : result.jobs;
    state.jobsNext = result.next;
    renderActivity();
  } catch (e) {
    toast(e.message);
  }
}
$("#modal").addEventListener("close", () => {
  dialogGeneration++;
});
$("#modal").addEventListener("cancel", (e) => {
  if (state.busy) e.preventDefault();
});
document.addEventListener("click", async (e) => {
  const b = e.target.closest("button");
  if (!b || b.disabled) return;
  if (b.dataset.close) {
    if (!state.busy) $("#" + b.dataset.close).close();
    return;
  }
  if (b.dataset.view) {
    navigate(b.dataset.view);
    if (b.dataset.view === "activity" && state.mode === "live") await jobs();
    return;
  }
  if (b.dataset.family) {
    state.family = b.dataset.family;
    state.version = null;
    navigate("detail");
    return;
  }
  if (b.dataset.version) {
    state.version = b.dataset.version;
    renderDetail();
    document.querySelectorAll("[data-version]").forEach((el) => {
      if (el.dataset.version === state.version) el.focus();
    });
    return;
  }
  const f = state.snapshot?.families.find((x) => x.id === state.family);
  const v = f?.versions.find((x) => x.as_of === state.version);
  if (b.dataset.fact) {
    const fact = v.facts[b.dataset.fact];
    evidence(
      labels[b.dataset.fact],
      fact.quote,
      fact.evidence,
      fact.reason,
      fact.quote_origin,
    );
    return;
  }
  if (b.dataset.clause) {
    const clause = v.clauses.find((x) => x.key === b.dataset.clause);
    evidence(
      "Section " + clause.key,
      clause.text,
      clause.evidence,
      clause.disputed ? "This clause is disputed." : null,
    );
    return;
  }
  if (b.dataset.result) {
    const r = state.searchResults[Number(b.dataset.result)];
    evidence("Original evidence", r.quote || r.text, r.evidence, null);
    return;
  }
  if (b.dataset.receipt) {
    try {
      const receipt = await api("/v1/receipts/" + b.dataset.receipt);
      modal(
        "A count you can trace.",
        "This saved receipt records the exact snapshot, predicate, coverage and result used at query time.",
        `<div class="source-code">${esc(JSON.stringify(receipt, null, 2))}</div>`,
      );
    } catch (e) {
      toast(e.message);
    }
    return;
  }
  if (b.dataset.resolve) {
    await resolve(b.dataset.resolve);
    return;
  }
  const action = b.dataset.action;
  if (action === "upload") upload();
  if (action === "connect") connect();
  if (action === "refresh") await refresh();
  if (action === "jobs") await jobs();
  if (action === "more-jobs") await jobs(true);
  if (action === "disconnect") {
    try {
      await api("/v1/session", { method: "DELETE" });
      state.connected = false;
      state.key = "";
      state.mode = "live";
      state.snapshot = null;
      state.sid = null;
      state.resolutions = {};
      state.exclusions = {};
      state.jobs = [];
      state.jobsNext = null;
      state.epoch++;
      $("#modal").close();
      render();
      toast("Workspace disconnected.");
    } catch (e) {
      toast(e.message);
    }
  }
  if (action === "sample") {
    if (!state.sample) {
      toast(
        "The sample preview is unavailable. You can still connect a live workspace.",
      );
      return;
    }
    state.key = "";
    state.mode = "sample";
    state.snapshot = state.sample.content;
    state.sid = "sample-preview";
    state.view = "agreements";
    location.hash = "agreements";
    state.resolutions = {};
    state.exclusions = {};
    state.jobs = [];
    state.jobsNext = null;
    state.epoch++;
    $("#modal").close();
    render();
  }
  if (action === "load-samples")
    await buildDocuments(
      state.sample.documents.map(
        (d) => new File([d.text], d.name, { type: "text/plain" }),
      ),
    );
});
$("#connection").addEventListener("click", connect);
$("#help").addEventListener("click", () =>
  modal(
    "Considered contracts. Clear evidence.",
    "Accord is a workspace for supported Master Services Agreements and their amendments.",
    `<div class="source-code">1. Connect a tenant key.\n2. Add TXT or supported DOCX agreements.\n3. Preview, then publish a snapshot.\n4. Explore terms and original source evidence.\n5. Review uncertainty before trusting results.</div><p class="dialog-sub">The sample workspace uses synthetic contracts. Extraction supports a documented sentence grammar; arbitrary legal documents and PDFs are outside this release’s scope.</p><a class="text-button" href="/docs" target="_blank" rel="noopener">Open API explorer ${icon("arrow")}</a>`,
  ),
);
window.addEventListener("hashchange", () => {
  const view = location.hash.slice(1);
  if (view.startsWith("agreement/")) {
    state.family = view.slice(10);
    state.view = "detail";
    render();
  } else if (
    ["agreements", "review", "search", "activity"].includes(view) &&
    view !== state.view
  )
    navigate(view);
});
async function init() {
  state.sample = await fetch("/sample.json")
    .then((r) => (r.ok ? r.json() : null))
    .catch(() => null);
  try {
    await api("/v1/session");
    state.connected = true;
    let current;
    try {
      current = await api("/v1/snapshots/current");
    } catch (e) {
      if (e.title !== "no_published_snapshot") throw e;
    }
    state.snapshot = current?.content || null;
    state.sid = current?.snapshot_id || null;
    state.resolutions = current?.build_options?.resolutions || {};
    state.exclusions = current?.build_options?.exclusions || {};
    const view = location.hash.slice(1);
    if (["agreements", "review", "search", "activity"].includes(view))
      state.view = view;
    else if (view.startsWith("agreement/")) {
      state.view = "detail";
      state.family = view.slice(10);
    }
    render();
    if (state.view === "activity") await jobs();
  } catch (e) {
    state.connected = false;
    render();
    if (e.status !== 401)
      toast(
        "The live workspace is unavailable. Check the backend services and reconnect.",
      );
  }
}
init();
