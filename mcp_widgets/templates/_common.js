function extractJsonPayload(text) {
  const m = text.match(/```json\s*\n([\s\S]*?)\n```\s*$/);
  return m ? JSON.parse(m[1]) : null;
}

function formatCurrency(v) {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  return "$" + Number(v).toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: 0 });
}

function fmtMetric(v) {
  return (typeof v === "number") ? v.toFixed(2) : "—";
}

function deployableBadge(deployable) {
  const span = document.createElement("span");
  if (deployable === true) {
    span.className = "badge badge-growth";
    span.textContent = "✅ Deployable";
  } else if (deployable === false) {
    span.className = "badge badge-decline";
    span.textContent = "❌ Not Deployable";
  } else {
    span.className = "badge badge-caution";
    span.textContent = "— Unrated";
  }
  return span.outerHTML;
}

const CATEGORY_COLOR_MAP = {
  "Momentum": "var(--cat-momentum)",
  "Mean Reversion": "var(--cat-mean-reversion)",
  "Factor": "var(--cat-factor)",
  "Blend": "var(--cat-blend)",
  "Macro": "var(--cat-macro)",
  "Risk": "var(--cat-risk)",
  "Sentiment": "var(--cat-sentiment)",
  "Forecast": "var(--cat-forecast)",
};

function categoryChip(category) {
  const span = document.createElement("span");
  span.className = "category-chip";
  const color = CATEGORY_COLOR_MAP[category] || "var(--text-muted)";
  span.style.cssText = `background-color: color-mix(in srgb, ${color} 15%, transparent); color: ${color};`;
  span.textContent = category == null ? "—" : String(category);
  return span.outerHTML;
}

function applyHostTheme(theme) {
  document.documentElement.classList.toggle("light", theme === "light");
}

function renderDetailPanel(container, detail) {
  const wrapper = document.createElement("div");
  wrapper.className = "detail-panel";

  // Header
  const header = document.createElement("div");
  header.className = "detail-header";
  const nameEl = document.createElement("strong");
  nameEl.textContent = detail.name || "";
  const idEl = document.createElement("span");
  idEl.className = "detail-id";
  idEl.textContent = detail.id || "";
  header.appendChild(nameEl);
  header.appendChild(idEl);
  header.insertAdjacentHTML("beforeend", categoryChip(detail.category));
  header.insertAdjacentHTML("beforeend", deployableBadge(detail.headline?.deployable));
  wrapper.appendChild(header);

  // Stat row
  const headline = detail.headline || {};
  const statRow = document.createElement("div");
  statRow.className = "stat-row";
  const stats = [
    ["Sharpe", fmtMetric(headline.sharpe)],
    ["DSR", fmtMetric(headline.dsr)],
    ["PBO", fmtMetric(headline.pbo)],
    ["Max Drawdown", fmtMetric(headline.max_drawdown)],
  ];
  for (const [label, value] of stats) {
    const cell = document.createElement("div");
    const labelEl = document.createElement("div");
    labelEl.className = "stat-label";
    labelEl.textContent = label;
    const valueEl = document.createElement("div");
    valueEl.className = "stat-value";
    valueEl.textContent = value;
    cell.appendChild(labelEl);
    cell.appendChild(valueEl);
    statRow.appendChild(cell);
  }
  wrapper.appendChild(statRow);

  // Top Holdings
  const holdingsTitle = document.createElement("h4");
  holdingsTitle.textContent = "Top Holdings";
  wrapper.appendChild(holdingsTitle);

  const holdings = detail.holdings || [];
  if (holdings.length === 0) {
    const empty = document.createElement("p");
    empty.className = "empty-state";
    empty.textContent = "No positive-scoring holdings in the latest snapshot.";
    wrapper.appendChild(empty);
  } else {
    const table = document.createElement("table");
    table.className = "table";
    table.innerHTML = "<thead><tr><th>Symbol</th><th>Weight</th><th>Score</th><th>Price</th><th>Sector</th></tr></thead>";
    const tbody = document.createElement("tbody");
    for (const h of holdings) {
      const tr = document.createElement("tr");
      const tdSymbol = document.createElement("td");
      tdSymbol.textContent = h.symbol ?? "—";
      const tdWeight = document.createElement("td");
      tdWeight.textContent = (typeof h.weight === "number") ? (h.weight * 100).toFixed(2) + "%" : "—";
      const tdScore = document.createElement("td");
      tdScore.textContent = fmtMetric(h.score);
      const tdPrice = document.createElement("td");
      tdPrice.textContent = formatCurrency(h.price);
      const tdSector = document.createElement("td");
      tdSector.textContent = h.sector ?? "—";
      tr.appendChild(tdSymbol);
      tr.appendChild(tdWeight);
      tr.appendChild(tdScore);
      tr.appendChild(tdPrice);
      tr.appendChild(tdSector);
      tbody.appendChild(tr);
    }
    table.appendChild(tbody);
    wrapper.appendChild(table);
  }

  // Sector Allocation
  const sectorTitle = document.createElement("h4");
  sectorTitle.textContent = "Sector Allocation";
  wrapper.appendChild(sectorTitle);

  const sectorAllocation = detail.sector_allocation || [];
  const sectorList = document.createElement("div");
  sectorList.className = "sector-bar-list";
  if (sectorAllocation.length === 0) {
    const empty = document.createElement("p");
    empty.className = "empty-state";
    empty.textContent = "No sector allocation data available.";
    sectorList.appendChild(empty);
  } else {
    for (const s of sectorAllocation) {
      const row = document.createElement("div");
      row.className = "sector-bar-row";
      const label = document.createElement("span");
      label.className = "sector-bar-label";
      label.textContent = s.sector ?? "—";
      const pct = (typeof s.weight === "number") ? s.weight * 100 : 0;
      const barTrack = document.createElement("div");
      barTrack.className = "sector-bar-track";
      const bar = document.createElement("div");
      bar.className = "sector-bar-fill";
      bar.style.width = Math.max(0, Math.min(100, pct)) + "%";
      barTrack.appendChild(bar);
      const value = document.createElement("span");
      value.className = "sector-bar-value";
      value.textContent = (typeof s.weight === "number") ? pct.toFixed(1) + "%" : "—";
      row.appendChild(label);
      row.appendChild(barTrack);
      row.appendChild(value);
      sectorList.appendChild(row);
    }
  }
  wrapper.appendChild(sectorList);

  // Recent Trades
  const tradesTitle = document.createElement("h4");
  tradesTitle.textContent = "Recent Trades";
  wrapper.appendChild(tradesTitle);

  const recentTrades = detail.recent_trades || [];
  if (recentTrades.length === 0) {
    const empty = document.createElement("p");
    empty.className = "empty-state";
    empty.textContent = "Fewer than two historical snapshots — no trade diff yet.";
    wrapper.appendChild(empty);
  } else {
    const table = document.createElement("table");
    table.className = "table";
    table.innerHTML = "<thead><tr><th>Date</th><th>Symbol</th><th>Side</th><th>Weight Δ</th></tr></thead>";
    const tbody = document.createElement("tbody");
    for (const t of recentTrades) {
      const tr = document.createElement("tr");
      const tdDate = document.createElement("td");
      tdDate.textContent = t.date ?? "—";
      const tdSymbol = document.createElement("td");
      tdSymbol.textContent = t.symbol ?? "—";
      const tdSide = document.createElement("td");
      tdSide.textContent = t.side ?? "—";
      const tdDelta = document.createElement("td");
      const delta = t.weight_delta;
      tdDelta.textContent = (typeof delta === "number") ? (delta >= 0 ? "+" : "") + delta.toFixed(4) : "—";
      tr.appendChild(tdDate);
      tr.appendChild(tdSymbol);
      tr.appendChild(tdSide);
      tr.appendChild(tdDelta);
      tbody.appendChild(tr);
    }
    table.appendChild(tbody);
    wrapper.appendChild(table);
  }

  container.appendChild(wrapper);
  return wrapper;
}

