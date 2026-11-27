import { initializeRecordDetail } from "../../js/record_detail.js";
import { postJSON } from "../../js/api.js";

export function renderTransferItems(panel, record) {
    if (!Array.isArray(record.items)) throw new Error('Could not load transfer products. Please try again.');
    panel.querySelector('[data-transfer-items]').replaceChildren(...record.items.map(item => {
        const row = document.createElement('tr');
        for (const value of [item.line_number, item.product_sku, item.product_name, item.quantity, item.unit_code]) {
            const cell = document.createElement('td'); cell.textContent = String(value ?? '—'); row.append(cell);
        }
        return row;
    }));
}
const panel = document.querySelector('[data-record-detail]');
if (panel) {
    const form = panel.querySelector('[data-transfer-post]');
    form?.addEventListener('click', async event => {
        const button = event.target.closest('[data-stage]');
        if (!button || button.disabled) return;
        button.disabled = true;
        try {
            await postJSON(button.dataset.url, {}, form.elements.csrfmiddlewaretoken.value);
            window.location.reload();
        } catch (error) {
            const feedback = form.querySelector('[role=alert]');
            feedback.textContent = Object.values(error.errors || {}).flat().join(' ') || error.message;
            feedback.hidden = false;
            button.disabled = false;
        }
    });
    initializeRecordDetail(panel, record => {
        renderTransferItems(panel, record);
        if (form) {
            form.hidden = record.is_completed;
            for (const button of form.querySelectorAll('[data-stage]')) button.hidden = button.dataset.stage !== record.stage;
        }
    });
}
