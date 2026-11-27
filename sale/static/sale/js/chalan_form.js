import { getJSON, patchJSON, postJSON } from "../../js/api.js";

const lineFields = ["product", "unit", "quantity", "unit_price", "discount_amount", "tax_rate", "description"];
const recipientFields = { shipping_address: "address", recipient_name: "recipient_name", recipient_phone: "recipient_phone" };
const deliveredMessage = "Delivered Chalans are read-only.";
const stockInTransitMessage = "The warehouse, products, and quantities are locked while stock is with the courier.";

function errorText(value) {
    if (Array.isArray(value)) return value.map(errorText).join(" ");
    if (value && typeof value === "object") return Object.values(value).map(errorText).join(" ");
    return String(value ?? "");
}

function initializeChalanForm(form) {
    const find = (name) => form.querySelector(`[data-chalan-${name}]`);
    const field = (name) => form.elements.namedItem(name);
    const controls = find("controls");
    const header = [...find("header").querySelectorAll("[name]")];
    const lines = find("lines");
    const status = find("status");
    const retry = find("retry");
    const addButton = find("add-line");
    const template = document.getElementById("chalan-line-template");
    const edit = Boolean(form.dataset.detailUrl);
    let options;
    let previousCustomer;
    let initialItems;
    let stockInTransit = false;
    let rowId = 0;

    function message(text, tone = "error") {
        status.dataset.tone = tone;
        status.textContent = text;
        status.hidden = false;
    }

    function choices(select, records, placeholder, existing) {
        const items = [new Option(placeholder, ""), ...records.map(({label, value}) => new Option(label, value))];
        if (existing && !records.some(({value}) => String(value) === String(existing.value))) {
            items.push(new Option(existing.label, existing.value));
        }
        select.replaceChildren(...items);
    }

    function updateRemoveButtons() {
        for (const button of lines.querySelectorAll("[data-line-remove]")) {
            button.disabled = stockInTransit || lines.children.length === 1;
        }
    }

    function selectedProduct(productInput) {
        return options.product.find(({value}) => String(value) === productInput.value);
    }

    function priceForCurrency(product) {
        return product?.currency === field("currency").value ? product.sale_price : "";
    }

    function setExchangeRate() {
        const code = field("currency").value;
        const currency = options.currency.find(({value}) => value === code);
        const rate = field("exchange_rate");
        rate.readOnly = code === options.base_currency;
        rate.value = rate.readOnly ? "1.00000000" : currency?.exchange_rate ?? "";
    }

    function addLine(item, focus = false) {
        const row = template.content.firstElementChild.cloneNode(true);
        const get = (name) => row.querySelector(`[data-line-field="${name}"]`);
        const lastNumber = Number(lines.lastElementChild?.dataset.lineNumber || 0);
        row.dataset.lineNumber = String(item?.line_number ?? lastNumber + 1);
        if (item) row.dataset.itemId = String(item.id);
        const title = `Item ${row.dataset.lineNumber}`;
        row.querySelector("[data-line-heading]").textContent = title;
        row.querySelector("[data-line-legend]").textContent = title;
        const prefix = `chalan-line-${++rowId}`;
        const unitLabel = row.querySelector("[data-line-unit-label]");
        for (const name of lineFields) {
            const input = name === "unit" ? unitLabel : get(name);
            input.id = `${prefix}-${name}`;
            row.querySelector(`[data-line-label="${name}"]`).htmlFor = input.id;
            const error = row.querySelector(`[data-line-error="${name}"]`);
            error.id = `${input.id}-error`;
            input.setAttribute("aria-describedby", error.id);
        }
        const product = get("product");
        choices(product, options.product, "Select a product", item && {
            value: item.product, label: `${item.product_sku} — ${item.product_name}`,
        });
        if (item) {
            for (const name of lineFields) get(name).value = item[name];
            unitLabel.value = item.unit_code;
        }
        product.disabled = stockInTransit;
        get("quantity").readOnly = stockInTransit;
        product.addEventListener("change", () => {
            const selected = selectedProduct(product);
            get("unit").value = selected?.unit ?? "";
            unitLabel.value = selected?.unit_label ?? "";
            get("unit_price").value = priceForCurrency(selected);
            get("tax_rate").value = selected?.tax_rate ?? "0";
        });
        const remove = row.querySelector("[data-line-remove]");
        remove.setAttribute("aria-label", `Remove ${title.toLowerCase()}`);
        remove.addEventListener("click", () => {
            row.remove();
            updateRemoveButtons();
            (lines.lastElementChild?.querySelector('[data-line-field="product"]') || addButton).focus();
        });
        lines.append(row);
        updateRemoveButtons();
        if (focus) product.focus();
    }

    function itemPayload() {
        return [...lines.children].map((row) => ({
            line_number: Number(row.dataset.lineNumber),
            ...(row.dataset.itemId ? { id: Number(row.dataset.itemId) } : {}),
            ...Object.fromEntries(lineFields.map((name) => [name, row.querySelector(`[data-line-field="${name}"]`).value])),
        }));
    }

    function currentCustomer() {
        return options.customer.find(({value}) => String(value) === field("customer").value);
    }

    function updateDateLimit() {
        field("expected_delivery_date").min = field("chalan_date").value;
    }

    async function load() {
        controls.disabled = true;
        retry.hidden = true;
        form.setAttribute("aria-busy", "true");
        message("Loading Chalan choices...", "info");
        try {
            const [data, record] = await Promise.all([
                getJSON(form.dataset.optionsUrl), edit ? getJSON(form.dataset.detailUrl) : null,
            ]);
            if (record?.is_delivered === true) {
                message(deliveredMessage, "info");
                return;
            }
            stockInTransit = record?.stock_in_transit === true;
            if (edit && (!record?.id || !Array.isArray(record.items))) throw new Error("Could not load the Chalan items.");
            for (const name of ["customer", "warehouse", "product", "currency"]) {
                if (!Array.isArray(data[name])) throw new Error(`Could not load ${name} choices.`);
                if (!data[name].length && !record) throw new Error(`No ${name} choices are available.`);
            }
            options = data;
            choices(field("customer"), options.customer, "Select a customer", record && {
                value: record.customer, label: `${record.customer_name} (${record.customer_code})`,
            });
            const warehouse = field("warehouse");
            choices(warehouse, options.warehouse, "Select a warehouse", record && {
                value: record.warehouse, label: record.warehouse_name,
            });
            warehouse.disabled = stockInTransit || options.warehouse_scope.all_warehouses === false;
            if (options.warehouse_scope.all_warehouses === false) warehouse.value = options.warehouse_scope.warehouse;
            choices(field("currency"), options.currency, "Select a currency");
            field("currency").value = options.base_currency;
            setExchangeRate();
            if (record) {
                for (const input of header) input.value = record[input.name] ?? "";
                field("exchange_rate").readOnly = record.currency === options.base_currency;
            }
            previousCustomer = currentCustomer();
            updateDateLimit();
            lines.replaceChildren();
            for (const item of record?.items ?? []) addLine(item);
            initialItems = JSON.stringify(itemPayload());
            if (!lines.children.length) addLine();
            addButton.disabled = stockInTransit || options.product.length === 0;
            if (stockInTransit) message(stockInTransitMessage, "info");
            else if (options.product.length) status.hidden = true;
            else message("No products are available to add.", "info");
            controls.disabled = false;
        } catch (error) {
            message(error.message || "Could not load the Chalan. Try again.");
            retry.hidden = false;
        } finally {
            form.setAttribute("aria-busy", "false");
        }
    }

    function showErrors(errors) {
        let summary = "";
        for (const [name, value] of Object.entries(errors ?? {})) {
            const target = form.querySelector(`[data-error-for="${name}"]`);
            if (target) {
                target.textContent = errorText(value);
                target.hidden = false;
                field(name).setAttribute("aria-invalid", "true");
            } else if (name === "items" && Array.isArray(value) && value.some((item) => item && typeof item === "object")) {
                value.forEach((item, index) => {
                    for (const [key, detail] of Object.entries(item ?? {})) {
                        const box = lines.children[index]?.querySelector(`[data-line-error="${key}"]`);
                        if (box) {
                            box.textContent = errorText(detail);
                            box.hidden = false;
                        } else summary ||= errorText(detail);
                    }
                });
            } else summary ||= errorText(value);
        }
        return summary;
    }

    field("customer").addEventListener("change", () => {
        const customer = currentCustomer();
        for (const [name, source] of Object.entries(recipientFields)) {
            const input = field(name);
            if (!input.value || input.value === previousCustomer?.[source]) input.value = customer?.[source] ?? "";
        }
        previousCustomer = customer;
    });
    field("chalan_date").addEventListener("change", updateDateLimit);
    field("currency").addEventListener("change", () => {
        setExchangeRate();
        for (const row of lines.children) {
            const product = selectedProduct(row.querySelector('[data-line-field="product"]'));
            row.querySelector('[data-line-field="unit_price"]').value = priceForCurrency(product);
        }
    });
    addButton.addEventListener("click", () => addLine(null, true));
    retry.addEventListener("click", load);
    form.addEventListener("submit", async (event) => {
        event.preventDefault();
        if (controls.disabled) return;
        status.hidden = true;
        for (const box of form.querySelectorAll("[data-error-for], [data-line-error]")) box.hidden = true;
        for (const input of form.querySelectorAll('[aria-invalid="true"]')) input.removeAttribute("aria-invalid");
        if (!form.reportValidity()) return;

        const payload = Object.fromEntries(header.map(({name, value}) => [name, value]));
        payload.expected_delivery_date ||= null;
        const items = itemPayload();
        if (!edit || JSON.stringify(items) !== initialItems) payload.items = items;
        controls.disabled = true;
        form.setAttribute("aria-busy", "true");
        message("Saving Chalan...", "info");
        try {
            const save = edit ? patchJSON : postJSON;
            await save(form.action, payload, field("csrfmiddlewaretoken").value);
            window.location.assign(form.dataset.successUrl);
        } catch (error) {
            let delivered = false;
            if (edit && error.status === 409) {
                try { delivered = (await getJSON(form.dataset.detailUrl)).is_delivered === true; }
                catch { /* Keep the original save error if details cannot be loaded. */ }
            }
            const summary = showErrors(error.errors);
            message(delivered ? deliveredMessage : summary || (error.status === 400 ? "Check the Chalan details and try again." : error.message));
            status.focus();
            controls.disabled = delivered;
            form.setAttribute("aria-busy", "false");
        }
    });
    load();
}

const form = document.querySelector("[data-chalan-form]");
if (form) initializeChalanForm(form);
