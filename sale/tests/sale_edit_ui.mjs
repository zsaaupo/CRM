// Run with Node: node sale/tests/sale_edit_ui.mjs
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import vm from 'node:vm';

const source = (await readFile(new URL('../static/sale/js/sales_form.js', import.meta.url), 'utf8'))
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
    const parts = Object.fromEntries(['heading', 'legend', 'remove', 'minimum-sale-price', 'minimum-label'].map(name => [name, new Element()]));
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

async function setup({ completed = false, failPage = false, conflict = false, edit = true, restricted = false,
    minimumPrice = '8.0000', quotation = false } = {}) {
    const form = new Element('form');
    const fields = Object.fromEntries(['customer', 'warehouse', 'customer_reference', 'order_date', 'expected_delivery_date', 'currency',
        'billing_address', 'shipping_address', 'exchange_rate', 'status', 'shipping_cost', 'notes', 'csrfmiddlewaretoken'].map(name => [name, new Element('input')]));
    if (quotation) {
        delete fields.status;
        fields.valid_until = new Element('input');
        fields.terms = new Element('textarea');
    }
    fields.csrfmiddlewaretoken.value = 'csrf';
    const parts = Object.fromEntries(['status', 'header', 'submit', 'add-line', 'retry', 'lines'].map(name => [name, new Element()]));
    const spinner = new Element(); const label = new Element(); label.textContent = edit ? 'Save changes' : 'Create sale';
    form.action = edit ? '/update/7/' : '/create/';
    form.dataset = { method: edit ? 'PATCH' : 'POST', optionsUrl: '/options/', detailUrl: '/detail/7/',
        itemsUrl: '/items/?sales_order=7', successUrl: quotation ? '/quotations/' : '/sales/',
        documentType: quotation ? 'quotation' : 'sale' };
    form.elements = { namedItem: name => fields[name] };
    form.querySelector = selector => selector === '[data-submit-spinner]' ? spinner : selector === '[data-submit-label]' ? label
        : parts[selector.match(/^\[data-sale-(.*)\]$/)?.[1]];
    form.querySelectorAll = selector => selector === '[data-sale-line]' ? parts.lines.children : [];
    parts.lines.querySelectorAll = () => parts.lines.children;
    const options = { warehouse_scope: {all_warehouses: !restricted, warehouse: restricted ? 1 : null}, base_currency: 'BDT', customer: [], warehouse: [{ value: 1, label: 'Main' }],
        currency: [{ value: 'BDT', label: 'Taka', exchange_rate: '1' }, { value: 'USD', label: 'Dollar', exchange_rate: '120' }],
        status: [{ value: 'draft', label: 'Draft' }, { value: 'review', label: 'Review' }], product: [{ value: 5, label: 'Rice', unit: 9, unit_label: 'Pieces (PCS)', currency: 'BDT', minimum_sale_price: minimumPrice }] };
    const record = { id: 7, is_completed: completed, customer: 44, customer_name: 'Historical customer', warehouse: restricted ? 1 : 2,
        warehouse_name: 'Inactive branch', customer_reference: 'REF', order_date: '2026-11-15', expected_delivery_date: null,
        currency: 'USD', exchange_rate: '121.50000000', status: 'review', shipping_cost: '3.0000', notes: 'Agreed terms' };
    const items = Array.from({ length: 26 }, (_, index) => ({ id: index + 101, line_number: index + 1, product: 5, unit: 9,
        unit_code: 'PCS', quantity: '2.500000', unit_price: '11.2500', discount_amount: '0.0000', tax_rate: '5.0000', description: 'Agreed item' }));
    if (quotation) Object.assign(record, {is_locked: completed, valid_until: '2026-12-15', terms: 'Quoted terms', items});
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
            if (conflict) { record.is_completed = true; throw Object.assign(new Error('Completed sale'), { status: 409 }); }
        },
    });
    vm.runInContext(source, context);
    context.testForm = form;
    await vm.runInContext('initializeSalesForm(testForm).ready', context);
    return { form, fields, parts, saved, redirects, calls, context };
}


