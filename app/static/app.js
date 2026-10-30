const LIVE_INTERVAL_MS = 8000;
const LIVE_WINDOW = 3;
const LIVE_MAX_CUES = 60;
const LIVE_MAX_SAMPLES = 90;
const LIVE_MAX_MS = 10 * 60 * 1000;
const FRAME_SIDE = 768;
const DEFAULT_CONFIG = { maxFrames: 16, minFrames: 4, frameIntervalSeconds: 8, disciplines: {}, retentionDays: 7 };
const LEGACY_AREA_LABELS = {
  guard_defense: "Guard and defense",
  stance_balance: "Stance and balance",
  footwork_distance: "Footwork and distance",
  offense: "Offense",
  defensive_reaction: "Defensive reactions",
};
const CONFIDENCE_LABELS = { observed: "Observed", inferred: "Inferred", uncertain: "Uncertain" };
const EVIDENCE_TEXT = { limited: "Limited evidence", insufficient: "Insufficient evidence" };
const ICONS = {
  observe:
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M2 12s3.5-6.5 10-6.5S22 12 22 12s-3.5 6.5-10 6.5S2 12 2 12z"/><circle cx="12" cy="12" r="3"/></svg>',
  work:
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="12" r="8.5"/><circle cx="12" cy="12" r="4.5"/><circle cx="12" cy="12" r="0.8" fill="currentColor"/></svg>',
  caveat:
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="12" r="9"/><path d="M12 11v5"/><circle cx="12" cy="7.9" r="0.6" fill="currentColor"/></svg>',
};
const SECTION_KINDS = {
  "observations": "observe",
  "areas to work on": "work",
  "sampling caveat": "caveat",
};
const reduceMotion = matchMedia("(prefers-reduced-motion: reduce)").matches;

const $ = (id) => document.getElementById(id);

const state = {
  config: DEFAULT_CONFIG,
  discipline: localStorage.getItem("filmroom-discipline") || "",
  clipFrames: [],
  clipTimes: [],
  lastReport: null,
  video: null,
  live: { stream: null, timer: null, samples: [], cues: 0, cuePending: false, startedAt: 0 },
};

const isMobile =
  /Android|iPhone|iPad|iPod|Mobile/i.test(navigator.userAgent) ||
  (navigator.maxTouchPoints > 1 && matchMedia("(pointer: coarse)").matches);

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function areaLabel(discipline, area) {
  const spec = state.config.disciplines[discipline];
  return (spec && spec.areas[area]) || LEGACY_AREA_LABELS[area] || area;
}

function disciplineLabel(key) {
  const spec = state.config.disciplines[key];
  return spec ? spec.label : "Unspecified";
}

function setStatus(stateName, text) {
  $("statusPill").dataset.state = stateName;
  $("statusText").textContent = text;
}

function showNotice(message) {
  $("notice").textContent = message;
  $("notice").hidden = !message;
}

