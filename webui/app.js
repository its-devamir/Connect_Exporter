const el = (id) => document.getElementById(id);

const RES_OPTS = Object.freeze({
  "1080": [1920, 1080],
  "720": [1280, 720],
  "480": [854, 480],
  "360": [640, 360],
  "240": [426, 240],
});

let toastTimer = null;
/** @type {number | null} */
let pollTimer = null;
let lastPreflightOk = false;
let uploadBusy = false;
/** @type {1|2|3|4} */
let wizardStep = 1;
let estimateDebounce = 0;
let prevPollState = "idle";
/** @type {number | null} */
let dlPollTimer = null;

const STEP2_NEXT_LABEL = "Next: Settings →";

/** Disable Next button and optionally change label while uploading/checking. */
function setStep1PrimaryBusy(busy, label) {
  const btn = el("btnToSettings");
  if (busy) {
    btn.dataset.busy = "1";
    btn.disabled = true;
    if (label) btn.textContent = label;
  } else {
    delete btn.dataset.busy;
    btn.textContent = STEP2_NEXT_LABEL;
    syncStep1NextButton();
  }
}

function syncStep1NextButton() {
  const btn = el("btnToSettings");
  if (btn.dataset.busy === "1") return;
  const path = getSessionFolder().trim();
  btn.disabled = uploadBusy || !path;
}

async function advanceFromStep1() {
  const folder = getSessionFolder().trim();
  if (!folder) {
    showToast("Add a recording first — upload, or paste a path.", "error");
    return;
  }
  setStep1PrimaryBusy(true, "Checking…");
  try {
    const ok = await runPreflight({ toastOk: true });
    if (ok) {
      wizardGo(3);
      refreshSystemOnly();
    }
  } finally {
    setStep1PrimaryBusy(false);
  }
}

function nowIso() {
  return new Date().toISOString().replace("T", " ").replace("Z", "");
}

/** Wall-clock / timeline seconds → HH:MM:SS (hours grow beyond 99 if needed). */
function formatHMS(totalSeconds) {
  if (totalSeconds === null || totalSeconds === undefined || Number.isNaN(totalSeconds)) {
    return "—";
  }
  const s = Math.max(0, Math.floor(Number(totalSeconds)));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  const hh = String(h).padStart(Math.max(2, String(h).length), "0");
  return `${hh}:${String(m).padStart(2, "0")}:${String(sec).padStart(2, "0")}`;
}

/** JSON numbers only; `typeof null === "object"` so never use typeof for null-safe numeric fields. */
function toFiniteNumber(v) {
  if (v === null || v === undefined) return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}

function fmtBytes(n) {
  const v = Number(n);
  if (!Number.isFinite(v) || v < 0) return "—";
  const units = ["B", "KB", "MB", "GB"];
  let x = v;
  let i = 0;
  while (x >= 1024 && i < units.length - 1) {
    x /= 1024;
    i++;
  }
  const digits = i === 0 ? 0 : x >= 100 ? 0 : x >= 10 ? 1 : 2;
  return `${x.toFixed(digits)} ${units[i]}`;
}

function fmtSpeed(bps) {
  const v = Number(bps);
  if (!Number.isFinite(v) || v <= 0) return "—";
  return `${fmtBytes(v)}/s`;
}

/** @param {string} msg @param {'info'|'error'} kind */
function showToast(msg, kind = "info") {
  const t = el("toast");
  t.textContent = msg;
  t.hidden = false;
  t.classList.toggle("error", kind === "error");
  clearTimeout(toastTimer);
  toastTimer = window.setTimeout(() => {
    t.hidden = true;
  }, 4600);
}

function appendLog(line) {
  const node = el("log");
  node.textContent += `[${nowIso()}] ${line}\n`;
  node.scrollTop = node.scrollHeight;
}

async function fetchJsonOk(res) {
  if (!res.ok) {
    const txt = await res.text();
    throw new Error(txt || `${res.status} ${res.statusText}`);
  }
  return res.json();
}

async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(opts.headers || {}) },
    ...opts,
  });
  return fetchJsonOk(res);
}

function getSessionFolder() {
  const hid = el("folder").value.trim();
  return hid || el("manualFolder").value.trim();
}

