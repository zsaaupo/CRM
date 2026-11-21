import { postJSON } from "../../js/api.js";
import { detailValue, initializeRecordDetail } from "../../js/record_detail.js";

export function initializeQuotationDetail(panel) {
    const form = panel.querySelector("[data-quotation-actions]");
    const buttons = form ? [...form.querySelectorAll("[data-quotation-action]")] : [];
    const rows = panel.querySelector("[data-quotation-lines]");
    let busy = false;

    function showLink(selector, url) {
        const link = panel.querySelector(selector);
        link.hidden = !url;
        if (url) link.href = url;
        else link.removeAttribute("href");
    }

    for (const button of buttons) {
        button.addEventListener("click", async () => {
            if (busy || button.hidden) return;
            busy = true;
            for (const action of buttons) action.disabled = true;
            const feedback = form.querySelector("[data-quotation-feedback]");
            feedback.hidden = true;
            try {
                const csrf = form.elements.namedItem("csrfmiddlewaretoken").value;
                const data = await postJSON(button.dataset.url, {}, csrf);
                const orderUrl = button.dataset.quotationAction === "convert" ? data.sales_quotation.sales_order_url : null;
                window.location.assign(orderUrl || data.quotation_url);
            } catch (error) {
                const details = Object.values(error.errors || {}).flat().join(" ");
                feedback.textContent = details || error.message || "Could not update the quotation.";
                feedback.dataset.tone = "error";
                feedback.hidden = false;
                feedback.focus();
                busy = false;
                for (const action of buttons) action.disabled = false;
            }
        });
    }

    return initializeRecordDetail(panel, record => {
        const edit = panel.querySelector("[data-quotation-edit]");
        if (edit) edit.hidden = record.is_locked !== false;
        showLink("[data-quotation-order]", record.sales_order_url);
        showLink("[data-quotation-source]", record.revised_from_url);
        for (const button of buttons) button.hidden = !record.actions.includes(button.dataset.quotationAction);
        if (form) form.hidden = buttons.every(button => button.hidden);
        if (!rows) return;
        rows.replaceChildren();
        for (const item of record.items || []) {
            const row = document.createElement("tr");
            const money = value => detailValue(value, "money", record.currency);
            const values = [item.line_number, `${item.product_name} (${item.product_sku})`, item.unit_code,
                item.quantity, money(item.unit_price), money(item.discount_amount), item.tax_rate, money(item.line_total)];
            for (const value of values) {
                const cell = document.createElement("td");
                cell.textContent = detailValue(value);
                row.append(cell);
            }
            rows.append(row);
        }
    });
}

const panel = document.querySelector("[data-quotation-detail]");
if (panel) initializeQuotationDetail(panel);