function formatClock(seconds) {
  const whole = Math.floor(seconds);
  return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, "0")}`;
}

function formatPrecise(seconds) {
  const minutes = Math.floor(seconds / 60);
  const rest = (seconds - minutes * 60).toFixed(1).padStart(4, "0");
  return `${String(minutes).padStart(2, "0")}:${rest}`;
}

function formatMegabytes(bytes) {
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function formatDate(iso) {
  return new Date(iso).toLocaleDateString([], { month: "short", day: "numeric" });
}

/* ---------- session and API ---------- */

function showLogin(message = "") {
  $("appShell").hidden = true;
  $("login").hidden = false;
  $("loginError").hidden = !message;
  $("loginError").textContent = message;
  $("tokenInput").value = "";
  $("tokenInput").focus();
}

function showApp() {
  $("login").hidden = true;
  $("appShell").hidden = false;
}

async function api(path, body, method) {
  const headers = { "X-Film-Room": "1" };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const verb = method || (body !== undefined ? "POST" : "GET");
  const res = await fetch(path, {
    method: verb,
    headers,
    credentials: "same-origin",
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (res.status === 401) {
    showLogin("Your session ended. Enter the access code to continue.");
    throw new Error("Log in to continue.");
  }
  if (!res.ok) throw new Error(data.error || `Request failed (${res.status}).`);
  return data;
}

$("loginForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  $("loginError").hidden = true;
  const code = $("tokenInput").value;
  const res = await fetch("/api/login", {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Film-Room": "1" },
    credentials: "same-origin",
    body: JSON.stringify({ code }),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    $("loginError").textContent = data.error || "Could not log in.";
    $("loginError").hidden = false;
    $("tokenInput").select();
    return;
  }
  enterApp();
});

$("logoutButton").addEventListener("click", async () => {
  await fetch("/api/logout", { method: "POST", headers: { "X-Film-Room": "1" }, credentials: "same-origin" });
  unloadPlayer();
  showLogin();
});

async function enterApp() {
  showApp();
  await loadConfig();
  loadVideos();
  loadHistory();
  setMode("upload");
  setStatus("idle", "Ready");
}

async function loadConfig() {
  try {
    state.config = { ...DEFAULT_CONFIG, ...(await api("/api/config")) };
  } catch {
    state.config = DEFAULT_CONFIG;
  }
  document.querySelectorAll(".retention-inline").forEach((node) => {
    node.textContent = String(state.config.retentionDays);
  });
  populateDisciplines();
}

function populateDisciplines() {
  const select = $("discipline");
  const keep = state.discipline;
  select.replaceChildren();
  select.append(new Option("Choose a discipline", ""));
  for (const [key, spec] of Object.entries(state.config.disciplines)) {
    select.append(new Option(spec.label, key));
  }
  state.discipline = keep && state.config.disciplines[keep] ? keep : "";
  select.value = state.discipline;
  updateControls();
}

$("discipline").addEventListener("change", (event) => {
  state.discipline = event.target.value;
  localStorage.setItem("filmroom-discipline", state.discipline);
  updateControls();
});

function updateControls() {
  const hasDiscipline = Boolean(state.discipline);
  $("reviewClip").disabled = !(hasDiscipline && state.clipFrames.length > 0);
  $("liveStart").disabled = !hasDiscipline || Boolean(state.live.stream);
  $("disciplineHint").textContent = hasDiscipline
    ? `Feedback will use the ${disciplineLabel(state.discipline)} criteria.`
    : "Pick the sport the footage shows. Feedback is written against that sport only.";
}

/* Adaptive sampling: one frame per interval of clip length, clamped to the configured
   floor and cap. Frames sit at the middle of each slice, so the first and last frames
   are not at the exact edges of the clip. */
function planSampling(duration, config = state.config) {
  const count = Math.min(
    config.maxFrames,
    Math.max(config.minFrames, Math.ceil(duration / config.frameIntervalSeconds))
  );
  const times = Array.from({ length: count }, (_, i) => (duration * (i + 0.5)) / count);
  return { count, times, spacing: duration / count };
}

function setMode(mode) {
  document.querySelectorAll(".mode").forEach((button) => {
    button.setAttribute("aria-pressed", String(button.dataset.mode === mode));
  });
  $("panelUpload").hidden = mode !== "upload";
  $("panelLive").hidden = mode !== "live";
  if (mode !== "live") stopLive(false);
}

document.querySelectorAll(".mode").forEach((button) => {
  button.addEventListener("click", () => setMode(button.dataset.mode));
});

if (isMobile) {
  document.querySelector('.mode[data-mode="live"]').hidden = false;
} else {
  $("desktopNote").hidden = false;
}

/* ---------- player ---------- */

const player = $("player");

function syncLayout() {
  const open = !$("playerCard").hidden;
  document.querySelector(".layout").classList.toggle("has-player", open);
}

function unloadPlayer() {
  player.pause();
  player.removeAttribute("src");
  player.load();
  $("playerCard").hidden = true;
  state.video = null;
  syncLayout();
}

function loadVideoIntoPlayer(video, time = null) {
  const sameVideo = state.video && state.video.id === video.id;
  if (!sameVideo) {
    state.video = { ...video, pendingTime: time };
    player.src = `/api/videos/${video.id}/media`;
    player.load();
    $("playerName").textContent = video.displayName || video.name || "Clip";
    $("playerNote").textContent = video.expiresAt
      ? `Deleted automatically on ${formatDate(video.expiresAt)}.`
      : "";
    $("playerCard").hidden = false;
    syncLayout();
    if (window.innerWidth < 900) {
      $("playerCard").scrollIntoView({ behavior: reduceMotion ? "auto" : "smooth", block: "start" });
    }
  } else if (time !== null) {
    seekTo(time);
  }
}

function seekTo(time) {
  if (player.readyState >= 1) {
    player.currentTime = Math.min(time, player.duration || time);
  } else if (state.video) {
    state.video.pendingTime = time;
  }
  player.pause();
  markActive(time);
}

function markActive(time) {
  document.querySelectorAll(".ref-link").forEach((button) => {
    button.setAttribute("aria-pressed", String(Math.abs(Number(button.dataset.time) - time) < 0.05));
  });
}

function highlightSource(node) {
  document.querySelectorAll(".is-source").forEach((n) => n.classList.remove("is-source"));
  const target = node && node.closest(".item, .priority, .drill, .legacy");
  if (target) target.classList.add("is-source");
}

function openAtTime(video, time, source) {
  loadVideoIntoPlayer(video, time);
  if (state.video && state.video.id === video.id && player.readyState >= 1) seekTo(time);
  highlightSource(source || null);
}

player.addEventListener("loadedmetadata", () => {
  const total = player.duration || 0;
  $("seek").max = String(total);
  $("timeTotal").textContent = formatPrecise(total);
  if (state.video && state.video.pendingTime != null) {
    player.currentTime = Math.min(state.video.pendingTime, total);
    state.video.pendingTime = null;
    player.pause();
    markActive(player.currentTime);
  }
});

player.addEventListener("timeupdate", () => {
  $("seek").value = String(player.currentTime);
  $("timeNow").textContent = formatPrecise(player.currentTime);
});

player.addEventListener("play", () => {
  $("playPause").textContent = "Pause";
  $("playPause").setAttribute("aria-label", "Pause");
});

player.addEventListener("pause", () => {
  $("playPause").textContent = "Play";
  $("playPause").setAttribute("aria-label", "Play");
});

const PLAYBACK_ERRORS = {
  2: "The video could not be loaded. Check the connection and try again.",
  3: "Playback stopped while decoding this clip. Try another browser, or re-export it as H.264 and upload again.",
  4: "This browser cannot play this clip's codec. Re-export it as H.264 MP4 or H.264 MOV and upload again. Frame sampling and the report still work when the browser can decode the clip.",
};

player.addEventListener("error", () => {
  const code = player.error ? player.error.code : 0;
  $("playerNote").textContent = PLAYBACK_ERRORS[code] || "This clip could not be played in this browser.";
});

$("playPause").addEventListener("click", () => {
  if (player.paused) player.play().catch(() => {});
  else player.pause();
});
$("replay").addEventListener("click", () => {
  player.currentTime = 0;
  player.play().catch(() => {});
});
$("skipBack").addEventListener("click", () => {
  player.currentTime = Math.max(0, player.currentTime - 5);
});
$("skipForward").addEventListener("click", () => {
  player.currentTime = Math.min(player.duration || 0, player.currentTime + 5);
});
$("speed").addEventListener("change", (event) => {
  player.playbackRate = Number(event.target.value);
});
$("seek").addEventListener("input", (event) => {
  player.currentTime = Number(event.target.value);
});

$("deleteVideo").addEventListener("click", () => {
  if (!state.video) return;
  deleteVideo(state.video.id, state.video.displayName || "this video");
});

async function deleteVideo(videoId, name) {
  if (!window.confirm(`Delete ${name}? This removes the stored video. Reports stay in your history without playback.`)) {
    return;
  }
  try {
    await api(`/api/videos/${videoId}`, undefined, "DELETE");
  } catch (err) {
    showNotice(err.message);
    return;
  }
  if (state.video && state.video.id === videoId) unloadPlayer();
  showNotice("Video deleted. Its reports remain in history without playback.");
  loadVideos();
  loadHistory();
}

/* ---------- stored videos ---------- */

async function loadVideos() {
  try {
    const { videos } = await api("/api/videos");
    const box = $("videoList");
    box.replaceChildren();
    if (!videos.length) {
      box.append(el("p", "hint", "No kept videos. Clips you keep for playback appear here."));
      return;
    }
    for (const video of videos) {
      const row = el("div", "video-item");
      const info = el("div");
      info.append(
        el("p", "video-name", video.display_name),
        el("p", "video-meta", `${formatClock(video.duration_seconds)} · ${formatMegabytes(video.size_bytes)} · deletes ${formatDate(video.expires_at)}`)
      );
      const actions = el("div", "video-actions");
      const open = el("button", "btn btn-ghost btn-sm", "Open");
      open.type = "button";
      open.addEventListener("click", () => loadVideoIntoPlayer({
        id: video.id, displayName: video.display_name, expiresAt: video.expires_at,
      }));
      const remove = el("button", "btn btn-ghost btn-sm", "Delete");
      remove.type = "button";
      remove.addEventListener("click", () => deleteVideo(video.id, video.display_name));
      actions.append(open, remove);
      row.append(info, actions);
      box.append(row);
    }
  } catch (err) {
    showNotice(err.message);
  }
}

/* ---------- report rendering ---------- */

function frameRef(report, number, link) {
  const frame = report.frames.find((f) => f.number === number);
  const label = `F${String(number).padStart(2, "0")}`;
  if (!frame) return el("span", "ref", label);
  if (!link) return el("span", "ref", `${label} · ${frame.label}`);
  const button = el("button", "ref-link", `${label} · ${frame.label}`);
  button.type = "button";
  button.dataset.time = String(frame.time_seconds);
  button.setAttribute("aria-label", `Jump to ${frame.label} in the clip`);
  button.setAttribute("aria-pressed", "false");
  button.addEventListener("click", () => openAtTime(link, frame.time_seconds, button));
  return button;
}

function frameRefs(report, numbers, link) {
  const row = el("div", "frame-refs");
  for (const number of numbers) row.append(frameRef(report, number, link));
  return row;
}

function confidenceBadge(confidence) {
  return el("span", `badge badge-${confidence}`, CONFIDENCE_LABELS[confidence] || confidence);
}

function group(title, count, body, open = true) {
  const details = el("details", "group");
  details.open = open;
  const summary = el("summary", "group-summary");
  summary.append(el("span", "group-title", title), el("span", "group-count", String(count)));
  details.append(summary, body);
  return details;
}

function listNode(items) {
  const list = el("div", "group-body");
  items.forEach((node) => list.append(node));
  return list;
}

function evidenceBanner(report) {
  const status = report.evidence_status;
  if (!status || status === "sufficient") return null;
  const banner = el("div", `evidence evidence-${status}`);
  banner.append(el("strong", null, EVIDENCE_TEXT[status]), el("p", null, report.evidence_note || ""));
  return banner;
}

function athleteBanner(report, link) {
  const described = report.athlete_description;
  if (!described) return null;
  const identified = report.athlete_frames || [];
  const warned = Boolean(report.athlete_warning) || report.athlete_status !== "identified";
  const banner = el("div", warned ? "athlete-banner athlete-warning" : "athlete-banner athlete-identified");
  banner.append(el("strong", null, `Athlete: ${described}`));
  if (report.athlete_summary) {
    banner.append(el("p", null, report.athlete_summary));
  } else {
    banner.append(el("p", null, report.athlete_status === "identified"
      ? `Identified in ${identified.length} of ${report.frames.length} sampled frames.`
      : "The athlete could not be identified reliably in the sampled frames."));
  }
  if (identified.length) banner.append(frameRefs(report, identified, link));
  if (report.athlete_warning) banner.append(el("p", "athlete-caution", report.athlete_warning));
  if (report.athlete_method) banner.append(el("p", "hint", report.athlete_method));
  return banner;
}

function areaOrder(report) {
  const spec = state.config.disciplines[report.discipline];
  if (spec) return Object.keys(spec.areas);
  return Object.keys(LEGACY_AREA_LABELS);
}

function renderReport(container, report, link = null) {
  container.replaceChildren();

  const athlete = athleteBanner(report, link);
  if (athlete) container.append(athlete);

  const banner = evidenceBanner(report);
  if (banner) container.append(banner);

  container.append(el("p", "report-summary", report.summary));

  const priorities = el("div", "priorities");
  if (report.top_priorities.length) {
    report.top_priorities.forEach((item, index) => {
      const row = el("div", "priority");
      row.append(el("span", "priority-rank", String(index + 1)));
      const text = el("div", "priority-text");
      text.append(el("strong", null, item.title), el("p", null, item.reason));
      if (item.frames.length) text.append(frameRefs(report, item.frames, link));
      row.append(text);
      priorities.append(row);
    });
  } else {
    priorities.append(el("p", "hint", "No priorities are clearly supported by this footage."));
  }
  container.append(group("Top priorities", report.top_priorities.length, priorities));

  const observations = report.observations.map((item) => {
    const row = el("div", "item");
    const head = el("div", "item-head");
    head.append(confidenceBadge(item.confidence));
    row.append(head, el("p", null, item.description));
    if (item.frames.length) row.append(frameRefs(report, item.frames, link));
    return row;
  });
  container.append(group("Observations", observations.length, listNode(observations)));

  const byArea = {};
  for (const finding of report.technical_findings) {
    (byArea[finding.area] = byArea[finding.area] || []).push(finding);
  }
  const areaGroups = areaOrder(report)
    .filter((area) => byArea[area])
    .map((area) => {
      const body = byArea[area].map((item) => {
        const row = el("div", "item");
        const head = el("div", "item-head");
        head.append(confidenceBadge(item.confidence));
        row.append(head, el("p", null, item.finding));
        if (item.fix) row.append(el("p", "item-fix", `Fix: ${item.fix}`));
        if (item.frames.length) row.append(frameRefs(report, item.frames, link));
        return row;
      });
      return group(areaLabel(report.discipline, area), body.length, listNode(body), false);
    });
  const findings = el("div", "group-body");
  if (areaGroups.length) {
    areaGroups.forEach((node) => findings.append(node));
  } else {
    findings.append(el("p", "hint", "No technical findings are clearly supported by this footage."));
  }
  container.append(group("Technical review", report.technical_findings.length, findings));

  const drills = report.drills.map((drill) => {
    const card = el("article", "drill");
    card.append(el("p", "drill-area", `Addresses: ${areaLabel(report.discipline, drill.addresses)}`));
    card.append(el("h4", null, drill.name));
    card.append(el("p", null, drill.instructions));
    const meta = el("dl", "drill-meta");
    [["Objective", drill.objective], ["Reps or duration", drill.reps_or_duration]].forEach(([label, value]) => {
      const pair = el("div");
      pair.append(el("dt", null, label), el("dd", null, value));
      meta.append(pair);
    });
    card.append(meta);
    return card;
  });
  if (drills.length) {
    container.append(group("Corrective drills", drills.length, listNode(drills)));
  } else {
    container.append(group("Corrective drills", 0, el("p", "hint", "No drills, because no area is clearly supported by this footage.")));
  }

  container.append(el("p", "caveat", report.sampling_caveat));
}

function reportToText(report) {
  const lines = [`${disciplineLabel(report.discipline)} report`, report.summary, ""];
  if (report.athlete_status === "uncertain" || report.athlete_status === "not_found") {
    lines.push(`Athlete warning: ${report.athlete_note}`, "");
  }
  if (report.evidence_status && report.evidence_status !== "sufficient") {
    lines.push(`${EVIDENCE_TEXT[report.evidence_status]}: ${report.evidence_note}`, "");
  }
  if (report.top_priorities.length) {
    lines.push("Top priorities:");
    report.top_priorities.forEach((item, i) => lines.push(`${i + 1}. ${item.title}: ${item.reason}`));
    lines.push("");
  }
  if (report.observations.length) {
    lines.push("Observations:");
    report.observations.forEach((item) => lines.push(`- [${item.confidence}] ${item.description}`));
    lines.push("");
  }
  if (report.technical_findings.length) {
    lines.push("Technical review:");
    report.technical_findings.forEach((item) =>
      lines.push(`- ${areaLabel(report.discipline, item.area)}: ${item.finding} Fix: ${item.fix}`)
    );
    lines.push("");
  }
  if (report.drills.length) {
    lines.push("Corrective drills:");
    report.drills.forEach((item) =>
      lines.push(`- ${item.name} (${item.reps_or_duration}): ${item.instructions} Objective: ${item.objective}`)
    );
    lines.push("");
  }
  lines.push(`Sampling caveat: ${report.sampling_caveat}`);
  return lines.join("\n");
}

/* Reports saved before structured output have only plain text. */
function parseFeedback(text) {
  const labelLine = /^(Observations|Areas to work on|Sampling caveat)\s*:\s*(.*)$/i;
  const sections = [];
  let current = { kind: null, title: null, lines: [] };
  for (const raw of text.split("\n")) {
    const line = raw.trim();
    const match = line.match(labelLine);
    if (match) {
      if (current.title || current.lines.length) sections.push(current);
      current = {
        kind: SECTION_KINDS[match[1].toLowerCase()],
        title: match[1],
        lines: match[2] ? [match[2]] : [],
      };
    } else if (line) {
      current.lines.push(line);
    }
  }
  if (current.title || current.lines.length) sections.push(current);
  return sections.length ? sections : [{ kind: null, title: null, lines: [text.trim()] }];
}

function renderLegacy(container, text) {
  container.replaceChildren();
  parseFeedback(text).forEach((section) => {
    const block = el("div", "legacy");
    if (section.title) {
      const head = el("div", "section-head");
      const icon = el("span", "section-icon");
      icon.innerHTML = ICONS[section.kind] || "";
      head.append(icon, el("h4", null, section.title));
      block.append(head);
    }
    section.lines.forEach((line) => block.append(el("p", null, line)));
    container.append(block);
  });
}

function showSkeletons() {
  const container = $("resultCards");
  container.replaceChildren();
  for (let i = 0; i < 3; i++) container.append(el("div", "skeleton"));
}

/* ---------- reviews ---------- */

async function runReview(mode, frames, times, button, videoId) {
  showNotice("");
  if (button) button.disabled = true;
  setStatus("busy", "Reviewing");
  $("empty").hidden = true;
  $("result").hidden = false;
  $("copyReport").hidden = true;
  $("resultSource").textContent = mode === "live" ? "Live session" : (videoId ? "Stored clip" : "Uploaded clip");
  $("resultSource").classList.toggle("live", mode === "live");
  $("resultMeta").textContent = "Writing report...";
  showSkeletons();
  try {
    const data = await api("/api/review", {
      mode,
      discipline: state.discipline,
      frames,
      frameTimes: times,
      context: $("context").value.trim(),
      athleteDescription: $("athlete").value.trim(),
      videoId: videoId || null,
    });
    state.lastReport = data.report;
    state.lastReviewId = data.id;
    const link = videoId && state.storedVideo && state.storedVideo.id === videoId ? state.storedVideo : null;
    renderReport($("resultCards"), data.report, link);
    const reused = data.cached ? " · reused an earlier report, no new API call" : "";
    $("resultMeta").textContent =
      `${disciplineLabel(data.report.discipline)} · Review #${data.id} · ${frames.length} frames over ${formatClock(times[times.length - 1] || 0)}${reused}`;
    $("copyReport").hidden = false;
    $("result").scrollIntoView({ behavior: reduceMotion ? "auto" : "smooth", block: "start" });
    loadHistory();
  } catch (err) {
    $("result").hidden = true;
    $("empty").hidden = false;
    showNotice(err.message);
  } finally {
    if (button) button.disabled = false;
    setStatus("idle", "Ready");
    updateControls();
  }
}

