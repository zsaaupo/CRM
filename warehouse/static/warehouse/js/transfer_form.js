import { getJSON, postJSON, patchJSON } from "../../js/api.js";

const errorText = value => Array.isArray(value) ? value.map(errorText).join(' ')
    : value && typeof value === 'object' ? Object.values(value).map(errorText).join(' ') : String(value ?? '');

export function initializeStockForm(form, {kind, warehouseFields, dateField, lineFields, selectOnlyWarehouse = false}) {
    const headerFields = [...warehouseFields, dateField, 'notes'];
    const readOnlyMessage = `This ${kind} is complete and read-only. Return to the list.`;
    const find = name => form.querySelector(`[data-${kind}-${name}]`);
    const field = name => form.elements.namedItem(name);
    const lines = find('lines');
    const status = find('status');
    const label = form.querySelector('[data-submit-label]');
    const saveLabel = label.textContent;
    const isEdit = form.dataset.method === 'PATCH';
    let options, ready = false, busy = false, loading = false, readOnly = false, lineId = 0;

    function message(text, tone = 'error') {
        status.textContent = text; status.dataset.tone = tone; status.hidden = false;
    }
    function controls() {
        const disabled = !ready || busy || readOnly;
        find('header').disabled = find('submit').disabled = find('add-line').disabled = disabled;
        find('retry').disabled = loading || busy;
        for (const row of lines.children) row.disabled = disabled;
        label.textContent = busy ? 'Saving...' : saveLabel;
        form.querySelector('[data-submit-spinner]').hidden = !busy;
        form.setAttribute('aria-busy', String(loading || busy));
        numberLines();
    }
    function choices(select, records) {
        const placeholder = document.createElement('option'); placeholder.value = ''; placeholder.textContent = 'Select an option';
        select.replaceChildren(placeholder);
        for (const record of records) {
            const option = document.createElement('option'); option.value = String(record.value); option.textContent = record.label;
            select.append(option);
        }
    }
    function numberLines() {
        [...lines.children].forEach((row, index) => {
            row.querySelector('[data-line-heading]').textContent = row.querySelector('[data-line-legend]').textContent = `Item ${index + 1}`;
            const remove = row.querySelector('[data-line-remove]');
            remove.disabled = busy || !ready || readOnly || lines.children.length <= 1;
            remove.setAttribute('aria-label', `Remove item ${index + 1}`);
        });
    }
    function addLine(item = null, focus = false) {
        if (!ready || busy || readOnly) return;
        const row = document.getElementById(`${kind}-line-template`).content.firstElementChild.cloneNode(true);
        const prefix = `${kind}-item-${++lineId}`;
        for (const input of row.querySelectorAll('[data-line-field], [data-line-unit-label]')) {
            if (input.type === 'hidden') continue;
            const name = input.dataset.lineField || 'unit'; input.id = `${prefix}-${name}`;
            row.querySelector(`[data-line-label="${name}"]`).setAttribute('for', input.id);
            const error = row.querySelector(`[data-line-error="${name}"]`);
            if (error) {
                error.id = `${input.id}-error`;
                input.setAttribute('aria-describedby', error.id);
            }
        }
        const product = row.querySelector('[data-line-field="product"]');
        const unit = row.querySelector('[data-line-field="unit"]');
        const unitLabel = row.querySelector('[data-line-unit-label]');
        choices(product, options.product);
        function showProductDetails(savedItem = null) {
            const selected = options.product.find(record => String(record.value) === String(product.value));
            if (unit) unit.value = savedItem ? savedItem.unit : selected?.unit ?? '';
            unitLabel.value = savedItem && unit ? savedItem.unit_name || savedItem.unit_code || '' : selected?.unit_label ?? '';
        }
        product.addEventListener('change', () => showProductDetails());
        row.querySelector('[data-line-remove]').addEventListener('click', () => {
            if (busy || readOnly || lines.children.length <= 1) return;
            row.remove(); numberLines(); find('add-line').focus();
        });
        if (item) {
            row.dataset.itemId = String(item.id);
            lineFields.forEach(name => { row.querySelector(`[data-line-field="${name}"]`).value = item[name] ?? ''; });
            showProductDetails(item);
        }
        lines.append(row); numberLines(); if (focus) product.focus();
    }
    async function load() {
        if (loading || busy) return;
        loading = true; ready = false; controls(); find('retry').hidden = true; message(`Loading ${kind} details...`, 'info');
        try {
            const [data, record] = await Promise.all([getJSON(form.dataset.optionsUrl),
                isEdit ? getJSON(form.dataset.detailUrl) : null]);
            if (isEdit && (!record?.id || !Array.isArray(record.items))) throw new Error(`Could not load all ${kind} products. Please try again.`);
            readOnly = record?.is_completed === true;
            if (readOnly) throw new Error(readOnlyMessage);
            for (const name of [...warehouseFields, 'product']) {
                if (!Array.isArray(data[name])) throw new Error(`Could not load ${kind} choices. Please try again.`);
            }
            if (!data.product.length) throw new Error('No products are available. Add products and try again.');
            options = data;
            for (const name of warehouseFields) {
                choices(field(name), data[name]);
                if (record && !data[name].some(row => String(row.value) === String(record[name]))) {
                    const historical = document.createElement('option'); historical.value = String(record[name]);
                    historical.textContent = `${record[`${name}_name`]} (existing)`; field(name).append(historical);
                }
                if (field(name).children.length <= 1) throw new Error('No warehouses are available. Add warehouses and try again.');
                if (selectOnlyWarehouse && !record && data[name].length === 1) field(name).value = String(data[name][0].value);
            }
            if (record) headerFields.forEach(name => { field(name).value = record[name] ?? ''; });
            ready = true; lines.replaceChildren();
            for (const item of record?.items || []) addLine(item);
            if (!lines.children.length) addLine();
            status.hidden = true;
        } catch (error) {
            message(error.status === 403 ? 'Your session or permissions have changed. Refresh this page and try again.'
                : error.message || `Could not load ${kind} details. Please try again.`);
            find('retry').hidden = false;
        } finally { loading = false; controls(); }
    }
    function showErrors(errors) {
        const rows = [...lines.children];
        for (const [name, value] of Object.entries(errors || {})) {
            if (name === 'items' && Array.isArray(value)) {
                value.forEach((itemErrors, index) => {
                    if (!rows[index] || !itemErrors || typeof itemErrors !== 'object') return;
                    for (const [key, detail] of Object.entries(itemErrors)) {
                        const box = rows[index].querySelector(`[data-line-error="${key}"]`) || rows[index].querySelector('[data-line-error="non_field_errors"]');
                        box.textContent = errorText(detail); box.hidden = false;
                    }
                });
            } else {
                const box = [...form.querySelectorAll('[data-error-for]')].find(node => node.dataset.errorFor === name);
                if (box) { box.textContent = errorText(value); box.hidden = false; }
            }
        }
    }
    find('retry').addEventListener('click', load);
    find('add-line').addEventListener('click', () => addLine(null, true));
    form.addEventListener('submit', async event => {
        event.preventDefault(); if (!ready || busy || readOnly) return;
        status.hidden = true;
        form.querySelectorAll('[data-error-for], [data-line-error]').forEach(box => { box.hidden = true; });
        if (!form.reportValidity()) return;
        const payload = Object.fromEntries(headerFields.map(name => [name, field(name).value]));
        payload.items = [...lines.children].map((row, index) => ({line_number: index + 1,
            ...(row.dataset.itemId ? {id: Number(row.dataset.itemId)} : {}),
            ...Object.fromEntries(lineFields.map(name => [name, row.querySelector(`[data-line-field="${name}"]`).value]))}));
        busy = true; controls();
        try {
            if (isEdit) await patchJSON(form.action, payload, field('csrfmiddlewaretoken').value);
            else await postJSON(form.action, payload, field('csrfmiddlewaretoken').value);
            window.location.assign(form.dataset.successUrl);
        } catch (error) {
            if (isEdit && error.status === 409) {
                try { readOnly = (await getJSON(form.dataset.detailUrl)).is_completed === true; }
                catch { /* Preserve the original error if access changed. */ }
            }
            showErrors(error.errors);
            message(readOnly ? readOnlyMessage
                : error.status === 403 ? 'Your session or permissions have changed. Refresh this page and try again.'
                : error.errors ? `Please check the ${kind} details. ${errorText(error.errors)}`
                : error.message || `Could not save the ${kind}. Please try again.`); status.focus();
        } finally { busy = false; controls(); }
    });
    return {ready: load()};
}

export function initializeTransferForm(form) {
    return initializeStockForm(form, {
        kind: 'transfer', warehouseFields: ['source_warehouse', 'destination_warehouse'],
        dateField: 'transfer_date', lineFields: ['product', 'unit', 'quantity'],
    });
}

const form = document.querySelector('[data-transfer-form]');
if (form) initializeTransferForm(form);
