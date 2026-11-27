import { getJSON, postJSON, patchJSON } from "../../js/api.js";

const headerFields = ['source_warehouse', 'destination_warehouse', 'transfer_date', 'notes'];
const lineFields = ['product', 'unit', 'quantity'];
const errorText = value => Array.isArray(value) ? value.map(errorText).join(' ')
    : value && typeof value === 'object' ? Object.values(value).map(errorText).join(' ') : String(value ?? '');

export function initializeTransferForm(form) {
    const find = name => form.querySelector(`[data-transfer-${name}]`);
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
        const row = document.getElementById('transfer-line-template').content.firstElementChild.cloneNode(true);
        const prefix = `transfer-item-${++lineId}`;
        for (const input of row.querySelectorAll('[data-line-field], [data-line-unit-label]')) {
            if (input.type === 'hidden') continue;
            const name = input.dataset.lineField || 'unit'; input.id = `${prefix}-${name}`;
            row.querySelector(`[data-line-label="${name}"]`).setAttribute('for', input.id);
            row.querySelector(`[data-line-error="${name}"]`).id = `${input.id}-error`;
            input.setAttribute('aria-describedby', `${input.id}-error`);
        }
        const product = row.querySelector('[data-line-field="product"]');
        choices(product, options.product);
        product.addEventListener('change', () => {
            const selected = options.product.find(record => String(record.value) === product.value);
            row.querySelector('[data-line-field="unit"]').value = selected?.unit ?? '';
            row.querySelector('[data-line-unit-label]').value = selected?.unit_label ?? '';
        });
        row.querySelector('[data-line-remove]').addEventListener('click', () => {
            if (busy || readOnly || lines.children.length <= 1) return;
            row.remove(); numberLines(); find('add-line').focus();
        });
        if (item) {
            row.dataset.itemId = String(item.id);
            lineFields.forEach(name => { row.querySelector(`[data-line-field="${name}"]`).value = item[name] ?? ''; });
            row.querySelector('[data-line-unit-label]').value = item.unit_name || item.unit_code || '';
        }
        lines.append(row); numberLines(); if (focus) product.focus();
    }
    async function load() {
        if (loading || busy) return;
        loading = true; ready = false; controls(); find('retry').hidden = true; message('Loading transfer details...', 'info');
        try {
            const [data, record] = await Promise.all([getJSON(form.dataset.optionsUrl),
                isEdit ? getJSON(form.dataset.detailUrl) : null]);
            if (isEdit && (!record?.id || !Array.isArray(record.items))) throw new Error('Could not load all transfer products. Please try again.');
            readOnly = record?.locked === true || record?.is_completed === true;
            if (readOnly) throw new Error('Dispatched and completed transfers are read-only. Return to the transfer list.');
            for (const name of ['source_warehouse', 'destination_warehouse', 'product']) {
                if (!Array.isArray(data[name])) throw new Error('Could not load transfer choices. Please try again.');
            }
            if (!data.product.length) throw new Error('No products are available. Add products and try again.');
            options = data;
            for (const name of ['source_warehouse', 'destination_warehouse']) {
                choices(field(name), data[name]);
                if (record && !data[name].some(row => String(row.value) === String(record[name]))) {
                    const historical = document.createElement('option'); historical.value = String(record[name]);
                    historical.textContent = `${record[`${name}_name`]} (existing)`; field(name).append(historical);
                }
                if (field(name).children.length <= 1) throw new Error('No warehouses are available. Add warehouses and try again.');
            }
            if (record) headerFields.forEach(name => { field(name).value = record[name] ?? ''; });
            ready = true; lines.replaceChildren();
            for (const item of record?.items || []) addLine(item);
            if (!lines.children.length) addLine();
            status.hidden = true;
        } catch (error) {
            message(error.status === 403 ? 'Your session or permissions have changed. Refresh this page and try again.'
                : error.message || 'Could not load transfer details. Please try again.');
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
                try { const latest = await getJSON(form.dataset.detailUrl); readOnly = latest.locked || latest.is_completed; }
                catch { /* Preserve the original error if access changed. */ }
            }
            showErrors(error.errors);
            message(readOnly ? 'Dispatched and completed transfers are read-only. Return to the transfer list.'
                : error.status === 403 ? 'Your session or permissions have changed. Refresh this page and try again.'
                : error.errors ? `Please check the transfer details. ${errorText(error.errors)}`
                : error.message || 'Could not save the transfer. Please try again.'); status.focus();
        } finally { busy = false; controls(); }
    });
    return {ready: load()};
}
const form = document.querySelector('[data-transfer-form]');
if (form) initializeTransferForm(form);
