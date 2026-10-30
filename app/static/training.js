/* Training view: overview, detections across sessions, comparison, drill library, practice history and history.
   Every figure comes from saved reports through /api/analytics, /api/history and /api/drill-practice.
   Text is always written with textContent, never as HTML.

   Each area of the view has its own request counter. A response that arrives after a newer request
   for the same area is ignored, so a slow older request cannot overwrite the filters on screen. */

const HISTORY_PAGE_SIZE = 25;
const CHANGE_NOTE_LABEL = {
  earlier_only: "Earlier only",
  newer_only: "Newer only",
};
const STATE_LABELS = {
  detected: "Detected",
  not_detected: "Not detected",
  not_judged: "Not assessed",
  unknown: "Coverage unknown",
};

const trainingState = {
  page: 1,
  selected: [],
  categoriesLoaded: false,
  disciplinesLoaded: false,
  seq: { refresh: 0, issues: 0, history: 0, compare: 0, drills: 0 },
};

function nextRequest(name) {
  trainingState.seq[name] += 1;
  return trainingState.seq[name];
}

function isCurrent(name, token) {
  return token === trainingState.seq[name];
}

function trainingFilters() {
  return {
    discipline: $("filterDiscipline").value,
    start: $("filterStart").value,
    end: $("filterEnd").value,
  };
}

function queryString(values) {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(values)) {
    if (value !== "" && value !== null && value !== undefined && value !== false) params.set(key, value);
  }
  return params.toString();
}

function sessionDate(iso) {
  return new Date(iso).toLocaleString([], { dateStyle: "medium", timeStyle: "short" });
}

function reportError(err) {
  const status = $("trainingStatus");
  status.textContent = err.message;
  status.dataset.state = "error";
}

function clearError() {
  const status = $("trainingStatus");
  status.textContent = "";
  delete status.dataset.state;
}

function statusBadge(item) {
  const node = el("span", "badge", item.status_label || item.category_status_label || "");
  node.dataset.status = item.status || item.category_status;
  return node;
}

function emptyNote(box, text) {
  box.replaceChildren(el("p", "hint empty", text));
}

function showView(name) {
  const training = name === "training";
  $("trainingView").hidden = !training;
  document.querySelector("main.layout").hidden = training;
  $("viewFilm").setAttribute("aria-pressed", String(!training));
  $("viewTraining").setAttribute("aria-pressed", String(training));
  if (training) {
    ensureFilterOptions();
    refreshTraining();
  }
}

function ensureFilterOptions() {
  if (trainingState.disciplinesLoaded) return;
  const select = $("filterDiscipline");
  for (const option of $("discipline").options) {
    if (option.value) select.append(new Option(option.textContent, option.value));
  }
  trainingState.disciplinesLoaded = true;
}

function fillCategories(categories) {
  if (trainingState.categoriesLoaded) return;
  for (const id of ["recurringCategory", "historyCategory"]) {
    const select = $(id);
    select.append(new Option("All categories", ""));
    for (const category of categories) select.append(new Option(category, category));
  }
  trainingState.categoriesLoaded = true;
}

/* ---------- loading ---------- */

async function refreshTraining() {
  const token = nextRequest("refresh");
  $("trainingStatus").textContent = "Loading saved sessions…";
  clearError();
  const filters = queryString(trainingFilters());
  try {
    const [dashboard, issues, drills, practice] = await Promise.all([
      api(`/api/analytics/dashboard?${filters}`),
      fetchIssues(),
      api(`/api/analytics/drills?${filters}`),
      api("/api/drill-practice"),
    ]);
    if (!isCurrent("refresh", token)) return;
    $("trainingStatus").textContent = "";
    renderOverview(dashboard);
    renderNeedsReview(dashboard.areas_needing_review);
    renderRules(dashboard.rules);
    renderIssues(issues);
    renderRecent(dashboard.recent);
    renderDrills(drills);
    renderPractice(practice);
    renderSelection();
  } catch (err) {
    if (isCurrent("refresh", token)) reportError(err);
    return;
  }
  loadHistory();
}

