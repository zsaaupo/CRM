import { getJSON } from "../../js/api.js";

export function catalogRow(record, columns) {
    const row = document.createElement("tr");
    for (const column of columns) {
        const cell = document.createElement("td");
        const value = record[column.key];
        if (column.type === "identity") {
            const identity = document.createElement("div");
            identity.className = "user-table-identity";
            const avatar = document.createElement("span");
            avatar.className = "user-avatar inventory-product-thumb";
            avatar.setAttribute("aria-hidden", "true");
            avatar.textContent = (record.name || "P").trim().slice(0, 1).toUpperCase();
            if (record.image) {
                const image = document.createElement("img");
                image.className = "avatar-image";
                image.alt = "";
                image.loading = "lazy";
                image.addEventListener("error", () => image.remove(), { once: true });
                image.src = record.image;
                avatar.append(image);
            }
            const name = document.createElement("span");
            name.textContent = value || "Unnamed product";
            identity.append(avatar, name);
            cell.append(identity);
        } else if (column.type === "status") {
            const badge = document.createElement("span");
            const active = value === true || value === "active";
            badge.className = `user-status-badge ${active ? "is-active" : "is-inactive"}`;
            badge.textContent = typeof value === "boolean" ? (value ? "Active" : "Inactive")
                : String(value || "Not set").replaceAll("-", " ").replaceAll("_", " ");
            cell.append(badge);
        } else {
            cell.textContent = column.type === "price" ? `${record.currency || ""} ${value ?? "0"}`.trim()
                : value === null || value === undefined || value === "" ? "\u2014" : String(value);
        }
        row.append(cell);
    }
    return row;
}

const panel = document.querySelector("[data-inventory-list]");
if (panel) {
    const find = (name) => panel.querySelector(`[data-catalog-${name}]`);
    const columns = JSON.parse(document.getElementById("catalog-columns").textContent);
    let page = 1;
    let query = "";
    let version = 0;
    let hasPrevious = false;
    let hasNext = false;

    async function load(requestedPage = 1) {
        const current = ++version;
        page = requestedPage;
        const url = new URL(panel.dataset.apiUrl, window.location.origin);
        url.searchParams.set("page", String(page));
        if (query) url.searchParams.set("q", query);
        panel.setAttribute("aria-busy", "true");
        find("status").textContent = "Loading records...";
        find("status").classList.remove("sr-only");
        find("status").hidden = false;
        for (const name of ["table", "empty", "error", "pagination", "count"]) find(name).hidden = true;
        find("previous").disabled = true;
        find("next").disabled = true;
        try {
            const data = await getJSON(url.href);
            if (current !== version) return;
            if (!Array.isArray(data.results)) throw new Error("Could not load records. Please try again.");
            find("rows").replaceChildren(...data.results.map((record) => catalogRow(record, columns)));
            find("count").textContent = String(data.count);
            find("count").hidden = false;
            find("table").hidden = data.results.length === 0;
            find("empty").hidden = data.results.length !== 0;
            find("status").textContent = `${data.count} records found.`;
            find("status").classList.add("sr-only");
            hasPrevious = Boolean(data.previous);
            hasNext = Boolean(data.next);
            find("previous").disabled = !hasPrevious;
            find("next").disabled = !hasNext;
            find("pagination").hidden = data.results.length === 0;
            const start = (page - 1) * 20 + 1;
            find("range").textContent = `Showing ${start}\u2013${start + data.results.length - 1} of ${data.count}`;
        } catch (error) {
            if (current !== version) return;
            if (error.status === 401) {
                window.location.replace(panel.dataset.loginUrl);
                return;
            }
            find("status").hidden = true;
            find("error-message").textContent = error.status === 403
                ? "You do not have permission to view this list. Contact your administrator."
                : error.message || "Could not load records. Please try again.";
            find("error").hidden = false;
        } finally {
            if (current === version) panel.setAttribute("aria-busy", "false");
        }
    }

    find("search").addEventListener("submit", (event) => {
        event.preventDefault();
        query = event.currentTarget.elements.namedItem("q").value.trim();
        load(1);
    });
    find("previous").addEventListener("click", () => { if (hasPrevious) load(page - 1); });
    find("next").addEventListener("click", () => { if (hasNext) load(page + 1); });
    find("retry").addEventListener("click", () => load(page));
    load();
}
