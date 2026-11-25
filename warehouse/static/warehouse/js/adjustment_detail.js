import { initializeRecordDetail } from '../../js/record_detail.js';

const panel = document.querySelector('[data-record-detail]');
if (panel) initializeRecordDetail(panel, record => {
    if (!Array.isArray(record.items)) throw new Error('Could not load adjustment products. Please try again.');
    const rows = record.items.map(item => {
        const row = document.createElement('tr');
        const operation = item.operation === 'add' ? 'Add' : 'Subtract';
        const values = [item.line_number, item.product_sku, item.product_name, operation, item.quantity, item.unit_code];
        for (const value of values) {
            const cell = document.createElement('td');
            cell.textContent = String(value ?? '\u2014');
            row.append(cell);
        }
        return row;
    });
    panel.querySelector('[data-adjustment-items]').replaceChildren(...rows);
});
