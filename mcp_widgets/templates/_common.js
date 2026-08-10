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