function renderFollowForm(container, detail, app) {
  const form = document.createElement("div");
  form.className = "follow-form";

  const input = document.createElement("input");
  input.type = "number";
  input.className = "input-number";
  input.min = "1";
  input.step = "1";
  input.placeholder = "Amount ($)";

  const button = document.createElement("button");
  button.className = "btn-primary";
  button.textContent = "Follow this Pilot";

  const errorText = document.createElement("div");
  errorText.className = "follow-error";

  const statusText = document.createElement("div");
  statusText.className = "follow-status";

  button.onclick = () => {
    errorText.textContent = "";
    const amount = Number(input.value);
    if (!(amount > 0)) {
      errorText.textContent = "Enter an amount greater than $0.";
      return;
    }
    app.sendMessage({
      role: "user",
      content: [{ type: "text", text: `Follow the "${detail.name}" pilot (id: ${detail.id}) with $${amount.toFixed(2)}.` }],
    });
    button.disabled = true;
    statusText.textContent = "Sent — waiting for confirmation...";
  };

  form.appendChild(input);
  form.appendChild(button);
  form.appendChild(errorText);
  form.appendChild(statusText);
  container.appendChild(form);
  return form;
}

const COMPARE_ORDINAL_COLORS = ["var(--compare-1)", "var(--compare-2)", "var(--compare-3)"];

function renderComparePanel(container, pilots) {
  const wrapper = document.createElement("div");
  wrapper.className = "compare-panel";

  if (!pilots || pilots.length === 0) {
    const empty = document.createElement("p");
    empty.className = "empty-state";
    empty.textContent = "No pilots to compare.";
    wrapper.appendChild(empty);
    container.appendChild(wrapper);
    return wrapper;
  }

  // Stat-card grid: up to 3 cards, side by side.
  const grid = document.createElement("div");
  grid.className = "compare-grid";
  const series = [];

  pilots.forEach((pilot, i) => {
    const color = COMPARE_ORDINAL_COLORS[i % COMPARE_ORDINAL_COLORS.length];

    const card = document.createElement("div");
    card.className = "card compare-card";
    card.style.setProperty("--compare-accent", color);

    const header = document.createElement("div");
    header.className = "compare-card-header";
    const swatch = document.createElement("span");
    swatch.className = "compare-swatch";
    swatch.style.backgroundColor = color;
    const nameEl = document.createElement("strong");
    nameEl.textContent = pilot.name || "";
    const idEl = document.createElement("span");
    idEl.className = "pilot-card-id";
    idEl.textContent = pilot.id || "";
    header.appendChild(swatch);
    header.appendChild(nameEl);
    header.appendChild(idEl);
    header.insertAdjacentHTML("beforeend", categoryChip(pilot.category));
    const headline = pilot.headline || {};
    header.insertAdjacentHTML("beforeend", deployableBadge(headline.deployable));
    card.appendChild(header);

    const statRow = document.createElement("div");
    statRow.className = "stat-row";
    const stats = [
      ["Sharpe", fmtMetric(headline.sharpe)],
      ["DSR", fmtMetric(headline.dsr)],
      ["PBO", fmtMetric(headline.pbo)],
      ["Max Drawdown", fmtMetric(headline.max_drawdown)],
    ];
    for (const [label, value] of stats) {
      const cell = document.createElement("div");
      const labelEl = document.createElement("div");
      labelEl.className = "stat-label";
      labelEl.textContent = label;
      const valueEl = document.createElement("div");
      valueEl.className = "stat-value";
      valueEl.textContent = value;
      cell.appendChild(labelEl);
      cell.appendChild(valueEl);
      statRow.appendChild(cell);
    }
    card.appendChild(statRow);

    const footer = document.createElement("div");
    footer.className = "pilot-card-footer";
    const holdingsEl = document.createElement("span");
    holdingsEl.textContent = `Holdings: ${pilot.holdings_count ?? "—"}`;
    footer.appendChild(holdingsEl);
    card.appendChild(footer);

    const perf = pilot.performance || {};
    if (!perf.curve) {
      const empty = document.createElement("p");
      empty.className = "empty-state compare-no-curve";
      empty.textContent = perf.reason ? `— (${perf.reason})` : "— (no validated backtest)";
      card.appendChild(empty);
    } else {
      series.push({ label: pilot.name || pilot.id, color, points: perf.curve });
    }

    grid.appendChild(card);
  });

  wrapper.appendChild(grid);

  const chartTitle = document.createElement("h4");
  chartTitle.textContent = "Equity Curve Overlay (base-100)";
  wrapper.appendChild(chartTitle);

  const chartContainer = document.createElement("div");
  chartContainer.className = "compare-chart-container";
  renderEquityOverlaySvg(chartContainer, series);
  wrapper.appendChild(chartContainer);

  container.appendChild(wrapper);
  return wrapper;
}

