// Modelfit app.
// Market data comes from /api/snapshot (the global build); the workspace (aliases, weights, caps, current models)
// from /api/workspace. Scores are computed server-side; cost basis, efficiency frontier, alias fits and picks are
// recomputed here with the same arithmetic so every control feels instant. Saves go to the workspace API.
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
    const PREF_KEY = "modelfit-prefs-v1";
    const VIEWS = ["picks", "matrix", "leaderboard", "methodology", "settings"];
    const icon = (n) => `<svg class="i"><use href="/assets/icons.svg#${n}"/></svg>`;

    const $ = (id) => document.getElementById(id);
    const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
    const clamp = (x, lo, hi) => Math.max(lo, Math.min(hi, x));

    // ── state ────────────────────────────────────────────────────────────────────────
    let DATA = null;
    let ME = { authenticated: false, mode: "selfhost" };  // /api/me
    let WS = null;                                        // /api/workspace
    let PRESETS = {};                                     // the snapshot's preset aliases (the build's own fits use these)
    let byId = {};
    let charts = [];
    let lastMtime = null;
    let selectedId = null;
    let showAllRows = false;
    let topLabs = [];

    const DEFAULTS = {
        dim: "cat:coding", blend: "3:1", fade: 90, hide: 0, q: "", labsOff: [], frontier: true, caps: true, grid: false, mineOnly: false,
        minScore: 50, fEfficient: false, fFree: false, fTools: false, fConfident: false,
        sort: { key: "usd", dir: 1 }, capOverride: {}, current: {}, weights: {}, wOpen: {}, wCatOpen: {},
    };
    const P = loadPrefs();
    // View preferences live in this browser; weights, caps and current models live in the workspace (server).
    function loadPrefs() {
        let saved = {};
        try { saved = JSON.parse(localStorage.getItem(PREF_KEY) || "{}"); } catch (e) { /* ignore */ }
        return { ...DEFAULTS, ...saved, weights: {}, capOverride: {}, current: {} };
    }
    function savePrefs() {
        try { const { weights, capOverride, current, ...rest } = P; localStorage.setItem(PREF_KEY, JSON.stringify(rest)); } catch (e) { /* private mode */ }
    }
    // ── plan helpers ────────────────────────────────────────────────────────────────
    const ent = () => (ME.workspace && ME.workspace.entitlements) || { features: {}, limits: {} };
    const can = (f) => !!ent().features[f];
    const isGuest = () => !ME.authenticated;
    const cloud = () => ME.mode === "cloud";

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
    async function api(method, url, body) {
        const r = await fetch(url, { method, credentials: "same-origin", headers: { "Content-Type": "application/json", "X-Requested-With": "modelfit" }, body: body === undefined ? undefined : JSON.stringify(body) });
        const out = await r.json().catch(() => ({}));
        if (!r.ok) throw Object.assign(new Error(out.error || r.statusText), { status: r.status, body: out });
        return out;
    }
    // A 402 from the API (or a locked control) opens the upgrade dialog; anything else is a toast.
    function apiFail(e) {
        if (e.status === 402) return openUpgrade(e.body && e.body.feature, e.message);
        if (e.status === 401 && cloud()) return openSignup();
        toast(e.message || "Something went wrong");
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
        const f = fitOf(m, key), share = fitCovOf(m, key);
        return f == null ? null : { s: f, conf: (share || 0) >= 0.8, cov: share, n: null };
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

    // ── alias weights and fit (mirrors modelfit/picks.py + scoring.py) ────────────
    // Effective weights for an alias: its definition's defaults, overlaid (unless `useEdits` is false) with
    // this browser's slider edits. cats: {category: weight}; metrics: {category: {benchmark: weight}},
    // limited to benchmarks that have a usable scale. A weight of 0 switches that item off.
    function aliasWeights(name, useEdits = true) {
        const a = DATA.config.aliases[name], o = (useEdits && P.weights[name]) || {}, cats = {}, metrics = {};
        // iterate categories in the alias's own order (as the Python build does) so float sums match exactly
        const order = [...Object.keys(a.weights), ...Object.keys(DATA.config.categories).filter((c) => !(c in a.weights))];
        for (const [c, cdef] of order.map((c) => [c, DATA.config.categories[c]])) {
            cats[c] = o.cats && o.cats[c] !== undefined ? o.cats[c] : (a.weights[c] || 0);
            const cfgOver = (a.metric_weights && a.metric_weights[c]) || {};
            metrics[c] = {};
            for (const k of new Set([...Object.keys(cdef.metrics), ...Object.keys(cfgOver)])) {
                if (!(DATA.metrics[k] && DATA.metrics[k].scale)) continue;
                const ov = o.metrics && o.metrics[c] && o.metrics[c][k];
                metrics[c][k] = ov !== undefined ? ov : (cfgOver[k] !== undefined ? cfgOver[k] : cdef.metrics[k]);
            }
        }
        return { cats, metrics };
    }
    // Python-style round(x, d): correctly rounded on the stored binary value, exact ties to even.
    // (Math.round(x*10)/10 and toFixed both break ties differently and can disagree with the Python build.)
    function pyRound(x, d) {
        const s = x.toFixed(Math.min(d + 25, 100)), cut = s.indexOf(".") + 1 + d;
        if (/^50*$/.test(s.slice(cut))) {
            const trunc = s.slice(0, cut), last = Number(trunc[trunc.length - 1]);
            return last % 2 === 0 ? Number(trunc) : Number((Number(trunc) + Math.pow(10, -d)).toFixed(d));
        }
        return Number(x.toFixed(d));
    }
    const round1 = (x) => pyRound(x, 1), round2 = (x) => pyRound(x, 2);
    // Exactly-rounded sum, the same algorithm as Python's math.fsum (Shewchuk partials, half-even finish), so a fit
    // computed here equals the one the build computes to the last bit. Plain addition can differ at decimal ties.
    function fsum(xs) {
        const p = [];
        for (let x of xs) {
            let i = 0;
            for (let y of p) {
                if (Math.abs(x) < Math.abs(y)) { const t = x; x = y; y = t; }
                const hi = x + y, lo = y - (hi - x);
                if (lo) p[i++] = lo;
                x = hi;
            }
            p.length = i; p.push(x);
        }
        let n = p.length, hi = 0, lo = 0;
        if (n > 0) {
            hi = p[--n];
            while (n > 0) {
                const x = hi, y = p[--n];
                hi = x + y; lo = y - (hi - x);
                if (lo !== 0) break;
            }
            if (n > 0 && ((lo < 0 && p[n - 1] < 0) || (lo > 0 && p[n - 1] > 0))) {
                const y = lo * 2, x = hi + y;
                if (y === x - hi) hi = x;
            }
        }
        return hi;
    }
    const fitCaches = {};
    const invalidateFit = (name) => { if (name) delete fitCaches[name]; else Object.keys(fitCaches).forEach((k) => delete fitCaches[k]); };
    // The snapshot's own fits are exact when this alias is an untouched preset (same definition, no metric weights,
    // no weight edits); anything else is recomputed here with the build's exact arithmetic.
    function usesBackendFit(name) {
        const a = DATA.config.aliases[name], pre = PRESETS[name];
        if (P.weights[name] || !pre || (a.metric_weights && Object.keys(a.metric_weights).length)) return false;
        const { preset, ...def } = a;
        return stable(def) === stable(pre) && DATA.models.length && DATA.models[0].fit && name in DATA.models[0].fit;
    }
    // Category scores as this alias sees them (same rules as scoring.composite).
    function computeCats(m, w) {
        const sc = DATA.config.scoring, out = {};
        for (const [c, mw] of Object.entries(w.metrics)) {
            const entries = Object.entries(mw).filter(([, x]) => x > 0), total = fsum(entries.map(([, x]) => x));
            const have = entries.filter(([k]) => m.metrics[k] && m.metrics[k].s != null);
            if (!have.length || total <= 0) continue;
            const haveW = fsum(have.map(([, x]) => x)), cov = haveW / total;
            if (cov < sc.min_coverage) continue;
            out[c] = { s: round1(fsum(have.map(([k, x]) => x * m.metrics[k].s)) / haveW), cov: round2(cov), n: have.length, confident: cov >= sc.confident_coverage };
        }
        return out;
    }
    function computeFit(m, name) {
        const w = aliasWeights(name), cats = computeCats(m, w), a = DATA.config.aliases[name];
        const used = Object.entries(w.cats).filter(([, x]) => x > 0), total = fsum(used.map(([, x]) => x));
        const have = used.filter(([c]) => cats[c]);
        const share = total > 0 ? fsum(have.map(([, x]) => x)) / total : 0;
        let fit = null;
        if (have.length && share >= (a.min_share ?? 0.6)) fit = round1(fsum(have.map(([c, x]) => x * cats[c].s)) / fsum(have.map(([, x]) => x)));
        return { fit, share: round2(share), cats };
    }
    // {fit, share, cats} for a model under an alias's effective weights.
    function fitInfo(m, name) {
        if (usesBackendFit(name)) return { fit: m.fit[name], share: m.fit_cov[name], cats: m.scores };
        const cache = fitCaches[name] || (fitCaches[name] = {});
        return cache[m.id] || (cache[m.id] = computeFit(m, name));
    }
    const fitOf = (m, name) => fitInfo(m, name).fit;
    const fitCovOf = (m, name) => fitInfo(m, name).share;

    // ── alias picks (mirrors modelfit/scoring.py) ─────────────────────────────────
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
        const all = [], ok = [];
        let unscored = 0;
        for (const m of DATA.models) {
            const f = fitOf(m, name);
            if (m._cost != null && f == null && eligible(m, a)) unscored++;
            if (m._cost == null || f == null) continue;
            const c = { id: m.id, cost: m._cost, fit: f };
            all.push(c);
            if (eligible(m, a)) ok.push(c);
        }
        const ranked = [...ok].sort((x, y) => y.fit - x.fit || x.cost - y.cost);
        const out = { a, n: ok.length, unscored, ranked, top: ranked[0] || null, current: null };
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
            dots.push(`<span title="${esc(s.error || "")}"><i class="dot ${state === "none" ? "" : state}"></i>${esc(meta.label.replace(/ \(.*\)/, ""))}<em>${esc(ago(s.fetched_at ? st.now - s.fetched_at : null))}</em></span>`);
        }
        $("sourceDots").innerHTML = dots.join("");
        if (st.running) { pill.className = "dot busy"; text.textContent = "Refreshing… " + (st.step || ""); }
        else if (st.generated_ts) {
            const stale = Object.entries(sources).some(([n, m]) => ["err", "stale"].includes(sourceState(n, live[n] || m, m.interval_minutes)));
            pill.className = "dot " + (stale ? "stale" : "ok");
            text.textContent = "Live · " + ago(st.now - st.generated_ts);
        } else { pill.className = "dot"; text.textContent = st.scheduler ? "Waiting for first data…" : "No data (refresh disabled)"; }
        $("refreshBtn").hidden = !st.can_refresh;
        $("refreshBtn").disabled = !!st.running;
    }
    async function poll() {
        let st;
        try { st = await getJSON("/api/status"); } catch (e) { $("statusText").textContent = "Server unreachable"; $("statusDot").className = "dot err"; return schedulePoll(10000); }
        if (lastMtime !== null && st.generated_ts && st.generated_ts !== lastMtime) {
            // never swap the page out from under a slider the user is dragging; try again shortly
            if (pointerDown || Date.now() - lastSliderInput < 2000) return schedulePoll(1000);
            // a rebuild we caused ourselves (saving weights) is not news worth a toast
            await loadData(Date.now() - lastOwnSave > 60000);
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
        let snap;
        try {
            snap = await getJSON("/api/snapshot");
        } catch (e) { if (e.status !== 404) console.error(e); return; }
        DATA = snap;
        PRESETS = DATA.config.aliases;
        if (!weightsReady) await initWeights(); else applyWorkspace(WS);
        prepare();
        $("emptyState").hidden = true; $("mainContent").hidden = false;
        renderAll();
        if (announce) toast("Market data updated");
    }
    // The workspace's alias definitions replace the snapshot's presets in everything the page computes.
    function applyWorkspace(ws) {
        if (!ws || !DATA) return;
        DATA.config.aliases = ws.alias_defs;
        DATA.config.current_models = ws.current_models || {};
        if (P.dim.startsWith("alias:") && !ws.alias_defs[P.dim.slice(6)]) P.dim = "cat:coding";  // that alias is gone
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

    // The model each alias runs today (set per alias in the workspace).
    const currentModelFor = (name) => (P.current[name] !== undefined ? P.current[name] : DATA.config.current_models[name]) || null;
    function currentSelection() {
        return Object.keys(DATA.config.aliases).map((alias) => { const id = currentModelFor(alias); return { alias, id, m: id ? byId[id] : null }; });
    }
    function renderMineNote() {
        const el = $("mineNote");
        if (!el) return;
        if (!P.mineOnly) { el.hidden = true; return; }
        const notSet = [], missing = [], noData = [];
        for (const { alias, id, m } of currentSelection()) {
            if (!id) notSet.push(alias);
            else if (!m) missing.push(`${alias} → ${id}`);
            else if (!dimInfo(m)) noData.push(`${alias} → ${m.name.replace(/^[^:]+: /, "")}`);
        }
        const parts = ["Showing only the models your aliases use today (set under <i>Tune → Model you use today</i> on each alias card)."];
        if (notSet.length) parts.push(`<b>Not set:</b> ${notSet.map(esc).join(", ")}.`);
        if (missing.length) parts.push(`<b>Not in the catalogue:</b> ${missing.map(esc).join("; ")}.`);
        if (noData.length) parts.push(`<b>No ${esc(dimLabel())} score:</b> ${noData.map(esc).join("; ")}.`);
        el.innerHTML = parts.join(" ");
        el.hidden = false;
    }
    // Draws each alias's name beside its point (only for datasets flagged aliasLabels).
    const aliasLabelPlugin = {
        id: "aliasLabels",
        afterDatasetsDraw(chart) {
            const ctx = chart.ctx, area = chart.chartArea, placed = [], dots = [];
            chart.data.datasets.forEach((ds, i) => { if (ds.aliasLabels) chart.getDatasetMeta(i).data.forEach((el) => dots.push(el)); });
            // a label may not overlap another label or any dot; try right, left, above, below
            const overlaps = (a, b) => a.x < b.x + b.w && a.x + a.w > b.x && a.y < b.y + b.h && a.y + a.h > b.y;
            const blocked = (r) => placed.some((p) => overlaps(r, p)) || dots.some((d) => overlaps(r, { x: d.x - 12, y: d.y - 12, w: 24, h: 24 }));
            chart.data.datasets.forEach((ds, i) => {
                if (!ds.aliasLabels) return;
                const meta = chart.getDatasetMeta(i);
                if (meta.hidden) return;
                ctx.save();
                const size = ds.labelSize || 12;
                ctx.font = `700 ${size}px ${Chart.defaults.font.family}`;
                ctx.fillStyle = ds.labelColor; ctx.textBaseline = "top"; ctx.textAlign = "left";
                meta.data.forEach((el, j) => {
                    const text = ds.data[j].label, w = ctx.measureText(text).width, h = size * 1.25;
                    const cands = [{ x: el.x + 14, y: el.y - h / 2 }, { x: el.x - 14 - w, y: el.y - h / 2 },
                        { x: el.x - w / 2, y: el.y - 14 - h }, { x: el.x - w / 2, y: el.y + 14 }].map((c) => ({ ...c, w, h }));
                    const inside = (r) => r.x >= area.left && r.x + r.w <= area.right && r.y >= area.top && r.y + r.h <= area.bottom;
                    const pick = cands.find((r) => inside(r) && !blocked(r)) || cands.find(inside) || cands[0];
                    placed.push(pick);
                    ctx.fillText(text, pick.x, pick.y + 1);
                });
                ctx.restore();
            });
        },
    };

    function buildChart(canvas, dim, mini) {
        const textColor = css("--text-secondary"), grid = css("--chart-grid");
        const vis = visibleModels();
        const rows = vis.map((m) => ({ m, di: dimInfo(m, dim) })).filter((r) => r.di);
        const front = pareto(rows.map((r) => ({ id: r.m.id, cost: r.m._cost, s: r.di.s })));
        const q = !!P.q;

        const groups = {};
        for (const r of rows) (groups[labGroup(r.m)] = groups[labGroup(r.m)] || []).push(r);
        const order = Object.keys(groups).sort((a, b) => (a === "other") - (b === "other") || groups[b].length - groups[a].length);
        const datasets = P.mineOnly ? [] : order.map((g) => {
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

        // models the workspace runs today; aliases sharing a model share a point
        const mine = [], byModel = {};
        for (const { alias, m } of currentSelection()) {
            const di = m && dimInfo(m, dim);
            if (!m || !di || m._cost == null) continue;
            if (byModel[m.id]) { byModel[m.id].aliases.push(alias); continue; }
            byModel[m.id] = { x: m._cost > 0 ? m._cost : FREE_X, y: di.s, m, di, dim, aliases: [alias] };
            mine.push(byModel[m.id]);
        }
        mine.forEach((p) => { p.label = p.aliases.join(" / "); p.alias = p.label; });
        const scaleOn = P.mineOnly ? mine : rows;
        const maxCost = scaleOn.reduce((mx, r) => Math.max(mx, r.m._cost), P.mineOnly ? 0.5 : 1);
        const xMax = maxCost * (P.mineOnly ? 3 : 1.8);
        if (P.frontier && !P.mineOnly) {
            const line = rows.filter((r) => front.has(r.m.id)).sort((a, b) => a.m._cost - b.m._cost)
                .map((r) => ({ x: r.m._cost > 0 ? r.m._cost : FREE_X, y: r.di.s }));
            if (line.length) line.push({ x: xMax, y: line[line.length - 1].y });
            datasets.push({ label: "Efficiency frontier", data: line, showLine: true, stepped: "after", borderDash: [6, 4], borderWidth: 1.5,
                borderColor: css("--text-muted"), pointRadius: 0, pointHitRadius: 0, fill: false, order: 1 });
        }
        if (mine.length && P.mineOnly) {
            datasets.push({ label: "My aliases", data: mine, aliasLabels: true, labelColor: css("--text-primary"), labelSize: mini ? 11 : 13,
                pointStyle: "circle", pointRadius: mini ? 7 : 10, pointHoverRadius: 12, pointHitRadius: 10, borderWidth: 2.5, order: 0,
                backgroundColor: mine.map((p) => withAlpha(labColor(labGroup(p.m)), p.di.conf ? 0.9 : 0.3)),
                borderColor: mine.map((p) => labColor(labGroup(p.m))) });
        } else if (mine.length) {
            datasets.push({ label: "Your aliases", data: mine, pointStyle: "rectRot", pointRadius: 10, borderWidth: 2.5,
                backgroundColor: "rgba(0,0,0,0)", borderColor: css("--text-primary"), order: 0 });
        }

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
                    legend: { display: !mini && !P.mineOnly, position: "top", onClick: () => {}, labels: { color: textColor, usePointStyle: true, boxWidth: 8, font: { size: 11 }, padding: 14,
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

    let chartsStale = true;
    const currentView = () => (VIEWS.includes(P.view) ? P.view : "picks");
    // Charts only draw while the matrix is on screen (a hidden canvas has no size); otherwise they redraw on show.
    function renderCharts() { if (currentView() === "matrix" && !$("mainContent").hidden) drawCharts(); else chartsStale = true; }
    const renderChartsIfVisible = renderCharts;
    function drawCharts() {
        chartsStale = false;
        destroyCharts();
        const grid = P.grid;
        $("chartBox").hidden = grid; $("chartGrid").hidden = !grid;
        renderMineNote();
        if (!grid) { buildChart($("scatterChart"), P.dim, false); return; }
        const box = $("chartGrid"); box.innerHTML = "";
        for (const cat of Object.keys(DATA.config.categories)) {
            const d = document.createElement("div"); d.className = "mini";
            const cv = document.createElement("canvas"); d.appendChild(cv); box.appendChild(d);
            buildChart(cv, "cat:" + cat, true);
        }
    }

    // ── alias cards ──────────────────────────────────────────────────────────────────
    // The three best different models under this alias's limits (equal fits go to the cheaper model; a model sold
    // two ways, paid and ':free', shows once as its cheaper row).
    function topPicks(st) {
        const seen = new Set(), picks = [];
        for (const c of st.ranked) {
            const base = byId[c.id].base;
            if (!seen.has(base)) { seen.add(base); picks.push(c); if (picks.length === 3) break; }
        }
        return picks;
    }
    function topList(st, name) {
        const picks = topPicks(st);
        if (!picks.length) return `<div class="pick-empty">No eligible model has enough benchmark data. Try raising the cap.</div>`;
        const [first, ...rest] = picks, m = byId[first.id];
        const ring = `<button class="fit-ring" style="--p:${first.fit}" data-fit="${esc(name)}" data-fitmodel="${esc(m.id)}" title="How was this fit calculated?"><b>${num1(first.fit)}</b></button>`;
        const hero = `<div class="hero-pick"><div class="hp-label"><span>Top pick</span><span class="muted" style="letter-spacing:0;text-transform:none;font-weight:500">fit</span></div>
            <div class="hp-main"><div style="flex:1;min-width:0"><div class="hp-name" data-model="${esc(m.id)}" title="${esc(m.id)}">${esc(shortName(m))}</div>
            <div class="hp-meta">${usdM(first.cost)} · via ${esc(m._cand.provider.replace(/ \(.*\)$/, ""))}</div>${missing(m, name)}</div>${ring}</div></div>`;
        const rows = rest.map((c, i) => {
            const mm = byId[c.id];
            return `<div class="ru"><span class="rk">${i + 2}</span><span class="pick-name" data-model="${esc(mm.id)}" title="${esc(mm.id)}">${esc(shortName(mm))}</span>
                <span class="cost">${usdM(c.cost)}</span><a class="fitlink" data-fit="${esc(name)}" data-fitmodel="${esc(mm.id)}" title="How was this fit calculated?">${num1(c.fit)}</a></div>`;
        }).join("");
        return hero + (rows ? `<div class="runner-ups">${rows}</div>` : "");
    }
    function missing(m, name) {
        const w = aliasWeights(name).cats, have = fitInfo(m, name).cats;
        const gone = Object.keys(w).filter((c) => w[c] > 0 && !have[c]).map((c) => DATA.config.categories[c].short.toLowerCase());
        return gone.length ? `<div class="pick-sub" title="Fit is extrapolated from the dimensions it has">no ${gone.join(" / ")} data</div>` : "";
    }
    // How the model this alias runs today compares (set in the card's tune panel).
    function verdict(st) {
        const c = st.current;
        if (!c) return "";
        const m = byId[c.id], nm = m ? shortName(m) : c.id;
        if (c.status === "unknown") return `<div class="verdict bad">${icon("info")}<span><b>${esc(c.id)}</b> isn't in today's catalogue.</span></div>`;
        if (c.status === "no_data") return `<div class="verdict warn">${icon("info")}<span><b>${esc(nm)}</b> has too little benchmark data to rank.</span></div>`;
        if (c.status === "ineligible") return `<div class="verdict warn">${icon("info")}<span>You use <b>${esc(nm)}</b>: outside this alias's cap or requirements.</span></div>`;
        if (c.rank === 1) return `<div class="verdict ok">${icon("check")}<span>You already use the top pick, <b>${esc(nm)}</b>.</span></div>`;
        const better = c.better.length;
        const top = st.top && byId[st.top.id];
        const save = top && c.cost > 0 && top.id && st.top.cost < c.cost ? ` · ${Math.round((1 - st.top.cost / c.cost) * 100)}% cheaper` : "";
        return `<div class="verdict ${better ? "warn" : "ok"}">${icon(better ? "zap" : "check")}<span>You use <b>${esc(nm)}</b> (rank #${c.rank} of ${st.n}).${better ? ` ${better} option${better === 1 ? " is" : "s are"} cheaper-or-equal <i>and</i> better${save}.` : " Nothing cheaper is better."}</span></div>`;
    }
    // Cap slider: position 0 = $0 (free only), 1..99 = log scale $0.01..$50, 100 = no cap.
    const CAP_MIN = 0.01, CAP_MAX = 50, CAP_STEPS = 98;
    function capToPos(cap) {
        if (cap == null) return 100;
        if (cap <= 0) return 0;
        if (cap <= CAP_MIN) return 1;
        if (cap >= CAP_MAX) return 99;
        return Math.round(1 + CAP_STEPS * Math.log(cap / CAP_MIN) / Math.log(CAP_MAX / CAP_MIN));
    }
    function posToCap(pos) {
        if (pos >= 100) return null;
        if (pos <= 0) return 0;
        return Number((CAP_MIN * Math.pow(CAP_MAX / CAP_MIN, (pos - 1) / CAP_STEPS)).toPrecision(2));
    }

    // ── weight sliders (per alias) ───────────────────────────────────────────────────
    // One slider per dimension and per benchmark. Values are relative: the % beside each slider is that
    // item's share of the alias's fit (0 = off). Edits live in this browser; "Copy config snippet" exports them.
    const DIM_MAX = 1, DIM_STEP = 0.05, MET_MAX = 5, MET_STEP = 0.25;
    const shareText = (x) => (x > 0 ? (x < 0.005 ? "<1%" : Math.round(x * 100) + "%") : "off");
    function weightRows(name) {
        const w = aliasWeights(name), totalC = sumOf(Object.values(w.cats).filter((x) => x > 0));
        return Object.keys(DATA.config.categories).map((c) => {
            const cs = w.cats[c], dimShare = totalC > 0 && cs > 0 ? cs / totalC : 0;
            const ms = Object.entries(w.metrics[c]), totalM = sumOf(ms.map(([, x]) => x).filter((x) => x > 0));
            return { c, cs, dimShare, metrics: ms.map(([k, x]) => ({ k, x, share: totalM > 0 && x > 0 ? dimShare * x / totalM : 0 })) };
        });
    }
    function weightsPanelInner(name) {
        const cats = DATA.config.categories;
        // Each dimension row has a chevron that shows/hides its benchmark sliders (collapsed by default).
        const groups = weightRows(name).map((r) => {
            const open = !!P.wCatOpen[`${name}|${r.c}`], n = r.metrics.length;
            return `<div class="w-dim">
            <div class="w-row dim"><span class="w-lead">${n ? `<button type="button" class="w-tog" data-wcat="${esc(name)}|${esc(r.c)}" aria-expanded="${open}" title="${open ? "Hide" : "Show"} ${n} benchmark${n === 1 ? "" : "s"}" aria-label="${esc(cats[r.c].label)}: ${open ? "hide" : "show"} ${n} benchmark${n === 1 ? "" : "s"}"><span class="w-chev" aria-hidden="true">▸</span></button>` : `<span class="w-tog-gap"></span>`}<a class="w-name" data-gloss="dim-${esc(r.c)}" title="${esc(cats[r.c].label)}: ${esc(cats[r.c].explain)}">${esc(cats[r.c].short)}</a></span>
                <input type="range" min="0" max="${DIM_MAX}" step="${DIM_STEP}" value="${r.cs}" data-wdim="${esc(name)}|${esc(r.c)}" aria-label="${esc(name)}: ${esc(cats[r.c].label)} weight">
                <span class="w-val" data-wval="d|${esc(r.c)}">${shareText(r.dimShare)}</span></div>
            <div class="w-mets" data-wmets="${esc(name)}|${esc(r.c)}" ${open ? "" : "hidden"}>${r.metrics.map((m) => `<div class="w-row"><a class="w-name" data-gloss="${esc(m.k)}" title="${esc(DATA.metrics[m.k].label)} (click for the glossary entry)">${esc(metricShort(m.k))}</a>
                <input type="range" min="0" max="${MET_MAX}" step="${MET_STEP}" value="${m.x}" data-wmet="${esc(name)}|${esc(r.c)}|${esc(m.k)}" aria-label="${esc(name)}: ${esc(DATA.metrics[m.k].label)} weight">
                <span class="w-val" data-wval="m|${esc(r.c)}|${esc(m.k)}">${shareText(m.share)}</span></div>`).join("")}</div></div>`;
        }).join("");
        const lockNote = isGuest() ? `<div class="preview-note">${icon("info")}<span>Preview only.</span><a href="/signup">Sign up to save</a></div>`
            : !can("custom_weights") ? `<div class="preview-note">${icon("lock")}<span>Preview: saving custom weights is a Pro feature.</span><a data-upgrade="custom_weights">Upgrade</a></div>` : "";
        const cur = (DATA.config.current_models || {})[name] || "";
        return `${lockNote}<p class="w-hint">How much each dimension (▸ for its benchmarks) counts toward <b>${esc(name)}</b>'s fit. The % is its share; 0 turns it off. A benchmark a model wasn't tested on is never scored as 0.</p>
            ${groups}
            <div class="w-actions"><button class="btn btn-sm" data-wreset="${esc(name)}" title="Back to this alias's default weights">Reset weights</button></div>
            <div class="cur-field"><span class="lbl">Model you use today</span><input type="text" list="modelList" data-current="${esc(name)}" value="${esc(cur)}" placeholder="e.g. openai/gpt-5.6-sol" ${isGuest() ? "disabled" : ""}></div>`;
    }
    function weightsDetails(name) {
        // collapsed by default; remembered per card
        const open = !!P.wOpen[name];
        return `<div class="tune" data-wdetails="${esc(name)}" ${open ? "" : "hidden"}><div class="w-panel" data-wpanel="${esc(name)}">${weightsPanelInner(name)}</div></div>`;
    }
    const aliasCardEl = (name) => $("aliasGrid").querySelector(`[data-alias="${CSS.escape(name)}"]`);
    function updateWeightLabels(name) {
        const card = aliasCardEl(name);
        if (!card) return;
        for (const r of weightRows(name)) {
            const d = card.querySelector(`[data-wval="d|${r.c}"]`);
            if (d) d.textContent = shareText(r.dimShare);
            for (const m of r.metrics) { const el = card.querySelector(`[data-wval="m|${r.c}|${m.k}"]`); if (el) el.textContent = shareText(m.share); }
        }
        const badge = card.querySelector("[data-wbadge]");
        if (badge) badge.hidden = !P.weights[name];
    }
    // Store only edits that differ from the defaults, so "custom" really means different.
    function setWeight(name, kind, c, k, value) {
        const def = aliasWeights(name, false), o = (P.weights[name] = P.weights[name] || {});
        const same = (a, b) => Math.abs(a - b) < 1e-9;
        if (kind === "d") {
            o.cats = o.cats || {}; o.cats[c] = value;
            if (same(value, def.cats[c])) delete o.cats[c];
            if (!Object.keys(o.cats).length) delete o.cats;
        } else {
            o.metrics = o.metrics || {}; (o.metrics[c] = o.metrics[c] || {})[k] = value;
            if (same(value, def.metrics[c][k])) delete o.metrics[c][k];
            if (!Object.keys(o.metrics[c]).length) delete o.metrics[c];
            if (!Object.keys(o.metrics).length) delete o.metrics;
        }
        if (!o.cats && !o.metrics) delete P.weights[name];
        invalidateFit(name); scheduleSave();
    }
    function onWeightInput(t) {
        lastSliderInput = Date.now();
        let name, c, k = null;
        const kind = t.dataset.wdim ? "d" : "m";
        if (kind === "d") [name, c] = t.dataset.wdim.split("|"); else [name, c, k] = t.dataset.wmet.split("|");
        setWeight(name, kind, c, k, Number(t.value));
        updateWeightLabels(name); refreshAlias(name, { skipSlider: true }); redrawViewsSoon();
    }
    function resetWeights(name) {
        delete P.weights[name]; invalidateFit(name); scheduleSave();
        const panel = aliasCardEl(name) && aliasCardEl(name).querySelector("[data-wpanel]");
        if (panel) panel.innerHTML = weightsPanelInner(name);
        updateWeightLabels(name); refreshAlias(name);
        renderCharts(); renderTable(); renderDetail();
        if (!$("fitModal").hidden && fitState.alias === name) renderFit();
    }
    // ── saving tuning to the workspace ───────────────────────────────────────────────
    // Every change is sent ~0.4 s after the last one (PUT /api/workspace/tuning). The server validates it, checks the
    // plan (custom weights are a Pro feature), stores it, re-scores the workspace and returns the new state.
    // Guests and plans without custom weights can still drag sliders: a live preview that is not saved.
    let weightsReady = false, saveTimer = null, retryTimer = null, saving = false, dirty = false, retryDelay = 5000;
    let knownVersion = null, bannerTimer = null, checkingWs = false;
    let lastOwnSave = 0, lastSliderInput = 0, pointerDown = false;
    function setSaveState(kind, text) {
        const el = $("saveState");
        if (el) { el.className = "save-state " + kind; el.textContent = text; }
    }
    const capsFromFile = (caps) => Object.fromEntries(Object.entries(caps || {}).map(([a, v]) => [a, v === null ? "none" : v]));
    const capsToFile = () => Object.fromEntries(Object.entries(P.capOverride).map(([a, v]) => [a, v === "none" ? null : v]));
    function adoptWorkspace(ws) {
        WS = ws;
        knownVersion = ws.version;
        P.weights = JSON.parse(JSON.stringify(ws.weights || {}));
        P.capOverride = capsFromFile(ws.caps);
        applyWorkspace(ws);
    }
    async function initWeights() {
        weightsReady = true;
        try {
            ME = await getJSON("/api/me");
        } catch (e) { ME = { authenticated: false, mode: "cloud" }; }
        try { adoptWorkspace(await getJSON("/api/workspace")); } catch (e) { adoptWorkspace({ alias_defs: PRESETS, weights: {}, caps: {}, current_models: {}, version: 0 }); }
        renderChrome();
        if (!isGuest()) startWorkspaceWatcher();
        if (isGuest()) setSaveState("preview", "Preview · sign up to save");
        else if (!can("custom_weights") && Object.keys(P.weights).length) setSaveState("preview", "Custom weights paused on your plan");
    }
    // What is actually sent: weights only when the plan includes them.
    const savableWeights = () => (can("custom_weights") ? JSON.parse(JSON.stringify(P.weights)) : {});
    function scheduleSave(delay = 400) {
        if (isGuest()) { setSaveState("preview", "Preview · sign up to save"); return; }
        dirty = true; clearTimeout(saveTimer); clearTimeout(retryTimer);
        setSaveState("saving", "Saving…");
        saveTimer = setTimeout(() => saveNow(), delay);
    }
    async function saveNow(keepalive = false) {
        clearTimeout(saveTimer);
        if (!dirty || isGuest()) return;
        if (saving) { saveTimer = setTimeout(() => saveNow(keepalive), 150); return; }  // one request at a time
        saving = true; dirty = false;
        const sent = { weights: savableWeights(), caps: capsToFile() };
        try {
            const r = await fetch("/api/workspace/tuning", {
                method: "PUT", keepalive, credentials: "same-origin", headers: { "Content-Type": "application/json", "X-Requested-With": "modelfit" },
                body: JSON.stringify(sent),
            });
            const out = await r.json().catch(() => ({}));  // always read the body, or the browser keeps the request open
            if (!r.ok) throw Object.assign(new Error(out.error || r.statusText), { status: r.status, body: out });
            lastOwnSave = Date.now(); retryDelay = 5000;
            const prevRecs = WS && WS.recommendations;
            WS = { ...out }; knownVersion = out.version;
            const rc = recommendationChanges(prevRecs, out.recommendations);
            const preview = Object.keys(P.weights).length && !can("custom_weights");
            if (!dirty) setSaveState(preview ? "preview" : "saved", preview ? "Caps saved · weights are a preview" : "Saved " + new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }));
            if (rc && rc.moved.length) showBanner(describeRecommendations(rc));
        } catch (e) {
            if (e.status === 402 || e.status === 400 || e.status === 401) { setSaveState("error", "Not saved"); apiFail(e); return; }
            dirty = true;
            setSaveState("error", `Not saved (${e.message}). Retrying…`);
            retryTimer = setTimeout(() => saveNow(), retryDelay); retryDelay = Math.min(retryDelay * 2, 60000);
        } finally { saving = false; }
    }
    // ── a short banner when the picks move ───────────────────────────────────────────
    const stable = (x) => JSON.stringify(x, (k, v) => (v && typeof v === "object" && !Array.isArray(v) ? Object.fromEntries(Object.keys(v).sort().map((kk) => [kk, v[kk]])) : v));
    const bareName = (n) => String(n || "").replace(/^[^:]+: /, "");
    function showBanner(text) {
        const el = $("fileBanner");
        if (!el) return;
        el.textContent = text; el.classList.add("show");
        clearTimeout(bannerTimer); bannerTimer = setTimeout(() => el.classList.remove("show"), 3500);
    }
    // What changed in the recommendations: null if nothing but the timestamp, else which #1 picks moved.
    function recommendationChanges(old, neu) {
        if (!neu || !old) return null;
        const strip = (r) => { const { generated_at, ...rest } = r || {}; return rest; };
        if (stable(strip(old)) === stable(strip(neu))) return null;
        const moved = [];
        for (const [alias, r] of Object.entries(neu.aliases || {})) {
            const was = ((old.aliases || {})[alias] || {}).picks, now = r.picks;
            const w = was && was[0], n = now && now[0];
            if (was && (w && w.model) !== (n && n.model)) moved.push(`${alias}: ${w ? bareName(w.name) : "none"} → ${n ? bareName(n.name) : "none"}`);
        }
        return { moved };
    }
    function describeRecommendations(rc) {
        return `New #1 pick · ${rc.moved.slice(0, 2).join("; ")}${rc.moved.length > 2 ? ` (+${rc.moved.length - 2} more)` : ""}`;
    }
    // Teammates and other tabs: poll the workspace version and take their changes (never mid-drag).
    async function checkWorkspace() {
        if (!weightsReady || saving || checkingWs || document.visibilityState === "hidden" || isGuest()) return;
        checkingWs = true;
        try {
            const { version } = await getJSON("/api/workspace/version");
            if (version === knownVersion || dirty || pointerDown || Date.now() - lastSliderInput < 2000) return;
            const ws = await getJSON("/api/workspace");
            if (saving || dirty) return;
            const rc = recommendationChanges(WS && WS.recommendations, ws.recommendations);
            adoptWorkspace(ws); invalidateFit(); rerenderViews(); renderSettings();
            showBanner(rc && rc.moved.length ? describeRecommendations(rc) : "Workspace updated from another session");
        } catch (e) { /* the next tick tries again */ } finally { checkingWs = false; }
    }
    let watcherStarted = false;
    function startWorkspaceWatcher() {
        if (watcherStarted) return;
        watcherStarted = true;
        setInterval(checkWorkspace, 4000);
        document.addEventListener("visibilitychange", () => { if (document.visibilityState === "visible") checkWorkspace(); });
    }
    let viewsTimer = null;
    const redrawViewsSoon = () => {
        clearTimeout(viewsTimer);
        viewsTimer = setTimeout(() => { renderCharts(); renderTable(); renderDetail(); if (!$("fitModal").hidden) renderFit(); }, 150);
    };

    function aliasBody(name) {
        const st = aliasState(name);
        return `${topList(st, name)}${verdict(st)}`;
    }
    function reqChips(a) {
        const out = [];
        for (const r of a.require || []) if (r !== "free") out.push(r === "tools" ? "Tool use" : r === "reasoning" ? "Reasoning" : r);
        if (a.min_context) out.push(`≥${Math.round(a.min_context / 1000)}k ctx`);
        return out.length ? `<div class="ac-reqs">${out.map((r) => `<span class="req">${esc(r)}</span>`).join("")}</div>` : "";
    }
    function aliasCard(name) {
        const a = DATA.config.aliases[name], cap = effectiveCap(name), freeOnly = a.max_cost === 0 || (a.require || []).includes("free");
        const capUI = freeOnly ? `<span class="cap-free">Free only</span>`
            : `<span class="cap">≤ $<input type="number" min="0" step="0.05" data-cap="${esc(name)}" value="${cap == null ? "" : cap}" placeholder="no cap" title="Blended $/M cap. Clear for no cap." aria-label="${esc(name)} cost cap"><button class="chip" data-capreset="${esc(name)}" title="Back to this alias's default cap" ${P.capOverride[name] === undefined ? "hidden" : ""}>↺</button></span>`;
        const marks = [0.1, 1, 10].map((v) => `<span style="left:${capToPos(v)}%">$${v}</span>`).join("");
        const slider = freeOnly ? "" : `<div class="cap-slider"><input type="range" min="0" max="100" step="1" value="${capToPos(cap)}" data-capslider="${esc(name)}" aria-label="${esc(name)} cost cap">
            <div class="cap-marks"><span style="left:0">free</span>${marks}<span style="left:100%">no cap</span></div></div>`;
        const custom = P.weights[name] ? `<span class="badge${can("custom_weights") ? "" : " lock"}" data-wbadge title="${can("custom_weights") ? "Custom weights" : "Custom weights (preview)"}">custom</span>` : `<span class="badge" data-wbadge hidden>custom</span>`;
        return `<div class="card alias-card ${P.dim === "alias:" + name ? "viewing" : ""}" data-alias="${esc(name)}">
            <div class="ac-head"><div class="ac-title"><div class="alias-name">${esc(name)} ${custom}</div><div class="alias-note">${esc(a.note || "")}</div>${reqChips(a)}</div>${capUI}</div>
            ${slider}
            <div class="alias-body" data-body="${esc(name)}">${aliasBody(name)}</div>
            ${weightsDetails(name)}
            <div class="ac-foot"><button class="btn btn-ghost" data-tune="${esc(name)}" aria-expanded="${!!P.wOpen[name]}">${icon("sliders")}Tune</button>
                <button class="btn btn-ghost" data-view="${esc(name)}">${icon("chart")}Matrix</button>
                <button class="btn btn-ghost" data-fit="${esc(name)}">${icon("info")}Explain</button><span class="spacer"></span>
                ${isGuest() ? "" : `<button class="btn btn-ghost btn-icon" style="width:28px" data-remove="${esc(name)}" title="Remove ${esc(name)}" aria-label="Remove ${esc(name)}">${icon("trash")}</button>`}</div>
        </div>`;
    }
    function addCard() {
        const lim = ent().limits.aliases, n = Object.keys(DATA.config.aliases).length;
        const note = isGuest() ? "Sign up to build your own" : lim == null ? "From a preset or from scratch" : `${n} of ${lim} on your plan`;
        return `<button class="add-card" data-addalias><div><div class="plus">${icon("plus")}</div><b>Add an alias</b><small>${esc(note)}</small></div></button>`;
    }
    function renderAliases() {
        $("aliasGrid").innerHTML = Object.keys(DATA.config.aliases).map(aliasCard).join("") + addCard();
    }
    // Re-render one card's picks without replacing its slider (which would cancel a drag in progress).
    function refreshAlias(name, { skipSlider = false } = {}) {
        const card = $("aliasGrid").querySelector(`[data-alias="${CSS.escape(name)}"]`);
        if (!card) return;
        card.querySelector("[data-body]").innerHTML = aliasBody(name);
        const cap = effectiveCap(name), input = card.querySelector("[data-cap]"), sl = card.querySelector("[data-capslider]"), rs = card.querySelector("[data-capreset]");
        if (input && document.activeElement !== input) input.value = cap == null ? "" : cap;
        if (sl && !skipSlider) sl.value = capToPos(cap);
        if (rs) rs.hidden = P.capOverride[name] === undefined;
    }
    let chartTimer = null;
    const redrawChartsSoon = () => { clearTimeout(chartTimer); chartTimer = setTimeout(renderCharts, 120); };

    // ── controls ─────────────────────────────────────────────────────────────────────
    function renderControls() {
        const tabs = Object.entries(DATA.config.categories).map(([k, c]) => [("cat:" + k), c.label]);
        const curAlias = P.dim.startsWith("alias:") ? [[P.dim, dimLabel(P.dim)]] : [];
        const short = (k, l) => (k.startsWith("cat:") ? DATA.config.categories[k.slice(4)].short : l);
        const tabsHtml = [...tabs, ...curAlias].map(([k, l]) => `<button class="tab ${P.dim === k ? "active" : ""}" data-dim="${esc(k)}" title="${esc(l)}">${esc(short(k, l))}</button>`).join("");
        $("dimTabs").innerHTML = tabsHtml; $("dimTabs2").innerHTML = tabsHtml;
        $("mineTabs").innerHTML = [["0", "All models"], ["1", "Models I use"]]
            .map(([v, l]) => `<button class="tab ${(P.mineOnly ? "1" : "0") === v ? "active" : ""}" data-mine="${v}">${l}</button>`).join("");
        const groups = [...topLabs, "other"];
        $("labChips").innerHTML = groups.map((g) => `<button class="chip ${P.labsOff.includes(g) ? "" : "active"}" data-lab="${esc(g)}"><i style="background:${labColor(g)}"></i>${esc(labName(g))}</button>`).join("");
        document.querySelectorAll(".blend-select").forEach((el) => { el.value = P.blend; }); $("fadeSelect").value = String(P.fade); $("hideSelect").value = String(P.hide);
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
    function select(id) { selectedId = id; renderDetail(); renderTable(); }
    function closeDetail() { selectedId = null; $("detailPanel").hidden = true; $("drawerScrim").hidden = true; renderTable(); }
    function renderDetail() {
        const el = $("detailPanel"), m = byId[selectedId];
        if (!m) { el.hidden = true; $("drawerScrim").hidden = true; return; }
        const costRows = CHANNELS.flatMap((ch) => (m.cost.channels[ch] || []).map((c) => ({ ch, c, v: (c.in * blendObj().input + c.out * blendObj().output) / (blendObj().input + blendObj().output) })));
        const best = Math.min(...costRows.map((r) => r.v));
        const cats = DATA.config.categories;
        const metricRows = Object.entries(m.metrics).map(([k, d]) => ({ k, d, meta: DATA.metrics[k] })).filter((r) => r.meta).sort((a, b) => (b.d.s ?? -1) - (a.d.s ?? -1));
        el.innerHTML = `<button class="btn btn-ghost btn-icon close" id="closeDetail" aria-label="Close">${icon("x")}</button>
            <h3>${esc(m.name)}${m._cost === 0 ? '<span class="tag free">free</span>' : ""}${m.tools ? '<span class="tag">tools</span>' : ""}${m.reasoning ? '<span class="tag">reasoning</span>' : ""}</h3>
            <div class="sub">${esc(m.id)} · released ${esc(m.released || "?")}${age(m) != null ? ` (${age(m)} days ago)` : ""} · context ${m.ctx ? Math.round(m.ctx / 1000) + "k" : "?"}</div>
            <div class="detail-cols">
              <div><h4>Cost by channel ($/M tokens)</h4><table class="plain"><tr><th>Channel</th><th>Provider</th><th class="num">In</th><th class="num">Out</th><th class="num">Blend</th></tr>
                ${costRows.map((r) => `<tr class="${Math.abs(r.v - best) < 1e-12 ? "best" : ""}"><td>${CHANNEL_NAME[r.ch]}</td><td>${esc(r.c.provider)}${r.c.note ? ` <span class="muted">· ${esc(r.c.note)}</span>` : ""}</td><td class="num">${usd(r.c.in)}</td><td class="num">${usd(r.c.out)}</td><td class="num">${usd(r.v)}</td></tr>`).join("")}</table></div>
              <div><h4>Category scores</h4><table class="plain"><tr><th>Category</th><th class="num">Score</th><th class="num">Coverage</th><th class="num">$/100 pts</th></tr>
                ${Object.entries(cats).map(([k, c]) => { const x = m.scores[k]; return `<tr><td>${esc(c.label)}</td>${x ? `<td class="num">${num1(x.s)}</td><td class="num">${Math.round(x.cov * 100)}% · ${x.n}</td><td class="num">${usd(usd100(m._cost, x.s))}</td>` : '<td class="num muted">—</td><td class="num muted">too little data</td><td></td>'}</tr>`; }).join("")}</table>
                <h4 style="margin-top:12px">Alias fit</h4><div class="chip-row">${Object.keys(DATA.config.aliases).map((a) => `<button class="chip active" data-fit="${esc(a)}" data-fitmodel="${esc(m.id)}" title="How was this fit calculated?">${esc(a)} ${fitOf(m, a) == null ? "—" : num1(fitOf(m, a))}</button>`).join("")}</div></div>
              <div><h4>Benchmark metrics</h4><table class="plain"><tr><th>Metric</th><th class="num">Value</th><th class="num">0–100</th></tr>
                ${metricRows.length ? metricRows.map((r) => `<tr><td title="${esc(r.meta.desc)}">${esc(r.meta.label)}${r.d.how === "family" ? ' <span class="tag fam" title="Matched by model family, not the exact snapshot">family</span>' : ""}<div class="muted" style="font-size:.7rem">${esc(r.d.label)}</div></td><td class="num">${esc(r.d.v >= 1000 ? Math.round(r.d.v) : r.d.v.toFixed(1))}</td><td class="num">${r.d.s == null ? "—" : num1(r.d.s)}</td></tr>`).join("") : '<tr><td colspan="3" class="muted">No benchmark data matched this model.</td></tr>'}</table></div>
            </div>`;
        el.hidden = false; $("drawerScrim").hidden = false;
        $("closeDetail").onclick = closeDetail;
    }

    // ── fit explainer ────────────────────────────────────────────────────────────────
    const fitState = { alias: null, model: null, mode: "score", rows: 12 };
    const sumOf = (xs) => xs.reduce((a, b) => a + b, 0);
    const pct0 = (x) => Math.round(x * 100) + "%";
    const shortName = (m) => m.name.replace(/^[^:]+: /, "");
    const fmtRaw = (v) => (v == null ? "—" : Math.abs(v) >= 1000 ? String(Math.round(v)) : Math.abs(v) < 1 ? v.toFixed(2) : v.toFixed(1));
    const metricShort = (k) => (DATA.metrics[k] && DATA.metrics[k].short) || k;

    // Benchmarks that feed an alias (using its effective weights), grouped by use case.
    function fitColumns(alias) {
        const w = aliasWeights(alias);
        const used = Object.keys(w.cats).filter((c) => w.cats[c] > 0);
        const totalAW = sumOf(used.map((c) => w.cats[c]));
        return used.map((c) => {
            const ms = Object.entries(w.metrics[c]).filter(([, x]) => x > 0).sort((x, y) => y[1] - x[1]);
            const totalMW = sumOf(ms.map(([, x]) => x)), aw = w.cats[c] / totalAW;
            return { cat: c, aw, metrics: ms.map(([k, x]) => ({ k, w: x, eff: aw * (x / totalMW) })) };
        }).filter((c) => c.metrics.length);
    }
    // Decomposition of one model's fit into the points each benchmark adds. Weights are re-spread over
    // whatever the model has, exactly as the Python build does, so the points add up to the fit (±0.1 rounding).
    function fitBreakdown(m, alias) {
        const w = aliasWeights(alias), cs = fitInfo(m, alias).cats;
        const have = Object.keys(w.cats).filter((c) => w.cats[c] > 0 && cs[c]);
        const totalAW = sumOf(have.map((c) => w.cats[c]));
        const out = { have, aw: {}, catScore: {}, pts: {}, catPts: {} };
        for (const c of have) {
            const avail = Object.entries(w.metrics[c]).filter(([k, x]) => x > 0 && m.metrics[k] && m.metrics[k].s != null);
            const totalMW = sumOf(avail.map(([, x]) => x));
            out.aw[c] = w.cats[c] / totalAW; out.catScore[c] = cs[c].s; out.catPts[c] = 0;
            for (const [k, x] of avail) { const p = out.aw[c] * (x / totalMW) * m.metrics[k].s; out.pts[c + "|" + k] = p; out.catPts[c] += p; }
        }
        return out;
    }
    function whyNotRanked(m, alias) {
        const a = { ...DATA.config.aliases[alias], max_cost: effectiveCap(alias) };
        if (fitOf(m, alias) == null) return `too little data (${pct0(fitCovOf(m, alias) || 0)} of this alias's weight; needs ${pct0(a.min_share ?? 0.6)})`;
        if (m._cost == null) return "no current price";
        if (!eligible(m, a)) return a.max_cost != null && m._cost > a.max_cost ? `over the ${usd(a.max_cost)} cap` : "outside this alias's requirements";
        return null;
    }
    function fitExample(m, alias) {
        const fb = fitBreakdown(m, alias), cats = DATA.config.categories, w = aliasWeights(alias).cats;
        if (fitOf(m, alias) == null || !fb.have.length) return `<p class="muted">${esc(shortName(m))} has too little data for a ${esc(alias)} fit.</p>`;
        const terms = fb.have.map((c) => `${esc(cats[c].short)} <b>${num1(fb.catScore[c])}</b> × ${pct0(fb.aw[c])}`).join(" + ");
        const dropped = Object.keys(w).filter((c) => w[c] > 0 && !fb.have.includes(c)).map((c) => cats[c].short.toLowerCase());
        return `<p class="example"><b>Worked example, ${esc(shortName(m))}:</b> ${terms} = <b>${num1(fitOf(m, alias))}</b>${dropped.length ? `<br><span class="muted">No ${esc(dropped.join(" / "))} score for this model, so that weight was spread over the others (which is why the percentages differ from the alias weights).</span>` : ""}</p>`;
    }
    function openFit(alias, modelId) {
        fitState.alias = alias; fitState.model = modelId && byId[modelId] ? modelId : null;
        $("fitModal").hidden = false; document.body.style.overflow = "hidden";
        renderFit(); $("fitModal").querySelector(".modal-card").scrollTop = 0; $("fitClose").focus();
    }
    function closeFit() { $("fitModal").hidden = true; document.body.style.overflow = ""; }
    function renderFit() {
        const alias = fitState.alias, a = DATA.config.aliases[alias];
        if (!a) return;
        const cats = DATA.config.categories, sc = DATA.config.scoring || { low_quantile: 0.05, high_quantile: 0.99, min_coverage: 0.35 };
        const cols = fitColumns(alias), st = aliasState(alias), cap = effectiveCap(alias), mode = fitState.mode;
        const card = $("fitModal").querySelector(".modal-card"), keep = card.scrollTop;
        const weights = cols.map((c) => `${esc(cats[c.cat].short)} ${pct0(c.aw)}`).join(" · ");
        const limits = [cap == null ? null : cap === 0 ? "free only" : `blended cost ≤ ${usd(cap)}/M`,
            ...(a.require || []).filter((r) => r !== "free").map((r) => (r === "tools" ? "tool use" : r === "reasoning" ? "reasoning support" : r)),
            a.min_context ? `≥${Math.round(a.min_context / 1000)}k context` : null].filter(Boolean).join(", ") || "no limits";
        const exId = fitState.model || (st.top && st.top.id), ex = exId && byId[exId];

        const benchRows = cols.map((c) => c.metrics.map((x, i) => {
            const meta = DATA.metrics[x.k];
            return `<tr>${i === 0 ? `<td rowspan="${c.metrics.length}"><b>${esc(cats[c.cat].label)}</b><div class="muted">${pct0(c.aw)} of fit</div></td>` : ""}
                <td title="${esc(meta.desc)}"><a data-gloss="${esc(x.k)}">${esc(meta.label)}</a></td><td class="num">×${x.w}</td><td class="num">${(x.eff * 100).toFixed(1)}%</td><td class="num">${meta.models}</td>
                <td class="num">${fmtRaw(meta.scale.lo)} → ${fmtRaw(meta.scale.hi)} <span class="muted">${esc(meta.unit)}</span></td>
                <td><a href="${esc(meta.url)}" target="_blank" rel="noopener">${esc(meta.source_label)}</a> <span class="muted">· ${esc(meta.as_of || "")}</span></td></tr>`;
        }).join("")).join("");

        const ranked = st.ranked.map((c) => c.id);
        const extra = [fitState.model, currentModelFor(alias)].filter((id) => id && byId[id] && !ranked.slice(0, fitState.rows).includes(id));
        const ids = [...ranked.slice(0, fitState.rows), ...new Set(extra)];
        const head1 = cols.map((c) => `<th colspan="${c.metrics.length + 1}" class="grp">${esc(cats[c.cat].label)} · ${pct0(c.aw)}</th>`).join("");
        const head2 = cols.map((c) => c.metrics.map((x) => `<th class="num mh" title="${esc(DATA.metrics[x.k].label)}: weight ×${x.w} in ${esc(cats[c.cat].short)}; ${(x.eff * 100).toFixed(1)}% of the ${esc(alias)} fit when present">${esc(metricShort(x.k))}<small>${(x.eff * 100).toFixed(0)}%</small></th>`).join("")
            + `<th class="num sub">${esc(cats[c.cat].short)}</th>`).join("");
        const body = ids.map((id) => {
            const m = byId[id], fb = fitBreakdown(m, alias), why = whyNotRanked(m, alias), rk = ranked.indexOf(id);
            const cells = cols.map((c) => {
                const counted = fb.have.includes(c.cat);
                const mc = c.metrics.map((x) => {
                    const d = m.metrics[x.k], meta = DATA.metrics[x.k];
                    if (!d || d.s == null) return `<td class="num muted" title="${esc(meta.label)}: not tested">—</td>`;
                    const tip = `${meta.label}: ${fmtRaw(d.v)} ${meta.unit} → ${d.s}/100 (weight ×${x.w})${d.how === "family" ? " · matched by model family" : ""}`;
                    if (!counted) return `<td class="num ghost" title="${esc(tip)} — not counted: too little data for this use case">${Math.round(d.s)}</td>`;
                    const shown = mode === "pts" ? fb.pts[c.cat + "|" + x.k].toFixed(1) : Math.round(d.s);
                    return `<td class="num" style="background:rgba(124,92,255,${(0.05 + d.s / 100 * 0.45).toFixed(2)})" title="${esc(tip)}">${shown}</td>`;
                }).join("");
                const sub = counted ? (mode === "pts" ? fb.catPts[c.cat].toFixed(1) : num1(fb.catScore[c.cat])) : "—";
                return mc + `<td class="num sub">${sub}</td>`;
            }).join("");
            return `<tr class="${fitState.model === id ? "hl" : ""}"><td class="num muted">${rk >= 0 ? rk + 1 : "—"}</td>
                <td class="stick"><span class="mname" data-model="${esc(id)}">${esc(shortName(m))}</span>${why ? `<div class="muted why">not ranked: ${esc(why)}</div>` : ""}</td>
                <td class="num">${usdM(m._cost)}</td>${cells}<td class="num muted">${pct0(fitCovOf(m, alias) || 0)}</td><td class="num fit"><b>${num1(fitOf(m, alias))}</b></td></tr>`;
        }).join("") || `<tr><td colspan="9" class="muted" style="padding:16px">No model has enough data under the current limits.</td></tr>`;

        const tab = (attr, val, label, on) => `<button class="tab ${on ? "active" : ""}" data-${attr}="${val}">${label}</button>`;
        $("fitBody").innerHTML = `<h2 id="fitTitle">How the “${esc(alias)}” fit is calculated</h2>
            ${P.weights[alias] ? `<p class="custom-note"><b>Using custom weights</b> for ${esc(alias)}${can("custom_weights") ? "" : " (preview: not saved on your plan)"}. <button class="btn btn-sm" data-wreset="${esc(alias)}">Reset to defaults</button></p>` : ""}
            <p class="lead">Fit is a 0–100 score: the weighted average of the use-case scores, using this alias's weights — <b>${weights}</b>. Ranked among models that meet its limits (${esc(limits)}).</p>
            <ol class="steps">
              <li><b>Benchmark → 0–100.</b> Each benchmark is rescaled so the ${Math.round(sc.low_quantile * 100)}th percentile of every model you can currently buy ≈ 0 and the ${Math.round(sc.high_quantile * 100)}th ≈ 100 (the “0 → 100” column). That puts Elo, % correct and index scores on one scale.</li>
              <li><b>Benchmarks → use-case score.</b> Weighted average of the benchmarks the model has, with the weights below. A use case only counts if the model has ≥ ${pct0(sc.min_coverage)} of its benchmark weight.</li>
              <li><b>Use cases → fit.</b> Weighted average of the use-case scores. A missing score is never treated as 0: its weight is spread over the rest, and the model needs ≥ ${pct0(a.min_share ?? 0.6)} of this alias's weight (the <i>Data</i> column) to be ranked at all.</li>
            </ol>
            ${ex ? fitExample(ex, alias) : ""}
            <h3>Benchmarks used</h3>
            <div class="tscroll"><table class="plain"><tr><th>Use case</th><th>Benchmark</th><th class="num" title="Weight inside the use case">Weight</th><th class="num" title="Share of the fit if the model has every benchmark">Share of fit</th><th class="num">Models scored</th><th class="num" title="Raw value that maps to 0 → 100">0 → 100 means</th><th>Source</th></tr>${benchRows}</table></div>
            <h3>How each model scored</h3>
            <div class="fit-controls"><div class="tabs">${tab("fitmode", "score", "Score 0–100", mode === "score")}${tab("fitmode", "pts", "Points added to fit", mode === "pts")}</div>
              <div class="tabs">${[12, 25, 50].map((n) => tab("fitrows", n, "Top " + n, fitState.rows === n)).join("")}</div></div>
            <div class="tscroll"><table class="plain fit-table"><tr><th colspan="3"></th>${head1}<th colspan="2"></th></tr>
              <tr><th class="num">#</th><th class="stick">Model</th><th class="num">$/M</th>${head2}<th class="num">Data</th><th class="num">Fit</th></tr>${body}</table></div>
            <p class="muted foot">Cells are the benchmark rescaled to 0–100 (hover for the raw value); the small percentage under each header is that benchmark's share of the fit when present. In <i>points</i> mode each cell is its contribution to the fit, so a row adds up to Fit (±0.1 from rounding). “—” = not tested; faint numbers = tested but not counted because the use case had too little data overall. Models are those ranked under the current cap, plus the model you clicked and the one set for this alias.</p>`;
        card.scrollTop = keep;
    }

    // ── methodology / sources ────────────────────────────────────────────────────────
    function renderMethod() {
        const cfg = DATA.config;
        const cards = Object.entries(cfg.categories).map(([k, c]) => {
            const items = Object.entries(c.metrics).filter(([m]) => DATA.metrics[m] && DATA.metrics[m].scale).sort((a, b) => b[1] - a[1])
                .map(([m, w]) => `<li title="${esc(DATA.metrics[m].desc)}"><span>${esc(DATA.metrics[m].label)} <span class="muted">×${w}</span></span><span>${DATA.metrics[m].models} models</span></li>`).join("");
            return `<div class="card"><h3>${esc(c.label)}</h3><ul>${items}</ul></div>`;
        }).join("");
        const sc = cfg.scoring || { low_quantile: 0.05, high_quantile: 0.99, min_coverage: 0.35, confident_coverage: 0.6 };
        const aliasW = Object.entries(cfg.aliases).map(([n, a]) => `<li><span>${esc(n)}</span><span>${Object.entries(a.weights).filter(([, w]) => w > 0).sort((x, y) => y[1] - x[1]).map(([c, w]) => `${esc(cfg.categories[c].short)} ${Math.round(w * 100)}%`).join(" · ")}</span></li>`).join("");
        $("methodology").innerHTML = cards + `<div class="card"><h3>Alias fit weights</h3><ul>${aliasW}</ul><p class="muted" style="margin-top:6px">Fit = weighted average of the use-case scores. Click any fit number for the full breakdown.</p></div><div class="card"><h3>Scoring</h3>
            <p>Each metric is rescaled to 0–100 between its ${Math.round(sc.low_quantile * 100)}th and ${Math.round(sc.high_quantile * 100)}th percentile across currently purchasable models, so Elo, % and index scales can be averaged.</p>
            <p>A category score is the weighted mean of the metrics a model has (weights ×n, tunable per alias), shown only if they carry ≥${Math.round(sc.min_coverage * 100)}% of the category's weight. Under ${Math.round(sc.confident_coverage * 100)}% is flagged <i>thin</i>.</p>
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
        $("diagnostics").innerHTML = `<summary>Strong benchmark entries with no purchasable match (${un.length}) (these are matched by exact name only, never guessed)</summary>
            <table class="plain"><tr><th>Metric</th><th>Name in source</th><th class="num">Value</th><th class="num">0–100</th></tr>${un.map((u) => `<tr><td>${esc(DATA.metrics[u.metric].label)}</td><td>${esc(u.name)}</td><td class="num">${u.value}</td><td class="num">${u.norm}</td></tr>`).join("")}</table>`;
    }

    // ── glossary ─────────────────────────────────────────────────────────────────────
    function glossaryTerms() {
        const sc = DATA.config.scoring || { low_quantile: 0.05, high_quantile: 0.99, min_coverage: 0.35, confident_coverage: 0.6 };
        const lo = Math.round(sc.low_quantile * 100), hi = Math.round(sc.high_quantile * 100);
        return [
            ["Fit", "A 0–100 score for how well a model suits one alias: the weighted average of the dimension scores, using that alias's weights. Click any fit number to see the arithmetic."],
            ["Dimension (use case)", `One of the four things being judged. Each dimension score is a weighted average of its benchmarks, shown only when the model has at least ${Math.round(sc.min_coverage * 100)}% of that dimension's benchmark weight.`],
            ["Benchmark score, 0–100", `Raw benchmark values are rescaled so the ${lo}th percentile of models you can buy ≈ 0 and the ${hi}th ≈ 100. That is relative to today's field rather than an absolute grade, so scores shift a little as new models arrive.`],
            ["Data (coverage)", "The share of an alias's weight that a model actually has scores for. A missing benchmark is never counted as 0: its weight is spread over the rest. Models below the alias's minimum share aren't ranked."],
            ["Thin", `Fewer benchmarks than usual (under ${Math.round(sc.confident_coverage * 100)}% of the weight) stand behind the score, so treat it as a rougher estimate. Drawn as a hollow dot on the matrix.`],
            ["Lowest available cost", "The cheapest of three channels: the lab's own price (primary), OpenRouter's cheapest healthy endpoint, and Nous Portal. Batch pricing, priority/flex tiers, unhealthy endpoints and third-party 4-bit endpoints are excluded."],
            ["Blended cost", "Input and output prices combined into one $/M tokens figure at a chosen ratio (default 3 input : 1 output). Input-only and output-only views are available from the cost-basis menu."],
            ["$ / 100 pts", "Blended $/M tokens ÷ score × 100: what 100 points of capability would cost at that model's efficiency. Lower is better; free models show Free."],
            ["Efficiency frontier", "Models for which nothing else is both cheaper (or equal) and better. Drawn as the dashed line on the matrix."],
            ["Top fit", "The three different models with the highest fit among those that meet the alias's limits (cost cap, tool use, context). Equal fits go to the cheaper model, and a model sold two ways (for example paid and :free) is listed once."],
            ["Elo", "A rating built from head-to-head votes: beating a stronger opponent moves it more. Only differences matter; a 100-point gap means the higher-rated model wins about 64% of votes."],
            ["Reasoning effort", "Many models run at several effort levels (low, high, max). Benchmarks list each separately and the leaderboard keeps the best, so real cost can be higher than the per-token price implies because high effort uses more tokens."],
            ["Family match", "A benchmark result matched by model family rather than the exact dated snapshot. Flagged “family” in the model detail."],
            ["Free", "A $0 price on at least one channel, such as an OpenRouter :free variant or a first-party free tier. These are usually rate-limited and often have different data-use terms, so check them before relying on them."],
        ];
    }
    function benchEntry(k) {
        const m = DATA.metrics[k], cats = DATA.config.categories;
        const uses = Object.entries(cats).filter(([, d]) => d.metrics[k] !== undefined).map(([c, d]) => `<a data-gloss="dim-${esc(c)}">${esc(d.short)}</a> ×${d.metrics[k]}`);
        const scale = m.scale ? `${fmtRaw(m.scale.lo)} → ${fmtRaw(m.scale.hi)} ${esc(m.unit)} maps to 0 → 100` : "not used: too few models have it";
        return `<div class="gl-item" id="gl-${esc(k)}"><h4>${esc(m.label)}</h4>
            <p>${esc(m.explain || m.desc)}</p>
            <dl class="gl-facts">
              <div><dt>Unit</dt><dd>${esc(m.unit)}</dd></div>
              <div><dt>0–100 scale</dt><dd>${scale}</dd></div>
              <div><dt>Models scored</dt><dd>${m.models}</dd></div>
              <div><dt>Used in (default weight)</dt><dd>${uses.join(" · ") || "—"}</dd></div>
              <div><dt>Source</dt><dd><a href="${esc(m.url)}" target="_blank" rel="noopener">${esc(m.source_label)}</a> · as of ${esc(m.as_of || "?")}</dd></div>
            </dl>
            ${m.caveat ? `<p class="gl-caveat"><b>Watch out:</b> ${esc(m.caveat)}</p>` : ""}</div>`;
    }
    function renderGlossary() {
        const cfg = DATA.config, cats = cfg.categories, seen = new Set();
        const dims = Object.entries(cats).map(([c, d]) => {
            const aliases = Object.entries(cfg.aliases).filter(([, a]) => (a.weights[c] || 0) > 0).sort((x, y) => y[1].weights[c] - x[1].weights[c])
                .map(([n, a]) => `${esc(n)} ${Math.round(a.weights[c] * 100)}%`);
            const bms = Object.entries(d.metrics).filter(([k]) => DATA.metrics[k]).sort((x, y) => y[1] - x[1])
                .map(([k, w]) => `<a data-gloss="${esc(k)}">${esc(metricShort(k))}</a> <span class="muted">×${w}</span>`);
            return `<div class="gl-item" id="gl-dim-${esc(c)}"><h4>${esc(d.label)}</h4><p>${esc(d.explain || "")}</p>
                <p class="gl-meta"><b>Counts most for:</b> ${aliases.join(" · ")}</p><p class="gl-meta"><b>Benchmarks (default weight):</b> ${bms.join(" · ")}</p></div>`;
        }).join("");
        // each benchmark is listed once, under the first dimension that uses it
        const benchGroups = Object.entries(cats).map(([c, d]) => {
            const keys = Object.keys(d.metrics).filter((k) => DATA.metrics[k] && !seen.has(k)).sort((x, y) => d.metrics[y] - d.metrics[x]);
            keys.forEach((k) => seen.add(k));
            return keys.length ? `<h4 class="gl-group">${esc(d.label)}</h4><div class="gl-grid">${keys.map(benchEntry).join("")}</div>` : "";
        }).join("");
        const terms = glossaryTerms().map(([t, d]) => `<dt>${esc(t)}</dt><dd>${esc(d)}</dd>`).join("");
        $("glossaryBody").innerHTML = `<h3>Dimensions</h3><div class="gl-grid">${dims}</div>
            <h3>Benchmarks</h3>${benchGroups}
            <h3>Terms</h3><dl class="gl-terms">${terms}</dl>`;
    }
    function goGlossary(key) {
        if (!$("fitModal").hidden) closeFit();
        const el = document.getElementById("gl-" + key);
        if (!el) return;
        el.scrollIntoView({ behavior: "smooth", block: "center" });
        el.classList.add("flash"); setTimeout(() => el.classList.remove("flash"), 2000);
    }

    // ── CSV ──────────────────────────────────────────────────────────────────────────
    function downloadCSV() {
        if (!can("csv_export")) return isGuest() ? openSignup("Export the leaderboard as CSV") : openUpgrade("csv_export");
        const cats = Object.keys(DATA.config.categories), aliases = Object.keys(DATA.config.aliases);
        const head = ["id", "name", "released", "cost_blended_usd_per_m", "cost_channel", "cost_provider", "input_usd_per_m", "output_usd_per_m",
            ...cats.flatMap((c) => [c + "_score", c + "_usd_per_100pts"]), ...aliases.map((a) => "fit_" + a)];
        const rows = DATA.models.filter((m) => m._cost != null).map((m) => [m.id, m.name, m.released || "", m._cost, m._ch, m._cand.provider, m._cand.in, m._cand.out,
            ...cats.flatMap((c) => [m.scores[c] ? m.scores[c].s : "", m.scores[c] ? usd100(m._cost, m.scores[c].s).toFixed(4) : ""]), ...aliases.map((a) => (fitOf(m, a) ?? ""))]);
        const csv = [head, ...rows].map((r) => r.map((c) => `"${String(c).replace(/"/g, '""')}"`).join(",")).join("\n");
        const a = document.createElement("a");
        a.href = URL.createObjectURL(new Blob([csv], { type: "text/csv" })); a.download = "modelfit_leaderboard.csv"; a.click();
        URL.revokeObjectURL(a.href);
    }

    // ── render + events ──────────────────────────────────────────────────────────────
    function renderAll() {
        if (!DATA.models.some((m) => Object.keys(m.scores).length)) {
            toast("No benchmark data yet — check the Sources section");
        }
        invalidateFit();
        renderControls(); renderAliases(); renderChartsIfVisible(); renderTable(); renderDetail(); renderMethod(); renderSources(null); renderGlossary();
        if (!$("fitModal").hidden) renderFit();
        renderSettings(); updateFooter();
    }
    function rerenderViews() { renderControls(); renderAliases(); renderChartsIfVisible(); renderTable(); renderDetail(); }
    function updateFooter() { $("footerText").innerHTML = `${DATA.models.length} models · market data built ${esc(new Date(DATA.generated_at).toLocaleString())} · benchmarks: Artificial Analysis (via OpenRouter), Epoch AI (CC-BY 4.0), LMArena (CC-BY 4.0)`; }

    // ── views (hash routing) ─────────────────────────────────────────────────────────
    function showView(v, sub) {
        P.view = VIEWS.includes(v) ? v : "picks"; savePrefs();
        document.querySelectorAll(".view").forEach((el) => el.classList.toggle("on", el.dataset.section === P.view));
        document.querySelectorAll("#viewNav [data-nav]").forEach((a) => a.classList.toggle("active", a.dataset.nav === P.view));
        if (P.view === "matrix" && DATA && chartsStale) requestAnimationFrame(drawCharts);
        if (P.view === "settings") { renderSettings(); if (sub) requestAnimationFrame(() => { const el = document.getElementById("set-" + sub); if (el) el.scrollIntoView({ behavior: "smooth", block: "start" }); }); }
        window.scrollTo({ top: 0 });
    }
    function route() {
        const [v, sub] = location.hash.replace(/^#/, "").split("/");
        if (v === "glossary") { showView("methodology"); return; }
        showView(v || P.view || "picks", sub);
    }

    // ── chrome: account menu, plan badge, guest bar ──────────────────────────────────
    function renderChrome() {
        const guest = isGuest(), ws = ME.workspace;
        $("guestBar").hidden = !guest || !cloud();
        $("signupBtn").hidden = !guest || !cloud(); $("signinBtn").hidden = !guest || !cloud();
        $("accountMenu").hidden = guest;
        $("wsName").hidden = guest || !cloud();
        $("addAliasBtn").hidden = guest;
        if (ws) {
            $("wsName").textContent = ws.name;
            const plan = ws.effective_plan;
            $("upgradeBtn").hidden = !cloud() || plan !== "free";
            const name = (ME.user && (ME.user.name || ME.user.email)) || "You";
            $("avatarBtn").textContent = name.trim().split(/[\s@.]+/).filter(Boolean).slice(0, 2).map((w) => w[0].toUpperCase()).join("") || "M";
            $("menuName").textContent = ME.user.name || "Your account"; $("menuEmail").textContent = ME.user.email || (cloud() ? "" : "Self-hosted instance");
            $("menuPlan").textContent = ws.entitlements.label + " plan";
            document.querySelectorAll("[data-cloud-only]").forEach((el) => { el.hidden = !cloud(); });
        } else $("upgradeBtn").hidden = true;
        document.title = guest ? "Live LLM matrix — Modelfit" : `${ws ? ws.name + " — " : ""}Modelfit`;
    }
    async function refreshMe() {
        try { ME = await getJSON("/api/me"); renderChrome(); } catch (e) { /* keep the last copy */ }
    }

    // ── dialogs ──────────────────────────────────────────────────────────────────────
    function openDialog(html) {
        $("dialogBody").innerHTML = html; $("dialog").hidden = false; document.body.style.overflow = "hidden";
        const f = $("dialogBody").querySelector("input:not([type=hidden]), button.btn-primary");
        if (f) setTimeout(() => f.focus(), 30);
    }
    function closeDialog() { $("dialog").hidden = true; document.body.style.overflow = ""; }
    let PLANS = null;
    async function plansData() { if (!PLANS) PLANS = await getJSON("/api/plans"); return PLANS; }
    async function openUpgrade(feature, reason) {
        if (!cloud()) return toast(reason || "Not available on this instance");
        if (isGuest()) return openSignup(reason);
        const { plans: PL, features } = await plansData();
        const want = feature && features[feature] ? features[feature] : null;
        const rec = feature && !PL.pro.features[feature] && PL.team.features[feature] ? "team" : "pro";
        const card = (k) => { const p = PL[k]; return `<div class="up-plan ${k === rec ? "rec" : ""}"><h3>${esc(p.label)}${k === rec ? '<span class="pill grad">Recommended</span>' : ""}</h3>
            <div class="pr">$${p.price.month}<span> / month</span></div><ul>${p.highlights.slice(1, 5).map((h) => `<li>${icon("check")}${esc(h)}</li>`).join("")}</ul>
            <button class="btn ${k === rec ? "btn-primary" : ""} btn-block" data-checkout="${k}">Upgrade to ${esc(p.label)}</button></div>`; };
        openDialog(`<div class="dlg"><h2>${want ? `Unlock ${esc(want.toLowerCase())}` : "Upgrade your workspace"}</h2>
            <p>${esc(reason || (want ? `${want} is included in paid plans.` : "Get custom weights, webhooks, higher API limits and more aliases."))}</p>
            <div class="up-plans">${card("pro")}${card("team")}</div>
            <p class="muted" style="font-size:.8rem;margin:0">Cancel any time. ${ME.dev_billing ? "<b>Development mode:</b> upgrades switch instantly, no payment." : "Secure checkout by Stripe."} <a href="/pricing" target="_blank" rel="noopener">Compare plans</a></p></div>`);
    }
    function openSignup(reason) {
        openDialog(`<div class="dlg"><h2>Create your free workspace</h2><p>${esc(reason ? reason + " with a free account." : "Save your own aliases, cost caps and current models, and get API access.")}</p>
            <div class="actions" style="justify-content:flex-start"><a class="btn btn-primary" href="/signup">Start free</a><a class="btn" href="/login">I have an account</a></div></div>`);
    }
    async function checkout(plan) {
        try { const r = await api("POST", "/api/billing/checkout", { plan, interval: "month" }); location.href = r.url; }
        catch (e) { apiFail(e); }
    }
    function openAddAlias() {
        if (isGuest()) return openSignup("Build your own aliases");
        const lim = ent().limits.aliases, n = Object.keys(DATA.config.aliases).length;
        if (lim != null && n >= lim) return openUpgrade("aliases", `Your plan includes ${lim} aliases.`);
        const have = new Set(Object.keys(DATA.config.aliases));
        const presets = Object.entries(PRESETS).map(([k, a]) => `<button type="button" class="preset" data-preset="${esc(k)}" ${have.has(k) ? "disabled" : ""}><b>${esc(k)}</b><span>${esc(a.note || "")}</span></button>`).join("");
        const cats = Object.entries(DATA.config.categories).map(([c, d]) => `<label class="field"><span class="lbl">${esc(d.short)} weight</span><input type="number" min="0" max="1" step="0.05" name="w_${esc(c)}" value="${c === "chat" ? 0.4 : 0.2}"></label>`).join("");
        openDialog(`<div class="dlg"><h2>Add an alias</h2><p>An alias is a model slot in your product, like <i>Summarizer</i> or <i>Code review</i>. Start from a preset or define your own.</p>
            <form id="aliasForm">
                <div class="field"><span class="lbl">Start from a preset</span><div class="preset-grid">${presets}<button type="button" class="preset on" data-preset=""><b>Custom</b><span>Set the budget, requirements and weights yourself.</span></button></div></div>
                <div class="field"><label for="aName">Name</label><input type="text" id="aName" maxlength="32" placeholder="e.g. Summarizer" required></div>
                <div id="customFields">
                    <div class="row2"><label class="field"><span class="lbl">Cost cap, $/M blended</span><input type="number" min="0" step="0.05" name="cap" placeholder="no cap"></label>
                        <label class="field"><span class="lbl">Min context (tokens)</span><input type="number" min="0" step="1000" name="ctx" placeholder="any"></label></div>
                    <div class="field" style="margin-top:12px"><span class="lbl">Requirements</span><div class="check-row"><label class="check"><input type="checkbox" name="tools"> Tool use</label><label class="check"><input type="checkbox" name="reasoning"> Reasoning</label><label class="check"><input type="checkbox" name="free"> Free only</label></div></div>
                    <div class="row2" style="margin-top:12px">${cats}</div>
                </div>
                <div class="form-error" id="aliasErr" hidden style="color:var(--bad);font-size:.86rem"></div>
                <div class="actions"><button type="button" class="btn" data-close-dialog>Cancel</button><button class="btn btn-primary" type="submit">Add alias</button></div>
            </form></div>`);
        let preset = "";
        $("dialogBody").querySelectorAll("[data-preset]").forEach((b) => b.addEventListener("click", () => {
            preset = b.dataset.preset;
            $("dialogBody").querySelectorAll("[data-preset]").forEach((x) => x.classList.toggle("on", x === b));
            $("customFields").hidden = !!preset;
            if (preset && !$("aName").value) $("aName").value = preset;
        }));
        $("aliasForm").addEventListener("submit", async (e) => {
            e.preventDefault();
            const f = e.target, name = $("aName").value.trim(), body = { name: name || preset };
            if (preset) body.preset = preset;
            else {
                const num = (k) => (f[k].value === "" ? null : Number(f[k].value));
                body.definition = { max_cost: num("cap"), min_context: num("ctx"), require: ["tools", "reasoning", "free"].filter((r) => f[r].checked),
                    weights: Object.fromEntries(Object.keys(DATA.config.categories).map((c) => [c, Number(f["w_" + c].value) || 0])), note: "Custom alias" };
            }
            try { const ws = await api("POST", "/api/workspace/aliases", body); closeDialog(); adoptWorkspace(ws); invalidateFit(); rerenderViews(); refreshMe(); toast(`Added ${body.name}`); }
            catch (err) { if (err.status === 402) { closeDialog(); apiFail(err); } else { $("aliasErr").textContent = err.message; $("aliasErr").hidden = false; } }
        });
    }
    async function removeAlias(name) {
        if (!confirm(`Remove the ${name} alias? Its weights, cap and current model are removed too.`)) return;
        try { const ws = await api("DELETE", "/api/workspace/aliases/" + encodeURIComponent(name)); adoptWorkspace(ws); invalidateFit(); if (P.dim === "alias:" + name) P.dim = "cat:coding"; rerenderViews(); refreshMe(); toast(`Removed ${name}`); }
        catch (e) { apiFail(e); }
    }
    async function setCurrent(alias, model) {
        if (isGuest()) return;
        try { const ws = await api("PUT", "/api/workspace/current", { alias, model }); adoptWorkspace(ws); invalidateFit(); refreshAlias(alias); renderCharts(); setSaveState("saved", "Saved"); }
        catch (e) { apiFail(e); }
    }

    // ── settings view ────────────────────────────────────────────────────────────────
    const fmtDate = (ts) => (ts ? new Date(ts * 1000).toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" }) : "never");
    function meter(label, used, lim) {
        const pct = lim ? Math.min(100, (used / lim) * 100) : 4;
        return `<div class="meter"><span class="lbl">${esc(label)}</span><b>${used.toLocaleString()} <small>/ ${lim == null ? "unlimited" : lim.toLocaleString()}</small></b><div class="bar"><i style="width:${pct}%"></i></div></div>`;
    }
    async function renderSettings() {
        const box = $("settingsBody");
        if (!box || P.view !== "settings") return;
        if (isGuest()) {
            box.innerHTML = `<div class="card set-card full"><h2>${icon("sparkle")}Create a free workspace</h2><p>Settings, API keys and webhooks belong to a workspace.</p><a class="btn btn-primary" href="/signup">Start free</a> <a class="btn" href="/login">Sign in</a></div>`;
            return;
        }
        const ws = ME.workspace, e = ws.entitlements, u = ME.usage || {}, admin = ["owner", "admin"].includes(ME.role);
        let keys = { keys: [] }, members = { members: [] }, hist = { history: [] };
        try { [keys, members, hist] = await Promise.all([getJSON("/api/keys"), getJSON("/api/members"), getJSON("/api/workspace/history")]); } catch (err) { /* partial */ }
        const lock = (feature, text) => `<div class="lock-note">${icon("lock")}<span>${esc(text)}</span><button class="btn btn-sm btn-primary" data-upgrade="${feature}">Upgrade</button></div>`;
        const planCard = `<div class="card set-card full" id="set-plan"><h2>${icon("credit")}Plan &amp; usage</h2><p>${cloud() ? "Billing is per workspace." : "A self-hosted instance has every feature enabled."}</p>
            <div class="plan-line"><span class="big">${esc(e.label)}</span>${ws.plan_status !== "active" ? `<span class="pill warn">${esc(ws.plan_status)}</span>` : '<span class="pill good">active</span>'}
                ${ws.current_period_end ? `<span class="muted" style="font-size:.84rem">renews ${fmtDate(ws.current_period_end)}</span>` : ""}
                <span style="flex:1"></span>${cloud() && admin ? (ws.effective_plan === "free" ? `<button class="btn btn-grad" data-upgrade="">${icon("sparkle")}Upgrade</button>` : `<button class="btn" id="portalBtn">${ME.dev_billing ? "Downgrade to Free (dev)" : "Manage billing"}</button>`) : ""}</div>
            <div class="meters">${meter("Aliases", u.aliases || 0, e.limits.aliases)}${meter("API requests today", u.api_requests_today || 0, e.limits.api_requests_per_day)}${meter("API keys", u.api_keys || 0, e.limits.api_keys)}${cloud() ? meter("Seats", u.members || 1, e.limits.members) : ""}</div></div>`;
        const base = location.origin;
        const keyRows = keys.keys.map((k) => `<div class="list-row">${icon("key")}<div class="grow"><b>${esc(k.name)}</b> <code>${esc(k.prefix)}…</code><div class="meta">created ${fmtDate(k.created_at)} · last used ${fmtDate(k.last_used_at)}</div></div>${admin ? `<button class="btn btn-sm btn-danger" data-revoke="${esc(k.id)}">Revoke</button>` : ""}</div>`).join("");
        const apiCard = `<div class="card set-card" id="set-api"><h2>${icon("key")}API keys</h2><p>Your router reads picks from <code>/v1/recommendations</code>. <a href="/docs" target="_blank" rel="noopener">API docs</a></p>
            ${cloud() ? `<div class="list">${keyRows || '<div class="list-empty">No keys yet.</div>'}</div>
            ${admin ? `<form class="inline-form" id="keyForm"><input type="text" id="keyName" placeholder="Key name, e.g. production router" maxlength="60"><button class="btn btn-primary" type="submit">${icon("plus")}Create key</button></form>` : ""}`
            : `<p class="muted" style="font-size:.84rem">Self-hosted: requests from this machine need no key.</p>`}
            <div class="code-snip" style="margin-top:14px">curl ${esc(base)}/v1/recommendations${cloud() ? ` \\\n  -H "Authorization: Bearer mf_live_…"` : ""}</div></div>`;
        const hook = WS.webhook || {};
        const hookCard = `<div class="card set-card" id="set-webhook"><h2>${icon("zap")}Webhook</h2><p>We POST a signed event whenever an alias's #1 pick changes.</p>
            ${can("webhooks") ? `<form class="inline-form" id="hookForm"><input type="url" id="hookUrl" placeholder="https://your-app.com/hooks/modelfit" value="${esc(hook.url || "")}" ${admin ? "" : "disabled"}><button class="btn btn-primary" type="submit" ${admin ? "" : "disabled"}>Save</button></form>
                <div class="w-actions" style="margin-top:12px">${hook.has_secret ? `<span class="muted" style="font-size:.8rem">Signing secret ends in <code>${esc(hook.secret_hint)}</code></span>` : ""}<span style="flex:1"></span>
                ${admin && hook.url ? `<button class="btn btn-sm" id="hookTest">Send test</button><button class="btn btn-sm" id="hookRotate">Rotate secret</button>` : ""}</div>`
            : lock("webhooks", "Webhooks are part of Pro.")}</div>`;
        const histRows = hist.history.slice(0, 12).map((h) => `<div class="list-row">${icon("history")}<div class="grow"><b>${esc(h.alias)}</b>: ${esc(h.from_model ? bareName((byId[h.from_model] || {}).name || h.from_model) : "—")} → <b>${esc(h.to_model ? bareName((byId[h.to_model] || {}).name || h.to_model) : "—")}</b></div><span class="meta">${new Date(h.at * 1000).toLocaleString()}</span></div>`).join("");
        const histCard = `<div class="card set-card" id="set-history"><h2>${icon("history")}Pick history</h2><p>Every change to an alias's #1 pick${hist.days ? `, last ${hist.days} days` : ""}.</p><div class="list">${histRows || '<div class="list-empty">No changes yet. They appear here when new data or your tuning moves a top pick.</div>'}</div></div>`;
        const memberRows = members.members.map((m) => `<div class="list-row"><span class="avatar" style="width:28px;height:28px;font-size:.7rem;display:grid;place-items:center">${esc((m.name || m.email)[0].toUpperCase())}</span><div class="grow"><b>${esc(m.name || m.email)}</b><div class="meta">${esc(m.email)}</div></div><span class="pill muted">${esc(m.role)}</span>${admin && m.role !== "owner" ? `<button class="btn btn-sm btn-ghost" data-unmember="${esc(m.id)}">${icon("x")}</button>` : ""}</div>`).join("");
        const teamCard = cloud() ? `<div class="card set-card" id="set-team"><h2>${icon("users")}Team</h2><p>Everyone in the workspace shares its aliases, weights, caps and keys.</p><div class="list">${memberRows}</div>
            ${can("team") ? (admin ? `<form class="inline-form" id="memberForm"><input type="email" id="memberEmail" placeholder="teammate@company.com"><button class="btn btn-primary" type="submit">Add</button></form>` : "") : lock("team", "Shared workspaces are part of Team.")}</div>` : "";
        box.innerHTML = planCard + apiCard + hookCard + teamCard + histCard;
    }
    async function settingsAction(fn, okMsg) {
        try { const out = await fn(); if (okMsg) toast(okMsg); await refreshMe(); WS = await getJSON("/api/workspace"); renderSettings(); return out; }
        catch (e) { apiFail(e); }
    }
    function bindSettings() {
        const box = $("settingsBody");
        box.addEventListener("submit", async (e) => {
            e.preventDefault();
            if (e.target.id === "keyForm") {
                const k = await settingsAction(() => api("POST", "/api/keys", { name: $("keyName").value }));
                if (k && k.key) openDialog(`<div class="dlg"><h2>Your new API key</h2><p>Copy it now: for your security it won't be shown again.</p>
                    <div class="key-reveal"><code id="newKey">${esc(k.key)}</code><button class="btn btn-sm" id="copyKey">${icon("copy")}Copy</button></div>
                    <div class="actions"><button class="btn btn-primary" data-close-dialog>Done</button></div></div>`);
            } else if (e.target.id === "hookForm") {
                const r = await settingsAction(() => api("PUT", "/api/workspace/webhook", { url: $("hookUrl").value }), "Webhook saved");
                if (r && r.secret) showSecret(r.secret);
            } else if (e.target.id === "memberForm") {
                await settingsAction(() => api("POST", "/api/members", { email: $("memberEmail").value }), "Member added");
            }
        });
        box.addEventListener("click", async (e) => {
            const t = e.target.closest("button");
            if (!t) return;
            if (t.dataset.revoke && confirm("Revoke this key? Anything using it stops working immediately.")) await settingsAction(() => api("DELETE", "/api/keys/" + t.dataset.revoke), "Key revoked");
            else if (t.dataset.unmember && confirm("Remove this member from the workspace?")) await settingsAction(() => api("DELETE", "/api/members/" + t.dataset.unmember), "Member removed");
            else if (t.id === "hookTest") { const r = await settingsAction(() => api("POST", "/api/workspace/webhook/test")); if (r) toast(r.ok ? "Test event delivered" : "Delivery failed: " + r.error); }
            else if (t.id === "hookRotate" && confirm("Rotate the signing secret? Your receiver must use the new one.")) { const r = await settingsAction(() => api("PUT", "/api/workspace/webhook", { url: (WS.webhook || {}).url, rotate: true })); if (r) showSecret(r.secret); }
            else if (t.id === "portalBtn") { try { const r = await api("POST", "/api/billing/portal"); location.href = r.url; } catch (err) { apiFail(err); } }
        });
    }
    function showSecret(secret) {
        openDialog(`<div class="dlg"><h2>Webhook signing secret</h2><p>Verify <code>X-Modelfit-Signature</code> with this secret. It is shown once.</p>
            <div class="key-reveal"><code id="newKey">${esc(secret)}</code><button class="btn btn-sm" id="copyKey">${icon("copy")}Copy</button></div>
            <div class="actions"><button class="btn btn-primary" data-close-dialog>Done</button></div></div>`);
    }

    function bind() {
        document.addEventListener("click", (e) => {
            const t = e.target.closest("[data-dim],[data-lab],[data-model],[data-view],[data-sort],[data-capreset],[data-fit],[data-fitmode],[data-fitrows],[data-mine],[data-wreset],[data-wcat],[data-gloss],[data-tune],[data-remove],[data-addalias],[data-upgrade],[data-checkout],[data-close-dialog],#copyKey");
            if (!t) return;
            if (t.id === "copyKey") { navigator.clipboard.writeText($("newKey").textContent).then(() => toast("Copied")); return; }
            if (t.dataset.closeDialog !== undefined) return closeDialog();
            if (t.dataset.upgrade !== undefined) { closeDialog(); return openUpgrade(t.dataset.upgrade || null); }
            if (t.dataset.checkout) return checkout(t.dataset.checkout);
            if (t.dataset.addalias !== undefined) return openAddAlias();
            if (t.dataset.remove) return removeAlias(t.dataset.remove);
            if (t.dataset.tune) {
                const name = t.dataset.tune, panel = aliasCardEl(name).querySelector("[data-wdetails]");
                P.wOpen[name] = panel.hidden; panel.hidden = !panel.hidden; t.setAttribute("aria-expanded", String(!panel.hidden)); savePrefs();
                return;
            }
            if (t.dataset.wcat) {
                const key = t.dataset.wcat, open = t.getAttribute("aria-expanded") !== "true", box = t.closest(".w-dim").querySelector("[data-wmets]");
                const n = box ? box.children.length : 0, noun = `${n} benchmark${n === 1 ? "" : "s"}`;
                if (box) box.hidden = !open;
                t.setAttribute("aria-expanded", String(open));
                t.title = `${open ? "Hide" : "Show"} ${noun}`;
                t.setAttribute("aria-label", t.getAttribute("aria-label").replace(/: (show|hide) .*$/, `: ${open ? "hide" : "show"} ${noun}`));
                P.wCatOpen[key] = open; savePrefs();
            }
            else if (t.dataset.dim) { P.dim = t.dataset.dim; savePrefs(); rerenderViews(); }
            else if (t.dataset.lab) { const g = t.dataset.lab, i = P.labsOff.indexOf(g); if (i >= 0) P.labsOff.splice(i, 1); else P.labsOff.push(g); savePrefs(); renderControls(); renderCharts(); renderTable(); }
            else if (t.dataset.view) { P.dim = "alias:" + t.dataset.view; P.grid = false; savePrefs(); renderControls(); renderAliases(); renderTable(); location.hash = "#matrix"; }
            else if (t.dataset.capreset) { delete P.capOverride[t.dataset.capreset]; scheduleSave(); refreshAlias(t.dataset.capreset); renderCharts(); }
            else if (t.dataset.wreset) resetWeights(t.dataset.wreset);
            else if (t.dataset.gloss) { if (P.view !== "methodology") location.hash = "#methodology"; setTimeout(() => goGlossary(t.dataset.gloss), 60); }
            else if (t.dataset.fitmode) { fitState.mode = t.dataset.fitmode; renderFit(); }
            else if (t.dataset.fitrows) { fitState.rows = Number(t.dataset.fitrows); renderFit(); }
            else if (t.dataset.fit) openFit(t.dataset.fit, t.dataset.fitmodel || null);
            else if (t.dataset.mine !== undefined) { P.mineOnly = t.dataset.mine === "1"; savePrefs(); renderControls(); renderCharts(); renderMineNote(); }
            else if (t.dataset.sort !== undefined && t.closest("#tableHead")) {
                const k = t.dataset.sort; if (!k) return;
                P.sort = P.sort.key === k ? { key: k, dir: -P.sort.dir } : { key: k, dir: k === "score" || k.startsWith("cat:") ? -1 : 1 };
                savePrefs(); renderTable();
            } else if (t.dataset.model) { if (!$("fitModal").hidden) closeFit(); select(t.dataset.model); }
        });
        $("aliasGrid").addEventListener("input", (e) => {
            const t = e.target;
            if (t.dataset.wdim || t.dataset.wmet) return onWeightInput(t);
            if (!t.dataset.capslider) return;
            lastSliderInput = Date.now();
            const name = t.dataset.capslider, cap = posToCap(Number(t.value));
            P.capOverride[name] = cap === null ? "none" : cap;
            scheduleSave(); refreshAlias(name, { skipSlider: true }); redrawChartsSoon();
        });
        $("aliasGrid").addEventListener("change", (e) => {
            const t = e.target;
            if (t.dataset.cap) {
                const name = t.dataset.cap, v = t.value.trim(), n = Number(v);
                if (v === "") P.capOverride[name] = "none";
                else if (Number.isFinite(n) && n >= 0) P.capOverride[name] = n;
                scheduleSave(); refreshAlias(name); t.value = effectiveCap(name) ?? ""; renderCharts();
            } else if (t.dataset.current) setCurrent(t.dataset.current, t.value.trim());
        });
        const on = (id, ev, fn) => $(id).addEventListener(ev, fn);
        document.querySelectorAll(".blend-select").forEach((el) => el.addEventListener("change", (e) => { P.blend = e.target.value; savePrefs(); prepare(); rerenderViews(); }));
        on("fadeSelect", "change", (e) => { P.fade = Number(e.target.value); savePrefs(); renderCharts(); renderTable(); });
        on("hideSelect", "change", (e) => { P.hide = Number(e.target.value); savePrefs(); renderCharts(); renderTable(); });
        on("searchBox", "input", (e) => { P.q = e.target.value.trim(); savePrefs(); renderCharts(); renderTable(); });
        on("minScore", "input", (e) => { P.minScore = clamp(Number(e.target.value) || 0, 0, 100); savePrefs(); renderTable(); });
        for (const [id, key, charts_] of [["optFrontier", "frontier", true], ["optCaps", "caps", true], ["optGrid", "grid", true],
            ["fEfficient", "fEfficient"], ["fFree", "fFree"], ["fTools", "fTools"], ["fConfident", "fConfident"]]) {
            on(id, "change", (e) => { P[key] = e.target.checked; savePrefs(); if (charts_) renderCharts(); renderTable(); });
        }
        on("filtersBtn", "click", () => { const p = $("filtersPanel"); p.hidden = !p.hidden; $("filtersBtn").setAttribute("aria-expanded", String(!p.hidden)); });
        on("showAll", "click", () => { showAllRows = true; renderTable(); });
        on("downloadCsv", "click", downloadCSV);
        on("addAliasBtn", "click", openAddAlias);
        on("upgradeBtn", "click", () => openUpgrade(null));
        on("refreshBtn", "click", async () => {
            $("refreshBtn").disabled = true;
            try { await api("POST", "/api/refresh"); toast("Refresh started"); schedulePoll(500); }
            catch (e) { toast("Could not start refresh"); $("refreshBtn").disabled = false; }
        });
        on("logoutBtn", "click", async () => { try { await api("POST", "/api/auth/logout"); } finally { location.href = "/"; } });
        const pop = (btn, el) => {
            on(btn, "click", (e) => { e.stopPropagation(); const open = $(el).hidden; document.querySelectorAll(".menu-pop,.sources-pop").forEach((x) => { x.hidden = true; }); $(el).hidden = !open; $(btn).setAttribute("aria-expanded", String(open)); });
        };
        pop("avatarBtn", "menuPop"); pop("statusPill", "sourcesPop");
        document.addEventListener("click", (e) => { if (!e.target.closest(".menu-pop,.sources-pop")) { $("menuPop").hidden = true; $("sourcesPop").hidden = true; } });
        $("menuPop").addEventListener("click", (e) => { if (e.target.closest("a")) $("menuPop").hidden = true; });
        on("drawerScrim", "click", closeDetail);
        on("fitClose", "click", closeFit);
        on("fitModal", "click", (e) => { if (e.target === $("fitModal")) closeFit(); });
        on("dialog", "click", (e) => { if (e.target === $("dialog")) closeDialog(); });
        document.addEventListener("keydown", (e) => {
            if (e.key !== "Escape") return;
            if (!$("dialog").hidden) closeDialog(); else if (!$("fitModal").hidden) closeFit(); else if (!$("detailPanel").hidden) closeDetail();
        });
        document.addEventListener("themechange", () => { if (DATA) { chartsStale = true; renderCharts(); } });
        window.addEventListener("hashchange", route);
        bindSettings();
    }

    function init() {
        const ann = window.chartjsPluginAnnotation || window["chartjs-plugin-annotation"];
        if (ann) Chart.register(ann);
        Chart.register(aliasLabelPlugin);
        Chart.defaults.font.family = getComputedStyle(document.body).fontFamily;
        if (/[?&]debug\b/.test(location.search)) window.__lb = { computeFit, aliasWeights, getData: () => DATA };  // console/test handle
        // pending edits are flushed when the tab is hidden or closed (keepalive lets the request outlive the page)
        const flush = () => { if (dirty) saveNow(true); };
        window.addEventListener("pagehide", flush);
        document.addEventListener("visibilitychange", () => { if (document.visibilityState === "hidden") flush(); });
        // remember a slider drag in progress so a background refresh never swaps the page under it
        document.addEventListener("pointerdown", (e) => { if (e.target.matches && e.target.matches('input[type="range"]')) pointerDown = true; });
        for (const ev of ["pointerup", "pointercancel"]) window.addEventListener(ev, () => { pointerDown = false; });
        const qs = new URLSearchParams(location.search);
        if (qs.get("upgraded")) setTimeout(() => toast(`Welcome to ${qs.get("upgraded")[0].toUpperCase() + qs.get("upgraded").slice(1)}! Every feature is unlocked.`), 600);
        if (qs.get("welcome")) setTimeout(() => toast("Your workspace is ready. Tune an alias or add your own."), 600);
        if (qs.toString()) history.replaceState(null, "", location.pathname + location.hash);
        bind(); route(); poll();
    }
    document.addEventListener("DOMContentLoaded", init);
})();
