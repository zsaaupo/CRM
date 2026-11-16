// Run with Node: node purchase/tests/purchase_edit_ui.mjs
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import vm from 'node:vm';

const source = (await readFile(new URL('../static/purchase/js/purchase_form.js', import.meta.url), 'utf8'))
    .replace(/^import .*;\r?\n/gm, '').replace(/^export /gm, '');
const catalog = (await readFile(new URL('../../inventory/static/inventory/js/catalog_list.js', import.meta.url), 'utf8'))
    .replace(/^import .*;\r?\n/gm, '').replace(/^export /gm, '');

class Element {
    constructor(tag = 'div') {
        this.tagName = tag.toUpperCase(); this.children = []; this.dataset = {}; this.listeners = {};
        this.attributes = {}; this.value = ''; this.textContent = ''; this.disabled = false; this.hidden = false;
    }
    append(...nodes) { for (const node of nodes) { node.parent = this; this.children.push(node); } }
    replaceChildren(...nodes) { this.children = []; this.append(...nodes); }
    setAttribute(name, value) { this.attributes[name] = value; }
    removeAttribute(name) { delete this.attributes[name]; }
    addEventListener(name, fn) { this.listeners[name] = fn; }
    remove() { this.parent.children = this.parent.children.filter(node => node !== this); }
    focus() {}
    reportValidity() { return true; }
}

const names = ['product', 'unit', 'quantity', 'unit_price', 'discount_amount', 'tax_rate', 'description'];
function line() {
    const row = new Element('fieldset');
    const fields = Object.fromEntries(names.map(name => {
        const field = new Element('input'); field.dataset.lineField = name;
        field.type = name === 'unit' ? 'hidden' : 'text';
        field.value = ['quantity'].includes(name) ? '1' : ['unit_price', 'discount_amount', 'tax_rate'].includes(name) ? '0' : '';
        return [name, field];
    }));
    const unitLabel = new Element('input');
    const labels = Object.fromEntries(names.map(name => [name, new Element('label')]));
    const errors = Object.fromEntries([...names, 'non_field_errors'].map(name => [name, new Element()]));
    const parts = Object.fromEntries(['heading', 'legend', 'remove'].map(name => [name, new Element()]));
    row.querySelector = selector => {
        if (selector === '[data-line-unit-label]') return unitLabel;
        const match = selector.match(/^\[data-line-(field|label|error)="([^"]+)"\]$/);
        if (match) return ({ field: fields, label: labels, error: errors })[match[1]][match[2]];
        return parts[selector.match(/^\[data-line-(.*)\]$/)?.[1]];
    };
    row.querySelectorAll = () => [...Object.values(fields), unitLabel];
    row.cloneNode = () => line();
    return row;
}

