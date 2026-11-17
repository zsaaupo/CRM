// Run with Node: node warehouse/tests/transfer_form_ui.mjs
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import vm from 'node:vm';

const source = (await readFile(new URL('../static/warehouse/js/transfer_form.js', import.meta.url), 'utf8'))
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

const names = ['product', 'unit', 'quantity'];
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


async function setup({edit = true, completed = false, conflict = false, failLoad = false, historical = false, delayed = false, failSave = false} = {}) {
    const form = new Element('form');
    const fields = Object.fromEntries(['source_warehouse', 'destination_warehouse', 'transfer_date', 'notes', 'csrfmiddlewaretoken'].map(name => [name, new Element('input')]));
    fields.csrfmiddlewaretoken.value = 'csrf';
    const parts = Object.fromEntries(['status', 'header', 'submit', 'add-line', 'retry', 'lines'].map(name => [name, new Element()]));
    const spinner = new Element(), label = new Element(); label.textContent = edit ? 'Save changes' : 'Create transfer';
    form.action = edit ? '/update/7/' : '/create/';
    form.dataset = {method: edit ? 'PATCH' : 'POST', optionsUrl: '/options/', detailUrl: '/detail/7/', successUrl: '/warehouse/transfers/'};
    form.elements = {namedItem: name => fields[name]};
    form.querySelector = selector => selector === '[data-submit-spinner]' ? spinner : selector === '[data-submit-label]' ? label
        : parts[selector.match(/^\[data-transfer-(.*)\]$/)?.[1]];
    form.querySelectorAll = () => [];
    const warehouses = historical ? [{value: 2, label: 'Destination'}] : [{value: 1, label: 'Source'}, {value: 2, label: 'Destination'}];
    const options = {source_warehouse: warehouses, destination_warehouse: warehouses,
        product: [{value: 5, label: 'Rice', unit: 9, unit_label: 'KG'}, {value: 6, label: 'Box', unit: 10, unit_label: 'PCS'}]};
    const record = {id: 7, source_warehouse: 1, source_warehouse_name: 'Historical source', destination_warehouse: 2,
        destination_warehouse_name: 'Destination', transfer_date: '2026-11-17', notes: 'Saved note', is_completed: completed,
        items: Array.from({length: 26}, (_, index) => ({id: index + 101, line_number: index + 1, product: 5, unit: 9, unit_code: 'KG', quantity: '2.500000'}))};
    const calls = [], redirects = []; let release;
    const wait = delayed ? new Promise(resolve => {release = resolve;}) : Promise.resolve();
    const save = async (method, url, data, csrf) => {
        calls.push(JSON.parse(JSON.stringify({method, url, data, csrf}))); await wait;
        if (conflict) {record.is_completed = true; throw {status: 409};}
        if (failSave) throw {status: 400, errors: {items: [{}, {quantity: ['Invalid quantity.']}]}};
        return {};
    };
    const context = vm.createContext({document: {querySelector: () => null,
        getElementById: () => ({content: {firstElementChild: line()}}), createElement: tag => new Element(tag)},
        getJSON: async url => {if (failLoad) {failLoad = false; throw new Error('Connection failed');} return url === '/options/' ? options : record;},
        postJSON: (...args) => save('POST', ...args), patchJSON: (...args) => save('PATCH', ...args),
        window: {location: {assign: url => redirects.push(url)}},
    });
    vm.runInContext(source, context); context.testForm = form;
    await vm.runInContext('initializeTransferForm(testForm).ready', context);
    return {form, fields, parts, calls, redirects, release};
}
const event = {preventDefault() {}};
const editor = await setup({historical: true});
assert.equal(editor.parts.submit.disabled, false, editor.parts.status.textContent);
assert.equal(editor.parts.lines.children.length, 26);
assert.equal(editor.fields.source_warehouse.children.at(-1).textContent, 'Historical source (existing)');
await editor.form.listeners.submit(event);
assert.equal(editor.calls[0].method, 'PATCH'); assert.equal(editor.calls[0].csrf, 'csrf');
assert.equal(editor.calls[0].data.items.length, 26);
assert.equal(editor.calls[0].data.items[0].id, 101);
assert.equal(editor.calls[0].data.items[0].quantity, '2.500000');
assert.deepEqual(editor.redirects, ['/warehouse/transfers/']);
const creator = await setup({edit: false, delayed: true});
assert.equal(creator.parts.lines.children.length, 1);
assert.equal(creator.parts.lines.children[0].querySelector('[data-line-remove]').disabled, true);
let product = creator.parts.lines.children[0].querySelector('[data-line-field="product"]');
product.value = '5'; product.listeners.change();
assert.equal(String(creator.parts.lines.children[0].querySelector('[data-line-field="unit"]').value), '9');
creator.parts['add-line'].listeners.click();
product = creator.parts.lines.children[1].querySelector('[data-line-field="product"]'); product.value = '6'; product.listeners.change();
assert.equal(creator.parts.lines.children[1].querySelector('[data-line-unit-label]').value, 'PCS');
const saving = creator.form.listeners.submit(event);
await creator.form.listeners.submit(event); assert.equal(creator.calls.length, 1);
assert.equal(creator.parts.lines.children[0].disabled, true); creator.release(); await saving;
assert.equal(creator.calls[0].method, 'POST'); assert.equal(creator.calls[0].data.items.length, 2);
assert.equal(creator.calls[0].data.items[0].id, undefined);
assert.equal(creator.calls[0].data.items[1].line_number, 2);
const removal = await setup();
removal.parts.lines.children[0].querySelector('[data-line-remove]').listeners.click();
assert.equal(removal.parts.lines.children.length, 25);
await removal.form.listeners.submit(event);
assert.equal(removal.calls[0].data.items[0].id, 102);
assert.equal(removal.calls[0].data.items[0].line_number, 1);
const locked = await setup({completed: true});
assert.equal(locked.parts.submit.disabled, true); await locked.form.listeners.submit(event); assert.equal(locked.calls.length, 0);
const race = await setup({conflict: true}); await race.form.listeners.submit(event);
assert.equal(race.parts.submit.disabled, true); assert.match(race.parts.status.textContent, /read-only/);
assert.equal(race.redirects.length, 0);
const retry = await setup({failLoad: true});
assert.equal(retry.parts.submit.disabled, true); await retry.form.listeners.submit(event); assert.equal(retry.calls.length, 0);
await retry.parts.retry.listeners.click(); assert.equal(retry.parts.submit.disabled, false);
const error = await setup({failSave: true}); await error.form.listeners.submit(event);
assert.equal(error.parts.lines.children[1].querySelector('[data-line-error="quantity"]').textContent, 'Invalid quantity.');
assert.equal(error.parts.submit.disabled, false);
console.log('Multi-product transfer form passed: add/remove, units, IDs, 26 saved lines, CSRF, nested errors, retry, completion and duplicate-submit guards.');
const detailSource = (await readFile(new URL('../static/warehouse/js/transfer_detail.js', import.meta.url), 'utf8'))
    .replace(/^import .*;\r?\n/gm, '').replace(/^export /gm, '');
const rows = new Element();
const ctx = vm.createContext({document: {querySelector: () => null, createElement: tag => new Element(tag)}});
vm.runInContext(detailSource, ctx); ctx.testPanel = {querySelector: () => rows};
ctx.record = {items: [{line_number: 1, product_sku: 'P-1', product_name: '<Product>', quantity: '2.500001', unit_code: 'KG'},
    {line_number: 2, product_sku: 'P-2', product_name: 'Box', quantity: '3.000000', unit_code: 'PCS'}]};
vm.runInContext('renderTransferItems(testPanel, record)', ctx);
assert.equal(rows.children.length, 2); assert.equal(rows.children[0].children[3].textContent, '2.500001');
assert.equal(rows.children[0].children[2].textContent, '<Product>');
console.log('Transfer details render every product and its quantity safely.');
