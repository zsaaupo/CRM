import { getJSON } from "./api.js";

export function detailValue(value, kind = "", currency = "") {
    if (value === null || value === undefined || value === "") return "\u2014";
    if (kind === "money") return `${currency} ${value}`.trim();
    if (kind === "percent") return `${value}%`;
    if (kind === "boolean") return value ? "Yes" : "No";
    if (kind === "json") return JSON.stringify(value, null, 2);
    if (kind === "datetime") {
        const date = new Date(value);
        return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString();
    }
    return String(value);
}

export function initializeRecordDetail(panel, onLoad = () => {}) {
    const find = (name) => panel.querySelector(`[data-record-${name}]`);
    const retry = find("retry");
    let loading = false;
    const image = find("image");
    if (image) image.addEventListener("error", () => { find("image-wrapper").hidden = true; });

    async function load() {
        if (loading) return;
        loading = true;
        retry.disabled = true;
        panel.setAttribute("aria-busy", "true");
        find("content").hidden = true;
        find("error").hidden = true;
        find("status").hidden = false;
        try {
            const record = await getJSON(panel.dataset.apiUrl);
            if (!record || !record.id) throw new Error("Could not load this record. Please try again.");
            for (const node of panel.querySelectorAll("[data-detail-field]")) {
                node.textContent = detailValue(record[node.dataset.detailField], node.dataset.detailKind, record.currency);
            }
            find("title").textContent = record[panel.dataset.titleField] || "Details";
            if (image) {
                find("image-wrapper").hidden = !record.image;
                if (record.image) {
                    image.alt = record.name || "Product";
                    image.src = record.image;
                } else image.removeAttribute("src");
            }
            find("content").hidden = false;
            find("status").hidden = true;
            await onLoad(record);
        } catch (error) {
            if (error.status === 401) {
                window.location.replace(panel.dataset.loginUrl);
                return;
            }
            find("content").hidden = true;
            find("status").hidden = true;
            find("error-message").textContent = error.status === 404
                ? "This record could not be found. It may have been deleted. Return to the list."
                : error.status === 403 ? "You do not have permission to view this record, or your session has changed. Contact your administrator or sign in again."
                : error.message || "Could not load details. Please try again.";
            find("error").hidden = false;
        } finally {
            loading = false;
            retry.disabled = false;
            panel.setAttribute("aria-busy", "false");
        }
    }

    retry.addEventListener("click", load);
    return { ready: load() };
}