async function fetchIssues() {
  const data = await api(`/api/analytics/issues?${queryString({
    ...trainingFilters(),
    category: $("recurringCategory").value,
  })}`);
  fillCategories(data.categories);
  return data.issues;
}

async function refreshIssues() {
  const token = nextRequest("issues");
  try {
    const issues = await fetchIssues();
    if (isCurrent("issues", token)) renderIssues(issues);
  } catch (err) {
    if (isCurrent("issues", token)) reportError(err);
  }
}

async function loadHistory() {
  const token = nextRequest("history");
  try {
    const result = await api(`/api/history?${queryString({
      ...trainingFilters(),
      q: $("historyQuery").value.trim(),
      drill: $("historyDrill").value.trim(),
      category: $("historyCategory").value,
      recurring: $("historyRecurring").checked ? "1" : "",
      page: trainingState.page,
    })}`);
    if (isCurrent("history", token)) renderHistory(result);
  } catch (err) {
    if (isCurrent("history", token)) reportError(err);
  }
}

async function refreshDrills() {
  const token = nextRequest("drills");
  try {
    const [drills, practice] = await Promise.all([
      api(`/api/analytics/drills?${queryString(trainingFilters())}`),
      api("/api/drill-practice"),
    ]);
    if (!isCurrent("drills", token)) return;
    renderDrills(drills);
    renderPractice(practice);
  } catch (err) {
    if (isCurrent("drills", token)) reportError(err);
  }
}

/* ---------- rendering ---------- */

function renderOverview(data) {
  const totals = $("overviewTotals");
  const bars = $("overviewBars");
  bars.replaceChildren();
  if (!data.sessions) {
    totals.textContent = "";
    emptyNote(bars, "No saved sessions match these filters. Save a review and it will appear here.");
    return;
  }
  const span = data.first_day === data.last_day ? data.first_day : `${data.first_day} to ${data.last_day}`;
  totals.textContent = `${data.sessions} saved ${data.sessions === 1 ? "session" : "sessions"} · ${span}`;
  const largest = Math.max(...data.by_discipline.map((row) => row.sessions));
  for (const row of data.by_discipline) {
    const line = el("div", "bar-row");
    const track = el("span", "bar-track");
    track.setAttribute("role", "img");
    track.setAttribute("aria-label", `${row.label}: ${row.sessions} of ${data.sessions} sessions`);
    const fill = el("span", "bar-fill");
    fill.style.width = `${Math.round((row.sessions / largest) * 100)}%`;
    track.append(fill);
    line.append(el("span", "bar-label", row.label), track, el("span", "bar-value", `${row.sessions}`));
    bars.append(line);
  }
  if (data.sessions_without_coverage) {
    const count = data.sessions_without_coverage;
    bars.append(el("p", "hint", `${count} ${count === 1 ? "session was" : "sessions were"} saved before assessed areas were recorded. For ${count === 1 ? "it" : "them"}, "not detected" is unknown.`));
  }
}

function renderRules(rules) {
  const list = $("countingRules");
  list.replaceChildren(...rules.map((rule) => el("li", "", rule)));
}

function issueCard(item) {
  const card = el("article", "issue");
  const head = el("div", "issue-head");
  head.append(el("span", "chip discipline", item.discipline_label), el("strong", "issue-title", item.category), statusBadge(item));
  const judged = item.sessions_judged
    ? `Detected in ${item.sessions_detected} of ${item.sessions_judged} sessions that assessed this area`
    : `Detected in ${item.sessions_detected} ${item.sessions_detected === 1 ? "session" : "sessions"}`;
  card.append(head, el("p", "issue-counts", judged));
  if (item.observed_sessions !== undefined && item.sessions_detected) {
    const others = item.sessions_detected - item.observed_sessions;
    const inferred = others ? `, and ${others} ${others === 1 ? "is" : "are"} inferred` : "";
    card.append(el("p", "hint", `Observed in ${item.observed_sessions} of ${item.sessions_detected} detected sessions${inferred}.`));
  }
  card.append(el("p", "hint", item.note));
  return card;
}

