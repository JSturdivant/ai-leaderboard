// Marketing pages: live highlights, pricing cards and table, sign-in / sign-up.
(function () {
    "use strict";
    const $ = (s, el = document) => el.querySelector(s);
    const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
    const icon = (n) => `<svg class="i"><use href="/assets/icons.svg#${n}"/></svg>`;
    const getJSON = (u) => fetch(u, { credentials: "same-origin" }).then((r) => (r.ok ? r.json() : Promise.reject(r)));
    const post = (u, body) => fetch(u, { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json", "X-Requested-With": "modelfit" }, body: JSON.stringify(body) })
        .then(async (r) => { const j = await r.json().catch(() => ({})); if (!r.ok) throw Object.assign(new Error(j.error || r.statusText), { status: r.status }); return j; });
    const usd = (x) => (x === 0 ? "Free" : x < 0.01 ? "$" + x.toFixed(4) : x < 1 ? "$" + x.toFixed(3) : "$" + x.toFixed(2));
    const ago = (iso) => { const s = (Date.now() - Date.parse(iso)) / 1000; return s < 90 ? "just now" : s < 5400 ? Math.round(s / 60) + " min ago" : s < 129600 ? Math.round(s / 3600) + " h ago" : Math.round(s / 86400) + " d ago"; };

    const nav = $("#siteNav");
    if (nav) { const f = () => nav.classList.toggle("scrolled", window.scrollY > 8); window.addEventListener("scroll", f, { passive: true }); f(); }

    // ── landing: live numbers and today's picks ──
    if ($("#ppRows")) {
        getJSON("/api/public/highlights").then((h) => {
            if (!h.ready) { $("#ppUpdated").textContent = "collecting data…"; return; }
            const set = (k, v) => { const el = document.querySelector(`[data-stat="${k}"]`); if (el) el.textContent = v; };
            set("models", h.models); set("benchmarks", h.benchmarks); set("sources", h.sources);
            $("#ppUpdated").textContent = "updated " + ago(h.generated_at);
            $("#ppRows").innerHTML = h.picks.map((p) => `<div class="pp-row"><span class="pp-alias">${esc(p.alias)}</span>
                <div style="min-width:0"><div class="pp-model" title="${esc(p.model)}">${esc(p.name)}</div><div class="pp-sub">${usd(p.cost)}/M · via ${esc(p.provider.replace(/ \(.*\)$/, ""))}</div><div class="fitbar"><b style="width:${p.fit}%"></b></div></div>
                <div class="pp-fit"><b>${p.fit.toFixed(1)}</b><span>fit</span></div></div>`).join("");
            const coding = h.picks.find((p) => p.alias === "Coding");
            if (coding && $("#apiModel")) {
                $("#apiModel").textContent = `"${coding.model}"`; $("#apiFit").textContent = coding.fit;
                $("#apiCost").textContent = coding.cost; $("#apiProv").textContent = `"${coding.provider.replace(/ \(.*\)$/, "")}"`;
            }
        }).catch(() => { $("#ppUpdated").textContent = "offline"; });
    }

    // ── pricing cards (landing + pricing page) ──
    let PLANS = null, ME = null;
    function planCard(key, p, interval) {
        const price = p.price[interval], monthly = interval === "year" ? Math.round(price / 12) : price;
        const cta = key === "free" ? (ME && ME.authenticated ? ["Open the app", "/app", "btn"] : ["Start free", "/signup", "btn"])
            : [ME && ME.authenticated ? `Upgrade to ${p.label}` : `Start with ${p.label}`, `#checkout:${key}`, p.popular ? "btn btn-primary" : "btn"];
        return `<div class="card plan ${p.popular ? "popular" : ""}">${p.popular ? '<span class="pill grad">Most popular</span>' : ""}
            <div><h3>${esc(p.label)}</h3><div class="tag-line">${esc(p.tagline)}</div></div>
            <div class="price"><b>$${monthly}</b><span>${price ? "/ month" + (interval === "year" ? ", billed yearly" : "") : "forever"}</span></div>
            ${p.per_seat_note ? `<div class="muted" style="font-size:.84rem;margin-top:-10px">${esc(p.per_seat_note)}</div>` : ""}
            <ul>${p.highlights.map((h) => `<li>${icon("check")}<span>${esc(h)}</span></li>`).join("")}</ul>
            <a class="${cta[2]} btn-block" href="${cta[1]}" data-plan="${key}">${esc(cta[0])}</a></div>`;
    }
    function renderPlans() {
        const box = $("#plans");
        if (!box || !PLANS) return;
        const interval = box.dataset.interval || "month";
        box.innerHTML = ["free", "pro", "team"].filter((k) => PLANS.plans[k]).map((k) => planCard(k, PLANS.plans[k], interval)).join("");
        renderCompare();
    }
    function renderCompare() {
        const t = $("#compare");
        if (!t || !PLANS) return;
        const keys = ["free", "pro", "team"], P = PLANS.plans;
        const fmt = (v) => (v === null ? "Unlimited" : typeof v === "number" ? v.toLocaleString() : v);
        const limits = [["aliases", "Model aliases"], ["api_requests_per_day", "API requests / day"], ["api_keys", "API keys"], ["members", "Seats"], ["history_days", "Pick history (days)"]];
        const rows = [`<tr><td>Live matrix, leaderboard &amp; methodology</td>${keys.map(() => `<td class="yes">${icon("check")}</td>`).join("")}</tr>`]
            .concat(limits.map(([k, l]) => `<tr><td>${l}</td>${keys.map((p) => `<td>${fmt(P[p].limits[k])}</td>`).join("")}</tr>`))
            .concat(Object.entries(PLANS.features).map(([k, l]) => `<tr><td>${esc(l)}</td>${keys.map((p) => (P[p].features[k] ? `<td class="yes">${icon("check")}</td>` : '<td class="no">—</td>')).join("")}</tr>`));
        t.innerHTML = `<thead><tr><th></th>${keys.map((k) => `<th>${esc(P[k].label)}</th>`).join("")}</tr></thead><tbody>${rows.join("")}</tbody>`;
    }
    if ($("#plans")) {
        Promise.all([getJSON("/api/plans"), getJSON("/api/me").catch(() => ({}))]).then(([p, me]) => { PLANS = p; ME = me; renderPlans(); });
        document.addEventListener("click", (e) => {
            const tg = e.target.closest("[data-interval-set]");
            if (tg) {
                $("#plans").dataset.interval = tg.dataset.intervalSet;
                document.querySelectorAll("[data-interval-set]").forEach((b) => b.classList.toggle("on", b === tg));
                renderPlans();
            }
            const a = e.target.closest('a[href^="#checkout:"]');
            if (a) {
                e.preventDefault();
                const plan = a.dataset.plan, interval = $("#plans").dataset.interval || "month";
                if (!ME || !ME.authenticated) { location.href = `/signup?plan=${plan}&interval=${interval}`; return; }
                a.classList.add("disabled"); a.textContent = "Opening checkout…";
                post("/api/billing/checkout", { plan, interval }).then((r) => { location.href = r.url; })
                    .catch((err) => { alert(err.message); renderPlans(); });
            }
        });
    }

    // ── sign in / sign up ──
    const form = $("#authForm");
    if (form) {
        const qs = new URLSearchParams(location.search);
        const signup = location.pathname.startsWith("/signup");
        document.title = (signup ? "Create your account" : "Sign in") + " — Modelfit";
        $("#authTitle").textContent = signup ? "Create your workspace" : "Welcome back";
        $("#authSub").textContent = signup ? "Free forever for up to 4 aliases. No credit card." : "Sign in to your Modelfit workspace.";
        $("#nameField").hidden = !signup;
        $("#authSubmit").textContent = signup ? "Create free account" : "Sign in";
        $("#authAlt").innerHTML = signup ? 'Already have an account? <a href="/login">Sign in</a>' : 'New to Modelfit? <a href="/signup">Create a free account</a>';
        $("#password").autocomplete = signup ? "new-password" : "current-password";
        $("#termsNote").hidden = !signup;
        form.addEventListener("submit", (e) => {
            e.preventDefault();
            const err = $("#authError"), btn = $("#authSubmit");
            err.hidden = true; btn.disabled = true;
            const body = { email: $("#email").value, password: $("#password").value };
            if (signup) body.name = $("#name").value;
            post(signup ? "/api/auth/signup" : "/api/auth/login", body).then((r) => {
                const plan = qs.get("plan");
                if (signup && (plan === "pro" || plan === "team")) {
                    return post("/api/billing/checkout", { plan, interval: qs.get("interval") || "month" }).then((c) => { location.href = c.url; })
                        .catch(() => { location.href = r.next; });
                }
                location.href = r.next || "/app";
            }).catch((x) => { err.textContent = x.message; err.hidden = false; btn.disabled = false; });
        });
    }
})();
