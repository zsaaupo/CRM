import { initializeStockForm } from './transfer_form.js';

export function initializeAdjustmentForm(form) {
    return initializeStockForm(form, {
        kind: 'adjustment', warehouseFields: ['warehouse'], dateField: 'adjustment_date',
        lineFields: ['product', 'operation', 'quantity'], selectOnlyWarehouse: true,
    });
}

const form = document.querySelector('[data-adjustment-form]');
if (form) initializeAdjustmentForm(form);
