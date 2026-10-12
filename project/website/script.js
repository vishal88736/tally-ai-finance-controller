// ---------------------------------------------------------------
// TALLY — AI Finance Controller front end.
// Talks to model/api/app.py over the endpoints documented in model/README.md.
// ---------------------------------------------------------------

const state = {
  runId: null,
  report: null,
  exceptions: [],
};

const $ = (id) => document.getElementById(id);
const apiBase = () => {
  const custom = $("apiBase")?.value?.trim();
  if (custom) return custom.replace(/\/$/, "");
  if (window.location.protocol.startsWith("http") && window.location.origin && window.location.origin !== "null") {
    return window.location.origin;
  }
  return "http://localhost:8001";
};

// ---------- settings drawer ----------
$("settingsToggle").addEventListener("click", () => {
  $("settingsDrawer").hidden = !$("settingsDrawer").hidden;
});

// ---------- tab navigation ----------
document.querySelectorAll(".nav-tab").forEach((btn) => {
  btn.addEventListener("click", () => {
    if (btn.disabled) return;
    switchView(btn.dataset.view);
  });
});

function switchView(view) {
  document.querySelectorAll(".nav-tab").forEach((b) => b.classList.toggle("active", b.dataset.view === view));
  document.querySelectorAll(".view").forEach((v) => v.classList.toggle("active", v.id === `view-${view}`));
}

function unlockTabs() {
  document.querySelectorAll(".nav-tab").forEach((b) => (b.disabled = false));
}

// ---------- file slots ----------
[
  ["fileInvoices", "nameInvoices"],
  ["fileSettlements", "nameSettlements"],
  ["fileBank", "nameBank"],
  ["fileGroundTruth", "nameGroundTruth"],
].forEach(([inputId, labelId]) => {
  $(inputId).addEventListener("change", (e) => {
    $(labelId).textContent = e.target.files[0] ? e.target.files[0].name : "No file selected";
    updateRunButton();
  });
});

function updateRunButton() {
  const runBtn = $("runBtn");
  const label = runBtn.querySelector(".run-label");
  if ($("fileInvoices").files.length) {
    runBtn.disabled = false;
    label.textContent = "Run reconciliation";
  } else {
    runBtn.disabled = true;
    label.textContent = "Add an invoices file to begin";
  }
}

// ---------- run reconciliation ----------
$("runBtn").addEventListener("click", runReconciliation);

async function runReconciliation() {
  hideError();
  $("loadingState").hidden = false;
  $("runBtn").disabled = true;
  cycleLoadingMessages();

  const formData = new FormData();
  formData.append("invoices_file", $("fileInvoices").files[0]);
  if ($("fileSettlements").files[0]) formData.append("settlements_file", $("fileSettlements").files[0]);
  if ($("fileBank").files[0]) formData.append("bank_file", $("fileBank").files[0]);
  if ($("fileGroundTruth").files[0]) formData.append("ground_truth_file", $("fileGroundTruth").files[0]);
  formData.append("enable_llm_investigation", $("cfgEnableLlm").checked);
  formData.append("amount_tolerance_abs", $("cfgAmountTol").value || "1.0");
  formData.append("date_window_days", $("cfgDateWindow").value || "3");
  formData.append("auto_reconcile_threshold", $("cfgAutoThresh").value || "95");
  formData.append("ai_investigation_threshold", $("cfgAiThresh").value || "80");

  try {
    const res = await fetch(`${apiBase()}/api/reconcile`, { method: "POST", body: formData });
    const data = await res.json();
    if (!res.ok || data.error) throw new Error(data.error || `Server responded ${res.status}`);

    state.runId = data.run_id;
    state.report = data.report;
    state.exceptions = data.report.exceptions || [];

    unlockTabs();
    renderOverview();
    renderExceptionsList();
    renderAuditTrail();
    switchView("overview");
  } catch (err) {
    showError(`Couldn't reach the AI Finance Controller: ${err.message}. Check Settings → API endpoint, and that "uvicorn api.app:app" is running in model/.`);
  } finally {
    $("loadingState").hidden = true;
    $("runBtn").disabled = false;
  }
}

function cycleLoadingMessages() {
  const messages = ["Parsing documents…", "Building the transaction lifecycle…", "Running constrained assignment…", "Investigating ambiguous cases…", "Writing the audit trail…", "Computing precision, recall, F1…"];
  let i = 0;
  $("loadingText").textContent = messages[0];
  const interval = setInterval(() => {
    i = (i + 1) % messages.length;
    $("loadingText").textContent = messages[i];
    if ($("loadingState").hidden) clearInterval(interval);
  }, 1600);
}

function showError(msg) {
  $("errorMsg").textContent = msg;
  $("errorMsg").hidden = false;
}
function hideError() {
  $("errorMsg").hidden = true;
}

