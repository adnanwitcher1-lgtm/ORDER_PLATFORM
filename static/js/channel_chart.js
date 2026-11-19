(function () {
    "use strict";
    if (typeof Chart === "undefined") { return; }
    var dataEl = document.getElementById("channel-chart-data");
    var canvas = document.getElementById("chartVideos");
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
        chart = new Chart(canvas, {
            type: "bar",
            data: {
                labels: data.labels,
                datasets: [{
                    label: "Views",
                    data: data.values,
                    backgroundColor: css("--c-1"),
                    borderRadius: 4,
                    maxBarThickness: 28
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                animation: { duration: 900, easing: "easeOutQuart" },
                plugins: {
                    legend: { display: false },
                    tooltip: {
                        backgroundColor: css("--sidebar-bg"),
                        titleColor: "#ffffff",
                        bodyColor: "#e2e8f0",
                        padding: 10,
                        cornerRadius: 8,
                        callbacks: {
                            title: function (items) {
                                var i = items[0].dataIndex;
                                return data.titles[i].slice(0, 70) + " (" + data.labels[i] + ")";
                            },
                            label: function (item) { return item.parsed.y.toLocaleString() + " views"; }
                        }
                    }
                },
                scales: {
                    x: { grid: { display: false }, ticks: { maxRotation: 0, autoSkip: true, maxTicksLimit: 8 } },
                    y: { beginAtZero: true, ticks: { precision: 0 } }
                }
            }
        });
    }

    build();
    document.addEventListener("themechange", function () { window.requestAnimationFrame(build); });
})();
