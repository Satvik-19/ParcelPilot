/* ParcelPilot UI — vanilla JS, no libraries. All dynamic text is escaped
   before insertion; the backend is the only source of truth. */
"use strict";

const state = {
  sessions: [],
  sessionKey: null,
  busy: false,
  insightsLoadedFor: null,
};

const el = {
  sessionSelect: document.getElementById("session-select"),
  sessionBadge: document.getElementById("session-badge"),
  tabChat: document.getElementById("tab-chat"),
  tabInsights: document.getElementById("tab-insights"),
  viewChat: document.getElementById("view-chat"),
  viewInsights: document.getElementById("view-insights"),
  messages: document.getElementById("messages"),
  emptyState: document.getElementById("empty-state"),
  composer: document.getElementById("composer"),
  input: document.getElementById("composer-input"),
  send: document.getElementById("composer-send"),
  insightsContent: document.getElementById("insights-content"),
  insightsRefresh: document.getElementById("insights-refresh"),
};

/* ------------------------------------------------------------------ utils */

function esc(text) {
  const div = document.createElement("div");
  div.textContent = text == null ? "" : String(text);
  return div.innerHTML;
}

function formatAnswer(text) {
  /* Escaped prose with only two safe enrichments: paragraphs and **bold**. */
  return esc(text)
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/`([^`]+)`/g, "<code>$1</code>");
}

async function api(path, options = {}) {
  const headers = { "X-Session-Key": state.sessionKey || "" };
  if (options.body !== undefined) headers["Content-Type"] = "application/json";
  const response = await fetch(path, { ...options, headers });
  let payload = {};
  try { payload = await response.json(); } catch (_) { /* non-JSON guard */ }
  return { ok: response.ok, status: response.status, payload };
}

function currentSession() {
  return state.sessions.find((s) => s.key === state.sessionKey) || null;
}

/* ---------------------------------------------------------------- sessions */

async function loadSessions() {
  const { ok, status, payload } = await api("/api/sessions").catch(() => ({
    ok: false, status: 0, payload: { error: "Network error." },
  }));
  if (!ok || !payload.sessions) {
    const msg = payload.error || payload.message || `HTTP ${status}`;
    el.sessionSelect.innerHTML =
      `<option disabled selected>Failed to load sessions: ${esc(msg)}</option>`;
    state.sessions = [];
    return;
  }
  state.sessions = payload.sessions;
  el.sessionSelect.innerHTML = state.sessions
    .map((s) => `<option value="${esc(s.key)}">${esc(s.label)}</option>`)
    .join("");
  const staff = state.sessions.find((s) => s.role === "staff");
  state.sessionKey = (state.sessions[0] || {}).key || null;
  el.sessionSelect.value = state.sessionKey || "";
  applySession();
  if (!staff) el.tabInsights.classList.add("hidden");
}

function applySession() {
  const session = currentSession();
  const isStaff = session && session.role === "staff";
  el.sessionBadge.textContent = isStaff ? "Internal staff" : "Customer";
  el.sessionBadge.classList.toggle("staff", isStaff);
  el.tabInsights.classList.toggle("hidden", !isStaff);
  if (!isStaff) switchView("chat");
  el.messages.innerHTML = "";
  el.messages.appendChild(el.emptyState);
  el.emptyState.classList.remove("hidden");
  state.insightsLoadedFor = null;
  if (isStaff && el.viewInsights.classList.contains("hidden") === false) {
    loadInsights();
  }
}

el.sessionSelect.addEventListener("change", () => {
  state.sessionKey = el.sessionSelect.value;
  applySession();
});

/* -------------------------------------------------------------------- tabs */

function switchView(view) {
  const insights = view === "insights";
  el.tabChat.classList.toggle("active", !insights);
  el.tabInsights.classList.toggle("active", insights);
  el.viewChat.classList.toggle("hidden", insights);
  el.viewInsights.classList.toggle("hidden", !insights);
  if (insights && state.insightsLoadedFor !== state.sessionKey) loadInsights();
}
el.tabChat.addEventListener("click", () => switchView("chat"));
el.tabInsights.addEventListener("click", () => switchView("insights"));

/* -------------------------------------------------------------------- chat */

function addUserMessage(text) {
  el.emptyState.classList.add("hidden");
  const node = document.createElement("div");
  node.className = "msg msg-user";
  node.textContent = text;
  el.messages.appendChild(node);
}

function addTyping() {
  const node = document.createElement("div");
  node.className = "typing";
  node.textContent = "Checking trusted sources…";
  el.messages.appendChild(node);
  el.messages.scrollTop = el.messages.scrollHeight;
  return node;
}

function statusBadge(stateName) {
  const label = { ANSWER: "ANSWER", ESCALATE: "ESCALATE",
                  INSUFFICIENT_EVIDENCE: "INSUFFICIENT EVIDENCE" }[stateName]
                || stateName;
  return `<span class="badge badge-state-${esc(stateName)}">${esc(label)}</span>`;
}

function rankLabel(ev) {
  const names = { 1: "Agreement", 2: "Policy / SOP", 3: "Product docs" };
  if (ev.authority_rank == null) return `<span class="ev-rank none">authority: NONE</span>`;
  return `<span class="ev-rank">${esc(names[ev.authority_rank] || "rank " + ev.authority_rank)}</span>`;
}

function renderEvidence(evidence) {
  if (!evidence || !evidence.length) return `<p class="muted">No document evidence was needed for this turn.</p>`;
  return evidence.map((ev) => {
    let note = "";
    if (ev.overridden_by) {
      note = `<div class="ev-note overridden">Overridden by ${esc(ev.overridden_by)} — shown for context only.</div>`;
    } else if (ev.excluded_reason) {
      note = `<div class="ev-note excluded">${esc(ev.excluded_reason)}</div>`;
    }
    return `<div class="ev-item">
      <span class="ev-doc">${esc(ev.source_doc)}</span>${rankLabel(ev)}
      <div class="ev-section">${esc(ev.section)} · ${esc(ev.status)} · ${esc(ev.applicable_to || "")}</div>
      ${note}
    </div>`;
  }).join("");
}

function renderTools(tools) {
  if (!tools || !tools.length) return `<p class="muted">No tools were called.</p>`;
  return tools.map((t) =>
    `<span class="tool-chip"><span class="dot dot-${esc(t.status)}"></span>${esc(t.name)} <small>${esc(t.status)} · ${t.latency_ms} ms</small></span>`
  ).join("");
}

function renderActionCard(action) {
  const card = document.createElement("div");
  card.className = "action-card";
  card.innerHTML = `
    <h4>Pending action — your explicit confirmation is required</h4>
    <div class="action-desc">${esc(action.description)}</div>
    <div class="action-payload">Exact payload: ${esc(JSON.stringify(action.payload))}</div>
    <div class="action-row">
      <button type="button">Confirm &amp; execute</button>
      <span class="expiry"></span>
    </div>`;
  const button = card.querySelector("button");
  const expiry = card.querySelector(".expiry");
  let remaining = action.seconds_until_expiry;

  const tick = () => {
    if (card.dataset.done) return;
    if (remaining <= 0) {
      expiry.textContent = "Draft expired — ask again to re-draft.";
      button.disabled = true;
      return;
    }
    const mm = Math.floor(remaining / 60), ss = String(remaining % 60).padStart(2, "0");
    expiry.textContent = `Expires in ${mm}:${ss}`;
    remaining -= 1;
  };
  tick();
  const timer = setInterval(tick, 1000);

  button.addEventListener("click", async () => {
    button.disabled = true;              /* double-click guard (UI-side; the
                                              backend one-shot claim is the
                                              real protection) */
    expiry.textContent = "Confirming…";
    const { ok, payload } = await api("/api/actions/confirm", {
      method: "POST",
      body: JSON.stringify({ action_id: action.action_id, token: action.token }),
    }).catch(() => ({ ok: false, payload: { message: "Network error." } }));
    clearInterval(timer);
    card.dataset.done = "1";
    if (ok && payload.status === "executed") {
      card.classList.add("executed");
      card.innerHTML = `
        <h4>Action executed</h4>
        <div class="action-desc">${esc(payload.effect || "")}</div>
        <div class="action-payload">Confirmed at ${esc(payload.confirmed_at)} · ${esc(payload.action_type)}</div>`;
    } else {
      card.classList.add("refused");
      card.innerHTML = `
        <h4>Confirmation refused</h4>
        <div class="action-desc">${esc(payload.message || "The action could not be confirmed.")}</div>
        <div class="action-payload">Reason: ${esc(payload.rejection_code || "UNKNOWN")}</div>`;
    }
  });
  return card;
}

function addAgentMessage(data) {
  const node = document.createElement("div");
  node.className = "msg msg-agent" + (data.provider_failure ? " provider-failure" : "");
  const latency = data.trace && data.trace.total_latency_ms != null
    ? `${data.trace.total_latency_ms} ms` : "";
  node.innerHTML = `
    <div class="agent-head">
      <span class="who">ParcelPilot</span>
      ${statusBadge(data.answer_state)}
      <span class="latency">${esc(latency)}</span>
    </div>
    <div class="answer-text">${data.answer ? formatAnswer(data.answer)
        : `<em class="muted">${esc(data.provider_failure
            ? "The model service is unavailable right now — the request was escalated for human follow-up."
            : "No answer text was produced.")}</em>`}</div>
    <details class="provenance">
      <summary>Tools &amp; evidence used this turn</summary>
      <div class="prov-grid">
        <div class="prov-block"><h4>Tools</h4>${renderTools(data.tools)}</div>
        <div class="prov-block"><h4>Evidence</h4>${renderEvidence(data.evidence)}</div>
      </div>
    </details>`;
  if (data.pending_action) node.appendChild(renderActionCard(data.pending_action));
  el.messages.appendChild(node);
  el.messages.scrollTop = el.messages.scrollHeight;
}

el.composer.addEventListener("submit", async (event) => {
  event.preventDefault();
  const text = el.input.value.trim();
  if (!text || state.busy) return;
  state.busy = true;
  el.send.disabled = true;
  el.input.value = "";
  addUserMessage(text);
  const typing = addTyping();
  try {
    const { ok, payload } = await api("/api/chat", {
      method: "POST",
      body: JSON.stringify({ message: text }),
    });
    typing.remove();
    if (ok) {
      addAgentMessage(payload);
    } else {
      addAgentMessage({
        answer: payload.error || "The request could not be processed.",
        answer_state: "ESCALATE",
        provider_failure: false,
        tools: [], evidence: [], trace: {},
      });
    }
  } catch (_) {
    typing.remove();
    addAgentMessage({
      answer: "Connection error — the server could not be reached. Nothing was executed.",
      answer_state: "ESCALATE", provider_failure: false,
      tools: [], evidence: [], trace: {},
    });
  } finally {
    state.busy = false;
    el.send.disabled = false;
    el.input.focus();
  }
});

el.input.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    el.composer.requestSubmit();
  }
});

/* ---------------------------------------------------------------- insights */

async function loadInsights() {
  state.insightsLoadedFor = state.sessionKey;
  el.insightsContent.innerHTML = `<p class="muted">Loading insights…</p>`;
  const { ok, status, payload } = await api("/api/insights").catch(() => ({
    ok: false, status: 0, payload: { message: "Network error." },
  }));
  if (!ok) {
    el.insightsContent.innerHTML =
      `<p class="muted">Insights unavailable (${esc(payload.message || payload.rejection_code || status)}).</p>`;
    return;
  }
  renderInsights(payload);
}
el.insightsRefresh.addEventListener("click", loadInsights);

function renderInsights(data) {
  const summary = data.summary || {};
  const sla = data.sla_status || [];
  const kis = (data.known_issues || []).filter((k) => k.matched_ki);
  const clusters = data.clusters || {};

  const slaRows = [...sla].sort((a, b) =>
    (b.breached - a.breached) || (b.escalation_required - a.escalation_required)
    || a.ticket_id.localeCompare(b.ticket_id));

  const slaTable = slaRows.map((row) => {
    const cls = row.breached ? "breached" : (row.escalation_required ? "at-risk" : "");
    const pill = row.breached
      ? `<span class="pill pill-breach">BREACHED ${row.minutes_over_or_remaining} min over</span>`
      : (row.minutes_over_or_remaining <= 30
          ? `<span class="pill pill-risk">AT RISK</span>`
          : `<span class="pill pill-ok">within SLA</span>`);
    return `<tr class="${cls}">
      <td>${esc(row.ticket_id)}</td><td>${esc(row.account_id)}</td>
      <td>${esc(row.severity)}</td><td>${esc(row.target_display)}</td>
      <td>${pill}</td>
      <td>${row.escalation_required ? "escalation required" : ""}</td></tr>`;
  }).join("");

  const kiRows = kis.map((k) => `<tr>
      <td>${esc(k.ticket_id)}</td><td>${esc(k.account_id)}</td>
      <td><span class="pill pill-ki">${esc(k.matched_ki)}</span></td>
      <td>${esc(k.confidence)}</td></tr>`).join("");

  const clusterItems = Object.entries(clusters)
    .filter(([, ids]) => ids.length)
    .map(([label, ids]) => `<div class="cluster-item">
        <strong>${esc(label)}</strong> — ${ids.length} ticket(s) across accounts:
        ${ids.map(esc).join(", ")}</div>`).join("")
    || `<p class="muted">No cross-account patterns detected.</p>`;

  el.insightsContent.innerHTML = `
    <div class="summary-strip">
      <div class="summary-card ${summary.breached_count ? "alert" : ""}">
        <div class="num">${summary.breached_count ?? 0}</div>
        <div class="cap">SLA breaches</div></div>
      <div class="summary-card">
        <div class="num">${summary.escalations_required ?? 0}</div>
        <div class="cap">Escalations required</div></div>
      <div class="summary-card">
        <div class="num">${kis.length}</div>
        <div class="cap">Known-issue matches</div></div>
      <div class="summary-card">
        <div class="num">${summary.tickets_in_scope ?? 0}</div>
        <div class="cap">Tickets in scope</div></div>
    </div>
    <h3>SLA status</h3>
    <table class="data"><thead><tr>
      <th>Ticket</th><th>Account</th><th>Severity</th><th>Target</th><th>Status</th><th></th>
    </tr></thead><tbody>${slaTable}</tbody></table>
    <h3>Known-issue matches</h3>
    ${kis.length ? `<table class="data"><thead><tr>
      <th>Ticket</th><th>Account</th><th>Issue</th><th>Confidence</th>
    </tr></thead><tbody>${kiRows}</tbody></table>`
      : `<p class="muted">No known-issue matches.</p>`}
    <h3>Cross-account patterns (deterministic keyword grouping)</h3>
    <div class="cluster-list">${clusterItems}</div>`;
}

/* -------------------------------------------------------------------- boot */

loadSessions();