$("copyReport").addEventListener("click", async () => {
  const button = $("copyReport");
  try {
    await navigator.clipboard.writeText(reportToText(state.lastReport));
    button.textContent = "Copied";
  } catch {
    button.textContent = "Copy failed";
  }
  setTimeout(() => { button.textContent = "Copy"; }, 1600);
});

/* ---------- upload ---------- */

const clipVideo = $("clipVideo");
const dropzone = $("dropzone");

function uploadToStorage(file) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/videos");
    xhr.setRequestHeader("X-Film-Room", "1");
    xhr.withCredentials = true;
    xhr.upload.onprogress = (event) => {
      if (!event.lengthComputable) return;
      const percent = Math.round((event.loaded / event.total) * 100);
      $("progressText").textContent = `Storing privately: ${percent}%`;
      $("progress").hidden = false;
      $("progressBar").style.width = `${percent}%`;
    };
    xhr.onload = () => {
      let data = {};
      try { data = JSON.parse(xhr.responseText); } catch { /* not JSON */ }
      if (xhr.status === 401) {
        showLogin("Your session ended. Enter the access code to continue.");
        reject(new Error("Log in again to store this video."));
        return;
      }
      if (xhr.status >= 200 && xhr.status < 300) resolve(data);
      else reject(new Error(data.error || `Upload failed (${xhr.status}).`));
    };
    xhr.onerror = () => reject(new Error("The upload was interrupted. Check the connection and try again."));
    const body = new FormData();
    body.append("file", file);
    xhr.send(body);
  });
}

