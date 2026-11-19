document.addEventListener("DOMContentLoaded", function () {
    const form = document.getElementById("orderForm");
    if (!form) return;

    const customerSelect = document.getElementById("id_customer");
    const channelSelect = document.getElementById("id_channel");
    const baselineInput = document.getElementById("id_baseline_count");
    const increaseInput = document.getElementById("id_requested_increase");
    const preview = document.getElementById("targetPreview");
    const urlTemplate = form.dataset.channelsUrl;

    // When the customer changes, show only that customer's channels
    function loadChannels(customerId, keepValue) {
        channelSelect.innerHTML = "";
        const placeholder = document.createElement("option");
        placeholder.value = "";
        placeholder.textContent = customerId ? "Select channel" : "Select a customer first";
        channelSelect.appendChild(placeholder);
        if (!customerId) return;

        fetch(urlTemplate.replace("/0/", "/" + customerId + "/"))
            .then(function (res) { return res.json(); })
            .then(function (data) {
                data.channels.forEach(function (ch) {
                    const opt = document.createElement("option");
                    opt.value = ch.id;
                    opt.textContent = ch.name;
                    if (String(ch.id) === String(keepValue)) opt.selected = true;
                    channelSelect.appendChild(opt);
                });
                if (!data.channels.length) {
                    placeholder.textContent = "This customer has no active channel";
                }
            });
    }

    // Live target = baseline + requested increase
    function updatePreview() {
        const baseline = parseInt(baselineInput.value, 10);
        const increase = parseInt(increaseInput.value, 10);
        if (isNaN(baseline) || isNaN(increase) || increase < 1 || baseline < 0) {
            preview.textContent = "-";
            return;
        }
        preview.textContent = baseline + increase;
    }

    // Baseline helper: read the channel's current public count from YouTube
    const hint = document.getElementById("baselineHint");
    const countUrl = form.dataset.countUrl;
    let liveCount = null;

    function showHint(kind, text, withButton) {
        hint.hidden = false;
        hint.className = "baseline-hint baseline-" + kind;
        hint.textContent = text + " ";
        if (withButton) {
            const btn = document.createElement("button");
            btn.type = "button";
            btn.className = "btn btn-light btn-sm";
            btn.textContent = "Use " + liveCount.toLocaleString() + " as baseline";
            btn.addEventListener("click", function () {
                baselineInput.value = liveCount;
                baselineInput.dataset.auto = "1";
                updatePreview();
                checkBaseline();
            });
            hint.appendChild(btn);
        }
    }

    function checkBaseline() {
        if (liveCount === null) return;
        const typed = parseInt(baselineInput.value, 10);
        if (isNaN(typed)) { return; }
        const diff = Math.abs(typed - liveCount) / Math.max(liveCount, 1);
        if (diff > 0.1) {
            showHint("warn", "YouTube shows " + liveCount.toLocaleString() + " now, but the baseline is " +
                typed.toLocaleString() + ". A wrong baseline gives a wrong target.", true);
        } else {
            showHint("ok", "Baseline matches YouTube's current count (" + liveCount.toLocaleString() + ").", false);
        }
    }

    function loadLiveCount() {
        liveCount = null;
        hint.hidden = true;
        if (!channelSelect.value || !countUrl) return;
        fetch(countUrl.replace("/0/", "/" + channelSelect.value + "/"), { credentials: "same-origin" })
            .then(function (res) { return res.json(); })
            .then(function (data) {
                if (!data.ok || data.count === null) {
                    showHint("warn", data.message || "The current count could not be read. Enter the baseline by hand.", false);
                    return;
                }
                liveCount = data.count;
                const note = data.accuracy === "approximate" ? " (approximate: YouTube rounds large counts)" : "";
                const empty = baselineInput.value === "" || baselineInput.dataset.auto === "1";
                if (empty) {
                    baselineInput.value = liveCount;
                    baselineInput.dataset.auto = "1";
                    updatePreview();
                    showHint("ok", "Baseline filled from YouTube: " + liveCount.toLocaleString() + note + ".", false);
                } else {
                    checkBaseline();
                }
            })
            .catch(function () {});
    }

    channelSelect.addEventListener("change", loadLiveCount);
    baselineInput.addEventListener("input", function () {
        delete baselineInput.dataset.auto;
        checkBaseline();
    });

    customerSelect.addEventListener("change", function () {
        loadChannels(customerSelect.value, "");
    });
    baselineInput.addEventListener("input", updatePreview);
    increaseInput.addEventListener("input", updatePreview);

    loadChannels(customerSelect.value, channelSelect.value);
    updatePreview();
    if (channelSelect.value) { loadLiveCount(); }
});