function applyResolutionPreset() {
  const v = el("resolutionPreset").value;
  const custom = v === "custom";
  el("customResRow").hidden = !custom;
  if (!custom && RES_OPTS[v]) {
    const [w, h] = RES_OPTS[v];
    el("w").value = String(w);
    el("h").value = String(h);
  }
}

function gatherOptions() {
  applyResolutionPreset();
  return {
    folder: getSessionFolder(),
    out: el("out").value.trim(),
    out_dir: el("outDir").value.trim(),
    w: Number.parseInt(el("w").value, 10),
    h: Number.parseInt(el("h").value, 10),
    fps: Number.parseInt(el("fps").value, 10),
    crf: Number.parseInt(el("crf").value, 10),
    preset: el("preset").value,
    encoder: el("encoder").value,
    burn_chat: el("burnChat").checked,
    chapters: el("chapters").checked,
    skip_breaks: el("skipBreaks").checked,
  };
}

function setPreflightBadge(ok) {
  const b = el("preflightBadge");
  if (ok) {
    b.textContent = "Session verified";
    b.classList.remove("subtle");
    b.style.borderStyle = "solid";
  } else {
    b.textContent = "Session: not verified";
    b.classList.add("subtle");
    b.style.borderStyle = "dashed";
  }
}

function clearVerification() {
  lastPreflightOk = false;
  el("btnStartExport").disabled = true;
  el("wizTab2").disabled = true;
  setPreflightBadge(false);
  el("warningsBox").hidden = true;
  el("warningsBox").innerHTML = "";
  syncStep1NextButton();
}

function wizardGo(step) {
  wizardStep = step;
  el("panel1").hidden = step !== 1;
  el("panel2").hidden = step !== 2;
  el("panel3").hidden = step !== 3;
  el("panel4").hidden = step !== 4;
  [["wizTab1", 1], ["wizTab2", 2], ["wizTab3", 3], ["wizTab4", 4]].forEach(([tid, sn]) => {
    el(tid).classList.toggle("active", sn === step);
  });
  syncWizardTabDisabled();
}

function syncWizardTabDisabled() {
  // step2 (Upload) is always reachable from step1 once user downloaded the ZIP
  el("wizTab2").disabled = false;
  el("wizTab3").disabled = !lastPreflightOk;
  el("wizTab4").disabled = wizardStep !== 4;
}

function fmtSec(sec) {
  const s = Number(sec);
  if (!Number.isFinite(s) || s < 0) return "—";
  if (s < 120) return `${Math.round(s)} sec`;
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  if (h > 0) return `${h}h ${m}m`;
  return `${m} min`;
}

function renderSystem(si) {
  const box = el("systemInfo");
  const cells = [];

  cells.push(cell("Operating system", si.os || "—"));
  const ff = si.ffmpeg_on_path && si.ffprobe_on_path;
  cells.push(cell("FFmpeg & FFprobe", ff ? "<span class='ok'>On PATH ✓</span>" : "<span class='warnTxt'>Missing ✗</span>"));
  cells.push(cell("NVENC (FFmpeg)", si.ffmpeg_h264_nvenc ? "<span class='ok'>Available ✓</span>" : "<span class='warnTxt'>Not listed</span>"));

  const gpus = (si.nvidia_gpus || []).join(", ") || "(none detected)";
  cells.push(cell("NVIDIA GPUs (nvidia-smi)", escapeHtmlShort(gpus)));
  cells.push(cell("Logical CPU cores", String(si.cpu_count_logical ?? "—")));

  box.innerHTML = cells.join("");
}

function cell(lab, val) {
  return `<div class="sysCell"><span class="sysLab">${escapeHtmlShort(lab)}</span>${val}</div>`;
}

function escapeHtmlShort(s) {
  return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;");
}

async function refreshSystemOnly() {
  try {
    const si = await api("/api/system");
    renderSystem(si);
  } catch {
    el("systemInfo").textContent = "Could not load system info.";
  }
}

