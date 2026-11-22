import { getJSON, deleteJSON, postJSON } from "../../js/api.js";

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

export function catalogRow(record, columns, editUrlTemplate = "", onDelete = null, viewUrlTemplate = "", editDisabledField = "", onReceive = null, onComplete = null, deleteDisabledField = "", completeLabel = "sale") {
    const row = document.createElement("tr");
    row.dataset.recordId = String(record.id);
    for (const column of columns) {
        const cell = document.createElement("td");
        const value = record[column.key];
        if (column.key === "actions") {
            const actions = document.createElement("div");
            actions.className = "flex items-center gap-1";
            if (viewUrlTemplate) {
                const view = document.createElement("a");
                view.href = viewUrlTemplate.replace("/0/", `/${encodeURIComponent(record.id)}/`);
                view.className = column.view_label ? "text-button" : "icon-button";
                view.title = `View ${record.name || record.full_name || record.order_number || record.transfer_number || record.return_number || record.code || record.id}`;
                view.setAttribute("aria-label", view.title);
                if (column.view_label) {
                    view.textContent = column.view_label;
                    view.removeAttribute("aria-label");
                } else {
                    view.append(actionIcon("M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12ZM15 12a3 3 0 1 1-6 0 3 3 0 0 1 6 0Z"));
                }
                actions.append(view);
            }
            if (editUrlTemplate) {
                const disabled = Boolean(editDisabledField && record[editDisabledField] === true);
                const link = document.createElement(disabled ? "button" : "a");
                if (disabled) {
                    link.type = "button";
                    link.disabled = true;
                } else link.href = editUrlTemplate.replace("/0/", `/${encodeURIComponent(record.id)}/`);
                link.dataset.catalogEditAction = "";
                link.className = "icon-button";
                link.setAttribute("aria-label", `Edit ${record.name || record.full_name || record.order_number || record.transfer_number || record.return_number || record.code || record.id}`);
                link.title = `Edit ${record.name || record.full_name || record.order_number || record.transfer_number || record.return_number || record.code || record.id}`;
                link.append(actionIcon("m16 3 5 5-12 12-6 1 1-6L16 3ZM13 6l5 5"));
                actions.append(link);
            }
            if (onReceive) {
                const receive = document.createElement("button");
                receive.type = "button";
                receive.className = "icon-button inventory-receive-button";
                receive.dataset.catalogReceiveAction = "";
                receive.disabled = record.is_received !== false;
                receive.title = `${record.is_received === true ? "Received" : "Receive"} ${record.order_number || record.transfer_number || record.id}`;
                receive.setAttribute("aria-label", receive.title);
                receive.append(actionIcon("M4 12H3v8h18v-8h-1M3 12h5l2 3h4l2-3h5M6 7l-3 5M18 7l3 5M12 3v8m-3-3 3 3 3-3"));
                receive.addEventListener("click", () => {
                    if (!receive.disabled) return onReceive(record, receive);
                });
                actions.append(receive);
            }
            if (onComplete) {
                const complete = document.createElement("button");
                complete.type = "button";
                complete.className = "icon-button inventory-receive-button";
                complete.dataset.catalogCompleteAction = "";
                complete.disabled = record.is_completed !== false;
                complete.title = `${record.is_completed === true ? "Completed" : `Complete ${completeLabel}`} ${record.order_number || record.transfer_number || record.return_number || record.id}`;
                complete.setAttribute("aria-label", complete.title);
                complete.append(actionIcon("M4 4h16v16H4V4Zm4 8 3 3 5-6"));
                complete.addEventListener("click", () => {
                    if (!complete.disabled) return onComplete(record, complete);
                });
                actions.append(complete);
            }
            if (onDelete) {
                const remove = document.createElement("button");
                remove.type = "button";
                remove.className = "icon-button inventory-delete-button";
                remove.dataset.catalogDeleteAction = "";
                remove.disabled = Boolean(deleteDisabledField && record[deleteDisabledField] === true);
                remove.title = `Delete ${record.name || record.full_name || record.order_number || record.transfer_number || record.return_number || record.code || record.id}`;
                remove.setAttribute("aria-label", remove.title);
                remove.append(actionIcon("M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7M14 10v7"));
                remove.addEventListener("click", () => {
                    if (!remove.disabled) return onDelete(record, remove);
                });
                actions.append(remove);
            }
            cell.append(actions);
        } else if (column.type === "identity") {
            const identity = document.createElement("div");
            identity.className = "user-table-identity";
            const avatar = document.createElement("span");
            avatar.className = "user-avatar inventory-product-thumb";
            avatar.setAttribute("aria-hidden", "true");
            avatar.textContent = (record.name || record.full_name || "P").trim().slice(0, 1).toUpperCase();
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
        } else if (column.type === "boolean") {
            cell.textContent = value === true ? "Yes" : value === false ? "No" : "\u2014";
        } else if (column.type === "weekdays") {
            const weekdays = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
            cell.textContent = Array.isArray(value) && value.length
                ? weekdays.filter((day, index) => value.includes(index)).join(", ") : "\u2014";
        } else if (column.type === "datetime") {
            // Use the date and time supplied by the API so the browser's timezone cannot shift them.
            const timestamp = /^(\d{4}-\d{2}-\d{2})T(\d{2}):(\d{2})(:\d{2})?/.exec(value ?? "");
            if (timestamp) {
                const [, date, hours, minutes, seconds = ""] = timestamp;
                const hour = Number(hours) % 24;
                const clockSeconds = seconds === ":00" ? "" : seconds;
                const period = hour < 12 ? "AM" : "PM";
                cell.textContent = `${date} ${hour % 12 || 12}:${minutes}${clockSeconds} ${period}`;
            } else {
                cell.textContent = value || "\u2014";
            }
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
    let filterValues = [];
    let version = 0;
    let hasPrevious = false;
    let hasNext = false;
    let visibleCount = 0;
    const deleteDialog = find("delete-dialog");
    const deleteForm = find("delete-form");
    const receiveForm = find("receive-form");
    const receiving = new Set();
    const completeForm = find("complete-form");
    const filters = find("filters");
    const total = find("total");
    const completing = new Set();
    let pendingDelete = null;

    function renderRecord(record) {
        const row = catalogRow(record, columns, panel.dataset.editUrlTemplate,
            deleteDialog && panel.dataset.deleteUrlTemplate ? openDelete : null,
            panel.dataset.viewUrlTemplate, panel.dataset.editDisabledField,
            receiveForm && panel.dataset.receiveUrl ? receivePurchase : null,
            completeForm && panel.dataset.completeUrl ? completeRecord : null, panel.dataset.deleteDisabledField, panel.dataset.completeLabel || "sale");
        const button = row.querySelector("[data-catalog-receive-action]");
        if (button && receiving.has(String(record.id))) button.disabled = true;
        const completeButton = row.querySelector("[data-catalog-complete-action]");
        if (completeButton && completing.has(String(record.id))) completeButton.disabled = true;
        return row;
    }

    async function completeRecord(record, trigger) {
        const id = String(record.id);
        if (record.is_completed !== false || completing.has(id)) return;
        completing.add(id);
        trigger.disabled = true;
        trigger.setAttribute("aria-busy", "true");
        const feedback = find("feedback");
        feedback.hidden = true;
        try {
            const result = await postJSON(panel.dataset.completeUrl, { [panel.dataset.completeIdField || "sale_id"]: record.id, is_completed: true },
                completeForm.elements.namedItem("csrfmiddlewaretoken").value);
            const completed = result?.[panel.dataset.completeResultKey || "sales_order"];
            if (String(completed?.id) !== id || completed.is_completed !== true) {
                throw new Error("Could not confirm completion. Refresh the list before trying again.");
            }
            Object.assign(record, completed);
            const row = trigger.closest("tr");
            if (row?.isConnected) row.replaceWith(renderRecord(record));
            else await load(page);
            feedback.dataset.tone = "success";
            feedback.textContent = result.message || "Completed and stock updated.";
        } catch (error) {
            feedback.dataset.tone = "error";
            feedback.textContent = error.status === 403
                ? "Your session or permissions have changed. Refresh this page and try again."
                : error.status === 404 ? `This ${panel.dataset.completeLabel || "sale"} no longer exists. Refresh the list.`
                : error.errors && Object.keys(error.errors).length
                    ? Object.entries(error.errors).map(([name, value]) => `${name}: ${Array.isArray(value) ? value.join(" ") : value}`).join(" ")
                    : error.message || `Could not complete this ${panel.dataset.completeLabel || "sale"}. Please try again.`;
        } finally {
            completing.delete(id);
            trigger.removeAttribute("aria-busy");
            if (record.is_completed !== true) {
                trigger.disabled = false;
                if (!trigger.isConnected) await load(page);
            }
            feedback.hidden = false;
            feedback.focus();
        }
    }

    async function receivePurchase(record, trigger) {
        const id = String(record.id);
        if (record.is_received !== false || receiving.has(id)) return;
        receiving.add(id);
        trigger.disabled = true;
        trigger.setAttribute("aria-busy", "true");
        const feedback = find("feedback");
        feedback.hidden = true;
        try {
            const result = await postJSON(panel.dataset.receiveUrl, { purchase_id: record.id, is_received: true },
                receiveForm.elements.namedItem("csrfmiddlewaretoken").value);
            if (String(result?.purchase_order?.id) !== id || result.purchase_order.is_received !== true) {
                throw new Error("Could not confirm receipt. Refresh the list before trying again.");
            }
            Object.assign(record, result.purchase_order);
            const row = trigger.closest("tr");
            if (row?.isConnected) row.replaceWith(renderRecord(record));
            else await load(page);
            feedback.dataset.tone = "success";
            feedback.textContent = result.message || "Purchase received and stock updated.";
        } catch (error) {
            feedback.dataset.tone = "error";
            feedback.textContent = error.status === 403
                ? "Your session or permissions have changed. Refresh this page and try again."
                : error.status === 404 ? "This purchase no longer exists. Refresh the list."
                : error.errors && Object.keys(error.errors).length
                    ? Object.entries(error.errors).map(([name, value]) => `${name}: ${Array.isArray(value) ? value.join(" ") : value}`).join(" ")
                    : error.message || "Could not receive this purchase. Please try again.";
        } finally {
            receiving.delete(id);
            trigger.removeAttribute("aria-busy");
            if (record.is_received !== true) {
                trigger.disabled = false;
                if (!trigger.isConnected) await load(page);
            }
            feedback.hidden = false;
            feedback.focus();
        }
    }

    function openDelete(record, trigger) {
        pendingDelete = {record, trigger};
        find("delete-name").textContent = record.name || record.full_name || record.order_number || record.transfer_number || record.return_number || record.code || record.id;
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
        for (const [name, value] of filterValues) url.searchParams.set(name, value);
        panel.setAttribute("aria-busy", "true");
        find("status").textContent = "Loading records...";
        find("status").classList.remove("sr-only");
        find("status").hidden = false;
        for (const name of ["table", "empty", "error", "pagination", "count"]) find(name).hidden = true;
        if (total) total.hidden = true;
        find("previous").disabled = true;
        find("next").disabled = true;
        try {
            const data = await getJSON(url.href);
            if (current !== version) return;
            if (!Array.isArray(data.results)) throw new Error("Could not load records. Please try again.");
            visibleCount = data.results.length;
            find("rows").replaceChildren(...data.results.map(renderRecord));
            find("count").textContent = String(data.count);
            find("count").hidden = false;
            if (total && data.total_amount !== undefined) {
                total.textContent = `${total.dataset.catalogTotal} ${data.currency} ${data.total_amount}`;
                total.hidden = false;
            }
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
            const filterError = filters && error.status === 400 ? Object.values(error.errors || {}).flat().join(" ") : "";
            find("error-message").textContent = error.status === 403
                ? "You do not have permission to view this list. Contact your administrator."
                : filterError || error.message || "Could not load records. Please try again.";
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
    filters?.addEventListener("submit", (event) => {
        event.preventDefault();
        filterValues = [...filters.elements].filter((input) => input.name && input.value)
            .map((input) => [input.name, input.value]);
        load(1);
    });
    filters?.addEventListener("reset", () => {
        filterValues = [];
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
                find("feedback").textContent = `${record.name || record.full_name || record.order_number || record.transfer_number || record.return_number || record.code || record.id} deleted.`;
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
