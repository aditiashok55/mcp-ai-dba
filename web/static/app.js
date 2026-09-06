// Minimal vanilla-JS controller for the AI-DBA UI.
// Everything happens in one POST /api/diagnose call.

const $ = (id) => document.getElementById(id);

const els = {
  question: $("question"),
  llm: $("llm"),
  steps: $("max-steps"),
  run: $("run"),
  status: $("status"),
  result: $("result"),
  summary: $("summary"),
  findings: $("findings"),
  tools: $("tools"),
  raw: $("raw"),
};

// Annotate the engine dropdown with which providers are actually usable.
fetch("/api/providers")
  .then((r) => r.json())
  .then((info) => {
    Array.from(els.llm.options).forEach((opt) => {
      const meta = info[opt.value];
      if (!meta) return;
      if (!meta.available) {
        opt.disabled = true;
        opt.textContent = `${opt.textContent} — unavailable (${meta.reason})`;
      } else if (opt.value !== "rule") {
        opt.textContent = `${opt.textContent} — ${meta.reason}`;
      }
    });
  })
  .catch(() => {});

// Sample-chip clicks fill the textarea.
document.querySelectorAll(".chip:not(.demo)").forEach((chip) => {
  chip.addEventListener("click", () => {
    els.question.value = chip.dataset.q;
    els.question.focus();
  });
});

// Demo-mode chips: inject a chaos scenario server-side, then diagnose.
document.querySelectorAll(".chip.demo").forEach((chip) => {
  chip.addEventListener("click", () => runDemo(chip.dataset.scenario));
});

els.run.addEventListener("click", diagnose);
els.question.addEventListener("keydown", (e) => {
  if ((e.metaKey || e.ctrlKey) && e.key === "Enter") diagnose();
});

// Strip the <untrusted> tags the server wraps around string leaves
// so they don't leak into the UI.
function unwrap(s) {
  if (typeof s !== "string") return s;
  return s.replaceAll("<untrusted>", "").replaceAll("</untrusted>", "");
}

function setStatus(text, kind = "info") {
  els.status.className = `status ${kind === "error" ? "error" : ""}`;
  els.status.classList.remove("hidden");
  els.status.innerHTML =
    kind === "info"
      ? `<span class="spinner"></span>${text}`
      : text;
}

function clearStatus() {
  els.status.classList.add("hidden");
  els.status.textContent = "";
}

async function diagnose() {
  const question = els.question.value.trim();
  if (!question) {
    setStatus("Enter a symptom first.", "error");
    return;
  }
  await callAgent("/api/diagnose", {
    question,
    llm: els.llm.value,
    max_steps: Number(els.steps.value) || 8,
  }, "Running diagnostic tools…");
}

async function runDemo(scenario) {
  const pretty = {
    slow_query: "🐢 slow query",
    lock_contention: "🔒 lock contention",
    idle_in_transaction: "💤 idle-in-transaction",
  }[scenario] || scenario;
  els.question.value = `[demo] Injecting ${pretty}. Investigate.`;
  await callAgent("/api/demo", {
    scenario,
    llm: els.llm.value,
    max_steps: Number(els.steps.value) || 8,
  }, `Injecting ${pretty} into Postgres, then running the agent…`);
}

async function callAgent(url, body, statusText) {
  els.run.disabled = true;
  els.result.classList.add("hidden");
  // Clear stale content so a failed run never leaves an old diagnosis
  // sitting under a fresh error banner.
  els.summary.textContent = "";
  els.findings.innerHTML = "";
  els.tools.innerHTML = "";
  els.raw.textContent = "";
  setStatus(statusText);

  try {
    const resp = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });

    const data = await resp.json();

    if (!resp.ok) {
      setStatus(`Error ${resp.status}: ${data.detail || "unknown"}`, "error");
      return;
    }

    if (!data.ok) {
      setStatus(`Agent could not produce a diagnosis: ${data.error}`, "error");
      renderRaw(data);
      els.result.classList.remove("hidden");
      return;
    }

    clearStatus();
    render(data);
  } catch (err) {
    setStatus(`Network error: ${err.message}`, "error");
  } finally {
    els.run.disabled = false;
  }
}

function render(data) {
  const diag = data.diagnosis;

  els.summary.textContent = unwrap(diag.summary);

  els.findings.innerHTML = "";
  diag.findings.forEach((f) => {
    const card = document.createElement("div");
    card.className = "finding";
    card.innerHTML = `
      <div class="finding-header">
        <p class="finding-title">${escape(unwrap(f.finding))}</p>
        <span class="badge ${f.confidence}">${f.confidence}</span>
      </div>
      <div class="action">${escape(unwrap(f.recommended_action) || "→ No action required.")}</div>
      <div class="evidence">
        <span>evidence:</span>
        ${f.evidence.map((cid) => `<span class="cid">${escape(unwrap(cid))}</span>`).join("")}
      </div>
    `;
    els.findings.appendChild(card);
  });

  els.tools.innerHTML = "";
  data.tool_call_ids.forEach((cid, i) => {
    const row = document.createElement("div");
    row.className = "tool-call";
    row.innerHTML = `
      <div class="step">${i + 1}</div>
      <span class="cid">${escape(cid)}</span>
    `;
    els.tools.appendChild(row);
  });

  renderRaw(data);
  els.result.classList.remove("hidden");
}

function renderRaw(data) {
  els.raw.textContent = JSON.stringify(data, null, 2);
}

function escape(s) {
  return String(s)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;");
}
