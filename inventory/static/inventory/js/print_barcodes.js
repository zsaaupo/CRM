import { getJSON } from "../../js/api.js";

const page = document.querySelector("[data-barcode-page]");
const form = page.querySelector("[data-barcode-form]");
const search = form.querySelector("[data-barcode-search]");
const results = form.querySelector("[data-barcode-results]");
const searchStatus = form.querySelector("[data-barcode-search-status]");
const rows = form.querySelector("[data-barcode-rows]");
const rowTemplate = page.querySelector("[data-barcode-row]");
const total = form.querySelector("[data-barcode-total]");
const custom = form.querySelector("[data-custom-paper]");
const submit = form.querySelector("[data-barcode-submit]");
const status = form.querySelector("[data-barcode-status]");
const preview = page.querySelector("[data-barcode-preview]");
const frame = preview.querySelector("[data-barcode-frame]");
const openPDF = preview.querySelector("[data-barcode-open]");
const downloadPDF = preview.querySelector("[data-barcode-download]");
const queue = new Map();
const maxLabels = Number(form.dataset.maxLabels);
let matches = [];
let selectedIndex = -1;
let searchVersion = 0;
let searchTimer;
let scanRequests = Promise.resolve();
let previewURL;
let revision = 0;

function showStatus(element, message, tone = "info") {
    element.textContent = message;
    element.dataset.tone = tone;
    element.hidden = !message;
}

function closeResults() {
    matches = [];
    selectedIndex = -1;
    results.replaceChildren();
    results.hidden = true;
    search.setAttribute("aria-expanded", "false");
    search.removeAttribute("aria-activedescendant");
}

function showResults(products) {
    closeResults();
    matches = products;
    if (!matches.length) return;
    matches.forEach((product, index) => {
        const option = document.createElement("div");
        option.id = "barcode-match-" + index;
        option.dataset.index = index;
        option.setAttribute("role", "option");
        option.setAttribute("aria-selected", "false");
        const name = document.createElement("span");
        name.textContent = product.name;
        const code = document.createElement("span");
        code.className = "barcode-result-code";
        code.textContent = product.code === product.sku ? product.code : product.code + " · SKU " + product.sku;
        option.append(name, code);
        results.append(option);
    });
    results.hidden = false;
    search.setAttribute("aria-expanded", "true");
}

function navigateResults(direction) {
    if (!matches.length) return;
    const index = selectedIndex === -1 && direction < 0 ? matches.length - 1
        : (selectedIndex + direction + matches.length) % matches.length;
    results.children[selectedIndex]?.setAttribute("aria-selected", "false");
    selectedIndex = index;
    const option = results.children[index];
    option.setAttribute("aria-selected", "true");
    search.setAttribute("aria-activedescendant", option.id);
    option.scrollIntoView({ block: "nearest" });
}

function lookup(query, exact = false) {
    const url = new URL(form.dataset.searchUrl, window.location.href);
    url.searchParams.set("q", query);
    if (exact) url.searchParams.set("exact", "1");
    return getJSON(url);
}

function scheduleSearch() {
    window.clearTimeout(searchTimer);
    const version = ++searchVersion;
    const query = search.value.trim();
    closeResults();
    showStatus(searchStatus, "");
    if (query.length < 2) return;
    searchTimer = window.setTimeout(async () => {
        try {
            const products = await lookup(query);
            if (version !== searchVersion || document.activeElement !== search) return;
            showResults(products);
            showStatus(searchStatus, products.length ? "" : "No matching products.");
        } catch (error) {
            if (version === searchVersion && document.activeElement === search) {
                showStatus(searchStatus, error.message, "error");
            }
        }
    }, 225);
}

