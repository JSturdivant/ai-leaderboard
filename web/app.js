// LLM Cost vs. Capability — local leaderboard UI.
// Data comes from /data/latest.json (built by the Python side). Scores are computed there;
// cost basis, efficiency frontier and alias picks are recomputed here so controls feel instant.
(function () {
    "use strict";

    const CHANNELS = ["primary", "openrouter", "nous"];
    const CHANNEL_NAME = { primary: "primary", openrouter: "OpenRouter", nous: "Nous Portal" };
    const FREE_X = 0.004; // where cost-0 models sit on the log axis
    const X_MIN = 0.003;
    const LAB_NAMES = {
        openai: "OpenAI", anthropic: "Anthropic", google: "Google", meta: "Meta", "x-ai": "xAI", mistralai: "Mistral",
        deepseek: "DeepSeek", qwen: "Qwen", "z-ai": "Z.ai", moonshotai: "Moonshot", minimax: "MiniMax", nvidia: "NVIDIA",
        xiaomi: "Xiaomi", tencent: "Tencent", amazon: "Amazon", cohere: "Cohere", other: "Other",
    };
    const LAB_COLORS = {
        openai: "#10a37f", anthropic: "#e07a45", google: "#4285f4", meta: "#8b5cf6", "x-ai": "#94a3b8", mistralai: "#f97316",
        deepseek: "#6366f1", qwen: "#ec4899", "z-ai": "#14b8a6", moonshotai: "#eab308", minimax: "#f43f5e", nvidia: "#84cc16",
        xiaomi: "#06b6d4", tencent: "#0ea5e9", amazon: "#a16207", cohere: "#c084fc", other: "#64748b",
    };
    const BLEND_LABEL = { "3:1": "blended 3:1 in:out", "1:1": "blended 1:1 in:out", "1:0": "input price", "0:1": "output price" };
    const PREF_KEY = "llm-leaderboard-prefs-v1";

    const $ = (id) => document.getElementById(id);
    const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
    const clamp = (x, lo, hi) => Math.max(lo, Math.min(hi, x));

    // ── state ────────────────────────────────────────────────────────────────────────
    let DATA = null;
    let byId = {};
    let charts = [];
    let lastMtime = null;
    let selectedId = null;
    let showAllRows = false;
    let topLabs = [];

    const DEFAULTS = {
        dim: "cat:coding", blend: "3:1", fade: 90, hide: 0, q: "", labsOff: [], frontier: true, caps: true, grid: false,
        minScore: 50, fEfficient: false, fFree: false, fTools: false, fConfident: false,
        sort: { key: "usd", dir: 1 }, capOverride: {}, current: {}, theme: "dark",
    };
    const P = loadPrefs();
    function loadPrefs() {
        try { return { ...DEFAULTS, ...JSON.parse(localStorage.getItem(PREF_KEY) || "{}") }; } catch (e) { return { ...DEFAULTS }; }
    }
    function savePrefs() { try { localStorage.setItem(PREF_KEY, JSON.stringify(P)); } catch (e) { /* private mode */ } }

    // ── formatting ───────────────────────────────────────────────────────────────────
    function usd(x) {
        if (x == null) return "—";
        if (x === 0) return "Free";
        if (x < 0.01) return "$" + x.toFixed(4);
        if (x < 1) return "$" + x.toFixed(3);
        return "$" + x.toFixed(2);
    }
    const num1 = (x) => (x == null ? "—" : x.toFixed(1));
    const usdM = (x) => (x === 0 ? "Free" : usd(x) + "/M");
    function ago(sec) {
        if (sec == null) return "never";
        if (sec < 90) return Math.round(sec) + "s ago";
        if (sec < 5400) return Math.round(sec / 60) + " min ago";
        if (sec < 129600) return Math.round(sec / 3600) + " h ago";
        return Math.round(sec / 86400) + " d ago";
    }
    function toast(msg) {
        const t = $("toast");
        t.textContent = msg; t.hidden = false;
        clearTimeout(toast._t);
        toast._t = setTimeout(() => (t.hidden = true), 3500);
    }

    // ── derived per-model values ─────────────────────────────────────────────────────
    const blendObj = () => { const [i, o] = P.blend.split(":").map(Number); return { input: i, output: o }; };

    function prepare() {
        const b = blendObj();
        byId = {};
        for (const m of DATA.models) {
            byId[m.id] = m;
            let best = null;
            for (const ch of CHANNELS) {
                for (const c of m.cost.channels[ch] || []) {
                    const v = (c.in * b.input + c.out * b.output) / (b.input + b.output);
                    if (!best || v < best.v - 1e-12) best = { v, ch, c };
                }
            }
            m._cost = best ? best.v : null;
            m._ch = best ? best.ch : null;
            m._cand = best ? best.c : null;
            m._lab = labKey(m);
        }
        const counts = {};
        for (const m of DATA.models) if (Object.keys(m.scores).length) counts[m._lab] = (counts[m._lab] || 0) + 1;
        topLabs = Object.keys(counts).sort((a, b2) => counts[b2] - counts[a]).slice(0, 12);
    }
    function labKey(m) { const v = m.vendor === "meta-llama" ? "meta" : m.vendor; return v; }
    const labGroup = (m) => (topLabs.includes(m._lab) ? m._lab : "other");
    const labName = (g) => LAB_NAMES[g] || g.replace(/(^|-)(\w)/g, (_, a, c) => (a ? " " : "") + c.toUpperCase());
    function labColor(g) {
        if (LAB_COLORS[g]) return LAB_COLORS[g];
        let h = 0; for (const ch of g) h = (h * 31 + ch.charCodeAt(0)) % 360;
        return `hsl(${h} 55% 55%)`;
    }

    function dimInfo(m, dim = P.dim) {
        const [kind, key] = dim.split(":");
        if (kind === "cat") {
            const x = m.scores[key];
            return x ? { s: x.s, conf: x.confident, cov: x.cov, n: x.n } : null;
        }
        const f = m.fit[key];
        return f == null ? null : { s: f, conf: (m.fit_cov[key] || 0) >= 0.8, cov: m.fit_cov[key], n: null };
    }
    function dimLabel(dim = P.dim) {
        const [kind, key] = dim.split(":");
        return kind === "cat" ? DATA.config.categories[key].label : `${key} fit`;
    }
    function dimShort(dim = P.dim) { const [k, v] = dim.split(":"); return k === "cat" ? DATA.config.categories[v].short : v + " fit"; }
    const usd100 = (cost, s) => (cost === 0 ? 0 : cost / Math.max(s, DATA.config.score_floor_for_ratio) * 100);

    function pareto(points) { // points: [{id, cost, s}] -> Set(id) not beaten on both axes
        let best = -Infinity; const out = new Set();
        for (const p of [...points].sort((a, b) => a.cost - b.cost || b.s - a.s)) if (p.s > best) { out.add(p.id); best = p.s; }
        return out;
    }

    function age(m) { return m.age_days == null ? null : m.age_days; }
    function fadeOf(m) {
        const a = age(m), cfg = DATA.config.age;
        if (!P.fade || a == null) return 1;
        const span = cfg.faded_days - cfg.fresh_days;
        return 1 - (1 - cfg.min_opacity) * clamp((a - P.fade) / span, 0, 1);
    }
    const matchesQuery = (m) => P.q && (m.id + " " + m.name).toLowerCase().includes(P.q.toLowerCase());

    function visibleModels() {
        return DATA.models.filter((m) => {
            if (m._cost == null) return false;
            if (P.labsOff.includes(labGroup(m))) return false;
            if (P.hide && age(m) != null && age(m) > P.hide) return false;
            return true;
        });
    }

    // ── alias picks (mirrors leaderboard/scoring.py) ─────────────────────────────────
    function effectiveCap(name) {
        const o = P.capOverride[name];
        if (o === undefined) return DATA.config.aliases[name].max_cost;
        return o === "none" ? null : o;
    }
    function eligible(m, a) {
        const c = m._cost;
        if (c == null) return false;
        if (a.max_cost != null && c > a.max_cost + 1e-12) return false;
        const req = a.require || [];
        if (req.includes("free") && c !== 0) return false;
        if (req.includes("tools") && !m.tools) return false;
        if (req.includes("reasoning") && !m.reasoning) return false;
        if (a.min_context && (m.ctx || 0) < a.min_context) return false;
        return true;
    }
    function aliasState(name) {
        const a = { ...DATA.config.aliases[name], max_cost: effectiveCap(name) };
        const tol = DATA.config.alias_value_tolerance;
        const all = [], ok = [];
        let unscored = 0;
        for (const m of DATA.models) {
            const f = m.fit[name];
            if (m._cost != null && f == null && eligible(m, a)) unscored++;
            if (m._cost == null || f == null) continue;
            const c = { id: m.id, cost: m._cost, fit: f };
            all.push(c);
            if (eligible(m, a)) ok.push(c);
        }
        const ranked = [...ok].sort((x, y) => y.fit - x.fit || x.cost - y.cost);
        const out = { a, n: ok.length, unscored, ranked, top: ranked[0] || null, value: null, current: null };
        if (ranked.length) {
            const near = ok.filter((c) => c.fit >= ranked[0].fit * (1 - tol));
            out.value = [...near].sort((x, y) => x.cost - y.cost || y.fit - x.fit)[0];
        }
        const curId = P.current[name] !== undefined ? P.current[name] : DATA.config.current_models[name];
        if (curId) {
            const cur = all.find((c) => c.id === curId);
            if (!cur) out.current = { id: curId, status: byId[curId] ? "no_data" : "unknown" };
            else {
                const inSet = ok.find((c) => c.id === curId);
                const better = ok.filter((c) => c.id !== curId && c.cost <= cur.cost + 1e-12 && c.fit > cur.fit).sort((x, y) => y.fit - x.fit || x.cost - y.cost);
                out.current = { id: curId, status: inSet ? "ok" : "ineligible", fit: cur.fit, cost: cur.cost, rank: inSet ? ranked.findIndex((c) => c.id === curId) + 1 : null, better: better.slice(0, 3) };
            }
        }
        return out;
    }

    // ── status / polling ─────────────────────────────────────────────────────────────
    async function getJSON(url, opts) {
        const r = await fetch(url, opts);
        if (!r.ok) throw Object.assign(new Error(r.statusText), { status: r.status });
        return r.json();
    }
    function sourceState(name, st, interval) {
        if (!st || !st.fetched_at) return st && st.error ? "err" : "none";
        if (st.ok === false) return "err";
        return (Date.now() / 1000 - st.fetched_at) > interval * 60 * 2.5 ? "stale" : "ok";
    }
    function renderStatus(st) {
        const pill = $("statusDot"), text = $("statusText");
        const sources = (DATA && DATA.sources) || {};
        const live = st.sources || {};
        const dots = [];
        for (const [name, meta] of Object.entries(sources)) {
            const s = live[name] || meta;
            const state = sourceState(name, s, meta.interval_minutes);
            dots.push(`<span title="${esc(s.error || "")}"><i class="dot ${state === "none" ? "" : state}"></i>${esc(meta.label.replace(/ \(.*\)/, ""))} · ${esc(ago(s.fetched_at ? st.now - s.fetched_at : null))}</span>`);
        }
        $("sourceDots").innerHTML = dots.join("");
        if (st.running) { pill.className = "dot busy"; text.textContent = "Refreshing… " + (st.step || ""); }
        else if (st.generated_ts) {
            const stale = Object.entries(sources).some(([n, m]) => ["err", "stale"].includes(sourceState(n, live[n] || m, m.interval_minutes)));
            pill.className = "dot " + (stale ? "stale" : "ok");
            text.textContent = "Data built " + ago(st.now - st.generated_ts);
        } else { pill.className = "dot"; text.textContent = st.scheduler ? "Waiting for first data…" : "No data (refresh disabled)"; }
        $("refreshBtn").disabled = !!st.running || !st.scheduler;
    }
    async function poll() {
        let st;
        try { st = await getJSON("/api/status"); } catch (e) { $("statusText").textContent = "Server unreachable"; $("statusDot").className = "dot err"; return schedulePoll(10000); }
        if (lastMtime !== null && st.generated_ts && st.generated_ts !== lastMtime) {
            await loadData(true);
        } else if (!DATA && st.generated_ts) {
            await loadData(false);
        }
        lastMtime = st.generated_ts;
        if (!DATA) { $("emptyState").hidden = false; $("emptyDetail").textContent = st.running ? `Fetching… ${st.step || ""}` : (st.last_error || "Waiting for the first refresh to start."); }
        renderStatus(st);
        if (DATA) renderSources(st);
        schedulePoll(st.running ? 2000 : 15000);
    }
    function schedulePoll(ms) { clearTimeout(poll._t); poll._t = setTimeout(poll, ms); }

    async function loadData(announce) {
        try {
            DATA = await getJSON("/data/latest.json");
        } catch (e) { if (e.status !== 404) console.error(e); return; }
        prepare();
        $("emptyState").hidden = true; $("mainContent").hidden = false;
        renderAll();
        if (announce) toast("Data updated");
    }

    // ── charts ───────────────────────────────────────────────────────────────────────
    const css = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
    function withAlpha(color, a) {
        if (color.startsWith("#")) {
            const n = parseInt(color.slice(1), 16);
            return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${a})`;
        }
        return color.replace(")", ` / ${a})`);
    }
    function destroyCharts() { charts.forEach((c) => c.destroy()); charts = []; }

    function buildChart(canvas, dim, mini) {
        const textColor = css("--text-secondary"), grid = css("--chart-grid");
        const vis = visibleModels();
        const rows = vis.map((m) => ({ m, di: dimInfo(m, dim) })).filter((r) => r.di);
        const front = pareto(rows.map((r) => ({ id: r.m.id, cost: r.m._cost, s: r.di.s })));
        const q = !!P.q;

        const groups = {};
        for (const r of rows) (groups[labGroup(r.m)] = groups[labGroup(r.m)] || []).push(r);
        const order = Object.keys(groups).sort((a, b) => (a === "other") - (b === "other") || groups[b].length - groups[a].length);
        const datasets = order.map((g) => {
            const base = labColor(g), list = groups[g];
            const pts = list.map((r) => ({ x: r.m._cost > 0 ? r.m._cost : FREE_X, y: r.di.s, m: r.m, di: r.di, dim }));
            const hit = list.map((r) => matchesQuery(r.m));
            const fade = list.map((r, i) => (q ? (hit[i] ? 1 : Math.min(0.18, fadeOf(r.m))) : fadeOf(r.m)));
            return {
                label: labName(g), data: pts, order: 2,
                backgroundColor: list.map((r, i) => withAlpha(base, r.di.conf ? fade[i] * 0.9 : fade[i] * 0.25)),
                borderColor: list.map((r, i) => (hit[i] ? css("--text-primary") : withAlpha(base, Math.max(0.25, fade[i])))),
                borderWidth: list.map((r, i) => (hit[i] ? 3 : 1.5)),
                pointRadius: list.map((r, i) => (hit[i] ? 9 : (mini ? 3 : 3.5) + (mini ? 2 : 3) * fade[i])),
                pointHoverRadius: 11, pointHitRadius: 8,
            };
        });

        const maxCost = rows.reduce((mx, r) => Math.max(mx, r.m._cost), 1);
        const xMax = maxCost * 1.8;
        if (P.frontier) {
            const line = rows.filter((r) => front.has(r.m.id)).sort((a, b) => a.m._cost - b.m._cost)
                .map((r) => ({ x: r.m._cost > 0 ? r.m._cost : FREE_X, y: r.di.s }));
            if (line.length) line.push({ x: xMax, y: line[line.length - 1].y });
            datasets.push({ label: "Efficiency frontier", data: line, showLine: true, stepped: "after", borderDash: [6, 4], borderWidth: 1.5,
                borderColor: css("--text-muted"), pointRadius: 0, pointHitRadius: 0, fill: false, order: 1 });
        }
        // models the user runs today (from the alias cards)
        const mine = [];
        for (const name of Object.keys(DATA.config.aliases)) {
            const id = P.current[name] !== undefined ? P.current[name] : DATA.config.current_models[name];
            const m = id && byId[id], di = m && dimInfo(m, dim);
            if (m && di && m._cost != null) mine.push({ x: m._cost > 0 ? m._cost : FREE_X, y: di.s, m, di, dim, alias: name });
        }
        if (mine.length) datasets.push({ label: "Your aliases", data: mine, pointStyle: "rectRot", pointRadius: 10, borderWidth: 2.5,
            backgroundColor: "rgba(0,0,0,0)", borderColor: css("--text-primary"), order: 0 });

        const annotations = {};
        if (P.caps) {
            annotations.free = { type: "box", xMin: X_MIN, xMax: 0.0058, backgroundColor: withAlpha("#34d399", 0.12), borderWidth: 0,
                label: { display: !mini, content: "Free", position: "start", color: css("--text-muted"), font: { size: 10 }, backgroundColor: "transparent", rotation: 0, yAdjust: -2 } };
            const byCap = {};
            for (const name of Object.keys(DATA.config.aliases)) {
                const cap = effectiveCap(name);
                if (cap) (byCap[cap] = byCap[cap] || []).push(name);
            }
            Object.entries(byCap).forEach(([cap, names], i) => {
                annotations["cap" + i] = { type: "line", scaleID: "x", value: Number(cap), borderColor: css("--text-muted"), borderWidth: 1, borderDash: [3, 4],
                    label: { display: !mini, content: names.join(" / ") + " ≤ $" + Number(cap), position: i % 2 ? "end" : "start", rotation: 90,
                        color: css("--text-muted"), backgroundColor: "transparent", font: { size: 10 }, xAdjust: -9 } };
            });
        }

        const fs = mini ? 10 : 11;
        const chart = new Chart(canvas.getContext("2d"), {
            type: "scatter", data: { datasets },
            options: {
                responsive: true, maintainAspectRatio: false, animation: false,
                interaction: { mode: "nearest", intersect: true },
                onClick: (evt, els, ch) => {
                    if (!els.length) return;
                    const raw = ch.data.datasets[els[0].datasetIndex].data[els[0].index];
                    if (raw && raw.m) select(raw.m.id);
                },
                onHover: (evt, els) => { evt.native.target.style.cursor = els.length ? "pointer" : "default"; },
                plugins: {
                    legend: { display: !mini, position: "top", onClick: () => {}, labels: { color: textColor, usePointStyle: true, boxWidth: 8, font: { size: 11 }, padding: 14,
                        generateLabels: (ch) => Chart.defaults.plugins.legend.labels.generateLabels(ch).map((l) => {
                            const g = order[l.datasetIndex];
                            if (g) { l.fillStyle = labColor(g); l.strokeStyle = labColor(g); }
                            else if (ch.data.datasets[l.datasetIndex].label === "Your aliases") { l.fillStyle = "rgba(0,0,0,0)"; l.strokeStyle = textColor; }
                            return l;
                        }) } },
                    title: { display: mini, text: dimLabel(dim), color: textColor, font: { size: 12, weight: "600" } },
                    annotation: { annotations },
                    tooltip: {
                        backgroundColor: css("--bg-card"), titleColor: css("--text-primary"), bodyColor: css("--text-secondary"),
                        borderColor: css("--accent"), borderWidth: 1, padding: 12, displayColors: false, cornerRadius: 8,
                        filter: (item) => item.raw && item.raw.m,
                        callbacks: {
                            title: (items) => { const m = items[0].raw.m; return m.name + (items[0].raw.alias ? `  ★ your ${items[0].raw.alias}` : ""); },
                            label: (item) => tooltipLines(item.raw),
                        },
                    },
                },
                scales: {
                    x: { type: "logarithmic", min: X_MIN, max: xMax,
                        title: { display: true, text: `Lowest available cost, $/M tokens (${BLEND_LABEL[P.blend]})`, color: textColor, font: { size: fs + 1, weight: "600" } },
                        grid: { color: grid },
                        afterBuildTicks: (axis) => { const t = []; for (let e = -2; e <= 3; e++) [1, 3].forEach((k) => { let v = k * Math.pow(10, e); v = Number(v.toPrecision(2)); if (v >= 0.01 && v <= axis.max * 1.01) t.push({ value: v }); }); t.unshift({ value: FREE_X }); axis.ticks = t; },
                        ticks: { color: textColor, font: { size: fs }, callback: (v) => (v === FREE_X ? "Free" : "$" + v) } },
                    y: { min: 0, max: 100, title: { display: true, text: dimLabel(dim) + " score (0–100)", color: textColor, font: { size: fs + 1, weight: "600" } },
                        grid: { color: grid }, ticks: { color: textColor, font: { size: fs } } },
                },
            },
        });
        charts.push(chart);
    }

    function tooltipLines(raw) {
        const m = raw.m, di = raw.di, c = m._cand;
        const lines = [
            `${dimLabel(raw.dim)}: ${num1(di.s)}` + (di.n ? `  (${Math.round(di.cov * 100)}% coverage, ${di.n} metrics${di.conf ? "" : " — thin"})` : (di.conf ? "" : "  (thin coverage)")),
            `$/100 pts: ${usd(usd100(m._cost, di.s))}`,
            `Cost: ${usd(m._cost)}/M  (in ${usd(c.in)} / out ${usd(c.out)})`,
            `via ${c.provider} · ${CHANNEL_NAME[m._ch]}${c.note ? " · " + c.note : ""}`,
            m.released ? `Released ${m.released}${age(m) != null ? ` (${age(m)} days ago)` : ""}` : "",
            "Click for details",
        ];
        return lines.filter(Boolean);
    }

    function renderCharts() {
        destroyCharts();
        const grid = P.grid;
        $("chartBox").hidden = grid; $("chartGrid").hidden = !grid;
        if (!grid) { buildChart($("scatterChart"), P.dim, false); return; }
        const box = $("chartGrid"); box.innerHTML = "";
        for (const cat of Object.keys(DATA.config.categories)) {
            const d = document.createElement("div"); d.className = "mini";
            const cv = document.createElement("canvas"); d.appendChild(cv); box.appendChild(d);
            buildChart(cv, "cat:" + cat, true);
        }
    }

    // ── alias cards ──────────────────────────────────────────────────────────────────
    function pickLine(label, c, name, extra) {
        if (!c) return `<div class="pick"><div class="pick-label">${label}</div><div class="pick-sub">No eligible model with enough benchmark data.</div></div>`;
        const m = byId[c.id];
        return `<div class="pick"><div class="pick-label">${label}</div><div class="pick-main">
            <span class="pick-name" data-model="${esc(m.id)}" title="${esc(m.id)}">${esc(m.name.replace(/^[^:]+: /, ""))}</span>
            <span class="pick-meta">${usdM(c.cost)} · fit ${num1(c.fit)}</span></div>
            ${missing(m, name)}
            ${extra ? `<div class="pick-sub">${extra}</div>` : ""}</div>`;
    }
    function missing(m, name) {
        const w = DATA.config.aliases[name].weights;
        const gone = Object.keys(w).filter((c) => w[c] > 0 && !m.scores[c]).map((c) => DATA.config.categories[c].short.toLowerCase());
        return gone.length ? `<div class="pick-sub">no ${gone.join(" / ")} data: fit is extrapolated from the rest</div>` : "";
    }
    function verdict(st, name) {
        const cur = st.current;
        if (!cur) return `<div class="verdict muted">Enter the model you use for this alias to check it against the field.</div>`;
        const nm = (id) => esc((byId[id] ? byId[id].name : id).replace(/^[^:]+: /, ""));
        if (cur.status === "unknown") return `<div class="verdict bad">"${esc(cur.id)}" is not a model I can see right now.</div>`;
        if (cur.status === "no_data") return `<div class="verdict warn">${nm(cur.id)} has too little benchmark data to score for this alias.</div>`;
        const betterTxt = (cur.better || []).map((c) => `${nm(c.id)} (+${(c.fit - cur.fit).toFixed(1)} fit, ${usd(c.cost)})`).join("; ");
        if (cur.status === "ineligible") {
            const a = st.a, why = a.max_cost != null && cur.cost > a.max_cost ? `costs ${usd(cur.cost)}, over the ${usd(a.max_cost)} cap` : "does not meet this alias's requirements";
            return `<div class="verdict warn">${nm(cur.id)} ${why}.${betterTxt ? " Better at ≤ its cost: " + betterTxt : ""}</div>`;
        }
        const rank = `#${cur.rank} of ${st.n} · fit ${num1(cur.fit)} · ${usd(cur.cost)}`;
        if (betterTxt) return `<div class="verdict warn">${nm(cur.id)}: ${rank}. Better at same-or-lower cost: ${betterTxt}.</div>`;
        return `<div class="verdict ok">${nm(cur.id)}: ${rank}. Nothing is both cheaper-or-equal and better, so it is on the efficient frontier.</div>`;
    }
    function renderAliases() {
        $("tolPct").textContent = Math.round(DATA.config.alias_value_tolerance * 100);
        const html = Object.keys(DATA.config.aliases).map((name) => {
            const st = aliasState(name), a = DATA.config.aliases[name], cap = effectiveCap(name);
            const overridden = P.capOverride[name] !== undefined;
            const runners = st.ranked.filter((c) => c.id !== (st.top && st.top.id) && c.id !== (st.value && st.value.id)).slice(0, 3);
            const sameTop = st.top && st.value && st.top.id === st.value.id;
            const curVal = P.current[name] !== undefined ? P.current[name] : (DATA.config.current_models[name] || "");
            return `<div class="alias-card ${P.dim === "alias:" + name ? "viewing" : ""}" data-alias="${esc(name)}">
                <div class="alias-top"><span class="alias-name">${esc(name)}</span>
                    <span class="cap">${a.max_cost === 0 && !overridden ? "free only" : `≤ $<input type="number" min="0" step="0.05" data-cap="${esc(name)}" value="${cap == null ? "" : cap}" placeholder="none" title="Blended $/M cap. Clear for no cap.">`}${overridden ? ` <button class="chip" data-capreset="${esc(name)}" title="Back to config.json value">↺</button>` : ""}</span></div>
                <div class="alias-note">${esc(a.note || "")}</div>
                ${pickLine("Top fit", st.top, name, `${st.n} ranked${st.unscored ? ` · ${st.unscored} more fit the constraints but have no benchmark data` : ""}`)}
                ${sameTop ? `<div class="pick-sub">Also the best value: nothing cheaper is within ${Math.round(DATA.config.alias_value_tolerance * 100)}% of it.</div>` : pickLine("Best value", st.value, name, st.top && st.value ? `${(100 - 100 * st.value.cost / st.top.cost).toFixed(0)}% cheaper than top fit, ${(st.top.fit - st.value.fit).toFixed(1)} fit lower` : "")}
                ${runners.length ? `<div class="runners">Next: ${runners.map((c) => `<span data-model="${esc(c.id)}">${esc(byId[c.id].name.replace(/^[^:]+: /, ""))} (${usd(c.cost)}, ${num1(c.fit)})</span>`).join(" · ")}</div>` : ""}
                <div class="validate"><div class="pick-label">Validate your current model</div>
                    <input type="text" list="modelList" data-current="${esc(name)}" value="${esc(curVal)}" placeholder="e.g. z-ai/glm-5.3" autocomplete="off">
                    ${verdict(st, name)}</div>
                <div class="card-actions"><button class="btn btn-sm" data-view="${esc(name)}">Show on matrix</button></div>
            </div>`;
        }).join("");
        $("aliasGrid").innerHTML = html;
    }

    // ── controls ─────────────────────────────────────────────────────────────────────
    function renderControls() {
        const tabs = Object.entries(DATA.config.categories).map(([k, c]) => [("cat:" + k), c.label]);
        const curAlias = P.dim.startsWith("alias:") ? [[P.dim, dimLabel(P.dim)]] : [];
        $("dimTabs").innerHTML = [...tabs, ...curAlias].map(([k, l]) => `<button class="tab ${P.dim === k ? "active" : ""}" data-dim="${esc(k)}">${esc(l)}</button>`).join("");
        const groups = [...topLabs, "other"];
        $("labChips").innerHTML = groups.map((g) => `<button class="chip ${P.labsOff.includes(g) ? "" : "active"}" data-lab="${esc(g)}"><i style="background:${labColor(g)}"></i>${esc(labName(g))}</button>`).join("");
        $("blendSelect").value = P.blend; $("fadeSelect").value = String(P.fade); $("hideSelect").value = String(P.hide);
        $("searchBox").value = P.q; $("minScore").value = P.minScore;
        for (const id of ["Frontier", "Caps", "Grid"]) $("opt" + id).checked = P[id.toLowerCase()];
        for (const id of ["Efficient", "Free", "Tools", "Confident"]) $("f" + id).checked = P["f" + id];
        $("modelList").innerHTML = DATA.models.map((m) => `<option value="${esc(m.id)}">${esc(m.name)}</option>`).join("");
    }

    // ── leaderboard table ────────────────────────────────────────────────────────────
    const COLS = [
        { key: "rank", label: "#", sortable: false },
        { key: "name", label: "Model" },
        { key: "age", label: "Released", num: false },
        { key: "cost", label: "Lowest $/M", num: true },
        { key: "score", label: "Score", num: true },
        { key: "usd", label: "$ / 100 pts", num: true },
    ];
    const catCols = () => Object.entries(DATA.config.categories).map(([c, v]) => ({ key: "cat:" + c, label: v.short, num: true }));
    function tableRows() {
        const vis = visibleModels();
        const rows = vis.map((m) => ({ m, di: dimInfo(m) })).filter((r) => r.di);
        const front = pareto(rows.map((r) => ({ id: r.m.id, cost: r.m._cost, s: r.di.s })));
        for (const r of rows) { r.front = front.has(r.m.id); r.usd = usd100(r.m._cost, r.di.s); }
        let out = rows.filter((r) => r.di.s >= P.minScore && (!P.fEfficient || r.front) && (!P.fFree || r.m._cost === 0) && (!P.fTools || r.m.tools) && (!P.fConfident || r.di.conf));
        if (P.q) out = out.filter((r) => matchesQuery(r.m));
        const { key, dir } = P.sort;
        const val = (r) => key === "name" ? r.m.name.toLowerCase() : key === "age" ? (r.m.age_days ?? 1e9) : key === "cost" ? r.m._cost : key === "score" ? r.di.s
            : key === "usd" ? r.usd : key.startsWith("cat:") ? (r.m.scores[key.slice(4)] ? r.m.scores[key.slice(4)].s : null) : 0;
        out.sort((a, b) => {
            const x = val(a), y = val(b);
            if (x == null && y == null) return 0; if (x == null) return 1; if (y == null) return -1;
            const c = typeof x === "string" ? x.localeCompare(y) : x - y;
            return (c || b.di.s - a.di.s) * dir;
        });
        return out;
    }
    function renderTable() {
        $("tableHead").innerHTML = [...COLS, ...catCols()].map((c) => `<th class="${c.num ? "num" : ""} ${P.sort.key === c.key ? "sorted" : ""}" data-sort="${c.sortable === false ? "" : c.key}">${esc(c.key === "score" ? dimShort() + " score" : c.label)}${P.sort.key === c.key ? (P.sort.dir > 0 ? " ▲" : " ▼") : ""}</th>`).join("");
        const rows = tableRows();
        const shown = showAllRows ? rows : rows.slice(0, 100);
        $("dataTableBody").innerHTML = shown.map((r, i) => {
            const m = r.m, g = labGroup(m), c = m._cand;
            const cell = (cat) => { const x = m.scores[cat]; return x ? `<td class="num" title="${Math.round(x.cov * 100)}% coverage">${num1(x.s)}</td>` : `<td class="num muted">—</td>`; };
            return `<tr data-model="${esc(m.id)}" class="${selectedId === m.id ? "sel" : ""}" style="opacity:${Math.max(0.55, fadeOf(m)).toFixed(2)}">
                <td class="muted">${i + 1}</td>
                <td><span class="mname"><i style="background:${labColor(g)}"></i>${esc(m.name.replace(/^[^:]+: /, ""))}</span>${m._cost === 0 ? '<span class="tag free">free</span>' : ""}${r.front ? '<span class="tag front">efficient</span>' : ""}${r.di.conf ? "" : '<span class="tag fam" title="Thin benchmark coverage">thin</span>'}<span class="ch">${esc(m.vendor)}</span></td>
                <td class="muted">${esc(m.released || "—")}</td>
                <td class="num">${usd(m._cost)}<span class="ch">${esc(CHANNEL_NAME[m._ch])}</span></td>
                <td class="num"><span class="scorebar"><b style="width:${r.di.s}%"></b></span>${num1(r.di.s)}</td>
                <td class="num"><b>${usd(r.usd)}</b></td>
                ${["coding", "agentic", "reasoning", "chat"].map(cell).join("")}</tr>`;
        }).join("") || `<tr><td colspan="${COLS.length + 4}" class="muted" style="padding:24px;text-align:center">No models match these filters.</td></tr>`;
        $("rowCount").textContent = `${shown.length} of ${rows.length} models`;
        $("showAll").hidden = showAllRows || rows.length <= 100;
    }

    // ── detail panel ─────────────────────────────────────────────────────────────────
    function select(id) { selectedId = id; renderDetail(); renderTable(); $("detailPanel").scrollIntoView({ behavior: "smooth", block: "nearest" }); }
    function renderDetail() {
        const el = $("detailPanel"), m = byId[selectedId];
        if (!m) { el.hidden = true; return; }
        const costRows = CHANNELS.flatMap((ch) => (m.cost.channels[ch] || []).map((c) => ({ ch, c, v: (c.in * blendObj().input + c.out * blendObj().output) / (blendObj().input + blendObj().output) })));
        const best = Math.min(...costRows.map((r) => r.v));
        const cats = DATA.config.categories;
        const metricRows = Object.entries(m.metrics).map(([k, d]) => ({ k, d, meta: DATA.metrics[k] })).filter((r) => r.meta).sort((a, b) => (b.d.s ?? -1) - (a.d.s ?? -1));
        el.innerHTML = `<button class="btn btn-sm close" id="closeDetail">Close</button>
            <h3>${esc(m.name)}${m._cost === 0 ? '<span class="tag free">free</span>' : ""}${m.tools ? '<span class="tag">tools</span>' : ""}${m.reasoning ? '<span class="tag">reasoning</span>' : ""}</h3>
            <div class="sub">${esc(m.id)} · released ${esc(m.released || "?")}${age(m) != null ? ` (${age(m)} days ago)` : ""} · context ${m.ctx ? Math.round(m.ctx / 1000) + "k" : "?"}</div>
            <div class="detail-cols">
              <div><h4>Cost by channel ($/M tokens)</h4><table class="plain"><tr><th>Channel</th><th>Provider</th><th class="num">In</th><th class="num">Out</th><th class="num">Blend</th></tr>
                ${costRows.map((r) => `<tr class="${Math.abs(r.v - best) < 1e-12 ? "best" : ""}"><td>${CHANNEL_NAME[r.ch]}</td><td>${esc(r.c.provider)}${r.c.note ? ` <span class="muted">· ${esc(r.c.note)}</span>` : ""}</td><td class="num">${usd(r.c.in)}</td><td class="num">${usd(r.c.out)}</td><td class="num">${usd(r.v)}</td></tr>`).join("")}</table></div>
              <div><h4>Category scores</h4><table class="plain"><tr><th>Category</th><th class="num">Score</th><th class="num">Coverage</th><th class="num">$/100 pts</th></tr>
                ${Object.entries(cats).map(([k, c]) => { const x = m.scores[k]; return `<tr><td>${esc(c.label)}</td>${x ? `<td class="num">${num1(x.s)}</td><td class="num">${Math.round(x.cov * 100)}% · ${x.n}</td><td class="num">${usd(usd100(m._cost, x.s))}</td>` : '<td class="num muted">—</td><td class="num muted">too little data</td><td></td>'}</tr>`; }).join("")}</table>
                <h4 style="margin-top:12px">Alias fit</h4><div class="chip-row">${Object.keys(DATA.config.aliases).map((a) => `<span class="chip active" style="cursor:default">${esc(a)} ${m.fit[a] == null ? "—" : num1(m.fit[a])}</span>`).join("")}</div></div>
              <div><h4>Benchmark metrics</h4><table class="plain"><tr><th>Metric</th><th class="num">Value</th><th class="num">0–100</th></tr>
                ${metricRows.length ? metricRows.map((r) => `<tr><td title="${esc(r.meta.desc)}">${esc(r.meta.label)}${r.d.how === "family" ? ' <span class="tag fam" title="Matched by model family, not the exact snapshot">family</span>' : ""}<div class="muted" style="font-size:.7rem">${esc(r.d.label)}</div></td><td class="num">${esc(r.d.v >= 1000 ? Math.round(r.d.v) : r.d.v.toFixed(1))}</td><td class="num">${r.d.s == null ? "—" : num1(r.d.s)}</td></tr>`).join("") : '<tr><td colspan="3" class="muted">No benchmark data matched this model.</td></tr>'}</table></div>
            </div>`;
        el.hidden = false;
        $("closeDetail").onclick = () => { selectedId = null; el.hidden = true; renderTable(); };
    }

    // ── methodology / sources ────────────────────────────────────────────────────────
    function renderMethod() {
        const cfg = DATA.config;
        const cards = Object.entries(cfg.categories).map(([k, c]) => {
            const items = Object.entries(c.metrics).filter(([m]) => DATA.metrics[m] && DATA.metrics[m].scale).sort((a, b) => b[1] - a[1])
                .map(([m, w]) => `<li title="${esc(DATA.metrics[m].desc)}"><span>${esc(DATA.metrics[m].label)} <span class="muted">×${w}</span></span><span>${DATA.metrics[m].models} models</span></li>`).join("");
            return `<div class="card"><h3>${esc(c.label)}</h3><ul>${items}</ul></div>`;
        }).join("");
        $("methodology").innerHTML = cards + `<div class="card"><h3>Scoring</h3>
            <p>Each metric is rescaled to 0–100 between its 5th and 99th percentile across currently purchasable models, so Elo, % and index scales can be averaged.</p>
            <p>A category score is the weighted mean of the metrics a model has (weights ×n, editable in <code>config.json</code>), shown only if they carry ≥35% of the category's weight. Under 60% is flagged <i>thin</i>.</p>
            <p>Lowest cost = cheapest of primary-provider list price, OpenRouter's cheapest healthy endpoint, and Nous Portal. Batch, priority/flex tiers and third-party 4-bit endpoints are excluded.</p>
            <p>Reasoning models spend extra output tokens, so per-token cost understates their real cost per task.</p></div>`;
    }
    function renderSources(st) {
        const live = (st && st.sources) || {};
        const now = st ? st.now : Date.now() / 1000;
        $("sourcesTable").innerHTML = `<tr><th>Source</th><th>Status</th><th>Fetched</th><th class="num">Every</th><th class="num">Records</th></tr>` +
            Object.entries(DATA.sources).map(([n, s0]) => { const s = live[n] || s0; const state = sourceState(n, s, s0.interval_minutes);
                return `<tr><td><a href="${esc(s0.url)}" target="_blank" rel="noopener">${esc(s0.label)}</a></td>
                <td><i class="dot ${state === "none" ? "" : state}"></i> ${esc(state)}${s.error ? `<div class="err-text" style="font-size:.74rem">${esc(s.error)}</div>` : ""}</td>
                <td>${esc(ago(s.fetched_at ? now - s.fetched_at : null))}</td><td class="num">${s0.interval_minutes >= 120 ? s0.interval_minutes / 60 + " h" : s0.interval_minutes + " min"}</td><td class="num">${s.records ?? "—"}</td></tr>`; }).join("") +
            `<tr><th colspan="5">Benchmarks in use</th></tr>` +
            Object.entries(DATA.metrics).map(([k, m]) => `<tr><td><a href="${esc(m.url)}" target="_blank" rel="noopener">${esc(m.label)}</a></td><td colspan="2">${esc(m.source_label)} · as of ${esc(m.as_of || "?")}</td><td class="num">${m.scale ? "" : "dropped (too few models)"}</td><td class="num">${m.models}</td></tr>`).join("");
        const un = DATA.diagnostics.unmatched;
        $("diagnostics").innerHTML = `<summary>Strong benchmark entries with no purchasable match (${un.length}): add a mapping under <code>name_overrides</code> in config.json if one is the same model</summary>
            <table class="plain"><tr><th>Metric</th><th>Name in source</th><th class="num">Value</th><th class="num">0–100</th></tr>${un.map((u) => `<tr><td>${esc(DATA.metrics[u.metric].label)}</td><td>${esc(u.name)}</td><td class="num">${u.value}</td><td class="num">${u.norm}</td></tr>`).join("")}</table>`;
    }

    // ── CSV ──────────────────────────────────────────────────────────────────────────
    function downloadCSV() {
        const cats = Object.keys(DATA.config.categories), aliases = Object.keys(DATA.config.aliases);
        const head = ["id", "name", "released", "cost_blended_usd_per_m", "cost_channel", "cost_provider", "input_usd_per_m", "output_usd_per_m",
            ...cats.flatMap((c) => [c + "_score", c + "_usd_per_100pts"]), ...aliases.map((a) => "fit_" + a)];
        const rows = DATA.models.filter((m) => m._cost != null).map((m) => [m.id, m.name, m.released || "", m._cost, m._ch, m._cand.provider, m._cand.in, m._cand.out,
            ...cats.flatMap((c) => [m.scores[c] ? m.scores[c].s : "", m.scores[c] ? usd100(m._cost, m.scores[c].s).toFixed(4) : ""]), ...aliases.map((a) => (m.fit[a] ?? ""))]);
        const csv = [head, ...rows].map((r) => r.map((c) => `"${String(c).replace(/"/g, '""')}"`).join(",")).join("\n");
        const a = document.createElement("a");
        a.href = URL.createObjectURL(new Blob([csv], { type: "text/csv" })); a.download = "llm_leaderboard.csv"; a.click();
        URL.revokeObjectURL(a.href);
    }

    // ── render + events ──────────────────────────────────────────────────────────────
    function renderAll() {
        if (!DATA.models.some((m) => Object.keys(m.scores).length)) {
            toast("No benchmark data yet — check the Sources section");
        }
        renderControls(); renderAliases(); renderCharts(); renderTable(); renderDetail(); renderMethod(); renderSources(null);
        updateFooter();
    }
    function rerenderViews() { renderControls(); renderAliases(); renderCharts(); renderTable(); renderDetail(); }
    function updateFooter() { $("footerText").innerHTML = `Runs locally · data saved to <code>data/latest.json</code> · ${DATA.models.length} models · built ${esc(DATA.generated_at)} · benchmarks: Artificial Analysis (via OpenRouter), Epoch AI (CC-BY 4.0), Arena (CC-BY 4.0)`; }

    function bind() {
        document.addEventListener("click", (e) => {
            const t = e.target.closest("[data-dim],[data-lab],[data-model],[data-view],[data-sort],[data-capreset]");
            if (!t) return;
            if (t.dataset.dim) { P.dim = t.dataset.dim; savePrefs(); rerenderViews(); }
            else if (t.dataset.lab) { const g = t.dataset.lab, i = P.labsOff.indexOf(g); if (i >= 0) P.labsOff.splice(i, 1); else P.labsOff.push(g); savePrefs(); renderControls(); renderCharts(); renderTable(); }
            else if (t.dataset.view) { P.dim = "alias:" + t.dataset.view; P.grid = false; savePrefs(); rerenderViews(); $("chartBox").scrollIntoView({ behavior: "smooth", block: "start" }); }
            else if (t.dataset.capreset) { delete P.capOverride[t.dataset.capreset]; savePrefs(); renderAliases(); renderCharts(); }
            else if (t.dataset.sort !== undefined && t.closest("#tableHead")) {
                const k = t.dataset.sort; if (!k) return;
                P.sort = P.sort.key === k ? { key: k, dir: -P.sort.dir } : { key: k, dir: k === "score" || k.startsWith("cat:") ? -1 : 1 };
                savePrefs(); renderTable();
            } else if (t.dataset.model) select(t.dataset.model);
        });
        $("aliasGrid").addEventListener("change", (e) => {
            const t = e.target;
            if (t.dataset.cap) { const v = t.value.trim(); P.capOverride[t.dataset.cap] = v === "" ? "none" : Math.max(0, Number(v)); savePrefs(); renderAliases(); renderCharts(); }
            else if (t.dataset.current !== undefined) {
                const v = t.value.trim(), name = t.dataset.current;
                const hit = byId[v] ? v : (DATA.models.find((m) => m.name.toLowerCase() === v.toLowerCase()) || {}).id;
                P.current[name] = hit || v; savePrefs(); renderAliases(); renderCharts();
            }
        });
        const on = (id, ev, fn) => $(id).addEventListener(ev, fn);
        on("blendSelect", "change", (e) => { P.blend = e.target.value; savePrefs(); prepare(); rerenderViews(); });
        on("fadeSelect", "change", (e) => { P.fade = Number(e.target.value); savePrefs(); renderCharts(); renderTable(); });
        on("hideSelect", "change", (e) => { P.hide = Number(e.target.value); savePrefs(); renderCharts(); renderTable(); });
        on("searchBox", "input", (e) => { P.q = e.target.value.trim(); savePrefs(); renderCharts(); renderTable(); });
        on("minScore", "input", (e) => { P.minScore = clamp(Number(e.target.value) || 0, 0, 100); savePrefs(); renderTable(); });
        for (const [id, key, charts_] of [["optFrontier", "frontier", true], ["optCaps", "caps", true], ["optGrid", "grid", true],
            ["fEfficient", "fEfficient"], ["fFree", "fFree"], ["fTools", "fTools"], ["fConfident", "fConfident"]]) {
            on(id, "change", (e) => { P[key] = e.target.checked; savePrefs(); if (charts_) renderCharts(); renderTable(); });
        }
        on("showAll", "click", () => { showAllRows = true; renderTable(); });
        on("downloadCsv", "click", downloadCSV);
        on("refreshBtn", "click", async () => {
            $("refreshBtn").disabled = true;
            try { await getJSON("/api/refresh", { method: "POST", headers: { "X-Requested-With": "leaderboard" } }); toast("Refresh started"); schedulePoll(500); }
            catch (e) { toast("Could not start refresh"); $("refreshBtn").disabled = false; }
        });
        on("themeToggle", "click", () => { P.theme = P.theme === "dark" ? "light" : "dark"; applyTheme(); savePrefs(); if (DATA) renderCharts(); });
    }
    function applyTheme() {
        document.documentElement.setAttribute("data-theme", P.theme);
        $("themeIcon").textContent = P.theme === "dark" ? "☀️" : "🌙";
    }

    function init() {
        const ann = window.chartjsPluginAnnotation || window["chartjs-plugin-annotation"];
        if (ann) Chart.register(ann);
        Chart.defaults.font.family = getComputedStyle(document.body).fontFamily;
        applyTheme(); bind(); poll();
    }
    document.addEventListener("DOMContentLoaded", init);
})();