/** @param {Record<string, any>} data preflight payload */
function applyEstimatesUi(data) {
  const es = data.estimates || {};
  el("estLoad").textContent = es.load_score != null ? `${es.load_score}× baseline` : "—";
  el("estEnc").textContent = fmtSec(es.encode_seconds_approx);
  el("estTot").textContent = fmtSec(es.total_seconds_approx);
  el("estMb").textContent = es.output_size_mb_approx != null ? `~ ${es.output_size_mb_approx} MB` : "—";

  el("encoderInfo").textContent = data.encoder_selected ? `Will use encoder: ${data.encoder_selected}` : "";

  const warns = [...(data.warnings || [])];
  const wb = el("warningsBox");
  if (warns.length) {
    wb.hidden = false;
    wb.innerHTML = "<strong>Heads-up</strong><ul>" + warns.map((w) => `<li>${escapeHtmlShort(w)}</li>`).join("") + "</ul>";
  } else {
    wb.hidden = true;
    wb.innerHTML = "";
  }

  el("preflight").textContent = JSON.stringify(data, null, 2);
}

async function runPreflight({ toastOk = false } = {}) {
  const opts = gatherOptions();
  const folder = opts.folder;
  if (!folder) {
    showToast("Choose a ZIP or folder, or paste a path.", "error");
    return false;
  }
  if (!opts.out.endsWith(".mp4")) {
    showToast("Output name should end with .mp4.", "error");
    return false;
  }
  try {
    const data = await api("/api/preflight", { method: "POST", body: JSON.stringify(opts) });
    el("folder").value = data.resolved_folder || folder;
    el("manualFolder").value = "";
    el("sessionPathDisplay").textContent = el("folder").value || "—";

    renderSystem(data.system || {});
    applyEstimatesUi(data);

    lastPreflightOk = true;
    setPreflightBadge(true);
    el("wizTab2").disabled = false;
    el("btnStartExport").disabled = false;
    syncWizardTabDisabled();
    syncStep1NextButton();
    if (toastOk) showToast("Recording verified.", "info");
    return true;
  } catch (e) {
    appendLog(`Preflight failed: ${e}`);
    clearVerification();
    syncWizardTabDisabled();
    showToast(String(e), "error");
    return false;
  }
}

function scheduleEstimatesRefresh() {
  if (!lastPreflightOk || wizardStep === 4) return;
  clearTimeout(estimateDebounce);
  estimateDebounce = window.setTimeout(() => {
    runPreflight({ toastOk: false }).catch(() => {});
  }, 520);
}

function refreshPresetHighlight() {
  const w = el("w").value,
    h = el("h").value,
    fps = el("fps").value,
    crf = el("crf").value,
    preset = el("preset").value,
    enc = el("encoder").value;
  const burn = el("burnChat").checked;
  document.querySelectorAll(".btn.preset").forEach((b) => b.classList.remove("active"));
  if (w === "854" && h === "480" && fps === "10" && crf === "32" && preset === "ultrafast" && enc === "auto" && !burn)
    el("presetFast").classList.add("active");
  else if (w === "1280" && h === "720" && fps === "15" && crf === "30" && preset === "ultrafast" && enc === "auto" && burn)
    el("presetBalanced").classList.add("active");
  else if (w === "1280" && h === "720" && fps === "30" && crf === "26" && preset === "veryfast" && enc === "libx264" && burn)
    el("presetQuality").classList.add("active");
}

/** @param {"fast"|"balanced"|"quality"} k */
function applyPresetBtn(k) {
  if (k === "fast") {
    el("resolutionPreset").value = "480";
    el("preset").value = "ultrafast";
    el("encoder").value = "auto";
    el("fps").value = "10";
    el("crf").value = "32";
    el("burnChat").checked = false;
  } else if (k === "balanced") {
    el("resolutionPreset").value = "720";
    el("preset").value = "ultrafast";
    el("encoder").value = "auto";
    el("fps").value = "15";
    el("crf").value = "30";
    el("burnChat").checked = true;
  } else {
    el("resolutionPreset").value = "720";
    el("preset").value = "veryfast";
    el("encoder").value = "libx264";
    el("fps").value = "30";
    el("crf").value = "26";
    el("burnChat").checked = true;
  }
  applyResolutionPreset();
  appendLog(`Preset applied: ${k}`);
  refreshPresetHighlight();
  scheduleEstimatesRefresh();
}