async function loadClip(file) {
  state.clipFrames = [];
  state.clipTimes = [];
  $("strip").replaceChildren();
  $("progress").hidden = true;
  $("dropSub").textContent = file.name;
  showNotice("");
  unloadPlayer();
  state.video = null;
  updateControls();

  let storedId = null;
  let storeWarning = "";
  state.storedVideo = null;
  state.storedVideoId = null;
  if ($("keepVideo").checked) {
    setStatus("busy", "Storing video");
    try {
      const info = await uploadToStorage(file);
      storedId = info.id;
      state.storedVideo = { id: info.id, displayName: info.displayName, expiresAt: info.expiresAt };
      state.storedVideoId = info.id;
      loadVideoIntoPlayer(state.storedVideo);
      loadVideos();
    } catch (err) {
      storeWarning = `The video was not stored: ${err.message}`;
    }
  }

  setStatus("busy", "Reading clip");
  clipVideo.src = URL.createObjectURL(file);
  readClip(storedId)
    .then(() => {
      if (storeWarning) showNotice(`${storeWarning} Feedback still works, but there is no playback.`);
    })
    .catch((err) => {
      showNotice([storeWarning, err.message].filter(Boolean).join(" "));
      setStatus("idle", "Ready");
    });
}

function seek(video, time) {
  return new Promise((resolve) => {
    const finish = () => {
      clearTimeout(fallback);
      video.removeEventListener("seeked", finish);
      resolve();
    };
    const fallback = setTimeout(finish, 1500);
    video.addEventListener("seeked", finish);
    video.currentTime = time;
  });
}