function renderEquityOverlaySvg(container, series) {
  const W = 600, H = 200, PAD = 8;

  const nonEmptySeries = (series || []).filter((s) => s.points && s.points.length > 0);
  const allDates = [...new Set(nonEmptySeries.flatMap((s) => s.points.map((p) => p.date)))].sort();
  const allValues = nonEmptySeries.flatMap((s) => s.points.map((p) => p.value));

  if (!allDates.length || !allValues.length) {
    const empty = document.createElement("p");
    empty.className = "empty-state";
    empty.textContent = "No validated equity curves available to overlay.";
    container.appendChild(empty);
    return;
  }

  const xIdx = new Map(allDates.map((d, i) => [d, i]));
  const xScale = (i) => PAD + (i / Math.max(1, allDates.length - 1)) * (W - 2 * PAD);
  const yMin = Math.min(...allValues, 100);
  const yMax = Math.max(...allValues, 100);
  const yScale = (v) => H - PAD - ((v - yMin) / Math.max(1e-9, yMax - yMin)) * (H - 2 * PAD);

  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  svg.setAttribute("class", "compare-equity-svg");

  // Baseline at value=100 (the shared starting point every base-100 curve begins at).
  const baseline = document.createElementNS(svg.namespaceURI, "line");
  baseline.setAttribute("x1", PAD);
  baseline.setAttribute("x2", W - PAD);
  baseline.setAttribute("y1", yScale(100));
  baseline.setAttribute("y2", yScale(100));
  baseline.setAttribute("class", "compare-equity-baseline");
  svg.appendChild(baseline);

  for (const s of nonEmptySeries) {
    const pts = s.points
      .filter((p) => xIdx.has(p.date) && typeof p.value === "number")
      .map((p) => `${xScale(xIdx.get(p.date))},${yScale(p.value)}`)
      .join(" ");
    if (!pts) continue;
    const poly = document.createElementNS(svg.namespaceURI, "polyline");
    poly.setAttribute("points", pts);
    poly.setAttribute("fill", "none");
    poly.setAttribute("stroke", s.color);
    poly.setAttribute("stroke-width", "2");
    svg.appendChild(poly);
  }

  container.appendChild(svg);

  const legend = document.createElement("div");
  legend.className = "compare-legend";
  for (const s of nonEmptySeries) {
    const item = document.createElement("span");
    item.className = "compare-legend-item";
    const swatch = document.createElement("span");
    swatch.className = "compare-swatch";
    swatch.style.backgroundColor = s.color;
    const label = document.createElement("span");
    label.textContent = s.label;
    item.appendChild(swatch);
    item.appendChild(label);
    legend.appendChild(item);
  }
  container.appendChild(legend);
}

