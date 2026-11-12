import { getJSON, deleteJSON } from "../../js/api.js";

function actionIcon(pathData) {
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    for (const [name, value] of Object.entries({width: "18", height: "18", viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", "stroke-width": "1.7", "aria-hidden": "true"})) svg.setAttribute(name, value);
    const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
    path.setAttribute("d", pathData);
    path.setAttribute("stroke-linejoin", "round");
    path.setAttribute("stroke-linecap", "round");
    svg.append(path);
    return svg;
}

export function catalogRow(record, columns, editUrlTemplate = "", onDelete = null, stockUrlTemplate = "") {
    const row = document.createElement("tr");
    for (const column of columns) {
        const cell = document.createElement("td");
        const value = record[column.key];
        if (column.key === "actions") {
            const actions = document.createElement("div");
            actions.className = "flex items-center gap-1";
            if (stockUrlTemplate) {
                const stock = document.createElement("a");
                stock.href = stockUrlTemplate.replace("__warehouse__", encodeURIComponent(record.id));
                stock.className = "icon-button inventory-stock-button";
                stock.title = `Add stock to ${record.name || record.code}`;
                stock.setAttribute("aria-label", stock.title);
                stock.append(actionIcon("M12 5v14M5 12h14"));
                actions.append(stock);
            }
            if (editUrlTemplate) {
                const link = document.createElement("a");
                link.href = editUrlTemplate.replace("/0/", `/${encodeURIComponent(record.id)}/`);
                link.className = "icon-button";
                link.setAttribute("aria-label", `Edit ${record.name || record.code}`);
                link.title = `Edit ${record.name || record.code}`;
                link.append(actionIcon("m16 3 5 5-12 12-6 1 1-6L16 3ZM13 6l5 5"));
                actions.append(link);
            }
            if (onDelete) {
                const remove = document.createElement("button");
                remove.type = "button";
                remove.className = "icon-button inventory-delete-button";
                remove.title = `Delete ${record.name || record.code}`;
                remove.setAttribute("aria-label", remove.title);
                remove.append(actionIcon("M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7M14 10v7"));
                remove.addEventListener("click", () => onDelete(record, remove));
                actions.append(remove);
            }
            cell.append(actions);
        } else if (column.type === "identity") {
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
    let visibleCount = 0;
    const deleteDialog = find("delete-dialog");
    const deleteForm = find("delete-form");
    let pendingDelete = null;

    function openDelete(record, trigger) {
        pendingDelete = {record, trigger};
        find("delete-name").textContent = record.name || record.code;
        find("delete-error").hidden = true;
        deleteDialog.showModal();
        find("delete-cancel").focus();
    }

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
            visibleCount = data.results.length;
            find("rows").replaceChildren(...data.results.map((record) => catalogRow(record, columns, panel.dataset.editUrlTemplate, deleteDialog && panel.dataset.deleteUrlTemplate ? openDelete : null, panel.dataset.stockUrlTemplate)));
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
            if (error.status === 404 && requestedPage > 1) {
                await load(1);
                return;
            }
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
    if (deleteForm) {
        const button = find("delete-submit");
        const cancel = find("delete-cancel");
        const originalLabel = button.textContent;
        const csrf = deleteForm.elements.namedItem("csrfmiddlewaretoken").value;
        cancel.addEventListener("click", () => deleteDialog.close());
        deleteDialog.addEventListener("cancel", (event) => {
            if (deleteForm.dataset.busy === "true") event.preventDefault();
        });
        deleteDialog.addEventListener("close", () => {
            if (pendingDelete?.trigger.isConnected) pendingDelete.trigger.focus();
        });
        deleteForm.addEventListener("submit", async (event) => {
            event.preventDefault();
            if (!pendingDelete || deleteForm.dataset.busy === "true") return;
            const record = pendingDelete.record;
            const currentPage = page;
            const nextPage = visibleCount === 1 && currentPage > 1 ? currentPage - 1 : currentPage;
            deleteForm.dataset.busy = "true";
            deleteForm.setAttribute("aria-busy", "true");
            button.disabled = true;
            cancel.disabled = true;
            button.textContent = "Deleting...";
            find("delete-error").hidden = true;
            try {
                const url = panel.dataset.deleteUrlTemplate.replace("/0/", `/${encodeURIComponent(record.id)}/`);
                await deleteJSON(url, csrf);
                deleteDialog.close();
                find("feedback").dataset.tone = "success";
                find("feedback").textContent = `${record.name || record.code} deleted.`;
                find("feedback").hidden = false;
                await load(nextPage);
                find("feedback").focus();
            } catch (error) {
                if (error.status === 401) {
                    window.location.replace(panel.dataset.loginUrl);
                    return;
                }
                find("delete-error").textContent = error.status === 403
                    ? "Your session or permissions have changed. Refresh this page and try again."
                    : error.status === 404 ? "This record no longer exists. Cancel and refresh the list."
                    : error.message || "Could not delete this record. Please try again.";
                find("delete-error").hidden = false;
            } finally {
                deleteForm.dataset.busy = "false";
                deleteForm.setAttribute("aria-busy", "false");
                button.disabled = false;
                cancel.disabled = false;
                button.textContent = originalLabel;
            }
        });
    }
    load();
}
