import { detailValue, initializeRecordDetail } from "../../js/record_detail.js";
import { postJSON } from "../../js/api.js";

const itemFields = ["line_number", "product_name", "product_sku", "unit_code", "quantity", "unit_price",
    "discount_amount", "net_amount", "tax_rate", "tax_amount", "line_total", "description"];
const moneyFields = new Set(["unit_price", "discount_amount", "net_amount", "tax_amount", "line_total"]);

const panel = document.querySelector("[data-chalan-detail]");
if (panel) {
    const form = panel.querySelector("[data-packing-slip-generate]");
    if (form) {
        const button = form.querySelector('button[type="submit"]');
        const feedback = panel.querySelector("[data-packing-slip-feedback]");
        form.addEventListener("submit", async (event) => {
            event.preventDefault();
            if (button.disabled) return;
            button.disabled = true;
            button.textContent = "Generating packing slip…";
            feedback.hidden = true;
            try {
                await postJSON(form.action, {}, form.elements.namedItem("csrfmiddlewaretoken").value);
                window.location.reload();
            } catch (error) {
                feedback.textContent = error.message;
                feedback.dataset.tone = "error";
                feedback.hidden = false;
                feedback.focus();
                button.disabled = false;
                button.textContent = "Generate packing slip";
            }
        });
    }

    initializeRecordDetail(panel, (record) => {
        const body = panel.querySelector("[data-chalan-items]");
        if (!body) return;
        body.replaceChildren(...(record.items ?? []).map((item) => {
            const row = document.createElement("tr");
            for (const field of itemFields) {
                const kind = moneyFields.has(field) ? "money" : field === "tax_rate" ? "percent" : "";
                row.insertCell().textContent = detailValue(item[field], kind, record.currency);
            }
            return row;
        }));
    });
}
