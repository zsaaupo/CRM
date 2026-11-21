import { getJSON, patchJSON, postJSON } from "./api.js";
import { detailValue, initializeRecordDetail } from "./record_detail.js";

const find = (root, attr) => root.querySelector(`[data-return-${attr}]`);
const returnTypes = {
    sale: {
        order: "sales_order", result: "sales_return", originalQuantity: "sold_quantity",
        source: "sale", plural: "sales", status: "completed", warehouse: "Restocking warehouse",
    },
    purchase: {
        order: "purchase_order", result: "purchase_return", originalQuantity: "received_quantity",
        source: "purchase", plural: "purchases", status: "received", warehouse: "Receiving warehouse",
    },
};

function element(tag, text) {
    const node = document.createElement(tag);
    if (text != null) node.textContent = text;
    return node;
}

function messages(value) {
    if (Array.isArray(value)) return value.flatMap(messages);
    if (value && typeof value === "object") return Object.values(value).flatMap(messages);
    return typeof value === "string" ? [value] : [];
}

function feedback(status, text, tone = "info") {
    status.textContent = text;
    status.dataset.tone = tone;
    status.hidden = !text;
}

function returnError(error, fallback) {
    return messages(error.errors).join(" ") || error.message || fallback;
}

export function initializeReturnForm(form) {
    const type = returnTypes[form.dataset.returnType || "sale"];
    const itemField = `${type.order}_item`;
    const status = find(form, "status");
    const retry = find(form, "retry");
    const fields = find(form, "fields");
    const rows = find(form, "rows");
    const submit = find(form, "submit");
    const items = find(form, "items");
    const warehouse = find(form, "warehouse");
    const orderField = form.elements.namedItem(type.order);
    const dateField = form.elements.namedItem("return_date");
    const notes = form.elements.namedItem("notes");
    const csrf = form.elements.namedItem("csrfmiddlewaretoken").value;
    const editing = form.dataset.method === "PATCH";
    let orderLoaded = false;
    let busy = false;
    let locked = false;
    let version = 0;
    let quantities = [];

    function updateControls() {
        fields.disabled = busy || locked;
        orderField.disabled = editing;
        submit.disabled = busy || locked || !orderLoaded || !quantities.some(input => !input.disabled && Number(input.max) > 0);
        form.setAttribute("aria-busy", String(busy));
    }

    function appendOrder(order) {
        const option = element("option", order.label);
        option.value = order.value;
        orderField.append(option);
    }

    function showOrder(data, existing = []) {
        const saved = new Map(existing.map(line => [line[itemField], line.quantity]));
        warehouse.textContent = `${type.warehouse}: ${data.order.warehouse}`;
        warehouse.hidden = false;
        dateField.min = data.order.order_date;
        quantities = [];
        rows.replaceChildren();
        for (const item of data.lines) {
            const row = element("tr");
            for (const value of [item.line_number, `${item.product_name} (${item.product_sku})`, item.unit_code,
                item[type.originalQuantity], item.returned_quantity, item.returnable ? item.remaining_quantity : "Unavailable"]) {
                row.append(element("td", value));
            }
            const qty = element("input");
            qty.type = "number";
            qty.className = "form-input min-w-28";
            qty.min = "0";
            qty.step = "0.000001";
            qty.max = item.returnable ? item.remaining_quantity : "0";
            qty.value = saved.get(item.id) ?? "";
            qty.disabled = Number(qty.max) === 0 && !qty.value;
            qty.dataset.itemId = item.id;
            qty.setAttribute("aria-label", `Return quantity for line ${item.line_number}, ${item.product_name}`);
            const cell = element("td");
            cell.append(qty);
            row.append(cell);
            rows.append(row);
            quantities.push(qty);
        }
        items.hidden = !data.lines.length;
        orderLoaded = true;
        const available = quantities.some(input => Number(input.max) > 0);
        feedback(status, available ? "" : `This ${type.source} has no quantity available to return.`);
        updateControls();
    }

    async function loadOrder() {
        const requestVersion = ++version;
        orderLoaded = false;
        items.hidden = warehouse.hidden = true;
        dateField.min = "";
        retry.hidden = true;
        updateControls();
        if (!orderField.value) {
            feedback(status, "");
            return;
        }
        feedback(status, `Loading ${type.source} order items…`);
        try {
            const url = new URL(form.dataset.optionsUrl, window.location.origin);
            url.searchParams.set(type.order, orderField.value);
            const details = await getJSON(url.href);
            if (requestVersion === version) showOrder(details);
        } catch (error) {
            if (requestVersion !== version) return;
            feedback(status, returnError(error, `Could not load ${type.source} items.`), "error");
            retry.hidden = false;
        }
    }

    async function load() {
        busy = true;
        retry.hidden = true;
        updateControls();
        feedback(status, `Loading ${type.source} return…`);
        try {
            const record = editing ? await getJSON(form.dataset.detailUrl) : null;
            if (record?.is_completed) {
                locked = true;
                feedback(status, "This return is completed. Its quantities are read-only.", "error");
                return;
            }
            const data = await getJSON(form.dataset.optionsUrl);
            if (record) {
                orderField.replaceChildren();
                appendOrder(data.order);
                orderField.value = String(data.order.value);
                dateField.value = record.return_date;
                notes.value = record.notes;
                showOrder(data, record.items);
            } else {
                const placeholder = element("option", `Select ${type.status} ${type.source}`);
                placeholder.value = "";
                orderField.replaceChildren(placeholder);
                for (const order of data.orders) appendOrder(order);
                const preselected = form.dataset.selectedOrder;
                if (data.orders.some(order => String(order.value) === preselected)) {
                    orderField.value = preselected;
                    await loadOrder();
                } else {
                    feedback(status, data.orders.length ? "" : `No ${type.status} ${type.plural} from active warehouses are available.`);
                }
            }
        } catch (error) {
            feedback(status, returnError(error, "Could not load return options."), "error");
            retry.hidden = false;
        } finally {
            busy = false;
            updateControls();
        }
    }

    orderField.addEventListener("change", loadOrder);
    retry.addEventListener("click", () => !editing && orderField.value ? loadOrder() : load());
    form.addEventListener("submit", async event => {
        event.preventDefault();
        if (busy || locked || !orderLoaded || submit.disabled) return;
        const items = quantities.filter(input => !input.disabled && Number(input.value) > 0)
            .map(input => ({[itemField]: Number(input.dataset.itemId), quantity: input.value}));
        if (!items.length) {
            feedback(status, `Enter a return quantity for at least one ${type.source} line.`, "error");
            status.focus();
            return;
        }
        const payload = {return_date: dateField.value, notes: notes.value, items};
        if (!editing) payload[type.order] = orderField.value;
        busy = true;
        updateControls();
        feedback(status, "Saving return…");
        try {
            await (editing ? patchJSON : postJSON)(form.action, payload, csrf);
            window.location.assign(form.dataset.successUrl);
        } catch (error) {
            if (editing && error.status === 409) {
                try { locked = (await getJSON(form.dataset.detailUrl)).is_completed === true; }
                catch { /* Keep the original error if the return cannot be reloaded. */ }
            }
            feedback(status, locked ? "This return is completed. Its quantities are read-only." :
                returnError(error, "Could not save the return."), "error");
            status.focus();
        } finally {
            busy = false;
            updateControls();
        }
    });
    return load();
}

