import { postJSON } from "../../js/api.js";
import { initializeRecordDetail } from "../../js/record_detail.js";

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
    initializeRecordDetail(panel, record => {
        renderTransferItems(panel, record);
        const dispatchBtn = document.getElementById('tr-dispatch-btn');
        const receiveBtn = document.getElementById('tr-receive-btn');
        if (dispatchBtn) dispatchBtn.hidden = record.status !== 'pending' || record.is_completed;
        if (receiveBtn) receiveBtn.hidden = record.status === 'completed' || record.is_completed;
    });
    const csrf = panel.dataset.csrf;
    document.getElementById('tr-dispatch-btn')?.addEventListener('click', async () => {
        await postJSON(panel.dataset.dispatchUrl, { request_key: crypto.randomUUID() }, csrf);
        window.location.reload();
    });
    document.getElementById('tr-receive-btn')?.addEventListener('click', async () => {
        await postJSON(panel.dataset.receiveUrl, { request_key: crypto.randomUUID() }, csrf);
        window.location.reload();
    });
}