function captureFrame(source) {
  const width = source.videoWidth;
  const height = source.videoHeight;
  const scale = Math.min(1, FRAME_SIDE / Math.max(width, height));
  const canvas = document.createElement("canvas");
  canvas.width = Math.round(width * scale);
  canvas.height = Math.round(height * scale);
  canvas.getContext("2d").drawImage(source, 0, 0, canvas.width, canvas.height);
  return canvas.toDataURL("image/jpeg", 0.8);
}

function addFrame(src, number, time) {
  const figure = el("figure", "frame");
  const img = new Image();
  img.src = src;
  img.alt = `Sampled frame ${number}`;
  const caption = el("figcaption", null, `F${String(number).padStart(2, "0")} · ${formatClock(time)}`);
  figure.append(img, caption);
  $("strip").append(figure);
}

function setProgress(done, total, spacing) {
  $("progress").hidden = false;
  $("progressBar").style.width = `${(done / total) * 100}%`;
  $("progressText").textContent = done < total
    ? `Sampling frame ${done} of ${total}`
    : `${total} frames, about one every ${spacing.toFixed(1)} s`;
}

async function readClip(storedId) {
  await new Promise((resolve, reject) => {
    clipVideo.onloadeddata = resolve;
    clipVideo.onerror = () => reject(new Error("This browser can't read that video. Try an MP4 (H.264)."));
  });
  if (!Number.isFinite(clipVideo.duration) || clipVideo.duration <= 0) {
    throw new Error("Couldn't read the clip length. Try an MP4 (H.264).");
  }
  const plan = planSampling(clipVideo.duration);
  setProgress(0, plan.count, plan.spacing);
  for (let i = 0; i < plan.count; i++) {
    const time = plan.times[i];
    await seek(clipVideo, time);
    const frame = captureFrame(clipVideo);
    state.clipFrames.push(frame);
    state.clipTimes.push(time);
    addFrame(frame, i + 1, time);
    setProgress(i + 1, plan.count, plan.spacing);
  }
  setStatus("idle", "Ready");
  updateControls();
}