async function setup({ received = false, failPage = false, conflict = false, edit = true, restricted = false } = {}) {
    const form = new Element('form');
    const fields = Object.fromEntries(['supplier', 'warehouse', 'supplier_reference', 'order_date', 'expected_date', 'currency',
        'exchange_rate', 'status', 'shipping_cost', 'notes', 'csrfmiddlewaretoken'].map(name => [name, new Element('input')]));
    fields.csrfmiddlewaretoken.value = 'csrf';
    const parts = Object.fromEntries(['status', 'header', 'submit', 'add-line', 'retry', 'lines'].map(name => [name, new Element()]));
    const spinner = new Element(); const label = new Element(); label.textContent = edit ? 'Save changes' : 'Create purchase';
    form.action = edit ? '/update/7/' : '/create/';
    form.dataset = { method: edit ? 'PATCH' : 'POST', optionsUrl: '/options/', detailUrl: '/detail/7/',
        itemsUrl: '/items/?purchase_order=7', successUrl: '/purchases/' };
    form.elements = { namedItem: name => fields[name] };
    form.querySelector = selector => selector === '[data-submit-spinner]' ? spinner : selector === '[data-submit-label]' ? label
        : parts[selector.match(/^\[data-purchase-(.*)\]$/)?.[1]];
    form.querySelectorAll = selector => selector === '[data-purchase-line]' ? parts.lines.children : [];
    parts.lines.querySelectorAll = () => parts.lines.children;
    const options = { warehouse_scope: {all_warehouses: !restricted, warehouse: restricted ? 1 : null}, base_currency: 'BDT', supplier: [], warehouse: [{ value: 1, label: 'Main' }],
        currency: [{ value: 'BDT', label: 'Taka', exchange_rate: '1' }, { value: 'USD', label: 'Dollar', exchange_rate: '120' }],
        status: [{ value: 'draft', label: 'Draft' }], product: [
            { value: 5, label: 'Rice', unit: 9, unit_label: 'Pieces (PCS)', cost_price: '12.3456' },
            { value: 6, label: 'Free sample', unit: 9, unit_label: 'Pieces (PCS)', cost_price: '0.0000' },
        ] };
    const record = { id: 7, is_received: received, supplier: 44, supplier_name: 'Historical supplier', warehouse: restricted ? 1 : 2,
        warehouse_name: 'Inactive branch', supplier_reference: 'REF', order_date: '2026-11-15', expected_date: null,
        currency: 'USD', exchange_rate: '121.50000000', status: 'draft', shipping_cost: '3.0000', notes: 'Agreed terms' };
    const items = Array.from({ length: 26 }, (_, index) => ({ id: index + 101, line_number: index + 1, product: 5, unit: 9,
        unit_code: 'PCS', quantity: '2.500000', unit_price: '11.2500', discount_amount: '0.0000', tax_rate: '5.0000', description: 'Agreed item' }));
    const calls = []; const saved = []; const redirects = [];
    const context = vm.createContext({
        document: { querySelector: () => null, getElementById: () => ({ content: { firstElementChild: line() } }),
            createElement: tag => new Element(tag), createElementNS: (ns, tag) => new Element(tag) },
        window: { location: { origin: 'https://crm.test', assign: url => redirects.push(url) } }, URL,
        getJSON: async url => {
            calls.push(url);
            if (url === '/options/') return structuredClone(options);
            if (url === '/detail/7/') return structuredClone(record);
            const page = Number(new URL(url, 'https://crm.test').searchParams.get('page'));
            if (failPage && page === 2) throw new Error('Item page failed');
            return { count: 26, next: page === 1 ? '/items/?page=2' : null,
                results: structuredClone(items.slice((page - 1) * 20, page * 20)) };
        },
        postJSON: async (url, data, csrf) => { assert.equal(csrf, 'csrf'); saved.push({ method: 'POST', url, data: structuredClone(data) }); },
        patchJSON: async (url, data, csrf) => {
            assert.equal(csrf, 'csrf'); saved.push({ method: 'PATCH', url, data: structuredClone(data) });
            if (conflict) { record.is_received = true; throw Object.assign(new Error('Received purchase'), { status: 409 }); }
        },
    });
    vm.runInContext(source, context);
    context.testForm = form;
    await vm.runInContext('initializePurchaseForm(testForm).ready', context);
    return { form, fields, parts, saved, redirects, calls, context };
}

const event = { preventDefault() {} };
const editor = await setup();
assert.equal(editor.parts.lines.children.length, 26);
assert.equal(editor.parts.submit.disabled, false);
assert.equal(editor.fields.exchange_rate.value, '121.50000000');
assert.equal(editor.fields.warehouse.value, 2);
assert.equal(editor.parts.lines.children[0].querySelector('[data-line-field="unit_price"]').value, '11.2500');
assert.ok(editor.calls.some(url => url.includes('page=2')));
editor.parts.lines.children[0].querySelector('[data-line-field="quantity"]').value = '4.000001';
await editor.form.listeners.submit(event);
assert.equal(editor.saved[0].method, 'PATCH');
assert.equal(editor.saved[0].data.items.length, 26);
assert.equal(editor.saved[0].data.items[0].id, 101);
assert.equal(editor.saved[0].data.items[0].quantity, '4.000001');
assert.equal(editor.saved[0].data.items[0].unit_price, '11.2500');
assert.equal(editor.saved[0].data.supplier, undefined);
assert.deepEqual(editor.redirects, ['/purchases/']);

