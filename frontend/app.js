// Image Text Extraction — frontend logic.
//
// Talks to the API defined in ComputeStack (infra/stacks/compute_stack.py):
//   POST /uploads       -> { id, uploadUrl }
//   PUT  <uploadUrl>     -> browser uploads the image straight to S3
//   GET  /results/{id}  -> { id, status, extracted_text?, error? }
//   GET  /results       -> { items: [...] }  (most recent first)
//
// window.API_BASE_URL comes from config.js, generated at deploy time by
// FrontendStack — see that file for why.

const POLL_INTERVAL_MS = 2000;
const POLL_MAX_ATTEMPTS = 30; // ~60s — generous for a single sync Textract call

const fileInput = document.getElementById("file-input");
const filePickerLabel = document.getElementById("file-picker-label");
const uploadButton = document.getElementById("upload-button");
const uploadStatus = document.getElementById("upload-status");
const resultPanel = document.getElementById("result-panel");
const resultText = document.getElementById("result-text");
const historyList = document.getElementById("history-list");
const envBadge = document.getElementById("env-badge");

// window.ENVIRONMENT_NAME comes from config.js, same as API_BASE_URL — see
// FrontendStack for why. Dev and prod are two independent copies of the
// same stacks with two different, equally random-looking CloudFront URLs;
// this badge is the fix for "which one am I looking at right now."
function initEnvironmentBadge() {
  const env = window.ENVIRONMENT_NAME;
  if (!env) return; // not set outside a real deploy — leave it hidden
  envBadge.textContent = env.toUpperCase();
  envBadge.classList.add(`env-badge-${env}`);
  envBadge.hidden = false;
}

initEnvironmentBadge();

function apiUrl(path) {
  const base = (window.API_BASE_URL || "").replace(/\/$/, "");
  return `${base}${path}`;
}

function setStatus(message, isError = false) {
  uploadStatus.textContent = message;
  uploadStatus.classList.toggle("status-error", isError);
}

function formatTimestamp(isoString) {
  try {
    return new Date(isoString).toLocaleString();
  } catch {
    return isoString;
  }
}

// --- file selection -----------------------------------------------------

fileInput.addEventListener("change", () => {
  const file = fileInput.files[0];
  filePickerLabel.textContent = file ? file.name : "Choose an image…";
  uploadButton.disabled = !file;
  setStatus("");
});

// --- upload + extract flow -----------------------------------------------

uploadButton.addEventListener("click", async () => {
  const file = fileInput.files[0];
  if (!file) return;

  uploadButton.disabled = true;
  resultPanel.hidden = true;

  try {
    setStatus("Requesting upload URL…");
    const createResponse = await fetch(apiUrl("/uploads"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ contentType: file.type }),
    });
    if (!createResponse.ok) {
      throw new Error(`Could not start upload (${createResponse.status})`);
    }
    const { id, uploadUrl } = await createResponse.json();

    setStatus("Uploading image…");
    const putResponse = await fetch(uploadUrl, {
      method: "PUT",
      headers: { "Content-Type": file.type },
      body: file,
    });
    if (!putResponse.ok) {
      throw new Error(`Upload to storage failed (${putResponse.status})`);
    }

    setStatus("Extracting text… this usually takes a few seconds.");
    const record = await pollForResult(id);

    if (record.status === "done") {
      showResult(record.extracted_text || "(no text found)");
      setStatus("Done.");
    } else if (record.status === "failed") {
      setStatus(`Extraction failed: ${record.error || "unknown error"}`, true);
    } else {
      setStatus("Still processing — it'll show up in History once it's done.");
    }

    await loadHistory();
  } catch (err) {
    setStatus(err.message || "Something went wrong.", true);
  } finally {
    uploadButton.disabled = false;
    fileInput.value = "";
    filePickerLabel.textContent = "Choose an image…";
  }
});

async function pollForResult(id) {
  for (let attempt = 0; attempt < POLL_MAX_ATTEMPTS; attempt++) {
    const response = await fetch(apiUrl(`/results/${id}`));
    if (response.ok) {
      const record = await response.json();
      if (record.status === "done" || record.status === "failed") {
        return record;
      }
    }
    await new Promise((resolve) => setTimeout(resolve, POLL_INTERVAL_MS));
  }
  return { status: "pending" };
}

function showResult(text) {
  resultText.textContent = text;
  resultPanel.hidden = false;
}

// --- history ---------------------------------------------------------------

async function loadHistory() {
  try {
    const response = await fetch(apiUrl("/results"));
    if (!response.ok) return;
    const { items } = await response.json();
    renderHistory(items || []);
  } catch {
    // History is a nice-to-have; a failed refresh here shouldn't block
    // the upload flow above, so this is deliberately silent.
  }
}

function renderHistory(items) {
  historyList.innerHTML = "";

  if (items.length === 0) {
    const empty = document.createElement("li");
    empty.className = "history-empty";
    empty.textContent = "No extractions yet.";
    historyList.appendChild(empty);
    return;
  }

  for (const item of items) {
    const li = document.createElement("li");
    li.className = "history-item";

    const button = document.createElement("button");
    button.type = "button";
    button.className = "history-item-button";
    button.innerHTML = `<span class="history-time">${formatTimestamp(item.created_at)}</span><span class="history-status history-status-${item.status}">${item.status}</span>`;
    button.addEventListener("click", () => viewHistoryItem(item.id));

    li.appendChild(button);
    historyList.appendChild(li);
  }
}

async function viewHistoryItem(id) {
  setStatus("Loading…");
  try {
    const response = await fetch(apiUrl(`/results/${id}`));
    if (!response.ok) throw new Error("Could not load that result.");
    const record = await response.json();
    if (record.status === "done") {
      showResult(record.extracted_text || "(no text found)");
      setStatus("");
    } else if (record.status === "failed") {
      setStatus(`Extraction failed: ${record.error || "unknown error"}`, true);
    } else {
      setStatus("Still processing.");
    }
  } catch (err) {
    setStatus(err.message, true);
  }
}

loadHistory();