function renderNeedsReview(items) {
  const box = $("needsReview");
  box.replaceChildren();
  if (!items.length) {
    emptyNote(box, "Nothing detected in two or more sessions yet. Each category needs detections in at least two saved sessions to appear here.");
    return;
  }
  for (const item of items) box.append(issueCard(item));
}

function renderIssues(issues) {
  const box = $("recurring");
  box.replaceChildren();
  if (!issues.length) {
    emptyNote(box, "No technique categories match these filters. Change the sport or date range, or save more reviews.");
    return;
  }
  for (const item of issues) box.append(issueCard(item));
}

function sessionRow(item, compare) {
  const row = el("div", "session-row");
  const top = el("div", "session-head");
  top.append(el("span", "chip discipline", item.discipline_label), el("span", "when", sessionDate(item.created_at)));
  if (item.frame_count) top.append(el("span", "when", `${item.frame_count} frames`));
  if (item.video_available) top.append(el("span", "chip", "Clip stored"));
  row.append(top);
  const detail = [item.athlete_description ? `Athlete: ${item.athlete_description}` : "", item.context || ""]
    .filter(Boolean).join(" · ");
  if (detail) row.append(el("p", "hint", detail));

  const actions = el("div", "report-actions");
  const open = el("button", "btn btn-ghost btn-sm", "Open report");
  open.type = "button";
  open.setAttribute("aria-label", `Open report from ${sessionDate(item.created_at)}`);
  open.addEventListener("click", () => openReport(item.id));
  actions.append(open);
  if (item.video_available && item.video_id) {
    const clip = el("button", "btn btn-ghost btn-sm", "Open clip");
    clip.type = "button";
    clip.addEventListener("click", () => {
      showView("film");
      loadVideoIntoPlayer({ id: item.video_id, displayName: "Stored clip", expiresAt: null });
    });
    actions.append(clip);
  }
  if (compare) {
    const label = el("label", "check");
    const box = el("input");
    box.type = "checkbox";
    box.dataset.compareId = String(item.id);
    box.setAttribute("aria-label", `Compare the report from ${sessionDate(item.created_at)}`);
    box.checked = trainingState.selected.includes(item.id);
    box.addEventListener("change", () => toggleSelected(item.id, box.checked));
    label.append(box, " Compare");
    actions.append(label);
  }
  row.append(actions);
  return row;
}

function renderRecent(items) {
  const box = $("recent");
  box.replaceChildren();
  if (!items.length) {
    emptyNote(box, "No saved sessions yet. Reports you save appear here.");
    return;
  }
  for (const item of items) box.append(sessionRow(item, true));
}

async function openReport(reviewId) {
  try {
    const review = await api(`/api/reviews/${reviewId}`);
    showView("film");
    const link = review.video_available && review.video_id
      ? { id: review.video_id, displayName: "Stored clip", expiresAt: null }
      : null;
    $("empty").hidden = true;
    $("result").hidden = false;
    $("resultSource").textContent = link ? "Stored clip" : "Uploaded clip";
    $("resultMeta").textContent = `${review.discipline_label} · ${review.frame_count} frames · ${sessionDate(review.created_at)}`;
    const cards = $("resultCards");
    if (review.report) {
      renderReport(cards, review.report, link);
    } else {
      renderLegacy(cards, review.feedback);
    }
    $("result").scrollIntoView({ behavior: reduceMotion ? "auto" : "smooth", block: "start" });
    $("result").focus({ preventScroll: true });
  } catch (err) {
    showNotice(err.message);
  }
}

function toggleSelected(reviewId, checked) {
  const others = trainingState.selected.filter((id) => id !== reviewId);
  trainingState.selected = checked ? [...others, reviewId].slice(-2) : others;
  renderSelection();
}