async function ingestUploadResult(uploaded) {
  el("folder").value = uploaded.resolved_folder || "";
  el("manualFolder").value = "";
  el("sessionPathDisplay").textContent = el("folder").value || "—";
  setStep1PrimaryBusy(true, "Checking…");
  try {
    const ok = await runPreflight({ toastOk: true });
    if (ok) {
      wizardGo(2);
      refreshSystemOnly();
    }
  } finally {
    setStep1PrimaryBusy(false);
  }
}

async function uploadZipBlob(file) {
  const fd = new FormData();
  fd.append("file", file, file.name || "session.zip");
  const res = await fetch("/api/upload_zip", { method: "POST", body: fd });
  return fetchJsonOk(res);
}

async function uploadFolderForm(fl) {
  const fd = new FormData();
  let n = 0;
  for (const f of fl) {
    const rel = (f.webkitRelativePath || f.name || "").replace(/\\/g, "/");
    if (!rel.trim()) continue;
    fd.append("files", f, rel);
    n++;
  }
  if (!n) throw new Error("No usable files.");
  const res = await fetch("/api/upload_folder", { method: "POST", body: fd });
  return fetchJsonOk(res);
}

function entryFile(ent) {
  return new Promise((resolve, reject) => ent.file(resolve, reject));
}

async function readAllDirEntries(reader) {
  const acc = [];
  while (true) {
    const batch = await new Promise((r) => reader.readEntries(r));
    if (!batch.length) break;
    acc.push(...batch);
  }
  return acc;
}

async function flattenDirectoryEntry(dir, prefix = "") {
  const reader = dir.createReader();
  const ents = await readAllDirEntries(reader);
  const out = [];
  for (const e of ents) {
    const rel = `${prefix}${e.name}`;
    if (e.isFile) {
      const file = await entryFile(e);
      out.push({ file, rel });
    } else if (e.isDirectory) out.push(...(await flattenDirectoryEntry(e, `${rel}/`)));
  }
  return out;
}

async function uploadFolderPairs(pairs) {
  const fd = new FormData();
  for (const { file, rel } of pairs) {
    fd.append("files", file, rel.replace(/\\/g, "/"));
  }
  const res = await fetch("/api/upload_folder", { method: "POST", body: fd });
  return fetchJsonOk(res);
}

function isZipFilename(name) {
  return !!(name && name.toLowerCase().endsWith(".zip"));
}

function showPickStatus(t) {
  const p = el("pickStatus");
  p.textContent = t;
  p.hidden = !t;
}
function hidePickStatus() {
  el("pickStatus").hidden = true;
}

// Step 1 is browser-driven: we generate a ZIP URL and the user downloads in their browser.

async function ingestZipDroppedOrPicked(file) {
  if (uploadBusy) return;
  uploadBusy = true;
  showPickStatus("Uploading & extracting ZIP…");
  setStep1PrimaryBusy(true, "Uploading…");
  try {
    const data = await uploadZipBlob(file);
    await ingestUploadResult(data);
  } catch (e) {
    clearVerification();
    showToast(String(e), "error");
  } finally {
    hidePickStatus();
    uploadBusy = false;
    setStep1PrimaryBusy(false);
  }
}

async function ingestFolderPairsUx(pairs) {
  if (uploadBusy) return;
  uploadBusy = true;
  showPickStatus(`Uploading ${pairs.length} files…`);
  setStep1PrimaryBusy(true, "Uploading…");
  try {
    const data = await uploadFolderPairs(pairs);
    showToast("Folder uploaded.", "info");
    await ingestUploadResult(data);
  } catch (e) {
    clearVerification();
    showToast(String(e), "error");
  } finally {
    hidePickStatus();
    uploadBusy = false;
    setStep1PrimaryBusy(false);
  }
}

