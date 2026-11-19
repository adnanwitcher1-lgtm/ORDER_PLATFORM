document.addEventListener("DOMContentLoaded", function () {
    "use strict";
    var form = document.getElementById("channelForm");
    if (!form || form.dataset.enabled !== "1") { return; }

    var urlInput = document.getElementById("id_url");
    var nameInput = document.getElementById("id_name");
    var idInput = document.getElementById("id_youtube_channel_id");
    var button = document.getElementById("lookupBtn");
    var box = document.getElementById("lookupResult");
    var lastQuery = "";

    function show(kind, text) {
        box.hidden = false;
        box.className = "lookup-result lookup-" + kind;
        box.textContent = text;
    }

    function lookup() {
        var query = urlInput.value.trim();
        if (!query || query === lastQuery) { return; }
        lastQuery = query;
        show("info", "Looking up the channel on YouTube...");
        var url = form.dataset.lookupUrl + "?q=" + encodeURIComponent(query) +
            (form.dataset.exclude ? "&exclude=" + encodeURIComponent(form.dataset.exclude) : "");
        fetch(url, { credentials: "same-origin" })
            .then(function (res) { return res.json(); })
            .then(function (data) {
                if (!data.ok) { show("error", data.error); return; }
                var ch = data.channel;
                if (!nameInput.value.trim()) { nameInput.value = ch.title; }
                if (!idInput.value.trim()) { idInput.value = ch.id; }
                var subs = ch.subscribers_hidden ? "hidden" :
                    (ch.subscribers === null ? "unknown" : ch.subscribers.toLocaleString() +
                    (ch.accuracy === "approximate" ? " (approximate, YouTube rounds it)" : ""));
                var text = ch.title + (ch.handle ? " (" + ch.handle + ")" : "") + " - subscribers: " + subs +
                    ", videos: " + (ch.video_count === null ? "?" : ch.video_count.toLocaleString());
                if (data.existing) {
                    show("error", text + ". Already added as '" + data.existing + "'.");
                } else {
                    show("ok", text);
                }
            })
            .catch(function () { show("error", "Could not reach the server. Try again."); });
    }

    urlInput.addEventListener("change", lookup);
    urlInput.addEventListener("blur", lookup);
    button.addEventListener("click", function () { lastQuery = ""; lookup(); });
});
