// ============================================================
// LLM Cost vs. Performance Tracker — Application Logic
// ============================================================

(function () {
    "use strict";

    // ── State ───────────────────────────────────────────────
    let currentBenchmark = "mmlu";
    let currentCostMode = "total";
    let activeProviders = new Set(Object.keys(PROVIDER_COLORS));
    let chart = null;
    let sortColumn = null;
    let sortDirection = "asc";

    // ── Initialization ──────────────────────────────────────
    document.addEventListener("DOMContentLoaded", init);

    function init() {
        setLastUpdated();
        populateBenchmarkSelect();
        populateProviderFilters();
        populateSources();
        populateExplainer();
        bindEvents();
        renderChart();
        renderTable();
        initTheme();
    }

    // ── Last Updated ────────────────────────────────────────
    function setLastUpdated() {
        document.getElementById("lastUpdated").textContent =
            "Data last updated: " + DATA_LAST_UPDATED;
    }

    // ── Populate Controls ───────────────────────────────────
    function populateBenchmarkSelect() {
        const sel = document.getElementById("benchmarkSelect");
        Object.entries(BENCHMARKS).forEach(([key, b]) => {
            const opt = document.createElement("option");
            opt.value = key;
            opt.textContent = b.name + " — " + b.measures;
            sel.appendChild(opt);
        });
        sel.value = currentBenchmark;
    }

    function populateProviderFilters() {
        const container = document.getElementById("providerFilters");
        Object.keys(PROVIDER_COLORS).forEach((provider) => {
            const chip = document.createElement("button");
            chip.className = "provider-chip active";
            chip.textContent = provider;
            chip.dataset.provider = provider;
            chip.addEventListener("click", () => toggleProvider(chip, provider));
            container.appendChild(chip);
        });
    }

    function toggleProvider(chip, provider) {
        if (activeProviders.has(provider)) {
            activeProviders.delete(provider);
            chip.classList.remove("active");
        } else {
            activeProviders.add(provider);
            chip.classList.add("active");
        }
        renderChart();
        renderTable();
    }

    // ── Sources ─────────────────────────────────────────────
    function populateSources() {
        const pList = document.getElementById("pricingSources");
        PRICING_SOURCES.forEach((s) => {
            const li = document.createElement("li");
            li.innerHTML = `<a href="${s.url}" target="_blank" rel="noopener">${s.name}</a>`;
            pList.appendChild(li);
        });
        const bList = document.getElementById("benchmarkSources");
        BENCHMARK_SOURCES.forEach((s) => {
            const li = document.createElement("li");
            li.innerHTML = `<a href="${s.url}" target="_blank" rel="noopener">${s.name}</a>`;
            bList.appendChild(li);
        });
    }

    // ── Benchmark Explainer ─────────────────────────────────
    function populateExplainer() {
        const grid = document.getElementById("explainerGrid");
        Object.values(BENCHMARKS).forEach((b) => {
            const card = document.createElement("div");
            card.className = "explainer-card";
            card.innerHTML = `
                <h3>${b.name}</h3>
                <div class="explainer-meta">${b.fullName} &middot; Scale: ${b.scale}</div>
                <p>${b.description}</p>
                <div class="explainer-measurement">
                    <svg width="13" height="13" viewBox="0 0 16 16" fill="none" style="flex-shrink:0;margin-top:1px"><circle cx="8" cy="8" r="6.5" stroke="currentColor" stroke-width="1.4"/><path d="M8 5v3.5l2 1.5" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"/></svg>
                    <span><strong>Data collected:</strong> ${b.lastCollected}</span>
                </div>
                <div class="explainer-meta"><strong>Source:</strong> ${b.source}</div>
            `;
            grid.appendChild(card);
        });
    }

    // ── Events ──────────────────────────────────────────────
    function bindEvents() {
        document.getElementById("benchmarkSelect").addEventListener("change", (e) => {
            currentBenchmark = e.target.value;
            renderChart();
            renderTable();
        });
        document.getElementById("costSelect").addEventListener("change", (e) => {
            currentCostMode = e.target.value;
            renderChart();
            renderTable();
        });
        document.getElementById("downloadCsv").addEventListener("click", downloadCSV);
        document.getElementById("themeToggle").addEventListener("click", toggleTheme);

        // Table sorting
        document.querySelectorAll("#dataTable thead th[data-sort]").forEach((th) => {
            th.addEventListener("click", () => {
                const col = th.dataset.sort;
                if (sortColumn === col) {
                    sortDirection = sortDirection === "asc" ? "desc" : "asc";
                } else {
                    sortColumn = col;
                    sortDirection = "asc";
                }
                renderTable();
            });
        });
    }

    // ── Cost helper ─────────────────────────────────────────
    function getCost(model) {
        switch (currentCostMode) {
            case "input":
                return model.input_cost;
            case "output":
                return model.output_cost;
            default:
                return model.input_cost + model.output_cost;
        }
    }

    function costLabel() {
        switch (currentCostMode) {
            case "input":
                return "Input Cost ($/1M tokens)";
            case "output":
                return "Output Cost ($/1M tokens)";
            default:
                return "Total Cost ($/1M tokens)";
        }
    }

    // ── Filtered Data ───────────────────────────────────────
    function getFilteredData() {
        return LLM_DATA.filter(
            (m) =>
                activeProviders.has(m.provider) &&
                m.benchmarks[currentBenchmark] != null
        );
    }

    // ── Chart ───────────────────────────────────────────────
    function renderChart() {
        const ctx = document.getElementById("scatterChart").getContext("2d");
        const filtered = getFilteredData();
        const benchInfo = BENCHMARKS[currentBenchmark];

        const datasets = [];
        const grouped = {};
        filtered.forEach((m) => {
            if (!grouped[m.provider]) grouped[m.provider] = [];
            grouped[m.provider].push(m);
        });

        Object.entries(grouped).forEach(([provider, models]) => {
            const color = PROVIDER_COLORS[provider];
            datasets.push({
                label: provider,
                data: models.map((m) => ({
                    x: getCost(m),
                    y: m.benchmarks[currentBenchmark],
                    model: m,
                })),
                backgroundColor: color.bg,
                borderColor: color.border,
                borderWidth: 2,
                pointRadius: 7,
                pointHoverRadius: 11,
                pointStyle: "circle",
            });
        });

        if (chart) chart.destroy();

        const isDark = document.documentElement.getAttribute("data-theme") === "dark";
        const gridColor = isDark ? "rgba(255,255,255,0.06)" : "rgba(0,0,0,0.06)";
        const textColor = isDark ? "#94a3c0" : "#4b5563";

        chart = new Chart(ctx, {
            type: "scatter",
            data: { datasets },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                interaction: {
                    mode: "nearest",
                    intersect: true,
                },
                plugins: {
                    legend: {
                        position: "top",
                        labels: {
                            color: textColor,
                            usePointStyle: true,
                            pointStyle: "circle",
                            padding: 20,
                            font: { family: "'Inter', sans-serif", size: 12, weight: 500 },
                        },
                    },
                    tooltip: {
                        backgroundColor: isDark ? "#1a2235" : "#ffffff",
                        titleColor: isDark ? "#f0f4fc" : "#111827",
                        bodyColor: isDark ? "#94a3c0" : "#4b5563",
                        borderColor: isDark ? "#3b82f6" : "#2563eb",
                        borderWidth: 1,
                        cornerRadius: 10,
                        padding: 14,
                        titleFont: { family: "'Inter', sans-serif", size: 14, weight: 700 },
                        bodyFont: { family: "'Inter', sans-serif", size: 12.5 },
                        displayColors: false,
                        callbacks: {
                            title: (items) => {
                                const d = items[0].raw;
                                const rel = d.model.releaseDate ? "  (released " + d.model.releaseDate + ")" : "";
                                return d.model.name + " (" + d.model.provider + ")" + rel;
                            },
                            label: (item) => {
                                const d = item.raw;
                                const m = d.model;
                                const lines = [
                                    benchInfo.name + ": " + d.y + (currentBenchmark === "arena_elo" ? "" : "%"),
                                    "  Data: " + (benchInfo.lastCollected.split(".")[0]),
                                    "",
                                    "Input:  $" + m.input_cost.toFixed(2) + " / 1M tokens",
                                    "Output: $" + m.output_cost.toFixed(2) + " / 1M tokens",
                                    "Total:  $" + (m.input_cost + m.output_cost).toFixed(2) + " / 1M tokens",
                                ];
                                return lines;
                            },
                        },
                    },
                },
                scales: {
                    x: {
                        type: "logarithmic",
                        title: {
                            display: true,
                            text: costLabel(),
                            color: textColor,
                            font: { family: "'Inter', sans-serif", size: 13, weight: 600 },
                        },
                        grid: { color: gridColor },
                        ticks: {
                            color: textColor,
                            font: { family: "'Inter', sans-serif", size: 11 },
                            callback: (v) => "$" + v,
                        },
                    },
                    y: {
                        title: {
                            display: true,
                            text: benchInfo.name + " Score" + (currentBenchmark === "arena_elo" ? " (ELO)" : " (%)"),
                            color: textColor,
                            font: { family: "'Inter', sans-serif", size: 13, weight: 600 },
                        },
                        grid: { color: gridColor },
                        ticks: {
                            color: textColor,
                            font: { family: "'Inter', sans-serif", size: 11 },
                        },
                    },
                },
            },
        });
    }

    // ── Table ───────────────────────────────────────────────
    function renderTable() {
        const tbody = document.getElementById("dataTableBody");
        const benchInfo = BENCHMARKS[currentBenchmark];
        document.getElementById("benchmarkHeader").innerHTML =
            benchInfo.name + ' <span class="sort-arrow"></span>';

        let data = getFilteredData().map((m) => ({
            ...m,
            total_cost: m.input_cost + m.output_cost,
            benchmarkScore: m.benchmarks[currentBenchmark],
        }));

        // Also include models without this benchmark score (show N/A)
        const allFiltered = LLM_DATA.filter((m) => activeProviders.has(m.provider));
        data = allFiltered.map((m) => ({
            ...m,
            total_cost: m.input_cost + m.output_cost,
            benchmarkScore: m.benchmarks[currentBenchmark],
        }));

        // Sort
        if (sortColumn) {
            data.sort((a, b) => {
                let va, vb;
                switch (sortColumn) {
                    case "name":
                        va = a.name.toLowerCase();
                        vb = b.name.toLowerCase();
                        break;
                    case "provider":
                        va = a.provider.toLowerCase();
                        vb = b.provider.toLowerCase();
                        break;
                    case "input_cost":
                        va = a.input_cost;
                        vb = b.input_cost;
                        break;
                    case "output_cost":
                        va = a.output_cost;
                        vb = b.output_cost;
                        break;
                    case "total_cost":
                        va = a.total_cost;
                        vb = b.total_cost;
                        break;
                    case "benchmark":
                        va = a.benchmarkScore ?? -1;
                        vb = b.benchmarkScore ?? -1;
                        break;
                    default:
                        return 0;
                }
                if (typeof va === "string") {
                    return sortDirection === "asc" ? va.localeCompare(vb) : vb.localeCompare(va);
                }
                return sortDirection === "asc" ? va - vb : vb - va;
            });
        }

        // Update sort arrows
        document.querySelectorAll("#dataTable thead th").forEach((th) => {
            const arrow = th.querySelector(".sort-arrow");
            if (arrow) {
                if (th.dataset.sort === sortColumn) {
                    arrow.textContent = sortDirection === "asc" ? " ▲" : " ▼";
                    arrow.style.opacity = "1";
                } else {
                    arrow.textContent = "";
                    arrow.style.opacity = "0.4";
                }
            }
        });

        tbody.innerHTML = data
            .map(
                (m) => `
            <tr>
                <td class="model-name">${m.name}</td>
                <td><span class="provider-badge">${m.provider}</span></td>
                <td class="cost-cell">$${m.input_cost.toFixed(2)}</td>
                <td class="cost-cell">$${m.output_cost.toFixed(2)}</td>
                <td class="cost-cell">$${m.total_cost.toFixed(2)}</td>
                <td class="${m.benchmarkScore != null ? "benchmark-cell" : "na-cell"}">${m.benchmarkScore != null ? (currentBenchmark === "arena_elo" ? m.benchmarkScore : m.benchmarkScore + "%") : "N/A"}</td>
            </tr>
        `
            )
            .join("");
    }

    // ── CSV Download ────────────────────────────────────────
    function downloadCSV() {
        const benchmarkKeys = Object.keys(BENCHMARKS);
        const headers = [
            "Model",
            "Provider",
            "Input Cost ($/1M tokens)",
            "Output Cost ($/1M tokens)",
            "Total Cost ($/1M tokens)",
            ...benchmarkKeys.map((k) => BENCHMARKS[k].name),
        ];

        const rows = LLM_DATA.map((m) => [
            m.name,
            m.provider,
            m.input_cost.toFixed(2),
            m.output_cost.toFixed(2),
            (m.input_cost + m.output_cost).toFixed(2),
            ...benchmarkKeys.map((k) => (m.benchmarks[k] != null ? m.benchmarks[k] : "")),
        ]);

        const csv =
            [headers.join(","), ...rows.map((r) => r.map((c) => `"${c}"`).join(","))].join(
                "\n"
            );

        const blob = new Blob([csv], { type: "text/csv;charset=utf-8;" });
        const link = document.createElement("a");
        link.href = URL.createObjectURL(blob);
        link.download = "llm_cost_performance_data.csv";
        link.click();
        URL.revokeObjectURL(link.href);
    }

    // ── Theme Toggle ────────────────────────────────────────
    function initTheme() {
        const saved = localStorage.getItem("llm-tracker-theme");
        if (saved) {
            document.documentElement.setAttribute("data-theme", saved);
        }
        updateThemeIcon();
    }

    function toggleTheme() {
        const current = document.documentElement.getAttribute("data-theme");
        const next = current === "dark" ? "light" : "dark";
        document.documentElement.setAttribute("data-theme", next);
        localStorage.setItem("llm-tracker-theme", next);
        updateThemeIcon();
        renderChart(); // Re-render chart with new theme colors
    }

    function updateThemeIcon() {
        const theme = document.documentElement.getAttribute("data-theme");
        document.getElementById("themeIcon").textContent = theme === "dark" ? "☀️" : "🌙";
    }
})();