async function handleDropPayload(dt) {
  if (!dt || uploadBusy) return;
  const items = [...dt.items].map((i) => i.webkitGetAsEntry?.()).filter(Boolean);
  if (items.length === 1) {
    const e = items[0];
    if (e.isFile) {
      const f = await entryFile(e);
      if (!isZipFilename(f.name)) {
        showToast("Drop one folder or one .zip file.", "error");
        return;
      }
      await ingestZipDroppedOrPicked(f);
      return;
    }
    if (e.isDirectory) {
      try {
        const pairs = await flattenDirectoryEntry(e);
        await ingestFolderPairsUx(pairs);
      } catch {
        showToast("Couldn't read dropped folder — use Choose folder or a .zip.", "error");
      }
      return;
    }
  }
  if (items.length > 1) {
    showToast("Drop a single folder or ZIP.", "error");
    return;
  }
  const flat = [...(dt.files || [])];
  const zips = flat.filter((f) => isZipFilename(f.name));
  if (flat.length === 1 && zips.length === 1) return ingestZipDroppedOrPicked(zips[0]);
  if (flat.some((f) => f.webkitRelativePath)) {
    try {
      uploadBusy = true;
      setStep1PrimaryBusy(true, "Uploading…");
      showPickStatus("Uploading folder…");
      const data = await uploadFolderForm(flat);
      await ingestUploadResult(data);
      showToast("Folder uploaded.", "info");
    } catch (e) {
      clearVerification();
      showToast(String(e), "error");
    } finally {
      hidePickStatus();
      uploadBusy = false;
      setStep1PrimaryBusy(false);
    }
    return;
  }
  showToast(
    "Folder-drag may be unsupported — zip the recording, drop the ZIP, Use Choose folder, or paste a full path.",
    "error",
  );
}

function isFileDrag(dt) {
  return Boolean(dt?.types?.includes?.("Files"));
}

function dropZoneBusy(on) {
  el("pickZipBtn").disabled = on;
  el("pickFolderBtn").disabled = on;
  el("dropZone").style.pointerEvents = on ? "none" : "";
}

document.addEventListener("dragover", (e) => e.preventDefault());

document.addEventListener(
  "drop",
  async (e) => {
    e.preventDefault();
    await handleDropPayload(e.dataTransfer);
  },
  true,
);

const dropZone = el("dropZone");
["dragenter", "dragover"].forEach((ev) => {
  dropZone.addEventListener(ev, (e) => {
    e.preventDefault();
    if (uploadBusy || !isFileDrag(e.dataTransfer)) return;
    dropZone.classList.add("dropHover");
  });
});
["dragleave", "dragend"].forEach((ev) => {
  dropZone.addEventListener(ev, () => dropZone.classList.remove("dropHover"));
});

async function poll() {
  try {
    const s = await api("/api/export/status");
    el("serverBadge").textContent = `Server • ${s.state}`;

    const pill = el("statusText");
    const norm = String(s.state || "idle").toLowerCase();
    pill.textContent = norm;
    pill.classList.remove("running", "finished", "error", "stopped");
    if (norm === "running") pill.classList.add("running");
    else if (norm === "finished") pill.classList.add("finished");
    else if (norm === "error") pill.classList.add("error");
    else if (norm === "stopped") pill.classList.add("stopped");

    el("progOutpath").textContent = s.out_path ? `Output: ${s.out_path}` : "";

    let pct = 0;
    if (typeof s.progress_pct === "number") {
      pct = Math.max(0, Math.min(100, s.progress_pct));
    }
    if (norm === "finished") {
      pct = 100;
    }
    el("bar").style.width = `${pct.toFixed(1)}%`;
    el("bar").title = `${pct.toFixed(1)}%`;
    const pctLabel =
      norm === "finished" ? "100%" : pct >= 99.95 ? `${Math.round(pct)}%` : `${pct.toFixed(1)}%`;
    el("progPercent").textContent = pctLabel;

    const ren = toFiniteNumber(s.rendered_s);
    const ffEl = toFiniteNumber(s.elapsed_s);
    const wall = toFiniteNumber(s.wall_elapsed_s);
    const activeTiming = norm === "running" || norm === "finished" || norm === "stopped" || norm === "error";

    const sub = el("progPercentSub");
    if (norm === "running" && pct < 1 && ren !== null && ren < 0.01) {
      sub.textContent = "Preparing timeline / filters…";
    } else {
      sub.textContent = "of recording encoded";
    }

    if (norm === "idle") {
      el("statRendered").textContent = "00:00:00";
      el("statElapsed").textContent = "00:00:00";
      el("statEta").textContent = "—";
    } else if (activeTiming) {
      if (norm === "running" && (ren === null || ren < 0.001)) {
        el("statRendered").textContent = "—";
      } else if (ren !== null) {
        el("statRendered").textContent = formatHMS(ren);
      } else {
        el("statRendered").textContent = "—";
      }
      const elapsedDisp =
        ffEl !== null && ffEl >= 0.25 ? ffEl : wall !== null ? wall : ffEl ?? 0;
      el("statElapsed").textContent = formatHMS(elapsedDisp);
      if (norm === "running") {
        el("statEta").textContent = s.eta_s === null || s.eta_s === undefined ? "—" : formatHMS(s.eta_s);
      } else if (norm === "finished") {
        el("statEta").textContent = "00:00:00";
      } else {
        el("statEta").textContent = "—";
      }
    }

    if (s.new_log?.length) {
      for (const ln of s.new_log) appendLog(ln);
    }

    if (wizardStep === 4) {
      const done = norm === "finished" || norm === "error" || norm === "stopped";
      el("btnStop").disabled = norm !== "running";
      el("btnCopyLog").disabled = false;
      el("btnBackAfterDone").hidden = !done;
      el("wizTab4").disabled = false;
      if (prevPollState === "running" && done) {
        showToast(norm === "finished" ? "Export finished." : `Export ended: ${norm}`, norm === "finished" ? "info" : "error");
      }
    }

    syncWizardTabDisabled();
    refreshPresetHighlight();
    prevPollState = norm;
  } catch {
    el("serverBadge").textContent = "Server • disconnected";
  }
}

