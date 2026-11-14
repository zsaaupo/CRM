import { getJSON } from "../../js/api.js";
import { detailValue, initializeRecordDetail } from "../../js/record_detail.js";

export function purchaseItemRow(item, currency, productUrlTemplate = "") {
    const row = document.createElement("tr");
    const fields = ["line_number", "product_name", "description", "unit_code", "quantity", "unit_price",
        "discount_amount", "net_amount", "tax_rate", "tax_amount", "line_total"];
    const money = new Set(["unit_price", "discount_amount", "net_amount", "tax_amount", "line_total"]);
    for (const name of fields) {
        const cell = document.createElement("td");
        if (name === "product_name") {
            const title = productUrlTemplate ? document.createElement("a") : document.createElement("span");
            title.textContent = item.product_name || `Product #${item.product}`;
            if (productUrlTemplate) {
                title.href = productUrlTemplate.replace("/0/", `/${encodeURIComponent(item.product)}/`);
                title.className = "text-button";
            }
            const sku = document.createElement("p");
            sku.textContent = item.product_sku || "";
            sku.className = "mt-1 text-xs text-stone-400";
            cell.append(title, sku);
        } else {
            cell.textContent = detailValue(item[name], money.has(name) ? "money" : name === "tax_rate" ? "percent" : "", currency);
        }
        row.append(cell);
    }
    return row;
}

export function initializePurchaseItems(section, currency) {
    const find = (name) => section.querySelector(`[data-items-${name}]`);
    let page = 1;
    let hasPrevious = false;
    let hasNext = false;
    let loading = false;

    async function load(requestedPage = 1) {
        if (loading) return;
        loading = true;
        page = requestedPage;
        section.setAttribute("aria-busy", "true");
        find("status").hidden = false;
        for (const name of ["table", "empty", "error", "pagination"]) find(name).hidden = true;
        find("previous").disabled = find("next").disabled = find("retry").disabled = true;
        try {
            const url = new URL(section.dataset.apiUrl, window.location.origin);
            url.searchParams.set("page", String(page));
            const data = await getJSON(url.href);
            if (!Array.isArray(data.results)) throw new Error("Could not load purchase items. Please try again.");
            find("rows").replaceChildren(...data.results.map((item) => purchaseItemRow(item, currency, section.dataset.productUrlTemplate)));
            find("table").hidden = data.results.length === 0;
            find("empty").hidden = data.results.length !== 0;
            hasPrevious = Boolean(data.previous);
            hasNext = Boolean(data.next);
            find("previous").disabled = !hasPrevious;
            find("next").disabled = !hasNext;
            find("pagination").hidden = data.results.length === 0;
            const start = (page - 1) * 20 + 1;
            find("range").textContent = `Showing ${start}\u2013${start + data.results.length - 1} of ${data.count}`;
        } catch (error) {
            if (error.status === 404 && requestedPage > 1) {
                loading = false;
                await load(1);
                return;
            }
            find("error-message").textContent = error.status === 403
                ? "You do not have permission to view purchase order items, or your session has changed."
                : error.message || "Could not load purchase items. Please try again.";
            find("error").hidden = false;
        } finally {
            find("status").hidden = true;
            find("retry").disabled = false;
            section.setAttribute("aria-busy", "false");
            loading = false;
        }
    }

    find("previous").addEventListener("click", () => { if (hasPrevious) return load(page - 1); });
    find("next").addEventListener("click", () => { if (hasNext) return load(page + 1); });
    find("retry").addEventListener("click", () => load(page));
    return { ready: load() };
}

const panel = document.querySelector("[data-record-detail]");
if (panel) {
    let items;
    initializeRecordDetail(panel, async (record) => {
        const section = panel.querySelector("[data-purchase-items]");
        if (section && !items) items = initializePurchaseItems(section, record.currency);
        if (items) await items.ready;
    });
}
