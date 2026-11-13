
  // ---------------- four-agent workspace (A2A coordinator) ----------------

  const PL_AGENTS = {
    clinical_data: "Clinical Data Agent",
    risk: "Risk Assessment Agent",
    management: "Clinical Management Agent",
    diet: "Diet Recommendation Agent"
  };

  const PL_ORDER = ["clinical_data", "risk", "management", "diet"];

  const PL_ST = {
    completed: ["Completed", "ok"],
    failed: ["Failed", "bad"],
    needs_review: ["Needs review", "warn"],
    insufficient_evidence: ["Insufficient evidence", "warn"]
  };

  const plChip = (st) => {
    const [label, color] = PL_ST[st] || ["Not run", "idle"];
    return `<span class="pl-st ${color}">${label}</span>`;
  };

  function renderPipeline(r) {
    const agents = (r && r.agents) || [];
    const byId = Object.fromEntries(
      agents.map((agent) => [agent.id, agent])
    );

    // Specialist execution flow
    $("#pipe-flow").innerHTML = PL_ORDER.map((id, index) => {
      const agent = byId[id];

      return `
        <li>
          <span class="pl-num">${index + 1}</span>
          <strong>${PL_AGENTS[id]}</strong>
          ${plChip(agent && agent.status)}
        </li>
      `;
    }).join("");

    $("#pipe-time").textContent = r
      ? "Last run: " + new Date().toLocaleString()
      : "not run yet";

    $("#pipe-notes").disabled = !agents.length;

    if (!r) {
      $("#pipe-out").innerHTML = `
        <p class="muted">
          Not run yet. The specialist agents read patient information
          and provide findings for doctor review.
        </p>
      `;

      if (cur) cur.pipe = null;
      return;
    }

    // Fallback response
    if (r.mode === "fallback") {
      $("#pipe-out").innerHTML = `
        <div class="notice">
          <strong>A2A coordinator did not complete.</strong>
          <p>${esc(r.fallback_reason || "Fallback pipeline was used.")}</p>
        </div>
      `;

      if (cur) cur.pipe = r;
      return;
    }

    // Specialist cards
    const cards = PL_ORDER.map((id) => {
      const agent = byId[id];

      if (!agent) {
        return `
          <article class="pl-card">
            <h3>${esc(PL_AGENTS[id])}</h3>
            ${plChip("failed")}
            <p class="muted">No result was returned for this specialist.</p>
          </article>
        `;
      }

      const name = PL_AGENTS[id];
      const transport = agent.transport || "unknown";

      const verification = agent.verified
        ? '<span class="pl-st ok">Verified</span>'
        : '<span class="pl-st warn">Needs review</span>';

      const tools = (agent.tools_used || [])
        .map((tool) => esc(tool))
        .join(", ") || "No tools recorded";

      const evidence = (agent.passages || []).length
        ? `
          <details>
            <summary>Guideline evidence (${agent.passages.length})</summary>
            <ul>
              ${agent.passages.map((passage) => `
                <li>
                  ${esc(passage.doc)}
                  · PDF page ${esc(passage.page)}
                </li>
              `).join("")}
            </ul>
          </details>
        `
        : "";

      const flags = (agent.flags || []).length
        ? `
          <details>
            <summary>Care-gap sources (${agent.flags.length})</summary>
            <ul>
              ${agent.flags.map((flag) => `
                <li>${esc(flag.source || "Source not specified")}</li>
              `).join("")}
            </ul>
          </details>
        `
        : "";

      const toolResults = (agent.tool_results || []).length
        ? `
          <details>
            <summary>Technical tool evidence</summary>
            ${(agent.tool_results || []).map((item) => `
              <div class="pl-topic">
                <strong>${esc(item.tool)}</strong>
                <pre>${esc(JSON.stringify(item.result, null, 2))}</pre>
              </div>
            `).join("")}
          </details>
        `
        : "";

      return `
        <article class="pl-card">
          <h3>${esc(name)} ${plChip(agent.status)}</h3>

          <p class="pl-meta">
            Transport: ${esc(transport.toUpperCase())}
            · ${verification}
          </p>

          <p>
            ${esc(agent.answer || agent.error || "No findings returned.")}
          </p>

          <p class="pl-meta">
            Tools used: ${tools}
          </p>

          ${evidence}
          ${flags}
          ${toolResults}

          <p class="field-hint">
            For doctor review. No patient record was changed.
          </p>
        </article>
      `;
    }).join("");

    // Coordinator summary
    const summary = `
      <div class="pl-cites">
        <h3>Coordinator summary</h3>
        <p>${esc(r.answer || "No combined summary returned.")}</p>

        <p class="field-hint">
          Coordinator verification:
          ${r.verification && r.verification.ok
            ? "Passed"
            : "Needs review"}
        </p>

        <p class="field-hint">
          Agents contacted:
          ${(r.agents_called || []).length} of 4
        </p>
      </div>
    `;

    $("#pipe-out").innerHTML = summary + cards;

    if (cur) cur.pipe = r;
  }

  // Run A2A coordinator
  $("#pipe-run").addEventListener("click", async (event) => {
    const mine = cur;

    if (!mine) return;

    $("#pipe-out").innerHTML = `
      <p class="muted">
        Coordinator is contacting the four specialist agents…
      </p>
    `;

    try {
      const result = await withBusy(
        event.currentTarget,
        "Running…",
        () => Api.runAgenticPipeline(mine.p.id)
      );

      if (cur === mine) {
        renderPipeline(result);
      }
    } catch (error) {
      $("#pipe-out").innerHTML = `
        <div class="notice">
          The A2A workflow did not complete.
          ${esc(error?.message || "Please try again.")}
        </div>
      `;
    }
  });

  // Copy findings into doctor notes
  $("#pipe-notes").addEventListener("click", () => {
    const result = cur && cur.pipe;

    if (!result || !result.agents || !result.agents.length) return;

    const lines = [
      "DRAFT AGENT SUMMARY (Doctor to review and edit)",
      "",
      ...result.agents.map((agent) => {
        const name = PL_AGENTS[agent.id] || agent.agent || agent.id;

        return `${name}: ${agent.answer || agent.error || "No findings returned."}`;
      }),
      "",
      "COORDINATOR SUMMARY:",
      result.answer || "Not available.",
      "",
      "All findings require doctor review."
    ];

    const box = $("#notes");

    box.value = (box.value ? box.value + "\n\n" : "") + lines.join("\n");

    box.scrollIntoView({
      behavior: "smooth",
      block: "center"
    });
  });

  // ---------------- end four-agent workspace ----------------