const locked = await setup({ received: true });
assert.equal(locked.parts.submit.disabled, true);
assert.match(locked.parts.status.textContent, /read-only/);
await locked.form.listeners.submit(event);
assert.equal(locked.saved.length, 0);
const failed = await setup({ failPage: true });
assert.equal(failed.parts.submit.disabled, true);
await failed.form.listeners.submit(event);
assert.equal(failed.saved.length, 0);
const race = await setup({ conflict: true });
await race.form.listeners.submit(event);
assert.equal(race.parts.submit.disabled, true);
assert.equal(race.parts.header.disabled, true);
assert.match(race.parts.status.textContent, /read-only/);
assert.equal(race.redirects.length, 0);

const creator = await setup({ edit: false });
assert.equal(creator.parts.lines.children.length, 1);
const firstLine = creator.parts.lines.children[0];
const productChoice = firstLine.querySelector('[data-line-field="product"]');
const unitPrice = firstLine.querySelector('[data-line-field="unit_price"]');
productChoice.value = '5';
productChoice.listeners.change();
assert.equal(unitPrice.value, '12.3456');
unitPrice.value = '15.0000';
await creator.form.listeners.submit(event);
assert.equal(creator.saved[0].method, 'POST');
assert.equal(creator.saved[0].data.items[0].id, undefined);
assert.equal(creator.saved[0].data.items[0].unit_price, '15.0000');
productChoice.value = '6';
productChoice.listeners.change();
assert.equal(unitPrice.value, '0.0000');
productChoice.value = '';
productChoice.listeners.change();
assert.equal(unitPrice.value, '');
creator.parts['add-line'].listeners.click();
const secondLine = creator.parts.lines.children[1];
const secondProduct = secondLine.querySelector('[data-line-field="product"]');
secondProduct.value = '5';
secondProduct.listeners.change();
assert.equal(secondLine.querySelector('[data-line-field="unit_price"]').value, '12.3456');
assert.equal(unitPrice.value, '');

vm.runInContext(catalog, editor.context);
editor.context.columns = [{ key: 'actions', type: 'edit' }];
editor.context.rowData = { id: 7, order_number: 'PO-7', is_received: false };
const editableRow = vm.runInContext("catalogRow(rowData, columns, '/purchase/0/edit/', null, '/purchase/0/', 'is_received')", editor.context);
assert.equal(editableRow.children[0].children[0].children.length, 2);
editor.context.rowData.is_received = true;
const receivedRow = vm.runInContext("catalogRow(rowData, columns, '/purchase/0/edit/', null, '/purchase/0/', 'is_received')", editor.context);
assert.equal(receivedRow.children[0].children[0].children.length, 2);
assert.equal(receivedRow.children[0].children[0].children[0].href, '/purchase/7/');
assert.equal(receivedRow.children[0].children[0].children[1].tagName, 'BUTTON');
assert.equal(receivedRow.children[0].children[0].children[1].disabled, true);
assert.equal(receivedRow.children[0].children[0].children[1].href, undefined);
const ordinaryRow = vm.runInContext("catalogRow(rowData, columns, '/inventory/0/edit/')", editor.context);
assert.equal(ordinaryRow.children[0].children[0].children.length, 1);
console.log('Purchase editing UI passed: conditional icons, all item pages, preserved IDs/prices, historical supplier, PATCH/POST, redirects and received-order locking.');

const restrictedCreator = await setup({edit: false, restricted: true});
assert.equal(restrictedCreator.fields.warehouse.disabled, true);
assert.equal(String(restrictedCreator.fields.warehouse.value), '1');
await restrictedCreator.form.listeners.submit(event);
assert.equal(String(restrictedCreator.saved[0].data.warehouse), '1');
const restrictedEditor = await setup({restricted: true});
assert.equal(restrictedEditor.fields.warehouse.disabled, true);
assert.equal(String(restrictedEditor.fields.warehouse.value), '1');
console.log('Assigned warehouse stays selected and disabled for creation and editing.');