// ---------- overview ----------
function renderOverview() {
  const m = state.report.metrics;
  $("overviewRunId").textContent = state.runId;

  const stamp = $("matchStamp");
  stamp.classList.remove("stamp-neutral", "stamp-good", "stamp-warn");
  stamp.classList.add(m.match_rate >= 90 ? "stamp-good" : m.match_rate >= 60 ? "stamp-neutral" : "stamp-warn");
  stamp.style.animation = "none"; void stamp.offsetWidth; stamp.style.animation = "";
  $("matchRateValue").textContent = `${m.match_rate}%`;

  $("mPrecision").textContent = `${(m.precision * 100).toFixed(1)}%`;
  $("mRecall").textContent = `${(m.recall * 100).toFixed(1)}%`;
  $("mF1").textContent = `${(m.f1 * 100).toFixed(1)}%`;
  $("mAuto").textContent = m.auto_resolved;
  $("mHuman").textContent = m.human_review;
  $("mUnresolved").textContent = m.unresolved;
  $("mReconciledValue").textContent = formatInr(m.reconciled_value);
  $("mUnreconciledValue").textContent = formatInr(m.unreconciled_value);
  $("mThroughput").textContent = `${m.throughput_records_per_sec.toLocaleString()} rec/s`;
  $("mLlmCalls").textContent = m.llm_calls;

  const counts = {};
  (state.exceptions || []).forEach((e) => (counts[e.category] = (counts[e.category] || 0) + 1));
  const maxCount = Math.max(1, ...Object.values(counts));
  const bars = $("categoryBars");
  bars.innerHTML = "";
  Object.entries(counts).sort((a, b) => b[1] - a[1]).forEach(([cat, count]) => {
    const row = document.createElement("div");
    row.className = "category-bar-row";
    row.innerHTML = `
      <span>${cat}</span>
      <span class="category-bar-track"><span class="category-bar-fill" style="width:${(count / maxCount) * 100}%"></span></span>
      <span class="category-bar-count">${count}</span>`;
    bars.appendChild(row);
  });
  if (!Object.keys(counts).length) {
    bars.innerHTML = `<p style="color:var(--paper-text-dim)">No exceptions — every record reconciled cleanly.</p>`;
  }
}

function formatInr(value) {
  return `₹${Number(value || 0).toLocaleString("en-IN", { maximumFractionDigits: 2 })}`;
}

