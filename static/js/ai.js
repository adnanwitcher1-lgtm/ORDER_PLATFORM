(function () {
    "use strict";

    function csrfToken() {
        var field = document.querySelector("[name=csrfmiddlewaretoken]");
        return field ? field.value : "";
    }

    function wire(button) {
        var scope = button.getAttribute("data-scope");
        var id = button.getAttribute("data-id");
        var card = button.closest(".card");
        var box = card.querySelector(".ai-summary-box");
        var select = card.querySelector(".ai-lang");
        var url = "/ai/summary/" + scope + "/" + (id ? id + "/" : "");

        button.addEventListener("click", function () {
            var original = button.textContent;
            button.disabled = true;
            button.textContent = "Working...";
            var body = new URLSearchParams();
            body.set("lang", select ? select.value : "en");
            fetch(url, {
                method: "POST",
                headers: { "X-CSRFToken": csrfToken(), "Content-Type": "application/x-www-form-urlencoded" },
                body: body.toString(),
                credentials: "same-origin"
            }).then(function (r) { return r.json(); }).then(function (data) {
                box.hidden = false;
                box.textContent = data.summary || data.error || "No summary available.";
                var small = document.createElement("small");
                small.textContent = (data.source === "ai" ? "Written with AI from the order facts." : "Generated from the order data.") +
                    (data.note ? " " + data.note : "");
                box.appendChild(small);
            }).catch(function () {
                box.hidden = false;
                box.textContent = "Could not create a summary right now.";
            }).finally(function () {
                button.disabled = false;
                button.textContent = original;
            });
        });
    }

    document.addEventListener("DOMContentLoaded", function () {
        document.querySelectorAll(".ai-summary-btn").forEach(wire);
    });
})();
