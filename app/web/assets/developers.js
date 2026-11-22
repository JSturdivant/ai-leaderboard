// Developers page: live playground for POST /v1/rank, plus samples rendered with the current host and beta key.
(function () {
    "use strict";
    const $ = (s, el = document) => el.querySelector(s);
    const $$ = (s, el = document) => [...el.querySelectorAll(s)];
    const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
    const icon = (n) => `<svg class="i"><use href="/assets/icons.svg#${n}"/></svg>`;
    const BASE = location.origin;
    let KEY = "mf_beta_public", OPTS = null, snipTab = "json", lastResult = null, CONV = null;
    // a long code-review conversation (~24k tokens) for the cache-aware switching examples
    function longConversation() {
        const msgs = [{ role: "system", content: "You are a senior engineer reviewing a large codebase. " + "Context about the repository, its modules and conventions. ".repeat(1500) }];
        for (let i = 0; i < 6; i++) {
            msgs.push({ role: "user", content: `Look at module ${i} and explain the data flow. `.repeat(20) });
            msgs.push({ role: "assistant", content: "Here is the analysis of the module and how data moves through it. ".repeat(60) });
        }
        msgs.push({ role: "user", content: "Now refactor the payment module to use dependency injection." });
        return msgs;
    }

    const FACTORS = ["performance", "savings", "latency", "reliability", "context", "recency"];
    const DIMS = ["coding", "agentic", "reasoning", "chat"];
    const W0 = { performance: 6, savings: 3, latency: 1, reliability: 0, context: 0, recency: 0 };
    const M0 = { coding: 5, agentic: 2, reasoning: 2, chat: 1 };
    const EXAMPLES = [
        { label: "Fix a bug", prompt: "My Flask app crashes on startup:\nTraceback (most recent call last):\n  File \"app.py\", line 12, in <module>\n    db.init_app(app)\nAttributeError: 'NoneType' object has no attribute 'init_app'\nWhat's wrong and how do I fix it?" },
        { label: "Math proof", prompt: "Prove that for every integer n ≥ 1, the sum 1 + 3 + 5 + … + (2n − 1) equals n². Show each step." },
        { label: "Support bot", task: "Customer support chatbot for a retail bank: answers account and card questions, high volume", filters: { tools: false }, weights: { savings: 5, chat: 3, latency: 2 } },
        { label: "Summarise", prompt: "Summarize this meeting transcript into 5 bullet points with owners and deadlines.", limits: { max_cost_per_request: 0.001 } },
        { label: "Agent + tools", task: "Autonomous coding agent: reads the repo, runs tests in a terminal, edits files until CI passes", filters: { tools: true, min_context: 128000 } },
        { label: "EU open model", prompt: "Draft a polite reply declining the meeting and proposing next week.", filters: { countries: ["FR", "DE"], open_weights: true, include_unknown_country: false } },
        { label: "Long chat on Opus: switch?", conv: true, current: "anthropic/claude-opus-5.5", cache: { age: 30, turns: 8 }, weights: { performance: 6, savings: 4 } },
        { label: "Warm cache: stay?", conv: true, current: "deepseek/deepseek-v4.1-flash-1017", cache: { age: 30, turns: 8 }, weights: { performance: 6, savings: 4 } },
        { label: "Translate", prompt: "Translate into Japanese: \"Thanks for your order! It will ship within two business days.\"" },
    ];
    const COUNTRY = { US: "United States", CN: "China", FR: "France", CA: "Canada", KR: "South Korea", JP: "Japan", IL: "Israel", DE: "Germany", GB: "United Kingdom", unknown: "Unknown" };

    // ── setup ──
    function slider(group, k, v, max = 10) {
        return `<div class="pg-sl"><span>${k}</span><input type="range" min="0" max="${max}" step="1" value="${v}" data-${group}="${k}"><b data-v="${group}-${k}">${v}</b></div>`;
    }
    function chips(box, values, sel) {
        $(box).innerHTML = values.map((c) => `<button type="button" class="chip ${sel.includes(c) ? "active" : ""}" data-c="${esc(c)}"><i style="background:var(--accent)"></i>${esc(c)}${OPTS && OPTS.countries[c] ? ` <span class="muted">${OPTS.countries[c]}</span>` : ""}</button>`).join("");
    }
    const selected = (box) => $$(`${box} .chip.active`).map((b) => b.dataset.c);
    function init() {
        $("#wSliders").innerHTML = FACTORS.map((k) => slider("w", k, W0[k])).join("");
        $("#mSliders").innerHTML = DIMS.map((k) => slider("m", k, M0[k])).join("");
        $("#pgExamples").innerHTML = '<span class="lbl">Examples</span>' + EXAMPLES.map((e, i) => `<button type="button" class="chip" data-ex="${i}">${esc(e.label)}</button>`).join("");
        chips("#fCountries", ["US", "FR", "CN", "CA", "KR", "JP", "IL"], []);
        chips("#fExCountries", ["CN", "US", "FR"], []);
        syncDisabled(); update();
        fetch("/api/config").then((r) => r.json()).then((c) => {
            if (c.beta_key) KEY = c.beta_key;
            $("#betaKey").textContent = KEY;
            const d = (c.beta_daily || 1000).toLocaleString();
            $("#betaDaily").textContent = d; $$(".betaDailyText").forEach((el) => { el.textContent = d; });
            if (!c.beta_key) $("#betaKey").textContent = "(beta key disabled on this server)";
            update(); samples();
        }).catch(() => samples());
        fetch("/v1/rank/options", { headers: { Authorization: `Bearer ${KEY}` } }).then((r) => r.json()).then((o) => {
            OPTS = o;
            const cs = Object.keys(o.countries).filter((c) => c !== "unknown").slice(0, 9);
            chips("#fCountries", cs, selected("#fCountries"));
            chips("#fExCountries", cs, selected("#fExCountries"));
            $("#factorTable").innerHTML = "<tr><th>Factor</th><th>How it is measured</th></tr>" + Object.entries(o.factors).map(([k, v]) => `<tr><td><code>${k}</code></td><td>${esc(v)}</td></tr>`).join("");
            $("#taskTable").innerHTML = "<tr><th>Task type</th><th>Maps to dimensions</th></tr>" + Object.values(o.task_types).map((t) => `<tr><td>${esc(t.label)}</td><td>${Object.entries(t.dimensions).map(([d, w]) => `${d} ${Math.round(w * 100)}%`).join(" · ")}</td></tr>`).join("");
        }).catch(() => {});
        samples();
    }
    function syncDisabled() {
        $$("#wSliders input").forEach((el) => { el.disabled = $("#wAuto").checked; });
        $$("#mSliders input").forEach((el) => { el.disabled = $("#mAuto").checked; });
        $("#wSliders").classList.toggle("off", $("#wAuto").checked);
        $("#mSliders").classList.toggle("off", $("#mAuto").checked);
    }

    // ── request building ──
    function body() {
        const b = {};
        const p = $("#pgPrompt").value.trim(), t = $("#pgTask").value.trim();
        if (CONV) b.messages = CONV;
        if (p) b.prompt = p;
        if (t) b.task = t;
        const cur = $("#curModel").value.trim();
        if (cur) b.current_model = cur;
        const cache = {};
        if ($("#cAge").value) cache.seconds_since_last_request = +$("#cAge").value;
        if ($("#cTurns").value) cache.expected_turns = +$("#cTurns").value;
        if ($("#cTokens").value) cache.cached_tokens = +$("#cTokens").value;
        if (Object.keys(cache).length) b.cache = cache;
        if ($("#cGain").value) b.min_switch_gain = +$("#cGain").value;
        if (!$("#wAuto").checked) {
            const w = {};
            for (const el of $$("#wSliders input")) if (+el.value > 0) w[el.dataset.w] = +el.value;
            if (Object.keys(w).length) b.weights = w;
        }
        if (!$("#mAuto").checked) {
            const m = {};
            for (const el of $$("#mSliders input")) if (+el.value > 0) m[el.dataset.m] = +el.value;
            if (Object.keys(m).length) b.performance = m;
        }
        const lim = {};
        if ($("#limReq").value) lim.max_cost_per_request = +$("#limReq").value;
        if ($("#limBlend").value) lim.max_blended_cost = +$("#limBlend").value;
        if (Object.keys(lim).length) b.limits = lim;
        const f = {};
        const cs = selected("#fCountries"), ex = selected("#fExCountries");
        if (cs.length) { f.countries = cs; if ($("#fUnknown").checked) f.include_unknown_country = true; }
        if (ex.length) f.exclude_countries = ex;
        if ($("#fOpen").value) f.open_weights = $("#fOpen").value === "true";
        if ($("#fCtx").value) f.min_context = +$("#fCtx").value;
        for (const [id, k] of [["fTools", "tools"], ["fReasoning", "reasoning"], ["fVision", "vision"], ["fFree", "free"]]) if ($("#" + id).checked) f[k] = true;
        const xv = $("#fExVendors").value.split(",").map((s) => s.trim()).filter(Boolean);
        if (xv.length) f.exclude_vendors = xv;
        if ($("#fAge").value) f.max_age_days = +$("#fAge").value;
        if (Object.keys(f).length) b.filters = f;
        if ($("#outTok").value) b.expected_output_tokens = +$("#outTok").value;
        const n = +$("#pgLimit").value;
        if (n !== 10) b.limit = n;
        return b;
    }
    function snippet(kind, b0) {
        // keep long example conversations readable in the snippet
        const b = b0.messages && JSON.stringify(b0.messages).length > 600
            ? { ...b0, messages: [`… ${b0.messages.length} messages (~${Math.round(JSON.stringify(b0.messages).length / 4 / 1000)}k tokens) …`] } : b0;
        const json = JSON.stringify(b, null, 2);
        if (kind === "json") return json;
        if (kind === "curl") return `curl -X POST ${BASE}/v1/rank \\\n  -H "Authorization: Bearer ${KEY}" \\\n  -H "Content-Type: application/json" \\\n  -d '${JSON.stringify(b).replace(/'/g, "'\\''")}'`;
        if (kind === "python") return `import requests\n\nresp = requests.post(\n    "${BASE}/v1/rank",\n    headers={"Authorization": "Bearer ${KEY}"},\n    json=${json.replace(/\btrue\b/g, "True").replace(/\bfalse\b/g, "False").replace(/\bnull\b/g, "None").replace(/\n/g, "\n    ")},\n)\nfor m in resp.json()["results"]:\n    print(m["rank"], m["score"], m["model"], m["cost"]["estimated_request_usd"])`;
        return `const resp = await fetch("${BASE}/v1/rank", {\n  method: "POST",\n  headers: { "Authorization": "Bearer ${KEY}", "Content-Type": "application/json" },\n  body: JSON.stringify(${json.replace(/\n/g, "\n  ")}),\n});\nconst { results, triage } = await resp.json();\nconsole.log(triage.category, results.map((m) => [m.rank, m.model, m.score]));`;
    }
    function update() {
        const b = body();
        $("#pgSnippet").textContent = snippet(snipTab, b);
        const nf = b.filters ? Object.keys(b.filters).length : 0;
        $("#fSummary").textContent = nf ? `${nf} active` : "";
        $("#wSummary").textContent = $("#wAuto").checked ? "auto from triage" : "custom";
        $("#cSummary").textContent = b.current_model ? `on ${b.current_model.split("/")[1]}` : "optional";
        $("#pgConv").hidden = !CONV;
        if (CONV) $("#pgConvText").textContent = `Example conversation loaded: ${CONV.length} messages, ~${Math.round(JSON.stringify(CONV).length / 4 / 1000)}k tokens`;
    }
    function samples() {
        const b = { prompt: "Refactor this React class component to hooks", weights: { coding: 6, savings: 3, latency: 1 }, filters: { open_weights: true }, limit: 5 };
        $("#heroCurl").textContent = `curl -X POST ${BASE}/v1/rank \\\n  -H "Authorization: Bearer ${KEY}" \\\n  -H "Content-Type: application/json" \\\n  -d '{"prompt": "Prove that √2 is irrational"}'`;
        $("#qsCurl").textContent = snippet("curl", b);
        $("#qsPy").textContent = snippet("python", b);
        $("#qsJs").textContent = snippet("js", b);
        $("#respSample").textContent = JSON.stringify({
            object: "ranking", generated_at: "2026-11-22T16:29:12+00:00",
            triage: { category: "coding", task_types: [{ type: "code_review", label: "Code review & refactoring", p: 0.91 }], complexity: "low", language: { code: "en" }, estimated_input_tokens: 12 },
            weights: { performance: 0.6, savings: 0.3, latency: 0.1 }, performance_mix: { coding: 1 },
            candidates: 51, excluded: { open_weights_filter: 150, insufficient_benchmark_data: 140 },
            results: [{ rank: 1, model: "deepseek/deepseek-v4.1-flash-1017", score: 69.1, factors: { performance: 76.1, savings: 71.2, latency: 47.5 },
                cost: { estimated_request_usd: 0.00013, input_per_m: 0.05, output_per_m: 0.25, provider: "…" }, latency: { basis: "estimated", ttft_s: 0.88, tokens_per_s: 147 },
                country: "CN", open_weights: true, explanation: ["Performance 76.1 (coding 76), top 9% of candidates", "Cost ≈$0.0001 for this request via … (top 12% cheapest)"] }, "…"],
        }, null, 2);
    }

    // ── run + render ──
    const bar = (v, cls = "") => `<span class="mbar ${cls}"><i style="width:${v == null ? 0 : Math.max(2, v)}%"></i></span>`;
    const usd = (x) => (x === 0 ? "free" : x < 0.0001 ? "<$0.0001" : x < 1 ? "$" + x.toFixed(4) : "$" + x.toFixed(2));
    async function run(e) {
        if (e) e.preventDefault();
        const out = $("#pgOut"), btn = $("#pgGo");
        btn.disabled = true; btn.innerHTML = `${icon("refresh")}Ranking…`;
        try {
            const r = await fetch("/v1/rank", { method: "POST", headers: { Authorization: `Bearer ${KEY}`, "Content-Type": "application/json" }, body: JSON.stringify(body()) });
            const j = await r.json();
            if (!r.ok) throw new Error(j.error || r.statusText);
            lastResult = j; render(j, r.headers.get("X-RateLimit-Remaining"));
        } catch (err) {
            out.innerHTML = `<div class="card pg-empty err">${icon("info")}<p>${esc(err.message)}</p></div>`;
        } finally { btn.disabled = false; btn.innerHTML = `${icon("sparkle")}Rank models`; }
    }
    function render(j, remaining) {
        const t = j.triage;
        const tri = `<div class="card pg-tri">
            <div class="pg-tri-head"><span class="lbl">Triage</span><span class="muted">${j.candidates} ranked · ${j.took_ms} ms${remaining ? ` · ${Number(remaining).toLocaleString()} beta calls left today` : ""}</span></div>
            <div class="pg-tri-main"><span class="pill grad">${esc(t.category)}</span>${t.task_types.filter((x, i) => i === 0 || x.p >= 0.05).map((x) => `<span class="pill muted">${esc(x.label)} ${Math.round(x.p * 100)}%</span>`).join("")}
                <span class="pill ${t.complexity === "high" ? "warn" : "muted"}">${esc(t.complexity)} complexity</span><span class="pill muted">${esc(t.language.name)}</span><span class="pill muted">~${t.estimated_input_tokens.toLocaleString()} tokens in</span></div>
            <div class="pg-mix">${Object.entries(j.weights).map(([k, v]) => `<span><b>${Math.round(v * 100)}%</b> ${k}</span>`).join("")}<span class="muted">· ${j.basis.weights === "auto" ? "auto weights" : "your weights"}</span></div>
            <div class="pg-mix">${Object.entries(j.performance_mix).map(([k, v]) => `<span><b>${Math.round(v * 100)}%</b> ${k}</span>`).join("")}<span class="muted">· performance mix (${j.basis.performance_mix})</span></div>
            ${t.signals.length ? `<div class="pg-sig">${t.signals.map((s) => `<span>${esc(s.reason)}</span>`).join("")}</div>` : ""}
            ${Object.keys(j.excluded).length ? `<details class="pg-ex"><summary>${Object.values(j.excluded).reduce((a, b) => a + b, 0)} models excluded</summary>${Object.entries(j.excluded).map(([k, v]) => `<span>${esc(k.replace(/_/g, " "))}: <b>${v}</b></span>`).join("")}</details>` : ""}
        </div>`;
        const sw = j.switching;
        const money = (x) => (x == null ? "—" : Math.abs(x) < 0.01 ? "$" + x.toFixed(4) : "$" + x.toFixed(2));
        const swCard = !sw ? "" : `<div class="card pg-switch ${sw.recommendation}">
            <div class="pg-sw-head"><span class="lbl">Cache-aware switching</span><span class="pill ${sw.recommendation === "stay" ? "good" : "warn"}">${esc(sw.recommendation.replace(/_/g, " "))}</span></div>
            <div class="pg-sw-main"><b>${esc((sw.current_name || sw.current_model || "").replace(/^[^:]+: /, ""))}</b> <span class="muted">${sw.cache.warm ? `cache warm · ${sw.cache.cached_tokens.toLocaleString()} tokens · ~${sw.cache.expires_in_seconds}s left` : "cache cold"}</span>
                ${sw.best_alternative ? `<span class="muted">vs</span> <b>${esc((sw.best_alternative_name || sw.best_alternative).replace(/^[^:]+: /, ""))}</b>` : ""}</div>
            ${sw.stay_horizon_usd != null ? `<div class="pg-sw-nums"><span>Stay <b>${money(sw.stay_horizon_usd)}</b></span><span>Switch <b>${money(sw.switch_horizon_usd)}</b></span>
                <span>over ${sw.horizon_turns} turns</span><span>first-turn penalty <b>${money(Math.max(0, sw.switch_now_extra_usd))}</b></span>
                <span>break-even <b>${sw.breakeven_turn ? "turn " + sw.breakeven_turn : "never (50 turns)"}</b></span><span>score gain <b>${sw.score_gain > 0 ? "+" : ""}${sw.score_gain}</b></span></div>` : ""}
            <p>${esc(sw.reason)}</p></div>`;
        const rows = j.results.map((m) => `<div class="card pg-res ${m.is_current ? "current" : ""}">
            <div class="pg-res-head"><span class="pg-rank">${m.rank}</span><div class="pg-res-name"><b title="${esc(m.model)}">${esc(m.name.replace(/^[^:]+: /, ""))}</b>
                <span class="muted">${m.is_current ? '<span class="tagx">current model</span> · ' : ""}${esc(m.vendor_name || m.vendor)}${m.country ? " · " + esc(COUNTRY[m.country] || m.country) : ""}${m.open_weights ? ' · <span class="tagx">open weights</span>' : ""}${m.tools ? ' · <span class="tagx">tools</span>' : ""}${m.reasoning ? ' · <span class="tagx">reasoning</span>' : ""}</span></div>
                <div class="pg-score"><b>${m.score.toFixed(1)}</b><span>score</span></div></div>
            <div class="pg-factors">${Object.keys(j.weights).map((k) => `<div><span>${k}</span>${bar(m.factors[k])}<b>${m.factors[k] == null ? "—" : Math.round(m.factors[k])}</b></div>`).join("")}</div>
            <div class="pg-facts"><span>${usd(m.cost.estimated_request_usd)} <em>this request</em></span>${m.cost.horizon_turns > 1 ? `<span>${usd(m.cost.horizon_usd)} <em>next ${m.cost.horizon_turns} turns</em></span>` : ""}
                ${m.cache.supported ? `<span>${m.cache.warm ? `${m.cache.cached_tokens.toLocaleString()} cached` : "caches"} <em>${m.cache.warm ? "warm" : "cold start"}</em></span>` : `<span>no cache <em>full price each turn</em></span>`}<span>$${m.cost.input_per_m}/$${m.cost.output_per_m} <em>per M in/out</em></span>
                <span>${m.latency.ttft_s ?? "?"} s · ${m.latency.tokens_per_s ?? "?"} tok/s <em>${esc(m.latency.basis || "")}</em></span><span>${m.context ? Math.round(m.context / 1000) + "k" : "?"} <em>context</em></span></div>
            ${m.explanation ? `<ul class="pg-why">${m.explanation.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>` : ""}
        </div>`).join("") || `<div class="card pg-empty">${icon("info")}<p>No model matches these filters and limits. See which ones removed the most above.</p></div>`;
        $("#pgModels").innerHTML = j.results.map((m) => `<option value="${esc(m.model)}">`).join("");
        $("#pgOut").innerHTML = swCard + tri + rows + `<details class="pg-raw"><summary>Raw JSON response</summary><div class="code-snip">${esc(JSON.stringify(j, null, 2))}</div></details>`;
    }

    // ── events ──
    document.addEventListener("click", (e) => {
        const ex = e.target.closest("[data-ex]");
        if (ex) {
            const x = EXAMPLES[+ex.dataset.ex];
            $("#pgPrompt").value = x.prompt || ""; $("#pgTask").value = x.task || "";
            CONV = x.conv ? longConversation() : null;
            $("#curModel").value = x.current || "";
            $("#cAge").value = x.cache ? x.cache.age : ""; $("#cTurns").value = x.cache ? x.cache.turns : "";
            $("#cTokens").value = ""; $("#cGain").value = "";
            if (x.current) $("#convSec").open = true;
            $$("#pgExamples .chip").forEach((c) => c.classList.toggle("active", c === ex));
            // reset, then apply the example's settings
            const dimW = x.weights ? DIMS.reduce((acc, d) => acc + (x.weights[d] || 0), 0) : 0;
            $("#wAuto").checked = !x.weights; $("#mAuto").checked = !dimW;
            for (const el of $$("#wSliders input")) {
                const k = el.dataset.w;
                el.value = x.weights ? (k === "performance" ? (x.weights.performance || 0) + dimW : (x.weights[k] || 0)) : W0[k];
                $(`[data-v="w-${k}"]`).textContent = el.value;
            }
            for (const el of $$("#mSliders input")) {
                el.value = dimW ? (x.weights[el.dataset.m] || 0) : M0[el.dataset.m];
                $(`[data-v="m-${el.dataset.m}"]`).textContent = el.value;
            }
            $("#limReq").value = (x.limits && x.limits.max_cost_per_request) || ""; $("#limBlend").value = "";
            const f = x.filters || {};
            $$("#fCountries .chip").forEach((c) => c.classList.toggle("active", (f.countries || []).includes(c.dataset.c)));
            $$("#fExCountries .chip").forEach((c) => c.classList.remove("active"));
            $("#fOpen").value = f.open_weights == null ? "" : String(f.open_weights);
            $("#fCtx").value = f.min_context ? String(f.min_context) : "";
            $("#fTools").checked = !!f.tools; $("#fReasoning").checked = false; $("#fVision").checked = false; $("#fFree").checked = false; $("#fUnknown").checked = false;
            syncDisabled(); update(); run();
            return;
        }
        const chip = e.target.closest("#fCountries .chip, #fExCountries .chip");
        if (chip) { chip.classList.toggle("active"); update(); return; }
        const sn = e.target.closest("[data-snip]");
        if (sn) { snipTab = sn.dataset.snip; $$("[data-snip]").forEach((b) => b.classList.toggle("active", b === sn)); update(); return; }
        const cp = e.target.closest("[data-copy]");
        if (cp) { navigator.clipboard.writeText($(cp.dataset.copy).textContent).then(() => { cp.innerHTML = `${icon("check")}Copied`; setTimeout(() => { cp.innerHTML = `${icon("copy")}Copy`; }, 1500); }); }
    });
    document.addEventListener("input", (e) => {
        const el = e.target;
        if (el.dataset.w || el.dataset.m) $(`[data-v="${el.dataset.w ? "w-" + el.dataset.w : "m-" + el.dataset.m}"]`).textContent = el.value;
        if (el.closest("#pgForm")) update();
    });
    document.addEventListener("change", (e) => {
        if (e.target.id === "wAuto" || e.target.id === "mAuto") syncDisabled();
        if (e.target.closest("#pgForm")) update();
    });
    $("#pgForm").addEventListener("submit", run);
    $("#pgConvClear").addEventListener("click", () => { CONV = null; update(); });
    init();
})();
