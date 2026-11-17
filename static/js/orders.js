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

    customerSelect.addEventListener("change", function () {
        loadChannels(customerSelect.value, "");
    });
    baselineInput.addEventListener("input", updatePreview);
    increaseInput.addEventListener("input", updatePreview);

    loadChannels(customerSelect.value, channelSelect.value);
    updatePreview();
});