function scanCode() {
    const query = search.value.trim();
    if (!query) return;
    window.clearTimeout(searchTimer);
    const version = ++searchVersion;
    search.value = "";
    closeResults();
    showStatus(searchStatus, "Looking up " + query + "…");
    const lookupResult = lookup(query, true).then(products => ({ products }), error => ({ error }));
    // Apply completed lookups in scan order, even if requests return out of order.
    scanRequests = scanRequests.then(async () => {
        const { products, error } = await lookupResult;
        if (products?.length === 1) {
            addProduct(products[0]);
        } else if (version === searchVersion) {
            search.value = query;
            if (document.activeElement === search && products?.length) showResults(products);
            showStatus(searchStatus, error?.message || (products.length
                ? "This code matches multiple products. Choose the correct product."
                : "No exact code match. Type a product name or check the code."), error ? "error" : "info");
        } else {
            showStatus(searchStatus, query + " was not added. Scan it again to review the match.", "error");
        }
    });
}

function selectProduct(product) {
    ++searchVersion;
    search.value = "";
    closeResults();
    addProduct(product);
    search.focus();
}

function labelCount() {
    return [...queue.values()].reduce((count, entry) => count + (entry.quantity.valueAsNumber || 0), 0);
}

function updateQueue() {
    form.querySelector("[data-barcode-queue]").hidden = queue.size === 0;
    form.querySelector("[data-barcode-empty]").hidden = queue.size > 0;
    const count = labelCount();
    const valid = [...queue.values()].every(entry => entry.quantity.validity.valid);
    const message = !queue.size ? "" : !valid ? "Enter a whole-number quantity from 1 to " + maxLabels + " per product."
        : count + (count === 1 ? " label" : " labels") + (count > maxLabels ? " · Maximum " + maxLabels + " per batch" : "");
    showStatus(total, message, count > maxLabels || !valid ? "error" : "info");
    invalidatePreview();
}

function addProduct(product) {
    if (labelCount() >= maxLabels) {
        showStatus(searchStatus, "A batch can contain up to " + maxLabels + " labels.", "error");
        return;
    }
    const existing = queue.get(product.id);
    if (existing) {
        existing.quantity.value = (existing.quantity.validity.valid ? existing.quantity.valueAsNumber : 0) + 1;
    } else {
        const row = rowTemplate.content.firstElementChild.cloneNode(true);
        row.querySelector("[data-name]").textContent = product.name;
        row.querySelector("[data-code]").textContent = product.code;
        const quantity = row.querySelector("input");
        quantity.setAttribute("aria-label", "Quantity for " + product.name);
        const remove = row.querySelector("[data-remove]");
        remove.setAttribute("aria-label", "Remove " + product.name);
        remove.addEventListener("click", () => {
            (row.nextElementSibling?.querySelector("[data-remove]") || search).focus();
            row.remove();
            queue.delete(product.id);
            updateQueue();
        });
        rows.append(row);
        queue.set(product.id, { quantity });
    }
    updateQueue();
    showStatus(searchStatus, product.name + " queued. Quantity " + queue.get(product.id).quantity.value + ".");
}

function invalidatePreview() {
    revision += 1;
    if (!submit.disabled) showStatus(status, "");
    if (!previewURL) return;
    preview.hidden = true;
    frame.removeAttribute("src");
    openPDF.removeAttribute("href");
    downloadPDF.removeAttribute("href");
    URL.revokeObjectURL(previewURL);
    previewURL = undefined;
}

function updatePaperControls() {
    custom.hidden = custom.disabled = form.elements.preset.value !== "custom";
    form.querySelector("[data-thermal-help]").hidden = form.elements.preset.value !== "continuous_31_25";
}

