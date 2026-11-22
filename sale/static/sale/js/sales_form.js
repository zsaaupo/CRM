import { getJSON, postJSON, patchJSON } from "../../js/api.js";

const headerFields = ["customer", "customer_reference", "warehouse", "billing_address", "shipping_address", "order_date", "expected_delivery_date", "currency", "exchange_rate", "status", "shipping_cost", "notes", "payment_account"];
const quotationFields = [...headerFields.filter(name => name !== "status" && name !== "payment_account"), "valid_until", "terms"];
const lineFields = ["product", "unit", "quantity", "unit_price", "discount_amount", "tax_rate", "description"];

export function salesPayload(form) {
    const names = form.dataset?.documentType === "quotation" ? quotationFields : headerFields;
    const payload = Object.fromEntries(names.map((name) => [name, form.elements.namedItem(name).value]));
    payload.expected_delivery_date ||= null;
    payload.customer ||= null;
    payload.warehouse ||= null;
    payload.items = [...form.querySelectorAll("[data-sale-line]")].map((row, index) => ({
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

export function initializeSalesForm(form) {
    const quotation = form.dataset.documentType === "quotation";
    const documentName = quotation ? "quotation" : "sale";
    const lockedMessage = quotation ? "This quotation has been issued and is read-only." :
        "This sale has been completed and is read-only. Return to the sales list.";
    const isLocked = (record) => quotation ? record?.is_locked === true : record?.is_completed === true;
    const find = (name) => form.querySelector(`[data-sale-${name}]`);
    const status = find("status");
    const header = find("header");
    const submit = find("submit");
    const addButton = find("add-line");
    const retry = find("retry");
    const lines = find("lines");
    const spinner = form.querySelector("[data-submit-spinner]");
    const label = form.querySelector("[data-submit-label]");
    const field = (name) => form.elements.namedItem(name);
    const isEdit = form.dataset.method === 'PATCH';
    const saveLabel = label.textContent;
    let originalCustomer = null;
    let autoAddress = '';
    let options = null;
    let ready = false;
    let readOnly = false;
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
        const rows = [...lines.querySelectorAll("[data-sale-line]")];
        rows.forEach((row, index) => {
            const title = `Item ${index + 1}`;
            row.querySelector("[data-line-heading]").textContent = title;
            row.querySelector("[data-line-legend]").textContent = title;
            const remove = row.querySelector("[data-line-remove]");
            remove.disabled = busy || readOnly || rows.length === 1;
            remove.setAttribute("aria-label", `Remove item ${index + 1}`);
        });
    }

    function addLine(focus = false, item = null) {
        if (!ready || busy || readOnly) return;
        const row = document.getElementById("sale-line-template").content.firstElementChild.cloneNode(true);
        const prefix = `sale-item-${++lineId}`;
        const minimumPrice = row.querySelector('[data-line-minimum-sale-price]');
        minimumPrice.id = `${prefix}-minimum-sale-price`;
        row.querySelector('[data-line-minimum-label]').setAttribute('for', minimumPrice.id);
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
            row.querySelector('[data-line-field="unit_price"]').value = selected?.currency === field('currency').value ? selected.sale_price : '';
            row.querySelector('[data-line-field="tax_rate"]').value = selected?.tax_rate ?? '0';
            updateMinimumPrice(row, selected);
        });
        row.querySelector("[data-line-remove]").addEventListener("click", () => {
            if (busy || lines.children.length <= 1) return;
            row.remove();
            updateLineNumbers();
            addButton.focus();
        });
        if (item) {
            row.dataset.itemId = String(item.id);
            for (const name of lineFields) row.querySelector(`[data-line-field="${name}"]`).value = item[name] ?? '';
            row.querySelector('[data-line-unit-label]').value = item.unit_name || item.unit_code || '';
        }
        updateMinimumPrice(row, options.product.find(record => String(record.value) === String(product.value)));
        lines.append(row);
        updateLineNumbers();
        if (focus) product.focus();
    }

    function updateMinimumPrice(row, product) {
        row.querySelector('[data-line-minimum-sale-price]').value = !product ? 'Select a product'
            : product.minimum_sale_price == null ? 'Not set'
            : `${product.minimum_sale_price} ${product.currency || ''}`.trim();
    }

    function updateCurrency() {
        const currency = options.currency.find((record) => String(record.value) === field("currency").value);
        const base = field("currency").value === options.base_currency;
        field("exchange_rate").value = base ? "1.00000000" : currency?.exchange_rate ?? "";
        field("exchange_rate").readOnly = base;
        for (const row of lines.children) {
            const product = options.product.find((item) => String(item.value) === row.querySelector('[data-line-field="product"]').value);
            row.querySelector('[data-line-field="unit_price"]').value = product?.currency === field('currency').value ? product.sale_price : '';
        }
    }

    async function loadItems() {
        const items = [];
        if (!isEdit || quotation) return items;
        const url = new URL(form.dataset.itemsUrl, window.location.origin);
        let page = 1;
        while (true) {
            url.searchParams.set('page', String(page++));
            const data = await getJSON(url.href);
            if (!Array.isArray(data.results)) throw new Error('Could not load order items. Please try again.');
            items.push(...data.results);
            if (!data.next) return items;
        }
    }

    async function load() {
        if (loading || busy) return;
        loading = true;
        ready = false;
        header.disabled = submit.disabled = addButton.disabled = true;
        retry.hidden = true;
        form.setAttribute("aria-busy", "true");
        message(`Loading ${documentName} choices...`, "info");
        try {
            const [data, record, items] = await Promise.all([
                getJSON(form.dataset.optionsUrl),
                isEdit ? getJSON(form.dataset.detailUrl) : null,
                loadItems(),
            ]);
            if (isEdit && !record?.id) throw new Error(`Could not load the ${documentName}. Return to the list.`);
            readOnly = isLocked(record);
            if (readOnly) throw new Error(lockedMessage);
            if (!quotation && !Array.isArray(data.payment_account)) throw new Error("Could not load payment accounts. Try again.");
            for (const name of quotation ? ["currency", "product"] : ["currency", "status", "product"]) {
                if (!Array.isArray(data[name])) throw new Error(`Could not load ${documentName} choices. Please try again.`);
                if (!data[name].length) throw new Error(`No ${name} records are available. Add the missing records or contact your administrator, then try again.`);
            }
            options = data;
            if (!Array.isArray(options.customer)) throw new Error('Could not load customers. Please try again.');
            choices(field("customer"), options.customer, quotation ? "Select customer" : "No customer");
            if (!Array.isArray(options.warehouse)) throw new Error('Could not load warehouses. Please try again.');
            choices(field('warehouse'), options.warehouse, quotation ? 'Select warehouse' : 'No warehouse');
            field('warehouse').disabled = options.warehouse_scope?.all_warehouses === false;
            if (field('warehouse').disabled) {
                field('warehouse').value = String(options.warehouse_scope.warehouse);
                if (!options.warehouse.some(row => String(row.value) === field('warehouse').value) && !record) {
                    throw new Error('Your assigned warehouse is unavailable. Contact an administrator.');
                }
            }
            choices(field("currency"), options.currency, "Select currency");
            field("currency").value = options.currency.some((row) => row.value === options.base_currency) ? options.base_currency : "";
            if (!quotation) {
                choices(field("status"), options.status, "Select status");
                field("status").value = options.status.some((row) => row.value === "draft") ? "draft" : "";
            }
            updateCurrency();
            if (record) {
                originalCustomer = record.customer;
                if (record.customer && !options.customer.some((item) => String(item.value) === String(record.customer))) {
                    const historical = document.createElement('option');
                    historical.value = String(record.customer);
                    historical.textContent = `${record.customer_name} (existing customer)`;
                    field('customer').append(historical);
                }
                if (record.warehouse && !options.warehouse.some((item) => String(item.value) === String(record.warehouse))) {
                    const historical = document.createElement('option');
                    historical.value = String(record.warehouse);
                    historical.textContent = `${record.warehouse_name} (existing warehouse)`;
                    field('warehouse').append(historical);
                }
                for (const name of quotation ? quotationFields : headerFields) field(name).value = record[name] ?? '';
                field('exchange_rate').readOnly = record.currency === options.base_currency;
            }
            if (!quotation) updatePaymentAccounts(record?.payment_account ?? "");
            updateDateLimits();
            ready = true;
            lines.replaceChildren();
            for (const item of quotation ? record?.items || [] : items) addLine(false, item);
            if (!lines.children.length) addLine();
            header.disabled = submit.disabled = addButton.disabled = false;
            status.hidden = true;
        } catch (error) {
            message(error.status === 403 ? "Your session or permissions have changed. Refresh this page and try again."
                : error.message || `Could not load ${documentName} choices. Please try again.`);
            retry.hidden = false;
        } finally {
            loading = false;
            form.setAttribute("aria-busy", "false");
        }
    }

    function showErrors(errors) {
        for (const [name, value] of Object.entries(errors || {})) {
            if (name === "items" && Array.isArray(value)) {
                const rows = [...lines.querySelectorAll("[data-sale-line]")];
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

    function updateDateLimits() {
        const date = field("order_date").value;
        field("expected_delivery_date").min = date;
        if (quotation) field("valid_until").min = date;
    }

    retry.addEventListener("click", load);
    addButton.addEventListener("click", () => addLine(true));
    field("currency").addEventListener("change", updateCurrency);
    if (!quotation) field("warehouse").addEventListener("change", () => updatePaymentAccounts());
    field('customer').addEventListener('change', () => {
        const customer = options.customer.find((item) => String(item.value) === field('customer').value);
        for (const name of ['billing_address', 'shipping_address']) {
            if (!field(name).value || field(name).value === autoAddress) field(name).value = customer?.address || '';
        }
        autoAddress = customer?.address || '';
    });
    field("order_date").addEventListener("change", updateDateLimits);
    updateDateLimits();
    form.addEventListener("input", (event) => { event.target.removeAttribute("aria-invalid"); });
    form.addEventListener("submit", async (event) => {
        event.preventDefault();
        if (!ready || busy || readOnly) return;
        status.hidden = true;
        for (const box of form.querySelectorAll("[data-error-for], [data-line-error]")) box.hidden = true;
        for (const input of form.querySelectorAll('[aria-invalid="true"]')) input.removeAttribute("aria-invalid");
        if (!form.reportValidity()) return;
        if (!lines.children.length) {
            message("Add at least one product.");
            return;
        }
        const payload = salesPayload(form);
        if (isEdit && String(payload.customer ?? '') === String(originalCustomer ?? '')) delete payload.customer;
        busy = true;
        header.disabled = submit.disabled = addButton.disabled = true;
        for (const row of lines.children) row.disabled = true;
        form.setAttribute("aria-busy", "true");
        label.textContent = isEdit ? "Saving changes..." : `Creating ${documentName}...`;
        spinner.hidden = false;
        try {
            if (isEdit) await patchJSON(form.action, payload, field('csrfmiddlewaretoken').value);
            else await postJSON(form.action, payload, field("csrfmiddlewaretoken").value);
            window.location.assign(form.dataset.successUrl);
        } catch (error) {
            if (isEdit && error.status === 409) {
                try { readOnly = isLocked(await getJSON(form.dataset.detailUrl)); }
                catch { /* Preserve the API error if the session changed. */ }
                if (readOnly) ready = false;
            }
            showErrors(error.errors);
            message(readOnly ? lockedMessage
                : error.status === 403 ? "Your session or permissions have changed. Refresh this page and try again."
                : error.status === 400 ? `Please check the ${documentName} details. ${errorText(error.errors)}`
                : error.message || `Could not save the ${documentName}. Please try again.`);
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

const form = document.querySelector("[data-sale-form]");
if (form) initializeSalesForm(form);