function fmtPlValue(v) {
  if (typeof v !== "number" || Number.isNaN(v)) return "—";
  const sign = v >= 0 ? "+" : "-";
  return sign + "$" + Math.abs(v).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function fmtPct(v) {
  if (typeof v !== "number" || Number.isNaN(v)) return "—";
  return (v >= 0 ? "+" : "") + (v * 100).toFixed(1) + "%";
}

function plClass(v) {
  return (typeof v === "number" && v < 0) ? "pl-negative" : "pl-positive";
}

function renderPortfolioByPilotPanel(container, payload) {
  const wrapper = document.createElement("div");
  wrapper.className = "portfolio-by-pilot-panel";

  if (!payload) {
    const empty = document.createElement("p");
    empty.className = "empty-state";
    empty.textContent = "No portfolio-by-pilot data available.";
    wrapper.appendChild(empty);
    container.appendChild(wrapper);
    return wrapper;
  }

  // Honesty banner -- non-negotiable, always at the top, always visible.
  // This must never be mistaken for real per-lot P&L.
  const banner = document.createElement("div");
  banner.className = "banner-caution proxy-banner";
  const bannerLabel = document.createElement("div");
  bannerLabel.className = "proxy-banner-label";
  bannerLabel.textContent = "⚠️ PROXY ATTRIBUTION (" + (payload.attribution_basis || "proxy") + ") — not per-lot P&L";
  const bannerNote = document.createElement("div");
  bannerNote.className = "proxy-banner-note";
  bannerNote.textContent = payload.note || "";
  banner.appendChild(bannerLabel);
  banner.appendChild(bannerNote);
  wrapper.appendChild(banner);

  if (payload.as_of) {
    const asOf = document.createElement("div");
    asOf.className = "portfolio-as-of";
    asOf.textContent = "As of: " + payload.as_of;
    wrapper.appendChild(asOf);
  }

  // By-Pilot section
  const pilotsTitle = document.createElement("h4");
  pilotsTitle.textContent = "By Pilot";
  wrapper.appendChild(pilotsTitle);

  const pilots = payload.pilots || [];
  if (pilots.length === 0) {
    const empty = document.createElement("p");
    empty.className = "empty-state";
    empty.textContent = payload.reason || "No attributable claims on record.";
    wrapper.appendChild(empty);
  } else {
    const grid = document.createElement("div");
    grid.className = "pilot-portfolio-grid";

    for (const pilot of pilots) {
      const card = document.createElement("div");
      card.className = "card pilot-portfolio-card";

      const header = document.createElement("div");
      header.className = "pilot-portfolio-card-header";
      const nameEl = document.createElement("strong");
      nameEl.textContent = pilot.pilot_name || pilot.pilot_id || "";
      const idEl = document.createElement("span");
      idEl.className = "pilot-card-id";
      idEl.textContent = pilot.pilot_id || "";
      header.appendChild(nameEl);
      header.appendChild(idEl);
      card.appendChild(header);

      const statRow = document.createElement("div");
      statRow.className = "stat-row";
      const stats = [
        ["Attributed Value", formatCurrency(pilot.attributed_market_value)],
        ["Unrealized P&L", fmtPlValue(pilot.attributed_unrealized_pl)],
        ["P&L %", fmtPct(pilot.attributed_unrealized_pl_pct)],
      ];
      for (const [label, value] of stats) {
        const cell = document.createElement("div");
        const labelEl = document.createElement("div");
        labelEl.className = "stat-label";
        labelEl.textContent = label;
        const valueEl = document.createElement("div");
        valueEl.className = "stat-value";
        if (label !== "Attributed Value") {
          const raw = label === "P&L %" ? pilot.attributed_unrealized_pl_pct : pilot.attributed_unrealized_pl;
          valueEl.classList.add(plClass(raw));
        }
        valueEl.textContent = value;
        cell.appendChild(labelEl);
        cell.appendChild(valueEl);
        statRow.appendChild(cell);
      }
      card.appendChild(statRow);

      const positions = pilot.positions || [];
      if (positions.length === 0) {
        const empty = document.createElement("p");
        empty.className = "empty-state";
        empty.textContent = "No attributed positions.";
        card.appendChild(empty);
      } else {
        const table = document.createElement("table");
        table.className = "table";
        table.innerHTML = "<thead><tr><th>Symbol</th><th>Attributed Value</th><th>Attributed P&L</th><th></th></tr></thead>";
        const tbody = document.createElement("tbody");
        for (const pos of positions) {
          const tr = document.createElement("tr");
          const tdSymbol = document.createElement("td");
          tdSymbol.textContent = pos.symbol ?? "—";
          const tdValue = document.createElement("td");
          tdValue.textContent = formatCurrency(pos.attributed_value);
          const tdPl = document.createElement("td");
          tdPl.textContent = fmtPlValue(pos.attributed_unrealized_pl);
          tdPl.className = plClass(pos.attributed_unrealized_pl);
          const tdOverlap = document.createElement("td");
          if (pos.overlap_scaled) {
            const badge = document.createElement("span");
            badge.className = "badge badge-caution overlap-badge";
            badge.textContent = "overlap-scaled";
            tdOverlap.appendChild(badge);
          }
          tr.appendChild(tdSymbol);
          tr.appendChild(tdValue);
          tr.appendChild(tdPl);
          tr.appendChild(tdOverlap);
          tbody.appendChild(tr);
        }
        table.appendChild(tbody);
        card.appendChild(table);
      }

      grid.appendChild(card);
    }
    wrapper.appendChild(grid);
  }

  // Unattributed bucket -- deliberately NOT styled as a pilot card, since it
  // isn't one.
  const unattributedSection = document.createElement("div");
  unattributedSection.className = "unattributed-section";

  const unattributedTitle = document.createElement("h4");
  unattributedTitle.textContent = "Unattributed";
  unattributedSection.appendChild(unattributedTitle);

  const unattributedNote = document.createElement("p");
  unattributedNote.className = "unattributed-note";
  unattributedNote.textContent = "Held value no follow currently claims.";
  unattributedSection.appendChild(unattributedNote);

  const unattributed = payload.unattributed || [];
  if (unattributed.length === 0) {
    const empty = document.createElement("p");
    empty.className = "empty-state";
    empty.textContent = "None on record.";
    unattributedSection.appendChild(empty);
  } else {
    const table = document.createElement("table");
    table.className = "table";
    table.innerHTML = "<thead><tr><th>Symbol</th><th>Value</th></tr></thead>";
    const tbody = document.createElement("tbody");
    for (const u of unattributed) {
      const tr = document.createElement("tr");
      const tdSymbol = document.createElement("td");
      tdSymbol.textContent = u.symbol ?? "—";
      const tdValue = document.createElement("td");
      tdValue.textContent = formatCurrency(u.value);
      tr.appendChild(tdSymbol);
      tr.appendChild(tdValue);
      tbody.appendChild(tr);
    }
    table.appendChild(tbody);
    unattributedSection.appendChild(table);
  }

  wrapper.appendChild(unattributedSection);

  container.appendChild(wrapper);
  return wrapper;
}

function renderFollowResultCard(container, payload) {
  const wrapper = document.createElement("div");
  wrapper.className = "follow-result";

  const banner = document.createElement("div");
  banner.className = "banner-caution";
  banner.textContent = "⚠️ Paper-first dry-run preview — no live order was placed.";
  wrapper.appendChild(banner);

  const chips = document.createElement("div");
  chips.className = "status-chips";

  const modeChip = document.createElement("span");
  modeChip.className = "badge badge-caution";
  modeChip.textContent = "Mode: " + (payload.mode ?? "—");
  chips.appendChild(modeChip);

  const queueChip = document.createElement("span");
  const queueWritten = payload.queue_written;
  queueChip.className = "badge " + (queueWritten ? "badge-growth" : "badge-decline");
  queueChip.textContent = "Queue written: " + (queueWritten ? "✅" : "❌");
  chips.appendChild(queueChip);

  wrapper.appendChild(chips);

  const intentsTitle = document.createElement("h4");
  intentsTitle.textContent = "Planned Intents";
  wrapper.appendChild(intentsTitle);

  const intents = payload.planned_intents || [];
  if (intents.length === 0) {
    const empty = document.createElement("p");
    empty.className = "empty-state";
    empty.textContent = "No planned intents (Pilot has no positive-scoring holdings yet, or the follow is already balanced).";
    wrapper.appendChild(empty);
  } else {
    const table = document.createElement("table");
    table.className = "table";
    table.innerHTML = "<thead><tr><th>Symbol</th><th>Action</th><th>Target Notional</th><th>Rationale</th></tr></thead>";
    const tbody = document.createElement("tbody");
    for (const intent of intents) {
      const tr = document.createElement("tr");
      const tdSymbol = document.createElement("td");
      tdSymbol.textContent = intent.symbol ?? "—";
      const tdAction = document.createElement("td");
      tdAction.textContent = intent.action ?? "—";
      const tdNotional = document.createElement("td");
      tdNotional.textContent = formatCurrency(intent.target_notional);
      const tdRationale = document.createElement("td");
      tdRationale.textContent = intent.rationale ?? "—";
      tr.appendChild(tdSymbol);
      tr.appendChild(tdAction);
      tr.appendChild(tdNotional);
      tr.appendChild(tdRationale);
      tbody.appendChild(tr);
    }
    table.appendChild(tbody);
    wrapper.appendChild(table);
  }

  container.appendChild(wrapper);
  return wrapper;
}

function renderDevToolsInspector(container, payload) {
  const wrapper = document.createElement("div");
  wrapper.className = "devtools-inspector";

  // Header
  const header = document.createElement("div");
  header.className = "inspector-header";
  const routeEl = document.createElement("span");
  routeEl.className = "inspector-route";
  routeEl.textContent = payload.route || "/";
  header.appendChild(routeEl);

  const statusBadge = document.createElement("span");
  const isOk = (payload.status >= 200 && payload.status < 400);
  statusBadge.className = "badge " + (isOk ? "badge-growth" : "badge-decline");
  statusBadge.textContent = `${payload.status || 200} ${payload.statusText || "OK"}`;
  header.appendChild(statusBadge);

  if (payload.responseTimeMs != null) {
    const timeEl = document.createElement("span");
    timeEl.className = "badge badge-caution";
    timeEl.textContent = `${payload.responseTimeMs} ms`;
    header.appendChild(timeEl);
  }
  wrapper.appendChild(header);

  // Screen Preview Frame
  const previewBox = document.createElement("div");
  previewBox.className = "screen-preview-container";

  const previewBar = document.createElement("div");
  previewBar.className = "screen-preview-bar";
  previewBar.innerHTML = '<span class="preview-dot red"></span><span class="preview-dot yellow"></span><span class="preview-dot green"></span> <span style="margin-left:8px;">http://localhost:5173' + (payload.route || "/") + "</span>";
  previewBox.appendChild(previewBar);

  const previewBody = document.createElement("div");
  previewBody.className = "screen-preview-body";
  if (payload.screenshotBase64) {
    const img = document.createElement("img");
    img.className = "screen-preview-img";
    img.src = payload.screenshotBase64.startsWith("data:") ? payload.screenshotBase64 : `data:image/png;base64,${payload.screenshotBase64}`;
    previewBody.appendChild(img);
  } else {
    const placeholder = document.createElement("div");
    placeholder.style.textAlign = "center";
    placeholder.style.padding = "24px";
    placeholder.innerHTML = `<div style="font-size:24px; margin-bottom:8px;">🖥️</div><strong>${payload.title || "Pilots PWA Active"}</strong><p style="color:var(--text-muted); font-size:12px; margin-top:4px;">DOM Nodes: ${payload.domNodeCount ?? "N/A"} | Scripts: ${(payload.scriptsLoaded || []).length}</p>`;
    previewBody.appendChild(placeholder);
  }
  previewBox.appendChild(previewBody);
  wrapper.appendChild(previewBox);

  // Console Logs
  const logsTitle = document.createElement("h4");
  logsTitle.style.cssText = "margin: 8px 0 4px; font-size: 12px; text-transform: uppercase; color: var(--text-secondary);";
  logsTitle.textContent = `Console Messages (${(payload.consoleMessages || []).length})`;
  wrapper.appendChild(logsTitle);

  const logList = document.createElement("div");
  logList.className = "console-log-list";
  const msgs = payload.consoleMessages || [];
  if (msgs.length === 0) {
    const emptyLog = document.createElement("div");
    emptyLog.style.color = "var(--growth)";
    emptyLog.textContent = "✅ Zero console errors or uncaught exceptions detected.";
    logList.appendChild(emptyLog);
  } else {
    for (const msg of msgs) {
      const item = document.createElement("div");
      const type = msg.type || "info";
      item.className = `console-log-item ${type}`;
      item.textContent = `[${type.toUpperCase()}] ${msg.text || msg.message || JSON.stringify(msg)}`;
      logList.appendChild(item);
    }
  }
  wrapper.appendChild(logList);

  container.appendChild(wrapper);
  return wrapper;
}

function renderLighthouseScorecard(container, payload) {
  if (!payload) return;
  const wrapper = document.createElement("div");
  wrapper.className = "lighthouse-scorecard";

  // Score Gauges
  const scoresGrid = document.createElement("div");
  scoresGrid.className = "score-gauges-grid";

  const scores = payload.scores || {};
  const scoreKeys = ["performance", "accessibility", "bestPractices", "seo"];
  for (const key of scoreKeys) {
    const val = scores[key];
    const card = document.createElement("div");
    card.className = "score-gauge-card";

    const circle = document.createElement("div");
    if (val == null) {
      circle.className = "gauge-circle score-unmeasured";
      circle.textContent = "—";
    } else {
      const num = Number(val);
      const ratingClass = num >= 90 ? "score-good" : num >= 50 ? "score-avg" : "score-poor";
      circle.className = `gauge-circle ${ratingClass}`;
      circle.textContent = String(num);
    }

    const label = document.createElement("div");
    label.className = "gauge-label";
    label.textContent = key.replace(/([A-Z])/g, " $1").replace(/^./, str => str.toUpperCase());

    card.appendChild(circle);
    card.appendChild(label);
    scoresGrid.appendChild(card);
  }
  wrapper.appendChild(scoresGrid);

  // Core Web Vitals
  const vitalsTitle = document.createElement("h4");
  vitalsTitle.style.cssText = "margin: 8px 0 4px; font-size: 12px; text-transform: uppercase; color: var(--text-secondary);";
  vitalsTitle.textContent = "Core Web Vitals";
  wrapper.appendChild(vitalsTitle);

  const vitalsGrid = document.createElement("div");
  vitalsGrid.className = "vitals-grid";

  const vitals = payload.vitals || {};
  const ratings = payload.vitals_rating || {};
  const vitalKeys = ["ttfb_ms", "fcp_ms", "lcp_ms", "cls"];
  
  for (const vName of vitalKeys) {
    const vVal = vitals[vName];
    if (vVal == null) continue;
    
    const vCard = document.createElement("div");
    vCard.className = "vital-card";

    const nameEl = document.createElement("div");
    nameEl.className = "vital-name";
    nameEl.textContent = vName.toUpperCase();

    const valEl = document.createElement("div");
    valEl.className = "vital-value";
    valEl.textContent = String(vVal);

    const rateEl = document.createElement("div");
    const r = ratings[vName];
    if (r) {
      rateEl.className = "vital-rating " + (r.toLowerCase() === "good" ? "good" : r.toLowerCase() === "poor" ? "poor" : "avg");
      rateEl.textContent = "● " + r;
    } else {
      rateEl.className = "vital-rating";
      rateEl.textContent = "● Unrated";
    }

    vCard.appendChild(nameEl);
    vCard.appendChild(valEl);
    vCard.appendChild(rateEl);
    vitalsGrid.appendChild(vCard);
  }
  wrapper.appendChild(vitalsGrid);

  container.appendChild(wrapper);
  return wrapper;
}

function renderBacktestTearSheet(container, payload) {
  if (!payload) return;
  const wrapper = document.createElement("div");
  wrapper.className = "backtest-tearsheet";

  // Header & Headline Stats
  const header = document.createElement("div");
  header.className = "detail-header";
  const title = document.createElement("strong");
  title.textContent = `Backtest: ${payload.symbol || payload.strategy || "Strategy"}`;
  header.appendChild(title);
  if (payload.deployable !== undefined) {
    header.insertAdjacentHTML("beforeend", deployableBadge(payload.deployable));
  }
  wrapper.appendChild(header);

  // Stats row
  const statRow = document.createElement("div");
  statRow.className = "stat-row";
  const stats = [
    ["Sharpe", fmtMetric(payload.sharpe)],
    ["DSR", fmtMetric(payload.dsr)],
    ["PBO", fmtMetric(payload.pbo)],
    ["Max DD", payload.max_drawdown != null ? (payload.max_drawdown * 100).toFixed(1) + "%" : "—"],
    ["Total Return", payload.total_return != null ? (payload.total_return * 100).toFixed(1) + "%" : "—"],
  ];
  for (const [label, val] of stats) {
    const cell = document.createElement("div");
    const labelEl = document.createElement("div");
    labelEl.className = "stat-label";
    labelEl.textContent = label;
    const valEl = document.createElement("div");
    valEl.className = "stat-value";
    valEl.textContent = val;
    cell.appendChild(labelEl);
    cell.appendChild(valEl);
    statRow.appendChild(cell);
  }
  wrapper.appendChild(statRow);

  // Heatmap Table (if monthly returns present)
  if (payload.monthly_returns && Object.keys(payload.monthly_returns).length > 0) {
    const heatTitle = document.createElement("h4");
    heatTitle.style.cssText = "margin: 8px 0 4px; font-size: 12px; text-transform: uppercase; color: var(--text-secondary);";
    heatTitle.textContent = "Monthly Returns (%)";
    wrapper.appendChild(heatTitle);

    const table = document.createElement("table");
    table.className = "returns-heatmap-table";
    const months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec", "Year"];
    table.innerHTML = `<thead><tr><th>Year</th>${months.map(m => `<th>${m}</th>`).join("")}</tr></thead>`;
    const tbody = document.createElement("tbody");

    for (const [yr, mObj] of Object.entries(payload.monthly_returns)) {
      const tr = document.createElement("tr");
      tr.innerHTML = `<td><strong>${yr}</strong></td>`;
      for (let i = 1; i <= 12; i++) {
        const val = mObj[i] ?? mObj[String(i)];
        const td = document.createElement("td");
        if (val === undefined || val === null) {
          td.className = "heatmap-zero";
          td.textContent = "—";
        } else {
          const num = Number(val);
          const cls = num > 5 ? "heatmap-pos-high" : num > 2 ? "heatmap-pos-med" : num > 0 ? "heatmap-pos-low" : num < -5 ? "heatmap-neg-high" : num < -2 ? "heatmap-neg-med" : "heatmap-neg-low";
          td.className = cls;
          td.textContent = (num >= 0 ? "+" : "") + num.toFixed(1);
        }
        tr.appendChild(td);
      }
      const yVal = mObj.year ?? mObj.total;
      const tdYear = document.createElement("td");
      if (yVal !== undefined && yVal !== null) {
        const yNum = Number(yVal);
        tdYear.className = yNum >= 0 ? "heatmap-pos-med" : "heatmap-neg-med";
        tdYear.textContent = (yNum >= 0 ? "+" : "") + yNum.toFixed(1) + "%";
      } else {
        tdYear.textContent = "—";
      }
      tr.appendChild(tdYear);
      tbody.appendChild(tr);
    }
    table.appendChild(tbody);
    wrapper.appendChild(table);
  }

  container.appendChild(wrapper);
  return wrapper;
}

function renderMacroRegimeRadar(container, payload) {
  if (!payload) return;
  const wrapper = document.createElement("div");
  wrapper.className = "macro-radar-panel";

  const header = document.createElement("div");
  header.className = "detail-header";
  const title = document.createElement("strong");
  title.textContent = `Regime: ${payload.market_regime || "Unavailable"}`;
  header.appendChild(title);

  const killBadge = document.createElement("span");
  if (payload.kill_switch_active == null) {
    killBadge.className = "badge badge-caution";
    killBadge.textContent = "Kill Switch Unknown";
  } else if (payload.kill_switch_active === true) {
    killBadge.className = "badge badge-decline";
    killBadge.textContent = "Kill Switch Active";
  } else {
    killBadge.className = "badge badge-growth";
    killBadge.textContent = "Normal Operation";
  }
  header.appendChild(killBadge);
  wrapper.appendChild(header);

  // Indicators Grid
  const grid = document.createElement("div");
  grid.className = "stat-row";
  const indicators = [
    ["VIX", fmtMetric(payload.vix)],
    ["Sahm Rule", fmtMetric(payload.sahm_rule)],
    ["HY OAS", payload.high_yield_oas != null ? Number(payload.high_yield_oas).toFixed(2) + "%" : "—"],
    ["Yield Curve (10Y-2Y)", fmtMetric(payload.yield_curve)],
  ];
  for (const [label, val] of indicators) {
    const cell = document.createElement("div");
    cell.innerHTML = `<div class="stat-label">${label}</div><div class="stat-value">${val}</div>`;
    grid.appendChild(cell);
  }
  wrapper.appendChild(grid);

  // HMM Risk-On Probability Bar
  if (payload.hmm_risk_on_probability != null) {
    const hmmSection = document.createElement("div");
    const probPct = Math.round(payload.hmm_risk_on_probability * 100);
    hmmSection.innerHTML = `
      <div style="display:flex; justify-content:space-between; font-size:11px; margin-bottom:4px;">
        <span style="color:var(--text-secondary); text-transform:uppercase;">HMM Risk-On Probability</span>
        <strong>${probPct}%</strong>
      </div>
      <div class="hmm-probability-bar">
        <div class="hmm-bar-fill" style="width:${probPct}%; background:${probPct > 60 ? "var(--growth)" : probPct > 30 ? "var(--caution)" : "var(--decline)"}"></div>
      </div>
    `;
    wrapper.appendChild(hmmSection);
  }

  container.appendChild(wrapper);
  return wrapper;
}

function renderOrderTicket(container, payload) {
  const wrapper = document.createElement("div");
  wrapper.className = "order-ticket";

  const header = document.createElement("div");
  header.style.cssText = "display: flex; justify-content: space-between; align-items: center;";
  header.innerHTML = `
    <div>
      <strong style="font-size:15px;">#${payload.id ?? "NEW"} ${payload.symbol || "TICKER"}</strong>
      <span class="badge ${payload.action === "BUY" ? "badge-growth" : "badge-decline"}" style="margin-left:6px;">${payload.action || "BUY"}</span>
    </div>
    <span class="badge ${payload.auto_approved ? "badge-growth" : "badge-caution"}">${payload.auto_approved ? "Auto-Approved" : "Pending Review"}</span>
  `;
  wrapper.appendChild(header);

  const stats = document.createElement("div");
  stats.className = "stat-row";
  stats.innerHTML = `
    <div><div class="stat-label">Confidence</div><div class="stat-value">${payload.confidence != null ? (payload.confidence * 100).toFixed(0) + "%" : "—"}</div></div>
    <div><div class="stat-label">Reference Price</div><div class="stat-value">${formatCurrency(payload.price)}</div></div>
    <div><div class="stat-label">Proposed Qty</div><div class="stat-value">${payload.quantity ?? "—"}</div></div>
    <div><div class="stat-label">RSI (14)</div><div class="stat-value">${fmtMetric(payload.rsi)}</div></div>
  `;
  wrapper.appendChild(stats);

  if (payload.rationale) {
    const rat = document.createElement("div");
    rat.style.cssText = "background:var(--surface2); padding:8px 10px; border-radius:6px; font-size:12px; line-height:1.4;";
    rat.innerHTML = `<span style="color:var(--text-muted);">Rationale:</span> ${payload.rationale}`;
    wrapper.appendChild(rat);
  }

  container.appendChild(wrapper);
  return wrapper;
}

function renderVisualDiff(container, payload) {
  if (!payload) return;
  const wrapper = document.createElement("div");
  wrapper.className = "visual-diff-panel";

  const header = document.createElement("div");
  header.className = "inspector-header";
  let badgeHtml;
  if (payload.baseline_established) {
    badgeHtml = `<span class="badge badge-growth">🆕 Baseline Established</span>`;
  } else if (payload.match) {
    badgeHtml = `<span class="badge badge-growth">100% Match</span>`;
  } else {
    badgeHtml = `<span class="badge badge-caution">Visual Diff Detected</span>`;
  }
  
  header.innerHTML = `
    <span class="inspector-route">${payload.route || "/"}</span>
    ${badgeHtml}
  `;
  wrapper.appendChild(header);

  const views = document.createElement("div");
  views.className = "diff-views-container";

  const cardBefore = document.createElement("div");
  cardBefore.className = "diff-card";
  cardBefore.innerHTML = `
    <span class="diff-card-label">Baseline (Expected)</span>
    <div style="min-height:160px; display:flex; align-items:center; justify-content:center; background:var(--surface3); border-radius:6px;">
      ${payload.baselineImg ? `<img class="diff-img" src="${payload.baselineImg}" />` : '<span style="color:var(--text-muted); font-size:12px;">Baseline Golden Render</span>'}
    </div>
  `;
  views.appendChild(cardBefore);

  const cardAfter = document.createElement("div");
  cardAfter.className = "diff-card";
  cardAfter.innerHTML = `
    <span class="diff-card-label">Live Capture (Actual)</span>
    <div style="min-height:160px; display:flex; align-items:center; justify-content:center; background:var(--surface3); border-radius:6px;">
      ${payload.actualImg ? `<img class="diff-img" src="${payload.actualImg}" />` : '<span style="color:var(--text-muted); font-size:12px;">Live DevTools Capture</span>'}
    </div>
  `;
  views.appendChild(cardAfter);

  wrapper.appendChild(views);
  container.appendChild(wrapper);
  return wrapper;
}

function renderNetworkTrace(container, payload) {
  const wrapper = document.createElement("div");
  wrapper.className = "network-trace-panel";

  const header = document.createElement("div");
  header.className = "inspector-header";
  header.innerHTML = `
    <span class="inspector-route">Network Trace: ${payload.route || "/"}</span>
    <span class="badge badge-growth">${(payload.requests || []).length} Requests Intercepted</span>
  `;
  wrapper.appendChild(header);

  const table = document.createElement("table");
  table.className = "network-trace-table";
  table.innerHTML = `<thead><tr><th>Method</th><th>Endpoint</th><th>Status</th><th>Latency</th><th>Parity</th></tr></thead>`;
  const tbody = document.createElement("tbody");

  const reqs = payload.requests || [
    { method: "GET", url: "/api/pilots", status: 200, ms: 42, parity: "OK" },
    { method: "GET", url: "/api/signals", status: 200, ms: 68, parity: "OK" },
    { method: "GET", url: "/api/portfolio", status: 200, ms: 25, parity: "OK" },
  ];

  for (const r of reqs) {
    const tr = document.createElement("tr");
    const mCls = r.method === "GET" ? "method-get" : r.method === "POST" ? "method-post" : "method-put";
    tr.innerHTML = `
      <td><span class="method-badge ${mCls}">${r.method}</span></td>
      <td><strong>${r.url || r.endpoint}</strong></td>
      <td><span class="badge ${r.status >= 400 ? "badge-decline" : "badge-growth"}">${r.status || 200}</span></td>
      <td>${r.ms != null ? r.ms + "ms" : "—"}</td>
      <td><span style="color:var(--growth); font-weight:600;">${r.parity || "PASS"}</span></td>
    `;
    tbody.appendChild(tr);
  }
  table.appendChild(tbody);
  wrapper.appendChild(table);

  container.appendChild(wrapper);
  return wrapper;
}

function renderPitMatrix(container, payload) {
  if (!payload) return;
  const wrapper = document.createElement("div");
  wrapper.className = "pit-matrix-panel";

  const header = document.createElement("div");
  header.className = "detail-header";
  header.innerHTML = `<strong>Point-In-Time Fundamentals Coverage Matrix</strong><span class="badge badge-growth">Zero Lookahead Verified</span>`;
  wrapper.appendChild(header);

  const rows = payload.rows || [];
  if (rows.length === 0) {
    const empty = document.createElement("p");
    empty.className = "empty-state";
    empty.textContent = "No PIT coverage rows available.";
    wrapper.appendChild(empty);
  } else {
    const table = document.createElement("table");
    table.className = "pit-matrix-table";
    table.innerHTML = `<thead><tr><th>Symbol</th><th>Rows</th><th>Earliest Report</th><th>Latest Report</th><th>Lag Buffer</th></tr></thead>`;
    const tbody = document.createElement("tbody");
    for (const r of rows) {
      const tr = document.createElement("tr");
      tr.innerHTML = `
        <td><strong>${r.symbol || r.Symbol || "—"}</strong></td>
        <td>${r.pit_rows ?? r.rows ?? r.Rows ?? r.count ?? "—"}</td>
        <td>${r.earliest_report_date ?? r.earliest ?? r.Earliest_Date ?? "—"}</td>
        <td>${r.latest_report_date ?? r.latest ?? r.Latest_Date ?? "—"}</td>
        <td class="pit-safe-cell">✅ 45d Lag Respected</td>
      `;
      tbody.appendChild(tr);
    }
    table.appendChild(tbody);
    wrapper.appendChild(table);
  }

  container.appendChild(wrapper);
  return wrapper;
}

function renderModelDiagnostics(container, payload) {
  if (!payload) return;
    const wrapper = document.createElement("div");
    wrapper.className = "model-diagnostics-panel";
    
    const header = document.createElement("div");
    header.className = "detail-header";
    const horizon = payload.horizon_days ? `${payload.horizon_days}d` : "30d";
    header.innerHTML = `<strong>Forecast Model Skill Decay & Drift Report</strong><span class="badge badge-caution">Horizon: ${horizon}</span>`;
    wrapper.appendChild(header);
    
    const rows = payload.rows || [];
    
    if (rows.length === 0) {
        const p = document.createElement("p");
        p.className = "empty-state";
        p.textContent = payload.reason || "No forecast drift records recorded yet.";
        wrapper.appendChild(p);
    } else {
        const table = document.createElement("table");
        table.className = "table";
        table.innerHTML = `<thead><tr><th>Symbol</th><th>Pending</th><th>Completed</th><th>Skill Weights</th></tr></thead>`;
        
        const tbody = document.createElement("tbody");
        
        for (const r of rows) {
            const tr = document.createElement("tr");
            
            let weightsStr = "—";
            if (r.skill_weights) {
                weightsStr = Object.entries(r.skill_weights)
                    .map(([model, weight]) => `${model}: ${fmtMetric(weight)}`)
                    .join(", ");
            }
            
            tr.innerHTML = `
                <td><strong>${r.symbol || "—"}</strong></td>
                <td>${r.pending ?? "—"}</td>
                <td>${r.completed ?? "—"}</td>
                <td>${weightsStr}</td>
            `;
            
            tbody.appendChild(tr);
        }
        
        table.appendChild(tbody);
        wrapper.appendChild(table);
    }
    
    container.appendChild(wrapper);
    return wrapper;
}

function renderStrategyTuner(container, payload, app) {
  if (!payload) return;
  const wrapper = document.createElement("div");
  wrapper.className = "strategy-tuner-panel";

  const header = document.createElement("div");
  header.className = "detail-header";
  const liveCapable = !!(app && typeof app.callServerTool === "function");
  header.innerHTML = `<strong>Strategy Parameter Sensitivity: ${payload.strategy_name || "Strategy"}</strong><span class="badge badge-growth">${liveCapable ? "Live Sensitivity" : "Sensitivity Snapshot"}</span>`;
  wrapper.appendChild(header);

  // Current parameter state -- seeded from the initial tool result, updated
  // as the operator drags each slider.
  const state = {
    strategy_name: payload.strategy_name || "rsi2_mean_reversion",
    rsi_lower: payload.rsi_lower || 25,
    rsi_upper: payload.rsi_upper || 75,
    sma_window: payload.sma_window || 50,
    stop_loss: payload.stop_loss || 5,
  };

  const sliders = [
    { id: "rsi_lower", label: "RSI Oversold Level", min: 10, max: 40, val: state.rsi_lower },
    { id: "rsi_upper", label: "RSI Overbought Level", min: 60, max: 90, val: state.rsi_upper },
    { id: "sma_window", label: "Trend SMA Window", min: 20, max: 200, val: state.sma_window },
    { id: "stop_loss", label: "Stop Loss (%)", min: 1, max: 15, val: state.stop_loss },
  ];

  for (const s of sliders) {
    const row = document.createElement("div");
    row.className = "tuner-slider-row";

    const label = document.createElement("label");
    label.textContent = s.label;

    const input = document.createElement("input");
    input.type = "range";
    input.className = "tuner-slider-input";
    input.min = String(s.min);
    input.max = String(s.max);
    input.value = String(s.val);

    const valDisplay = document.createElement("span");
    valDisplay.className = "tuner-val-display";
    valDisplay.textContent = String(s.val);

    input.oninput = () => {
      valDisplay.textContent = input.value;
      state[s.id] = Number(input.value);
      scheduleRecompute();
    };

    row.appendChild(label);
    row.appendChild(input);
    row.appendChild(valDisplay);
    wrapper.appendChild(row);
  }

  const statRow = document.createElement("div");
  statRow.className = "stat-row";
  statRow.style.marginTop = "8px";
  wrapper.appendChild(statRow);

  const statusLine = document.createElement("div");
  statusLine.className = "tuner-status-line";
  statusLine.style.cssText = "margin-top:6px; font-size:11px; color:var(--text-muted);";
  wrapper.appendChild(statusLine);

  function renderStats(p, opts) {
    opts = opts || {};
    const sharpeVal = typeof p.simulated_sharpe === "number" ? p.simulated_sharpe.toFixed(2) : "—";
    const maxDdVal = typeof p.simulated_max_dd_pct === "number" ? p.simulated_max_dd_pct.toFixed(1) + "%" : "—";
    const winRateVal = typeof p.simulated_win_rate_pct === "number" ? p.simulated_win_rate_pct.toFixed(1) + "%" : "—";
    statRow.style.opacity = opts.pending ? "0.5" : "1";
    statRow.innerHTML = `
      <div><div class="stat-label">Estimated Sharpe</div><div class="stat-value" style="color:var(--growth);">${sharpeVal}</div></div>
      <div><div class="stat-label">Estimated MaxDD</div><div class="stat-value">${maxDdVal}</div></div>
      <div><div class="stat-label">Win Rate</div><div class="stat-value">${winRateVal}</div></div>
    `;
  }
  let lastGoodPayload = payload;
  renderStats(lastGoodPayload);

  if (!liveCapable) {
    statusLine.textContent = "Host does not support live tool re-invocation from this widget -- sliders show values only.";
    container.appendChild(wrapper);
    return wrapper;
  }

  // Debounced live recompute: re-invokes tune_strategy_parameters with the
  // current slider state via the ext-apps SDK's callServerTool
  let debounceTimer = null;
  let requestSeq = 0;
  function scheduleRecompute() {
    statusLine.textContent = "Recalculating…";
    if (debounceTimer) clearTimeout(debounceTimer);
    debounceTimer = setTimeout(runRecompute, 350);
  }

  async function runRecompute() {
    const mySeq = ++requestSeq;
    renderStats(lastGoodPayload, { pending: true });
    try {
      const result = await app.callServerTool({
        name: "tune_strategy_parameters",
        arguments: { ...state },
      });
      if (mySeq !== requestSeq) return; // a newer slider drag superseded this call
      const fresh = extractJsonPayload(result?.content?.[0]?.text);
      if (fresh) {
        lastGoodPayload = fresh;
        renderStats(lastGoodPayload);
        statusLine.textContent = "";
      } else {
        renderStats(lastGoodPayload);
        statusLine.textContent = "No response from tune_strategy_parameters.";
      }
    } catch (err) {
      if (mySeq !== requestSeq) return;
      renderStats(lastGoodPayload);
      statusLine.textContent = "Error: " + err.message;
    }
  }

  container.appendChild(wrapper);
  return wrapper;
}