export function initializeReturnDetail(panel) {
    const type = returnTypes[panel.dataset.returnType || "sale"];
    const edit = find(panel, "edit");
    const complete = find(panel, "complete");
    const rows = find(panel, "lines");
    let record;
    if (complete) {
        const button = complete.querySelector('button[type="submit"]');
        const status = find(complete, "complete-status");
        complete.addEventListener("submit", async event => {
            event.preventDefault();
            if (button.disabled || record?.is_completed !== false) return;
            button.disabled = true;
            const csrf = complete.elements.namedItem("csrfmiddlewaretoken").value;
            try {
                const result = await postJSON(complete.action, {return_id: record.id, is_completed: true}, csrf);
                const completed = result?.[type.result];
                if (completed?.id !== record.id || completed.is_completed !== true) {
                    throw new Error("Could not confirm completion. Refresh the page before trying again.");
                }
                window.location.reload();
            } catch (error) {
                feedback(status, returnError(error, "Could not complete this return."), "error");
                status.focus();
                button.disabled = false;
            }
        });
    }
    return initializeRecordDetail(panel, current => {
        record = current;
        if (edit) edit.hidden = record.is_completed !== false;
        if (complete) complete.hidden = record.is_completed !== false;
        if (rows) {
            rows.replaceChildren();
            for (const item of record.items || []) {
                const row = element("tr");
                for (const value of [item.line_number, item.product_name, item.product_sku, item.unit_code, item.quantity]) {
                    row.append(element("td", detailValue(value)));
                }
                rows.append(row);
            }
        }
    });
}

const form = document.querySelector("[data-return-form]");
if (form) initializeReturnForm(form);
const detail = document.querySelector("[data-return-detail]");
if (detail) initializeReturnDetail(detail);
