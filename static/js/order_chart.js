(function () {
    "use strict";
    if (typeof Chart === "undefined") { return; }
    var dataEl = document.getElementById("order-chart-data");
    var canvas = document.getElementById("chartGrowth");
    if (!dataEl || !canvas) { return; }
    var data = JSON.parse(dataEl.textContent);
    var chart = null;

    function css(name) {
        return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    }

    function build() {
        if (chart) { chart.destroy(); }
        Chart.defaults.color = css("--muted");
        Chart.defaults.borderColor = css("--grid");
        Chart.defaults.font.family = '"Segoe UI", Roboto, Arial, sans-serif';
        var n = data.labels.length;
        var targetLine = [];
        for (var i = 0; i < n; i++) { targetLine.push(data.target); }

        var datasets = [
            {
                label: "Observed count",
                data: data.values,
                borderColor: css("--c-1"),
                backgroundColor: css("--c-1") + "26",
                pointBackgroundColor: css("--c-1"),
                pointBorderColor: css("--card"),
                pointBorderWidth: 2,
                pointRadius: 4,
                borderWidth: 3,
                tension: 0.25,
                fill: true,
                spanGaps: false
            },
            {
                label: "Target (" + data.target.toLocaleString() + ")",
                data: targetLine,
                borderColor: css("--c-3"),
                borderDash: [6, 6],
                borderWidth: 2,
                pointRadius: 0,
                fill: false
            }
        ];
        var hasProjection = data.projection.some(function (v) { return v !== null; });
        if (hasProjection) {
            datasets.splice(1, 0, {
                label: "Estimate",
                data: data.projection,
                borderColor: css("--c-4"),
                borderDash: [3, 5],
                borderWidth: 2,
                pointRadius: 3,
                pointBackgroundColor: css("--c-4"),
                fill: false,
                spanGaps: true
            });
        }

        chart = new Chart(canvas, {
            type: "line",
            data: { labels: data.labels, datasets: datasets },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                animation: { duration: 1100, easing: "easeOutQuart" },
                interaction: { mode: "index", intersect: false },
                plugins: {
                    legend: { position: "bottom", labels: { usePointStyle: true, boxWidth: 8 } },
                    tooltip: {
                        backgroundColor: css("--sidebar-bg"),
                        titleColor: "#ffffff",
                        bodyColor: "#e2e8f0",
                        padding: 10,
                        cornerRadius: 8
                    }
                },
                scales: {
                    x: { grid: { display: false }, ticks: { maxRotation: 0, autoSkip: true, maxTicksLimit: 7 } },
                    y: { ticks: { precision: 0 } }
                }
            }
        });
    }

    build();
    document.addEventListener("themechange", function () { window.requestAnimationFrame(build); });
})();