async function startExport() {
  if (!lastPreflightOk) return showToast("Verify your recording first (step 1).", "error");
  const opts = gatherOptions();

  wizardGo(4);
  el("wizTab4").disabled = false;
  el("btnBackAfterDone").hidden = true;

  el("log").textContent = "";
  el("bar").style.width = "0%";
  el("progPercent").textContent = "0%";
  el("statRendered").textContent = "00:00:00";
  el("statElapsed").textContent = "00:00:00";
  el("statEta").textContent = "—";
  el("progPercentSub").textContent = "of recording encoded";

  prevPollState = "idle";

  appendLog("Starting export…");

  try {
    const r = await api("/api/export/start", { method: "POST", body: JSON.stringify(opts) });
    appendLog(`Started — PID ${r.pid}`);
    showToast("Export running — watch live log.", "info");
    el("btnStop").disabled = false;
  } catch (e) {
    appendLog(`Start failed: ${e}`);
    showToast(String(e), "error");
    wizardGo(3);
  }
}

async function stopExport() {
  appendLog("Stop requested.");
  try {
    await api("/api/export/stop", { method: "POST", body: JSON.stringify({}) });
    showToast("Stopping…", "info");
  } catch (e) {
    appendLog(`Stop failed: ${e}`);
  }
}

async function pastePath() {
  try {
    const t = await navigator.clipboard.readText();
    el("manualFolder").value = t.trim();
    el("folder").value = "";
    clearVerification();
    showToast("Path pasted — click Next to verify.", "info");
  } catch {
    showToast("Use Ctrl+V in the path box.", "error");
  }
}

async function copyLog() {
  const text = el("log").textContent || "";
  if (!text.trim()) return showToast("Log is empty.", "info");
  try {
    await navigator.clipboard.writeText(text);
    showToast("Copied.", "info");
  } catch {
    showToast("Manual copy.", "error");
  }
}

// —— Buttons ——

el("pickZipBtn").addEventListener("click", () => el("fileZipInput").click());
el("pickFolderBtn").addEventListener("click", () => el("fileFolderInput").click());

el("sessionUrl").addEventListener("input", syncZipUrlUi);
el("sessionUrl").addEventListener("change", syncZipUrlUi);
el("btnCopyZipUrl").addEventListener("click", copyZipUrl);

el("btnToUpload").addEventListener("click", () => wizardGo(2));

function deriveZipUrlFromSession(sessionUrl) {
  const raw = String(sessionUrl || "").trim();
  if (!raw) return "";
  try {
    const u = new URL(raw);
    const basePath = u.pathname.endsWith("/") ? u.pathname : u.pathname + "/";
    return `${u.protocol}//${u.host}${basePath}output/stream.zip?download=zip`;
  } catch {
    return "";
  }
}

