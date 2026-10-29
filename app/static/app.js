const REVIEW_FRAMES = 8;
const FRAME_SIDE = 768;
const LIVE_INTERVAL_MS = 8000;
const LIVE_WINDOW = 3;
const LIVE_MAX_CUES = 60;
const LIVE_MAX_SAMPLES = 90;
const LIVE_MAX_MS = 10 * 60 * 1000;

const $ = (id) => document.getElementById(id);

const state = {
  token: localStorage.getItem("filmroom-token") || "",
  clipFrames: [],
  live: { stream: null, timer: null, samples: [], cues: 0, cuePending: false, startedAt: 0 },
};

const isMobile =
  /Android|iPhone|iPad|iPod|Mobile/i.test(navigator.userAgent) ||
  (navigator.maxTouchPoints > 1 && matchMedia("(pointer: coarse)").matches);

function showNotice(message) {
  $("notice").textContent = message;
  $("notice").hidden = !message;
}

async function api(path, body) {
  const headers = {};
  if (state.token) headers["X-App-Token"] = state.token;
  if (body) headers["Content-Type"] = "application/json";
  const res = await fetch(path, {
    method: body ? "POST" : "GET",
    headers,
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (res.status === 401) {
    $("gate").hidden = false;
    throw new Error("Enter the access code to continue.");
  }
  if (!res.ok) throw new Error(data.error || `Request failed (${res.status}).`);
  return data;
}

$("gate").addEventListener("submit", (event) => {
  event.preventDefault();
  state.token = $("tokenInput").value.trim();
  localStorage.setItem("filmroom-token", state.token);
  $("gate").hidden = true;
  loadHistory();
});

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

function evenlySpaced(items, count) {
  if (items.length <= count) return items.slice();
  return Array.from({ length: count }, (_, i) =>
    items[Math.round((i * (items.length - 1)) / (count - 1))]
  );
}

function addThumb(src) {
  const img = new Image();
  img.src = src;
  img.alt = "Sampled frame";
  $("thumbs").append(img);
}

function showResult(text, meta) {
  $("resultBody").textContent = text;
  $("resultMeta").textContent = meta;
  $("result").hidden = false;
  $("result").scrollIntoView({ behavior: "smooth", block: "start" });
}

async function runReview(mode, frames, button) {
  showNotice("");
  if (button) button.disabled = true;
  $("resultMeta").textContent = "Reviewing...";
  try {
    const data = await api("/api/review", {
      mode,
      frames,
      context: $("context").value.trim(),
    });
    const source = mode === "live" ? "live session" : "uploaded clip";
    showResult(data.feedback, `Review #${data.id} · ${frames.length} frames · ${source}`);
    loadHistory();
  } catch (err) {
    showNotice(err.message);
  } finally {
    if (button) button.disabled = false;
  }
}

const clipVideo = $("clipVideo");

$("clipInput").addEventListener("change", async (event) => {
  const file = event.target.files[0];
  if (!file) return;
  state.clipFrames = [];
  $("thumbs").replaceChildren();
  $("reviewClip").disabled = true;
  showNotice("");
  clipVideo.src = URL.createObjectURL(file);
  try {
    await new Promise((resolve, reject) => {
      clipVideo.onloadeddata = resolve;
      clipVideo.onerror = () => reject(new Error("This browser can't read that video. Try an MP4 (H.264)."));
    });
    if (!Number.isFinite(clipVideo.duration) || clipVideo.duration <= 0) {
      throw new Error("Couldn't read the clip length. Try an MP4 (H.264).");
    }
    for (let i = 0; i < REVIEW_FRAMES; i++) {
      await seek(clipVideo, (clipVideo.duration * (i + 0.5)) / REVIEW_FRAMES);
      const frame = captureFrame(clipVideo);
      state.clipFrames.push(frame);
      addThumb(frame);
    }
    $("reviewClip").disabled = false;
  } catch (err) {
    showNotice(err.message);
  }
});

$("reviewClip").addEventListener("click", () => {
  runReview("upload", state.clipFrames, $("reviewClip"));
});

async function startLive() {
  showNotice("");
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
  $("liveStart").disabled = true;
  $("liveStop").disabled = false;
  $("cue").textContent = "Watching...";
  state.live.timer = setInterval(liveTick, LIVE_INTERVAL_MS);
  setTimeout(liveTick, 2000);
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

  if (live.samples.length < LIVE_MAX_SAMPLES) live.samples.push(captureFrame(video));
  updateLiveStats();

  if (live.cuePending || live.cues >= LIVE_MAX_CUES) return;
  live.cuePending = true;
  try {
    const data = await api("/api/live-cue", { frames: live.samples.slice(-LIVE_WINDOW) });
    if (live.stream) {
      $("cue").textContent = data.cue;
      live.cues++;
    }
  } catch (err) {
    showNotice(err.message);
  } finally {
    live.cuePending = false;
    updateLiveStats();
  }
}

function updateLiveStats() {
  const live = state.live;
  const seconds = live.startedAt ? Math.floor((Date.now() - live.startedAt) / 1000) : 0;
  const minutes = Math.floor(seconds / 60);
  const rest = String(seconds % 60).padStart(2, "0");
  $("liveStats").textContent =
    `${live.cues} cues · ${live.samples.length} frames sampled · ${minutes}:${rest}`;
}

async function stopLive(shouldReview) {
  const live = state.live;
  if (!live.stream) return;
  clearInterval(live.timer);
  live.stream.getTracks().forEach((track) => track.stop());
  const samples = live.samples;
  live.stream = null;
  $("liveVideo").srcObject = null;
  $("liveStart").disabled = false;
  $("liveStop").disabled = true;
  $("cue").textContent = "Camera off.";
  updateLiveStats();

  if (!shouldReview) return;
  if (samples.length < 2) {
    showNotice("Record a little longer. At least 2 frames are needed for a review.");
    return;
  }
  await runReview("live", evenlySpaced(samples, REVIEW_FRAMES), null);
}

$("liveStart").addEventListener("click", startLive);
$("liveStop").addEventListener("click", () => stopLive(true));

document.addEventListener("visibilitychange", () => {
  if (document.hidden) stopLive(true);
});

async function loadHistory() {
  try {
    const { reviews } = await api("/api/reviews");
    const box = $("history");
    box.replaceChildren();
    if (!reviews.length) {
      const empty = document.createElement("p");
      empty.className = "hint";
      empty.textContent = "No reviews yet.";
      box.append(empty);
      return;
    }
    for (const review of reviews) {
      const details = document.createElement("details");
      const summary = document.createElement("summary");
      const source = review.mode === "live" ? "Live" : "Upload";
      summary.textContent = `${review.created_at} · ${source} · ${review.frame_count} frames` +
        (review.context ? ` · ${review.context}` : "");
      const body = document.createElement("div");
      body.className = "body";
      body.textContent = review.feedback;
      details.append(summary, body);
      box.append(details);
    }
  } catch (err) {
    showNotice(err.message);
  }
}

setMode("upload");
loadHistory();