function payload() {
    const items = [...queue].map(([productId, entry]) => ({
        product_id: productId,
        quantity: entry.quantity.valueAsNumber,
    }));
    const labelConfig = { auto_retail_barcodes: form.elements.auto_retail_barcodes.checked };
    for (const toggle of form.querySelectorAll("[data-field-toggle]")) {
        labelConfig[toggle.name] = toggle.checked;
        const fontSize = form.elements.namedItem(toggle.dataset.fieldToggle + "_size");
        labelConfig[fontSize.name] = toggle.checked ? fontSize.valueAsNumber : 15;
    }
    const dimensions = custom.disabled ? null : Object.fromEntries(
        [...custom.querySelectorAll("input")].map(input => [input.name, input.value]),
    );
    return {
        items,
        label_config: labelConfig,
        paper_config: { preset: form.elements.preset.value, custom: dimensions },
    };
}

function firstError(value) {
    if (typeof value === "string") return value;
    if (value && typeof value === "object") {
        for (const detail of Object.values(value)) {
            const message = firstError(detail);
            if (message) return message;
        }
    }
    return "";
}

search.addEventListener("input", scheduleSearch);
search.addEventListener("focus", () => {
    if (search.value) scheduleSearch();
});
search.addEventListener("blur", closeResults);
search.addEventListener("keydown", event => {
    if (event.isComposing) return;
    if (event.key === "Escape") {
        window.clearTimeout(searchTimer);
        ++searchVersion;
        closeResults();
    } else if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        if (!matches.length) return;
        event.preventDefault();
        navigateResults(event.key === "ArrowDown" ? 1 : -1);
    } else if (event.key === "Enter") {
        event.preventDefault();
        if (selectedIndex >= 0) selectProduct(matches[selectedIndex]);
        else scanCode();
    }
});
results.addEventListener("mousedown", event => event.preventDefault());
results.addEventListener("click", event => {
    const option = event.target.closest("[data-index]");
    if (option) selectProduct(matches[Number(option.dataset.index)]);
});
form.addEventListener("input", event => {
    if (event.target === search) return;
    if (rows.contains(event.target)) updateQueue();
    else invalidatePreview();
});
form.addEventListener("change", event => {
    if (event.target === form.elements.preset) updatePaperControls();
    const field = event.target.dataset.fieldToggle;
    if (field) form.elements.namedItem(field + "_size").disabled = !event.target.checked;
});

form.addEventListener("submit", async event => {
    event.preventDefault();
    if (submit.disabled) return;
    if (!queue.size || labelCount() > maxLabels) {
        showStatus(status, queue.size ? "Print up to " + maxLabels + " labels per batch." : "Add at least one product.", "error");
        return;
    }
    const currentRevision = revision;
    submit.disabled = true;
    form.setAttribute("aria-busy", "true");
    showStatus(status, "Generating barcode labels…");
    try {
        const response = await fetch(form.dataset.printUrl, {
            method: "POST",
            credentials: "same-origin",
            headers: {
                "Accept": "application/pdf, application/json",
                "Content-Type": "application/json",
                "X-CSRFToken": form.elements.csrfmiddlewaretoken.value,
            },
            body: JSON.stringify(payload()),
            signal: AbortSignal.timeout(60000),
        });
        if (!response.ok || !response.headers.get("Content-Type")?.startsWith("application/pdf")) {
            const errors = await response.json().catch(() => null);
            throw new Error(firstError(errors) || "Could not generate the barcode PDF.");
        }
        const pdf = await response.blob();
        if (revision !== currentRevision) {
            showStatus(status, "The queue or settings changed. Generate a new preview.");
            return;
        }
        if (previewURL) URL.revokeObjectURL(previewURL);
        previewURL = URL.createObjectURL(pdf);
        frame.src = openPDF.href = downloadPDF.href = previewURL;
        preview.hidden = false;
        showStatus(status, "Print preview ready.");
        preview.scrollIntoView({ block: "nearest" });
    } catch (error) {
        showStatus(status, error.name === "TimeoutError" ? "Generation timed out. Try a smaller batch."
            : error.message || "Could not generate the barcode PDF.", "error");
    } finally {
        submit.disabled = false;
        form.removeAttribute("aria-busy");
    }
});

updatePaperControls();