$("clipInput").addEventListener("change", (event) => {
  const file = event.target.files[0];
  if (file) loadClip(file);
});

dropzone.addEventListener("click", () => $("clipInput").click());
dropzone.addEventListener("dragover", (event) => {
  event.preventDefault();
  dropzone.classList.add("is-over");
});
dropzone.addEventListener("dragleave", () => dropzone.classList.remove("is-over"));
dropzone.addEventListener("drop", (event) => {
  event.preventDefault();
  dropzone.classList.remove("is-over");
  const file = event.dataTransfer.files[0];
  if (!file) return;
  const transfer = new DataTransfer();
  transfer.items.add(file);
  $("clipInput").files = transfer.files;
  loadClip(file);
});

$("reviewClip").addEventListener("click", () => {
  runReview("upload", state.clipFrames, state.clipTimes, $("reviewClip"), state.storedVideoId || null);
});

/* ---------- live camera ---------- */

async function startLive() {
  showNotice("");
  if (!state.discipline) {
    showNotice("Choose a discipline before starting the camera.");
    return;
  }
  let stream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({
      video: { facingMode: { ideal: "environment" } },
      audio: false,
    });
  } catch {
    showNotice("Camera access was blocked. Allow camera permission for this site and try again.");
    return;
  }
  const video = $("liveVideo");
  video.srcObject = stream;
  await video.play();

  Object.assign(state.live, {
    stream,
    samples: [],
    cues: 0,
    cuePending: false,
    startedAt: Date.now(),
  });
  $("liveStop").disabled = false;
  $("rec").hidden = false;
  setStatus("live", "Live");
  showCue("Watching for the first cue...");
  updateLiveStats();
  updateControls();
  state.live.timer = setInterval(liveTick, LIVE_INTERVAL_MS);
  setTimeout(liveTick, 2000);
}

