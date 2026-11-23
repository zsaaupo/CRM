import { getJSON, postJSON, patchJSON } from "../../js/api.js";

const headerFields = ["supplier", "warehouse", "supplier_reference", "order_date", "expected_date", "currency", "exchange_rate", "status", "shipping_cost", "notes", "payment_account"];
const lineFields = ["product", "unit", "quantity", "unit_price", "discount_amount", "tax_rate", "description"];

export function purchasePayload(form) {
    const payload = Object.fromEntries(headerFields.map((name) => [name, form.elements.namedItem(name).value]));
    payload.expected_date ||= null;
    payload.supplier ||= null;
    payload.warehouse ||= null;
    payload.items = [...form.querySelectorAll("[data-purchase-line]")].map((row, index) => ({
        line_number: index + 1,
        ...(row.dataset.itemId ? {id: Number(row.dataset.itemId)} : {}),
        ...Object.fromEntries(lineFields.map((name) => [name, row.querySelector(`[data-line-field="${name}"]`).value])),
    }));
    return payload;
}

function errorText(value) {
    if (Array.isArray(value)) return value.map(errorText).join(" ");
    if (value && typeof value === "object") return Object.values(value).map(errorText).join(" ");
    return String(value ?? "");
}

export function initializePurchaseForm(form) {
    const find = (name) => form.querySelector(`[data-purchase-${name}]`);
    const status = find("status");
    const header = find("header");
    const submit = find("submit");
    const addButton = find("add-line");
    const retry = find("retry");
    const lines = find("lines");
    const spinner = form.querySelector("[data-submit-spinner]");
    const label = form.querySelector("[data-submit-label]");
    const field = (name) => form.elements.namedItem(name);
    const isEdit = form.dataset.method === "PATCH";
    const saveLabel = label.textContent;
    let originalSupplier = null;
    let readOnly = false;
    let options = null;
    let ready = false;
    let loading = false;
    let busy = false;
    let lineId = 0;

    function message(text, tone = "error") {
        status.dataset.tone = tone;
        status.textContent = text;
        status.hidden = false;
    }

    function choices(select, records, placeholder) {
        const first = document.createElement("option");
        first.value = "";
        first.textContent = placeholder;
        select.replaceChildren(first);
        for (const record of records) {
            const option = document.createElement("option");
            option.value = String(record.value);
            option.textContent = record.label;
            select.append(option);
        }
    }

    function updatePaymentAccounts(preferred = field("payment_account").value) {
        const select = field("payment_account");
        const warehouse = String(field("warehouse").value);
        const eligible = options.payment_account.filter(account => account.warehouse == null || String(account.warehouse) === warehouse);
        choices(select, eligible, eligible.length ? "Select payment account" : "No payment account for this warehouse");
        select.value = eligible.some(account => String(account.value) === String(preferred)) ? String(preferred) : "";
    }

    function updateLineNumbers() {
        const rows = [...lines.querySelectorAll("[data-purchase-line]")];
        rows.forEach((row, index) => {
            const title = `Item ${index + 1}`;
            row.querySelector("[data-line-heading]").textContent = title;
            row.querySelector("[data-line-legend]").textContent = title;
            const remove = row.querySelector("[data-line-remove]");
            remove.disabled = busy || rows.length === 1;
            remove.setAttribute("aria-label", `Remove item ${index + 1}`);
        });
    }

    function addLine(focus = false, item = null) {
        if (!ready || busy) return;
        const row = document.getElementById("purchase-line-template").content.firstElementChild.cloneNode(true);
        const prefix = `purchase-item-${++lineId}`;
        for (const input of row.querySelectorAll("[data-line-field], [data-line-unit-label]")) {
            const name = input.dataset.lineField || "unit";
            if (name === "unit" && input.type === "hidden") continue;
            input.id = `${prefix}-${name}`;
            input.setAttribute("aria-describedby", `${input.id}-error`);
            row.querySelector(`[data-line-label="${name}"]`).setAttribute("for", input.id);
            row.querySelector(`[data-line-error="${name}"]`).id = `${input.id}-error`;
        }
        const product = row.querySelector('[data-line-field="product"]');
        choices(product, options.product, "Select a product");
        product.addEventListener("change", () => {
            const selected = options.product.find((record) => String(record.value) === product.value);
            row.querySelector('[data-line-field="unit"]').value = selected?.unit ?? "";
            row.querySelector("[data-line-unit-label]").value = selected?.unit_label ?? "";
            row.querySelector('[data-line-field="unit_price"]').value = selected?.cost_price ?? "";
        });
        row.querySelector("[data-line-remove]").addEventListener("click", () => {
            if (busy || lines.children.length <= 1) return;
            row.remove();
            updateLineNumbers();
            addButton.focus();
        });
        if (item) {
            row.dataset.itemId = String(item.id);
            for (const name of lineFields) row.querySelector(`[data-line-field="${name}"]`).value = item[name] ?? "";
            row.querySelector('[data-line-unit-label]').value = item.unit_code || item.unit_name || "";
        }
        lines.append(row);
        updateLineNumbers();
        if (focus) product.focus();
    }

    function updateCurrency() {
        const currency = options.currency.find((record) => String(record.value) === field("currency").value);
        const base = field("currency").value === options.base_currency;
        field("exchange_rate").value = base ? "1.00000000" : currency?.exchange_rate ?? "";
        field("exchange_rate").readOnly = base;
    }

    async function loadItems() {
        const items = [];
        if (!isEdit) return items;
        const url = new URL(form.dataset.itemsUrl, window.location.origin);
        let page = 1;
        while (true) {
            url.searchParams.set("page", String(page++));
            const data = await getJSON(url.href);
            if (!Array.isArray(data.results)) throw new Error("Could not load purchase items. Please try again.");
            items.push(...data.results);
            if (!data.next) return items;
        }
    }

    async function load() {
        if (loading || busy) return;
        loading = true;
        ready = false;
        readOnly = false;
        header.disabled = submit.disabled = addButton.disabled = true;
        retry.hidden = true;
        form.setAttribute("aria-busy", "true");
        message("Loading purchase choices...", "info");
        try {
            const [data, record, items] = await Promise.all([
                getJSON(form.dataset.optionsUrl),
                isEdit ? getJSON(form.dataset.detailUrl) : null,
                loadItems(),
            ]);
            if (isEdit && !record?.id) throw new Error("Could not load this purchase. Return to the purchase list.");
            if (record?.is_received === true) {
                readOnly = true;
                throw new Error("This purchase has been received and is read-only. Return to the purchase list.");
            }
            if (!Array.isArray(data.payment_account)) throw new Error("Could not load payment accounts. Try again.");
            for (const name of ["currency", "status", "product"]) {
                if (!Array.isArray(data[name])) throw new Error("Could not load purchase choices. Please try again.");
                if (!data[name].length) throw new Error(`No ${name} records are available. Add the missing records or contact your administrator, then try again.`);
            }
            options = data;
            if (!Array.isArray(options.supplier)) throw new Error('Could not load suppliers. Please try again.');
            choices(field("supplier"), options.supplier, "No supplier");
            if (!Array.isArray(options.warehouse)) throw new Error('Could not load warehouses. Please try again.');
            choices(field("warehouse"), options.warehouse, "Choose a warehouse before receiving");
            field('warehouse').disabled = options.warehouse_scope?.all_warehouses === false;
            if (field('warehouse').disabled) {
                field('warehouse').value = String(options.warehouse_scope.warehouse);
                if (!options.warehouse.some(row => String(row.value) === field('warehouse').value) && !record) {
                    throw new Error('Your assigned warehouse is unavailable. Contact an administrator.');
                }
            }
            choices(field("currency"), options.currency, "Select currency");
            choices(field("status"), options.status, "Select status");
            field("currency").value = options.currency.some((row) => row.value === options.base_currency) ? options.base_currency : "";
            field("status").value = options.status.some((row) => row.value === "draft") ? "draft" : "";
            updateCurrency();
            if (record) {
                originalSupplier = record.supplier;
                for (const [name, records, text] of [["supplier", options.supplier, record.supplier_name],
                                                    ["warehouse", options.warehouse, record.warehouse_name]]) {
                    if (record[name] && !records.some((row) => String(row.value) === String(record[name]))) {
                        const historical = document.createElement("option");
                        historical.value = String(record[name]);
                        historical.textContent = `${text || name} (existing selection)`;
                        field(name).append(historical);
                    }
                }
                for (const name of headerFields) field(name).value = record[name] ?? "";
                field("exchange_rate").readOnly = record.currency === options.base_currency;
            }
            updatePaymentAccounts(record?.payment_account ?? "");
            field("expected_date").min = field("order_date").value;
            ready = true;
            if (isEdit) {
                lines.replaceChildren();
                for (const item of items) addLine(false, item);
            }
            if (!lines.children.length) addLine();
            header.disabled = submit.disabled = addButton.disabled = false;
            status.hidden = true;
        } catch (error) {
            message(error.status === 403 ? "Your session or permissions have changed. Refresh this page and try again."
                : error.message || "Could not load purchase choices. Please try again.");
            retry.hidden = readOnly;
        } finally {
            loading = false;
            form.setAttribute("aria-busy", "false");
        }
    }

    function showErrors(errors) {
        for (const [name, value] of Object.entries(errors || {})) {
            if (name === "items" && Array.isArray(value)) {
                const rows = [...lines.querySelectorAll("[data-purchase-line]")];
                value.forEach((itemErrors, index) => {
                    if (!rows[index] || !itemErrors || typeof itemErrors !== "object") return;
                    for (const [key, detail] of Object.entries(itemErrors)) {
                        const box = rows[index].querySelector(`[data-line-error="${key}"]`)
                            || rows[index].querySelector('[data-line-error="non_field_errors"]');
                        box.textContent = errorText(detail);
                        box.hidden = false;
                        rows[index].querySelector(`[data-line-field="${key}"]`)?.setAttribute("aria-invalid", "true");
                    }
                });
            } else {
                const box = [...form.querySelectorAll("[data-error-for]")].find((node) => node.dataset.errorFor === name);
                if (box) {
                    box.textContent = errorText(value);
                    box.hidden = false;
                    field(name).setAttribute("aria-invalid", "true");
                }
            }
        }
    }

    retry.addEventListener("click", load);
    addButton.addEventListener("click", () => addLine(true));
    field("currency").addEventListener("change", updateCurrency);
    field("warehouse").addEventListener("change", () => updatePaymentAccounts());
    field("order_date").addEventListener("change", () => { field("expected_date").min = field("order_date").value; });
    field("expected_date").min = field("order_date").value;
    form.addEventListener("input", (event) => { event.target.removeAttribute("aria-invalid"); });
    form.addEventListener("submit", async (event) => {
        event.preventDefault();
        if (!ready || busy) return;
        status.hidden = true;
        for (const box of form.querySelectorAll("[data-error-for], [data-line-error]")) box.hidden = true;
        for (const input of form.querySelectorAll('[aria-invalid="true"]')) input.removeAttribute("aria-invalid");
        if (!form.reportValidity()) return;
        if (!lines.children.length) {
            message("Add at least one product.");
            return;
        }
        const payload = purchasePayload(form);
        if (isEdit && String(payload.supplier ?? "") === String(originalSupplier ?? "")) delete payload.supplier;
        busy = true;
        header.disabled = submit.disabled = addButton.disabled = true;
        for (const row of lines.children) row.disabled = true;
        form.setAttribute("aria-busy", "true");
        label.textContent = isEdit ? "Saving changes..." : "Creating purchase...";
        spinner.hidden = false;
        try {
            if (isEdit) await patchJSON(form.action, payload, field("csrfmiddlewaretoken").value);
            else await postJSON(form.action, payload, field("csrfmiddlewaretoken").value);
            window.location.assign(form.dataset.successUrl);
        } catch (error) {
            if (isEdit && error.status === 409) {
                try { readOnly = (await getJSON(form.dataset.detailUrl)).is_received === true; }
                catch { /* Keep the original API error if the session has changed. */ }
                if (readOnly) ready = false;
            }
            showErrors(error.errors);
            message(readOnly ? "This purchase has been received and is read-only. Return to the purchase list."
                : error.status === 403 ? "Your session or permissions have changed. Refresh this page and try again."
                : error.status === 404 ? "This purchase could not be found. Return to the purchase list."
                : error.status === 400 ? `Please check the purchase details. ${errorText(error.errors)}`
                : error.message || "Could not save the purchase. Please try again.");
            status.focus();
        } finally {
            busy = false;
            header.disabled = submit.disabled = addButton.disabled = readOnly;
            for (const row of lines.children) row.disabled = readOnly;
            updateLineNumbers();
            form.setAttribute("aria-busy", "false");
            label.textContent = saveLabel;
            spinner.hidden = true;
        }
    });
    return { ready: load() };
}

const form = document.querySelector("[data-purchase-form]");
if (form) initializePurchaseForm(form);
