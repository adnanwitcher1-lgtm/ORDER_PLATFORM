(function () {
    "use strict";

    var reduceMotion = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

    /* ---------- Theme toggle (light / dark) ---------- */
    function currentTheme() {
        return document.documentElement.getAttribute("data-theme") === "dark" ? "dark" : "light";
    }

    function setTheme(theme) {
        document.documentElement.setAttribute("data-theme", theme);
        try { localStorage.setItem("theme", theme); } catch (e) { /* storage may be blocked */ }
        document.dispatchEvent(new CustomEvent("themechange", { detail: { theme: theme } }));
    }

    /* ---------- Count-up numbers: <span data-count="123"> ---------- */
    function countUp(el) {
        var target = parseInt(el.getAttribute("data-count"), 10);
        if (isNaN(target)) { return; }
        if (reduceMotion || target === 0) { el.textContent = target.toLocaleString(); return; }
        var duration = 900;
        var start = null;
        function step(ts) {
            if (start === null) { start = ts; }
            var t = Math.min((ts - start) / duration, 1);
            var eased = 1 - Math.pow(1 - t, 3);
            el.textContent = Math.round(target * eased).toLocaleString();
            if (t < 1) { window.requestAnimationFrame(step); }
        }
        window.requestAnimationFrame(step);
    }

    document.addEventListener("DOMContentLoaded", function () {
        var themeBtn = document.getElementById("themeToggle");
        if (themeBtn) {
            themeBtn.addEventListener("click", function () {
                setTheme(currentTheme() === "dark" ? "light" : "dark");
            });
        }

        var menuBtn = document.getElementById("menuBtn");
        var sidebar = document.getElementById("sidebar");
        if (menuBtn && sidebar) {
            menuBtn.addEventListener("click", function () {
                sidebar.classList.toggle("open");
            });
        }

        document.querySelectorAll("[data-count]").forEach(countUp);

        // <form data-confirm="Are you sure?"> asks before submitting
        document.querySelectorAll("form[data-confirm]").forEach(function (form) {
            form.addEventListener("submit", function (event) {
                if (!window.confirm(form.getAttribute("data-confirm"))) { event.preventDefault(); }
            });
        });

        // Stagger table rows slightly
        document.querySelectorAll(".table tbody tr").forEach(function (row, i) {
            row.style.animationDelay = Math.min(i * 40, 400) + "ms";
        });

        // Auto-hide alerts after 5 seconds with a fade
        document.querySelectorAll(".alert").forEach(function (alertBox) {
            setTimeout(function () {
                alertBox.style.transition = "opacity 0.4s ease, transform 0.4s ease";
                alertBox.style.opacity = "0";
                alertBox.style.transform = "translateX(20px)";
                setTimeout(function () { alertBox.style.display = "none"; }, 400);
            }, 5000);
        });
    });
})();
