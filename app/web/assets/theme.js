// Applies the saved theme before first paint (no flash) and wires every [data-theme-toggle] button.
// "system" (no saved choice) follows the OS; a click toggles between light and dark and remembers it.
(function () {
    var KEY = "modelfit-theme";
    var root = document.documentElement;
    function saved() { try { return localStorage.getItem(KEY); } catch (e) { return null; } }
    function current() {
        var t = root.getAttribute("data-theme");
        if (t === "light" || t === "dark") return t;
        return window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
    }
    var s = saved();
    if (s === "light" || s === "dark") root.setAttribute("data-theme", s);
    window.ModelfitTheme = {
        current: current,
        toggle: function () {
            var next = current() === "dark" ? "light" : "dark";
            root.setAttribute("data-theme", next);
            try { localStorage.setItem(KEY, next); } catch (e) { /* private mode */ }
            document.dispatchEvent(new CustomEvent("themechange", { detail: next }));
            return next;
        },
    };
    document.addEventListener("click", function (e) {
        var b = e.target.closest && e.target.closest("[data-theme-toggle]");
        if (b) window.ModelfitTheme.toggle();
    });
})();