function renderSelection() {
  for (const box of document.querySelectorAll("[data-compare-id]")) {
    box.checked = trainingState.selected.includes(Number(box.dataset.compareId));
  }
  const out = $("compareOut");
  if (trainingState.selected.length < 2) {
    out.replaceChildren(el("p", "hint empty", `Select two sessions from the same sport to compare. ${trainingState.selected.length} of 2 selected.`));
    return;
  }
  const [first, second] = trainingState.selected;
  const token = nextRequest("compare");
  out.replaceChildren(el("p", "hint", "Comparing the two reports…"));
  api(`/api/analytics/compare?${queryString({ first, second })}`)
    .then((data) => {
      if (isCurrent("compare", token)) renderCompare(data);
    })
    .catch((err) => {
      if (isCurrent("compare", token)) out.replaceChildren(el("p", "notice-inline", err.message));
    });
}

function sessionSummary(label, session) {
  const box = el("div", "compare-session");
  box.append(el("p", "eyebrow", label), el("strong", "", sessionDate(session.created_at)));
  const facts = [`${session.frame_count} frames`];
  if (session.sampled_span_seconds !== null) facts.push(`sampled over ${formatClock(session.sampled_span_seconds)}`);
  if (session.video_seconds !== null) facts.push(`clip ${formatClock(session.video_seconds)}`);
  box.append(el("p", "hint", facts.join(" · ")));
  box.append(el("p", "hint", `Evidence: ${session.evidence_status}. Athlete: ${session.athlete_status}${session.athlete_description ? `, "${session.athlete_description}"` : ""}.`));
  const actions = el("div", "report-actions");
  const open = el("button", "btn btn-ghost btn-sm", `Open ${label.toLowerCase()} report`);
  open.type = "button";
  open.addEventListener("click", () => openReport(session.id));
  actions.append(open);
  if (session.video_available && session.video_id) {
    const clip = el("button", "btn btn-ghost btn-sm", `Open ${label.toLowerCase()} clip`);
    clip.type = "button";
    clip.addEventListener("click", () => {
      showView("film");
      loadVideoIntoPlayer({ id: session.video_id, displayName: "Stored clip", expiresAt: null });
    });
    actions.append(clip);
  }
  box.append(actions);
  return box;
}

function frameChip(session, frameNumber, link) {
  const time = session.frame_times[frameNumber - 1];
  if (time === undefined) return el("span", "chip", `Frame ${frameNumber}`);
  const label = `Frame ${frameNumber} · ${formatClock(time)}`;
  if (!link) return el("span", "chip", label);
  const button = el("button", "ref-link", label);
  button.type = "button";
  button.addEventListener("click", () => {
    showView("film");
    loadVideoIntoPlayer(link, time);
  });
  return button;
}

function sideDetail(label, state, side, session) {
  const box = el("div", "compare-side");
  box.append(el("p", "eyebrow", label));
  box.append(el("span", `badge state-${state}`, STATE_LABELS[state] || state));
  if (state === "not_detected") {
    box.append(el("p", "hint", "Assessed in this session. Nothing detected in its frames."));
  }
  const link = session.video_available && session.video_id
    ? { id: session.video_id, displayName: "Stored clip", expiresAt: null }
    : null;
  for (const item of side.findings) {
    const finding = el("div", "compare-finding");
    finding.append(el("p", "", item.finding));
    if (item.fix) finding.append(el("p", "hint", `Fix: ${item.fix}`));
    const frames = el("div", "frame-refs");
    for (const number of item.frames) frames.append(frameChip(session, number, link));
    finding.append(frames);
    box.append(finding);
  }
  if (side.drills.length) {
    box.append(el("p", "hint", `Recommended: ${side.drills.map((d) => d.name).join(", ")}`));
  }
  return box;
}