function syncZipUrlUi() {
  const s = el("sessionUrl").value.trim();
  const zip = deriveZipUrlFromSession(s);
  el("zipUrl").value = zip || "—";
  const ok = Boolean(zip);
  el("btnCopyZipUrl").disabled = !ok;
  const a = el("btnOpenZipUrl");
  if (ok) {
    a.href = zip;
    a.setAttribute("aria-disabled", "false");
  } else {
    a.href = "#";
    a.setAttribute("aria-disabled", "true");
  }
  el("btnToUpload").disabled = !ok;
}

async function copyZipUrl() {
  const v = el("zipUrl").value.trim();
  if (!v || v === "—") return;
  try {
    await navigator.clipboard.writeText(v);
    showToast("Copied download URL.", "info");
  } catch {
    showToast("Manual copy.", "error");
  }
}

el("fileZipInput").addEventListener("change", async () => {
  const f = el("fileZipInput").files?.[0];
  el("fileZipInput").value = "";
  if (f && isZipFilename(f.name)) await ingestZipDroppedOrPicked(f);
  else if (f) showToast("Pick a .zip file.", "error");
});

el("fileFolderInput").addEventListener("change", async () => {
  const fl = el("fileFolderInput").files;
  el("fileFolderInput").value = "";
  if (!fl?.length) return;
  const n = fl.length;
  uploadBusy = true;
  dropZoneBusy(true);
  showPickStatus(`Uploading ${n} files — this can take a while…`);
  setStep1PrimaryBusy(true, "Uploading…");
  try {
    const data = await uploadFolderForm(fl);
    await ingestUploadResult(data);
  } catch (e) {
    clearVerification();
    showToast(String(e), "error");
  } finally {
    hidePickStatus();
    uploadBusy = false;
    dropZoneBusy(false);
    setStep1PrimaryBusy(false);
  }
});

el("btnPreflight").addEventListener("click", () => runPreflight({ toastOk: false }));
el("btnPastePath").addEventListener("click", pastePath);

el("btnToSettings").addEventListener("click", advanceFromStep1);

el("btnBackToDownload").addEventListener("click", () => wizardGo(1));

el("btnBackToUpload").addEventListener("click", () => wizardGo(2));

el("btnReplaceSession").addEventListener("click", () => {
  el("folder").value = "";
  el("manualFolder").value = "";
  clearVerification();
  wizardGo(2);
});

el("btnStartExport").addEventListener("click", startExport);
el("btnStop").addEventListener("click", stopExport);
el("btnCopyLog").addEventListener("click", copyLog);

el("btnBackAfterDone").addEventListener("click", () => {
  wizardGo(3);
  el("btnBackAfterDone").hidden = true;
  showToast('Ready to tweak settings — click "Start export" again.', "info");
});

el("wizTab1").addEventListener("click", () => wizardGo(1));
el("wizTab2").addEventListener("click", () => wizardGo(2));
el("wizTab3").addEventListener("click", () => {
  if (!lastPreflightOk) return;
  wizardGo(3);
  refreshSystemOnly();
});
el("wizTab4").addEventListener("click", () => {
  if (wizardStep === 4) return;
});

el("presetFast").addEventListener("click", () => applyPresetBtn("fast"));
el("presetBalanced").addEventListener("click", () => applyPresetBtn("balanced"));
el("presetQuality").addEventListener("click", () => applyPresetBtn("quality"));

el("resolutionPreset").addEventListener("change", () => {
  applyResolutionPreset();
  refreshPresetHighlight();
  scheduleEstimatesRefresh();
});

["w", "h", "fps", "crf", "preset", "encoder", "out", "outDir", "burnChat", "chapters", "skipBreaks"].forEach(
  (id) => {
  el(id)?.addEventListener("change", () => {
    refreshPresetHighlight();
    scheduleEstimatesRefresh();
  });
  el(id)?.addEventListener("input", () => {
    refreshPresetHighlight();
    scheduleEstimatesRefresh();
  });
});

function onManualFolderEdit() {
  el("folder").value = "";
  clearVerification();
  refreshPresetHighlight();
  syncStep1NextButton();
}

el("manualFolder").addEventListener("change", onManualFolderEdit);
el("manualFolder").addEventListener("input", onManualFolderEdit);

// Boot
clearVerification();
applyResolutionPreset();
wizardGo(1);
syncZipUrlUi();
syncStep1NextButton();
pollTimer = window.setInterval(poll, 700);
poll();