function showCue(text) {
  const cue = $("cue");
  cue.textContent = text;
  if (!reduceMotion) {
    cue.animate(
      [{ opacity: 0.3, transform: "translateY(6px)" }, { opacity: 1, transform: "none" }],
      { duration: 300, easing: "ease-out" }
    );
  }
}

async function liveTick() {
  const live = state.live;
  if (!live.stream) return;
  if (Date.now() - live.startedAt > LIVE_MAX_MS) {
    stopLive(true);
    return;
  }
  const video = $("liveVideo");
  if (!video.videoWidth) return;

  if (live.samples.length < LIVE_MAX_SAMPLES) {
    live.samples.push({ src: captureFrame(video), t: (Date.now() - live.startedAt) / 1000 });
  }
  updateLiveStats();

  if (live.cuePending || live.cues >= LIVE_MAX_CUES) return;
  live.cuePending = true;
  try {
    const frames = live.samples.slice(-LIVE_WINDOW).map((s) => s.src);
    const data = await api("/api/live-cue", { frames, discipline: state.discipline });
    if (live.stream) {
      showCue(data.cue);
      live.cues++;
      updateLiveStats();
    }
  } catch (err) {
    showNotice(err.message);
  } finally {
    live.cuePending = false;
  }
}

function updateLiveStats() {
  const live = state.live;
  const seconds = live.startedAt ? (Date.now() - live.startedAt) / 1000 : 0;
  $("statCues").textContent = live.cues;
  $("statFrames").textContent = live.samples.length;
  $("statTime").textContent = formatClock(seconds);
}

async function stopLive(shouldReview) {
  const live = state.live;
  if (!live.stream) return;
  clearInterval(live.timer);
  live.stream.getTracks().forEach((track) => track.stop());
  const samples = live.samples;
  live.stream = null;
  $("liveVideo").srcObject = null;
  $("liveStop").disabled = true;
  $("rec").hidden = true;
  showCue("Camera off. Your session is saved to the report.");
  setStatus("idle", "Ready");
  updateControls();

  if (!shouldReview) return;
  if (samples.length < 2) {
    showNotice("Record a little longer. At least 2 frames are needed for a review.");
    return;
  }
  const picks = evenlySpaced(samples, state.config.maxFrames);
  await runReview("live", picks.map((s) => s.src), picks.map((s) => s.t), null, null);
}

function evenlySpaced(items, count) {
  if (items.length <= count) return items.slice();
  return Array.from({ length: count }, (_, i) =>
    items[Math.round((i * (items.length - 1)) / (count - 1))]
  );
}

$("liveStart").addEventListener("click", startLive);
$("liveStop").addEventListener("click", () => stopLive(true));

document.addEventListener("visibilitychange", () => {
  if (document.hidden) stopLive(true);
});

/* ---------- confirmation and deletion ---------- */

let activeConfirm = null;