function compareGroup(title, entries, data) {
  const section = el("section", "compare-group");
  section.append(el("h3", "compare-group-title", `${title} · ${entries.length}`));
  for (const entry of entries) {
    const card = el("article", "compare-card");
    const head = el("div", "issue-head");
    head.append(el("strong", "issue-title", entry.category), el("span", "badge", entry.change_label));
    head.append(el("span", `badge ${entry.reliable ? "reliable" : "caution"}`, entry.reliable ? "Comparable" : "Interpret with care"));
    card.append(head, el("p", "hint", entry.change_note));
    if (entry.caveats.length) {
      const list = el("ul", "plain-list caveats");
      for (const text of entry.caveats) list.append(el("li", "", text));
      card.append(list);
    }
    const grid = el("div", "compare-grid");
    grid.append(
      sideDetail("Earlier", entry.earlier.state, entry.earlier, data.earlier),
      sideDetail("Newer", entry.newer.state, entry.newer, data.newer),
    );
    card.append(grid);
    if (entry.drills_changed) card.append(el("p", "hint", "The recommended drills differ between the two reports."));
    section.append(card);
  }
  return section;
}

function renderCompare(data) {
  const out = $("compareOut");
  out.replaceChildren();
  const head = el("div", "compare-head");
  head.append(sessionSummary("Earlier", data.earlier), sessionSummary("Newer", data.newer));
  out.append(head);
  for (const warning of data.warnings) out.append(el("p", "notice-inline", warning));
  out.append(el("p", "hint", data.note));
  const limits = el("ul", "plain-list limits");
  for (const text of data.limits) limits.append(el("li", "", text));
  out.append(limits);
  if (!data.categories.length) {
    out.append(el("p", "hint empty", "Neither report assessed or detected an area, so there is nothing to compare."));
    return;
  }
  const groups = [
    ["Detected in both", (e) => e.change === "in_both"],
    ["Detected only in the earlier session", (e) => e.change === "earlier_only"],
    ["Detected only in the newer session", (e) => e.change === "newer_only"],
    ["Assessed in both, not detected", (e) => e.change === "not_detected_both"],
    ["Cannot be compared", (e) => e.change === "cannot_compare"],
  ];
  for (const [title, test] of groups) {
    const entries = data.categories.filter(test);
    if (entries.length) out.append(compareGroup(title, entries, data));
  }
}

function drillCard(drill) {
  const card = el("article", "drill-card");
  const head = el("div", "issue-head");
  head.append(el("strong", "issue-title", drill.name), statusBadge({
    category_status: drill.category_status,
    category_status_label: drill.category_status_label,
  }));
  card.append(head);
  card.append(el("p", "hint", `${drill.discipline_label} · ${drill.category} · recommended in ${drill.sessions} ${drill.sessions === 1 ? "session" : "sessions"}, last on ${drill.last_recommended}`));
  if (drill.linked_findings.length) {
    const list = el("ul", "plain-list");
    for (const text of drill.linked_findings) list.append(el("li", "", text));
    card.append(el("p", "eyebrow", "Detected findings it addresses"), list);
  }
  const details = el("details", "drill-details");
  details.append(el("summary", "", "Instructions"));
  details.append(el("p", "", drill.objective), el("p", "", drill.instructions), el("p", "hint", drill.reps_or_duration));
  card.append(details);

  const practice = el("div", "practice");
  const last = drill.practice.last ? `Last logged ${drill.practice.last}` : "Not logged yet";
  practice.append(el("p", "hint", `${drill.practice.count} practice ${drill.practice.count === 1 ? "day" : "days"} logged · ${last}`));
  practice.append(practiceForm(drill.drill_key, drill.name));
  card.append(practice);
  return card;
}

function practiceForm(drillKey, drillName) {
  const form = el("form", "practice-form");
  const date = el("input", "text-input");
  date.type = "date";
  date.max = new Date().toISOString().slice(0, 10);
  date.value = date.max;
  date.setAttribute("aria-label", "Practice date");
  const note = el("input", "text-input");
  note.type = "text";
  note.maxLength = 300;
  note.placeholder = "Optional note";
  note.setAttribute("aria-label", "Practice note");
  const submit = el("button", "btn btn-ghost btn-sm", "Log practice day");
  submit.type = "submit";
  submit.setAttribute("aria-label", `Log practice day for ${drillName}`);
  form.append(date, note, submit);
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      await api("/api/drill-practice", { drill_key: drillKey, practiced_on: date.value, note: note.value.trim() });
      note.value = "";
      await refreshDrills();
    } catch (err) {
      reportError(err);
    }
  });
  return form;
}

