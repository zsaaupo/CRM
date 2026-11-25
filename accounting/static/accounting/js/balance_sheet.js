const form = document.querySelector("[data-balance-filters]");
if (form) {
    let timer;
    let pending;
    const search = form.elements.search;

    async function refresh() {
        const url = new URL(form.action);
        url.search = new URLSearchParams(new FormData(form)).toString();
        const controller = new AbortController();
        pending = controller;
        try {
            const response = await fetch(url, {signal: controller.signal});
            const html = await response.text();
            const doc = new DOMParser().parseFromString(html, "text/html");
            const result = doc.querySelector("[data-balance-results]");
            if (!response.ok || !result) throw new Error("Report unavailable");
            if (controller.signal.aborted) return;
            document.querySelector("[data-balance-results]").replaceWith(result);
            history.replaceState(null, "", url);
        } catch (error) {
            if (!controller.signal.aborted) location.assign(url);
        }
    }

    function schedule(delay) {
        pending?.abort();
        clearTimeout(timer);
        timer = setTimeout(refresh, delay);
    }
    search.addEventListener("input", () => schedule(250));
    form.elements.limit.addEventListener("change", () => schedule(0));
    form.addEventListener("submit", event => {
        event.preventDefault();
        schedule(0);
    });
}