function confirmDialog({ title, body, confirmLabel, phrase = "" }) {
  return new Promise((resolve) => {
    if (activeConfirm) activeConfirm(false);
    const dialog = $("confirmDialog");
    const ok = $("confirmOk");
    const phraseInput = $("confirmPhrase");
    $("confirmTitle").textContent = title;
    $("confirmBody").textContent = body;
    ok.textContent = confirmLabel;
    $("confirmPhraseLabel").hidden = !phrase;
    $("confirmPhraseText").textContent = phrase ? `Type ${phrase} to confirm` : "";
    phraseInput.value = "";
    ok.disabled = Boolean(phrase);
    phraseInput.oninput = phrase
      ? () => { ok.disabled = phraseInput.value.trim() !== phrase; }
      : null;
    const finish = () => {
      dialog.removeEventListener("close", finish);
      activeConfirm = null;
      resolve(dialog.returnValue === "ok");
    };
    activeConfirm = (value) => {
      dialog.removeEventListener("close", finish);
      activeConfirm = null;
      resolve(value);
    };
    dialog.addEventListener("close", finish);
    dialog.showModal();
    (phrase ? phraseInput : $("confirmCancel")).focus();
  });
}

$("confirmCancel").addEventListener("click", () => $("confirmDialog").close("cancel"));

async function deleteReport(reviewId, createdAt) {
  const when = new Date(createdAt).toLocaleString([], { dateStyle: "medium", timeStyle: "short" });
  const confirmed = await confirmDialog({
    title: "Delete this report?",
    body: `The report from ${when} is removed from your history. Its stored video, if any, is kept until it expires or you delete it.`,
    confirmLabel: "Delete report",
  });
  if (!confirmed) return;
  try {
    await api(`/api/reviews/${reviewId}`, undefined, "DELETE");
  } catch (err) {
    showNotice(err.message);
    return;
  }
  if (state.lastReviewId === reviewId) {
    state.lastReviewId = null;
    state.lastReport = null;
    $("result").hidden = true;
    $("empty").hidden = false;
  }
  showNotice("Report deleted. Its video, if any, is unchanged.");
  loadHistory();
}

$("deleteAllReports").addEventListener("click", async () => {
  const phrase = "DELETE ALL REPORTS";
  const confirmed = await confirmDialog({
    title: "Delete all reports?",
    body: "Every saved report is removed from your history. Stored videos are not deleted. This cannot be undone.",
    confirmLabel: "Delete all reports",
    phrase,
  });
  if (!confirmed) return;
  try {
    const data = await api("/api/reviews", { confirm: phrase }, "DELETE");
    state.lastReviewId = null;
    state.lastReport = null;
    $("result").hidden = true;
    $("empty").hidden = false;
    showNotice(`Deleted ${data.deleted} report${data.deleted === 1 ? "" : "s"}. Stored videos are unchanged.`);
  } catch (err) {
    showNotice(err.message);
  }
  loadHistory();
});

/* ---------- history ---------- */

async function loadHistory() {
  try {
    const { reviews } = await api("/api/reviews");
    const box = $("history");
    box.replaceChildren();
    if (!reviews.length) {
      box.append(el("p", "hint", "No reviews yet. Completed reports are listed here."));
      return;
    }
    for (const review of reviews) {
      const details = el("details", "history-item");
      const summary = el("summary");
      const discipline = el("span", "chip discipline", review.discipline_label || "Unspecified");
      const chip = el("span", review.mode === "live" ? "chip live" : "chip", review.mode === "live" ? "Live" : "Upload");
      const when = el("span", "when", new Date(review.created_at).toLocaleString([], {
        dateStyle: "medium",
        timeStyle: "short",
      }));
      const frames = el("span", "when", `${review.frame_count} frames`);
      summary.append(discipline, chip, when, frames);
      if (review.athlete_description) summary.append(el("span", "note-text", `Athlete: ${review.athlete_description}`));
      if (review.context) summary.append(el("span", "note-text", review.context));

      const body = el("div", "history-body");
      const link = review.video_available && review.video_id
        ? { id: review.video_id, displayName: "Stored clip", expiresAt: null }
        : null;
      const actions = el("div", "report-actions");
      if (link) {
        const open = el("button", "btn btn-ghost btn-sm", "Open clip");
        open.type = "button";
        open.addEventListener("click", () => loadVideoIntoPlayer(link));
        actions.append(open);
      }
      const remove = el("button", "btn btn-ghost btn-sm", "Delete report");
      remove.type = "button";
      remove.addEventListener("click", () => deleteReport(review.id, review.created_at));
      actions.append(remove);
      body.append(actions);
      const content = el("div", "history-content");
      if (review.report) {
        renderReport(content, review.report, link);
      } else {
        renderLegacy(content, review.feedback);
      }
      body.append(content);
      details.append(summary, body);
      box.append(details);
    }
  } catch (err) {
    showNotice(err.message);
  }
}

/* ---------- start ---------- */

syncLayout();
setStatus("idle", "Ready");
fetch("/api/session", { credentials: "same-origin" })
  .then((res) => (res.ok ? enterApp() : showLogin()))
  .catch(() => showLogin());
