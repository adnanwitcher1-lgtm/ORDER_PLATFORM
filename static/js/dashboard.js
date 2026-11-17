(function () {
    "use strict";

    if (typeof Chart === "undefined") { return; }

    var dataEl = document.getElementById("chart-data");
    if (!dataEl) { return; }
    var data = JSON.parse(dataEl.textContent);
    var charts = [];

    var STATUS_VARS = {
        draft: "--c-7", pending_review: "--c-4", approved: "--c-1", assigned: "--c-6",
        monitoring: "--c-2", paused: "--c-10", target_observed: "--c-8",
        completed: "--c-3", cancelled: "--c-5", exception: "--c-9"
    };

    function css(name) {
        return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    }

    function setBase() {
        Chart.defaults.color = css("--muted");
        Chart.defaults.borderColor = css("--grid");
        Chart.defaults.font.family = '"Segoe UI", Roboto, Arial, sans-serif';
        Chart.defaults.font.size = 12;
    }

    function tooltip() {
        return {
            backgroundColor: css("--sidebar-bg"),
            titleColor: "#ffffff",
            bodyColor: "#e2e8f0",
            padding: 10,
            cornerRadius: 8,
            displayColors: true
        };
    }

    function build() {
        charts.forEach(function (c) { c.destroy(); });
        charts = [];
        setBase();

        // ---- 1. Orders created per day (line) ----
        var daily = document.getElementById("chartDaily");
        if (daily) {
            var ctx = daily.getContext("2d");
            charts.push(new Chart(ctx, {
                type: "line",
                data: {
                    labels: data.daily.labels,
                    datasets: [{
                        label: "Orders created",
                        data: data.daily.values,
                        borderColor: css("--c-1"),
                        backgroundColor: css("--c-1") + "33",
                        pointBackgroundColor: css("--c-1"),
                        pointBorderColor: css("--card"),
                        pointBorderWidth: 2,
                        pointRadius: 4,
                        pointHoverRadius: 6,
                        borderWidth: 3,
                        tension: 0.35,
                        fill: true
                    }]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    animation: { duration: 1100, easing: "easeOutQuart" },
                    interaction: { mode: "index", intersect: false },
                    plugins: { legend: { display: false }, tooltip: tooltip() },
                    scales: {
                        x: { grid: { display: false } },
                        y: { beginAtZero: true, ticks: { precision: 0 } }
                    }
                }
            }));
        }

        // ---- 2. Orders by status (doughnut) ----
        var statusCard = document.getElementById("cardStatus");
        var legend = document.getElementById("statusLegend");
        var rows = data.status.filter(function (s) { return s.count > 0; });
        if (legend) { legend.innerHTML = ""; }
        if (!rows.length) {
            statusCard.classList.add("is-empty");
        } else {
            statusCard.classList.remove("is-empty");
            var colors = rows.map(function (s) { return css(STATUS_VARS[s.key] || "--c-7"); });
            charts.push(new Chart(document.getElementById("chartStatus"), {
                type: "doughnut",
                data: {
                    labels: rows.map(function (s) { return s.label; }),
                    datasets: [{
                        data: rows.map(function (s) { return s.count; }),
                        backgroundColor: colors,
                        borderColor: css("--card"),
                        borderWidth: 3,
                        hoverOffset: 8
                    }]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    cutout: "66%",
                    animation: { animateRotate: true, animateScale: true, duration: 1100 },
                    plugins: { legend: { display: false }, tooltip: tooltip() }
                }
            }));
            rows.forEach(function (s, i) {
                var li = document.createElement("li");
                var dot = document.createElement("span");
                dot.className = "dot";
                dot.style.background = colors[i];
                var name = document.createElement("span");
                name.textContent = s.label;
                var num = document.createElement("span");
                num.className = "num";
                num.textContent = s.count;
                li.appendChild(dot); li.appendChild(name); li.appendChild(num);
                legend.appendChild(li);
            });
        }

        // ---- 3. Top customers (horizontal bar) ----
        var custCard = document.getElementById("cardCustomers");
        if (!data.customers.labels.length) {
            custCard.classList.add("is-empty");
        } else {
            custCard.classList.remove("is-empty");
            charts.push(new Chart(document.getElementById("chartCustomers"), {
                type: "bar",
                data: {
                    labels: data.customers.labels,
                    datasets: [{
                        label: "Orders",
                        data: data.customers.values,
                        backgroundColor: css("--c-2"),
                        borderRadius: 6,
                        maxBarThickness: 26
                    }]
                },
                options: {
                    indexAxis: "y",
                    responsive: true,
                    maintainAspectRatio: false,
                    animation: { duration: 1000, easing: "easeOutQuart" },
                    plugins: { legend: { display: false }, tooltip: tooltip() },
                    scales: {
                        x: { beginAtZero: true, ticks: { precision: 0 } },
                        y: { grid: { display: false } }
                    }
                }
            }));
        }

        // ---- 4. Average progress (half-doughnut gauge) ----
        var avg = data.progress.average;
        var gauge = document.getElementById("chartProgress");
        if (gauge) {
            charts.push(new Chart(gauge, {
                type: "doughnut",
                data: {
                    datasets: [{
                        data: [avg, 100 - avg],
                        backgroundColor: [avg >= 100 ? css("--c-3") : css("--c-1"), css("--grid")],
                        borderWidth: 0,
                        borderRadius: 8
                    }]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    rotation: -90,
                    circumference: 180,
                    cutout: "74%",
                    animation: { duration: 1200, easing: "easeOutQuart" },
                    plugins: { legend: { display: false }, tooltip: { enabled: false } }
                }
            }));
        }
        var value = document.getElementById("gaugeValue");
        var note = document.getElementById("gaugeNote");
        if (value) { value.textContent = avg + "%"; }
        if (note) {
            var n = data.progress.tracked;
            note.textContent = n ? "across " + n + " tracked order" + (n === 1 ? "" : "s") : "no observations yet";
        }
    }

    build();
    document.addEventListener("themechange", function () {
        // wait one frame so the new CSS variables are applied
        window.requestAnimationFrame(build);
    });
})();