// ---------- exceptions list + detail ----------
function renderExceptionsList() {
  const body = $("exceptionsBody");
  body.innerHTML = "";
  state.exceptions.forEach((exc) => {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${exc.exception_id}</td>
      <td>${exc.category}</td>
      <td>${exc.severity}</td>
      <td class="num">${exc.confidence?.toFixed?.(1) ?? exc.confidence}%</td>
      <td><span class="status-pill status-${exc.status}">${exc.status}</span></td>
      <td><button class="view-link" data-id="${exc.exception_id}">View →</button></td>
    `;
    tr.querySelector(".view-link").addEventListener("click", () => showExceptionDetail(exc.exception_id));
    body.appendChild(tr);
  });
  if (!state.exceptions.length) {
    body.innerHTML = `<tr><td colspan="6" style="color:var(--paper-text-dim)">No open exceptions.</td></tr>`;
  }
}

async function showExceptionDetail(exceptionId) {
  $("exceptionsListWrap").hidden = true;
  $("exceptionDetailWrap").hidden = false;
  $("exceptionDetailContent").innerHTML = `<p style="color:var(--paper-text-dim)">Loading…</p>`;

  try {
    const res = await fetch(`${apiBase()}/api/runs/${state.runId}/exceptions/${exceptionId}`);
    const data = await res.json();
    if (data.error) throw new Error(data.error);
    renderExceptionDetail(data);
  } catch (err) {
    $("exceptionDetailContent").innerHTML = `<p class="error-msg">Couldn't load exception detail: ${err.message}</p>`;
  }
}

function renderExceptionDetail(data) {
  const { exception: exc, transaction: txn, resolution } = data;
  const inv = txn?.invoice, settle = txn?.settlement, bank = txn?.bank_transaction;

  const cards = [];
  if (inv) cards.push(`<div class="evidence-card"><div class="label">Invoice ${inv.record_id}</div><div class="value">₹${inv.amount}</div></div>`);
  if (settle) cards.push(`<div class="evidence-card"><div class="label">Settlement ${settle.record_id}</div><div class="value">₹${settle.net_amount}</div></div>`);
  if (bank) cards.push(`<div class="evidence-card"><div class="label">Bank ${bank.record_id}</div><div class="value">₹${bank.amount}</div></div>`);

  const checklist = (exc.evidence || []).map((e) => `<li>${escapeHtml(e)}</li>`).join("");

  const decisionType = resolution?.action_type || "ESCALATE";
  const isResolve = decisionType !== "ESCALATE" && decisionType !== "REQUEST_HUMAN_REVIEW";
  const bannerClass = isResolve ? "resolve" : "escalate";
  const bannerLabel = isResolve ? "SAFE TO RESOLVE" : decisionType === "REQUEST_HUMAN_REVIEW" ? "NEEDS HUMAN REVIEW" : "ESCALATED";

  $("exceptionDetailContent").innerHTML = `
    <h3>Exception ${exc.exception_id} — ${exc.category}</h3>
    <div class="evidence-chain">${cards.join("")}</div>

    <h3>Rule-engine evidence</h3>
    <ul class="checklist">${checklist || "<li>No additional evidence recorded.</li>"}</ul>

    <div class="decision-banner ${bannerClass}">
      <span class="decision-label">${bannerLabel}</span>
      <span>Confidence: ${((resolution?.confidence ?? exc.confidence / 100) * 100).toFixed(1)}%</span>
    </div>
    <p><strong>Reason:</strong> ${escapeHtml(resolution?.reason || "No resolution recorded yet.")}</p>
    ${resolution?.policy_used?.length ? `<p><strong>Policy used:</strong> ${resolution.policy_used.join(", ")}</p>` : ""}

    <div class="action-row">
      <button class="approve" data-action="approve">Approve resolution</button>
      <button class="escalate-btn" data-action="escalate">Escalate</button>
      <button data-action="audit">View audit trail →</button>
    </div>
  `;

  $("exceptionDetailContent").querySelector('[data-action="approve"]').addEventListener("click", () => actOnException(exc.exception_id, "approve"));
  $("exceptionDetailContent").querySelector('[data-action="escalate"]').addEventListener("click", () => actOnException(exc.exception_id, "escalate"));
  $("exceptionDetailContent").querySelector('[data-action="audit"]').addEventListener("click", () => {
    switchView("audit");
    renderAuditTrail(exc.transaction_id);
  });
}

async function actOnException(exceptionId, action) {
  try {
    const res = await fetch(`${apiBase()}/api/runs/${state.runId}/exceptions/${exceptionId}/${action}`, { method: "POST" });
    const data = await res.json();
    if (data.error) throw new Error(data.error);
    // reflect the status change locally
    const exc = state.exceptions.find((e) => e.exception_id === exceptionId);
    if (exc) exc.status = data.exception.status;
    renderExceptionsList();
    showExceptionDetail(exceptionId);
  } catch (err) {
    alert(`Action failed: ${err.message}`);
  }
}

$("backToExceptions").addEventListener("click", () => {
  $("exceptionDetailWrap").hidden = true;
  $("exceptionsListWrap").hidden = false;
});

// ---------- audit trail ----------
async function renderAuditTrail(transactionId) {
  const body = $("auditBody");
  body.innerHTML = `<tr><td colspan="6" style="color:var(--paper-text-dim)">Loading…</td></tr>`;
  try {
    const url = new URL(`${apiBase()}/api/runs/${state.runId}/audit`);
    if (transactionId) url.searchParams.set("transaction_id", transactionId);
    const res = await fetch(url);
    const entries = await res.json();
    body.innerHTML = "";
    (entries || []).forEach((e) => {
      const tr = document.createElement("tr");
      tr.innerHTML = `
        <td>${e.transaction_id}</td>
        <td>${e.decision}</td>
        <td class="num">${(e.confidence <= 1 ? e.confidence * 100 : e.confidence).toFixed(1)}%</td>
        <td>${escapeHtml(e.reason)}</td>
        <td>${(e.policy_used || []).join(", ")}</td>
        <td>${new Date(e.timestamp).toLocaleString()}</td>
      `;
      body.appendChild(tr);
    });
    if (!entries.length) body.innerHTML = `<tr><td colspan="6" style="color:var(--paper-text-dim)">No audit entries yet.</td></tr>`;
  } catch (err) {
    body.innerHTML = `<tr><td colspan="6" class="error-msg">Failed to load audit trail: ${err.message}</td></tr>`;
  }
}

// ---------- ask controller ----------
document.querySelectorAll(".chip").forEach((chip) => {
  chip.addEventListener("click", () => {
    $("askInput").value = chip.dataset.q;
    askController();
  });
});
$("askBtn").addEventListener("click", askController);
$("askInput").addEventListener("keydown", (e) => { if (e.key === "Enter") askController(); });

async function askController() {
  const question = $("askInput").value.trim();
  if (!question || !state.runId) return;
  const answerEl = $("askAnswer");
  answerEl.textContent = "Thinking…";

  try {
    const formData = new FormData();
    formData.append("question", question);
    const res = await fetch(`${apiBase()}/api/runs/${state.runId}/ask`, { method: "POST", body: formData });
    const data = await res.json();

    if (data.error && !data.answer) {
      answerEl.innerHTML = `<span class="error-msg" style="display:block;">${escapeHtml(data.error)}</span>`;
      return;
    }

    let html = escapeHtml(data.answer || "No answer returned.");
    if (data.tool_calls?.length) {
      html += `<ul class="tool-trace">${data.tool_calls.map((c) => `<li>called <strong>${c.tool}</strong>(${JSON.stringify(c.input)})</li>`).join("")}</ul>`;
    }
    answerEl.innerHTML = html;
  } catch (err) {
    answerEl.innerHTML = `<span class="error-msg" style="display:block;">Request failed: ${escapeHtml(err.message)}</span>`;
  }
}

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str ?? "";
  return div.innerHTML;
}