const event = { preventDefault() {} };
const editor = await setup();
assert.equal(editor.parts.submit.disabled, false, editor.parts.status.textContent);
assert.equal(editor.fields.status.value, 'review');
assert.equal(editor.parts.lines.children.length, 26);
assert.equal(editor.parts.lines.children[0].querySelector('[data-line-minimum-sale-price]').value, '8.0000 BDT');
assert.equal(editor.fields.exchange_rate.value, '121.50000000');
assert.equal(editor.fields.warehouse.children.at(-1).textContent, 'Inactive branch (existing warehouse)');
editor.fields.status.value = 'draft';
await editor.form.listeners.submit(event);
assert.equal(editor.saved[0].method, 'PATCH');
assert.equal(editor.saved[0].data.status, 'draft');
assert.equal(editor.saved[0].data.items.length, 26);
assert.equal(editor.saved[0].data.items[0].id, 101);
assert.equal(editor.saved[0].data.items[0].unit_price, '11.2500');
assert.deepEqual(editor.redirects, ['/sales/']);
const locked = await setup({completed: true});
assert.equal(locked.parts.submit.disabled, true);
assert.match(locked.parts.status.textContent, /read-only/);
await locked.form.listeners.submit(event);
assert.equal(locked.saved.length, 0);
const race = await setup({conflict: true});
await race.form.listeners.submit(event);
assert.equal(race.parts.submit.disabled, true);
assert.equal(race.parts.header.disabled, true);
assert.match(race.parts.status.textContent, /read-only/);
assert.equal(race.redirects.length, 0);
const failed = await setup({failPage: true});
assert.equal(failed.parts.submit.disabled, true);
await failed.form.listeners.submit(event);
assert.equal(failed.saved.length, 0);
const creator = await setup({edit: false});
assert.equal(creator.fields.status.value, 'draft');
creator.fields.status.value = 'review';
await creator.form.listeners.submit(event);
assert.equal(creator.saved[0].data.status, 'review');
console.log('Sales edit UI passed: custom status, all item pages, saved prices and IDs, completion read-only guard, concurrent completion, creation and success redirect.');

const restrictedCreator = await setup({edit: false, restricted: true});
assert.equal(restrictedCreator.fields.warehouse.disabled, true);
assert.equal(String(restrictedCreator.fields.warehouse.value), '1');
await restrictedCreator.form.listeners.submit(event);
assert.equal(String(restrictedCreator.saved[0].data.warehouse), '1');
const restrictedEditor = await setup({restricted: true});
assert.equal(restrictedEditor.fields.warehouse.disabled, true);
assert.equal(String(restrictedEditor.fields.warehouse.value), '1');
console.log('Assigned warehouse stays selected and disabled for creation and editing.');

const priceCreator = await setup({edit: false});
const priceLine = priceCreator.parts.lines.children[0];
assert.equal(priceLine.querySelector('[data-line-minimum-sale-price]').value, 'Select a product');
const productField = priceLine.querySelector('[data-line-field="product"]');
productField.value = '5'; productField.listeners.change();
assert.equal(priceLine.querySelector('[data-line-minimum-sale-price]').value, '8.0000 BDT');
priceCreator.fields.currency.value = 'USD'; priceCreator.fields.currency.listeners.change();
assert.equal(priceLine.querySelector('[data-line-minimum-sale-price]').value, '8.0000 BDT');
productField.value = ''; productField.listeners.change();
assert.equal(priceLine.querySelector('[data-line-minimum-sale-price]').value, 'Select a product');
const noMinimum = await setup({minimumPrice: null});
assert.equal(noMinimum.parts.lines.children[0].querySelector('[data-line-minimum-sale-price]').value, 'Not set');
const zeroMinimum = await setup({minimumPrice: '0.0000'});
assert.equal(zeroMinimum.parts.lines.children[0].querySelector('[data-line-minimum-sale-price]').value, '0.0000 BDT');
console.log('Minimum sale price displays on existing lines and product changes, with currency and zero/null handling.');

const quotationEditor = await setup({quotation: true});
assert.equal(quotationEditor.parts.submit.disabled, false, quotationEditor.parts.status.textContent);
assert.deepEqual(quotationEditor.calls, ['/options/', '/detail/7/']);
assert.equal(quotationEditor.fields.valid_until.value, '2026-12-15');
assert.equal(quotationEditor.fields.valid_until.min, '2026-11-15');
await quotationEditor.form.listeners.submit(event);
assert.equal(quotationEditor.saved[0].data.terms, 'Quoted terms');
assert.equal(quotationEditor.saved[0].data.status, undefined);
assert.equal(quotationEditor.saved[0].data.items[0].id, 101);
assert.deepEqual(quotationEditor.redirects, ['/quotations/']);
const issuedQuotation = await setup({quotation: true, completed: true});
assert.equal(issuedQuotation.parts.submit.disabled, true);
assert.match(issuedQuotation.parts.status.textContent, /issued and is read-only/);
console.log('Quotation editing restores the validity and terms, preserves item IDs, and locks issued quotes.');