function renderDrills(data) {
  const box = $("drills");
  box.replaceChildren();
  box.append(el("p", "hint", data.note));
  if (!data.drills.length) {
    emptyNote(box, "No drills yet. A drill appears here when a saved report in the current filters recommends it.");
    return;
  }
  for (const drill of data.drills) box.append(drillCard(drill));
}

function renderPractice(data) {
  const box = $("practiceLog");
  box.replaceChildren();
  $("practiceCount").textContent = data.total ? `${data.total} logged` : "";
  if (!data.entries.length) {
    emptyNote(box, "No practice days logged yet. Log one from a drill above.");
    return;
  }
  for (const entry of data.entries) {
    const row = el("div", "practice-entry");
    row.append(el("span", "when", entry.practiced_on), el("strong", "", entry.drill_name || "Drill"));
    if (!entry.in_library) row.append(el("span", "chip", "Not in a saved report now"));
    if (entry.note) row.append(el("span", "note-text", entry.note));
    const remove = el("button", "btn btn-ghost btn-sm", "Remove");
    remove.type = "button";
    remove.setAttribute("aria-label", `Remove practice day ${entry.practiced_on} for ${entry.drill_name}`);
    remove.addEventListener("click", () => removePractice(entry.id, entry.practiced_on));
    row.append(remove);
    box.append(row);
  }
  box.append(el("p", "hint", data.note));
}

async function removePractice(practiceId, practicedOn) {
  const confirmed = await confirmDialog({
    title: "Remove this practice day?",
    body: `The practice day ${practicedOn} is removed from your log. Saved reports are not changed.`,
    confirmLabel: "Remove practice day",
  });
  if (!confirmed) return;
  try {
    await api(`/api/drill-practice/${practiceId}`, undefined, "DELETE");
    await refreshDrills();
  } catch (err) {
    reportError(err);
  }
}

function renderHistory(result) {
  const box = $("historyList");
  box.replaceChildren();
  if (!result.items.length) {
    emptyNote(box, result.total === 0 ? "No saved sessions match these filters." : "No sessions on this page.");
  }
  for (const item of result.items) {
    const row = sessionRow(item, true);
    const remove = el("button", "btn btn-ghost btn-sm", "Delete report");
    remove.type = "button";
    remove.setAttribute("aria-label", `Delete report from ${sessionDate(item.created_at)}`);
    remove.addEventListener("click", async () => {
      await deleteReport(item.id, item.created_at);
      refreshTraining();
    });
    row.querySelector(".report-actions").append(remove);
    box.append(row);
  }
  const pages = Math.max(1, Math.ceil(result.total / result.page_size));
  trainingState.page = result.page;
  $("pageInfo").textContent = `Page ${result.page} of ${pages} · ${result.total} ${result.total === 1 ? "session" : "sessions"}`;
  $("prevPage").disabled = result.page <= 1;
  $("nextPage").disabled = result.page >= pages;
}

/* ---------- events ---------- */

$("viewFilm").addEventListener("click", () => showView("film"));
$("viewTraining").addEventListener("click", () => showView("training"));
$("trainingFilters").addEventListener("submit", (event) => {
  event.preventDefault();
  trainingState.page = 1;
  refreshTraining();
});
$("recurringCategory").addEventListener("change", refreshIssues);
$("historySearch").addEventListener("submit", (event) => {
  event.preventDefault();
  trainingState.page = 1;
  loadHistory();
});
$("historyCategory").addEventListener("change", () => {
  trainingState.page = 1;
  loadHistory();
});
$("historyRecurring").addEventListener("change", () => {
  trainingState.page = 1;
  loadHistory();
});
$("prevPage").addEventListener("click", () => {
  trainingState.page = Math.max(1, trainingState.page - 1);
  loadHistory();
});
$("nextPage").addEventListener("click", () => {
  trainingState.page += 1;
  loadHistory();
});
