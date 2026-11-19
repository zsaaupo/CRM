// Run with Node: node sale/tests/sale_return_ui.mjs
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import vm from 'node:vm';

const source = (await readFile(new URL('../static/sale/js/sales_return.js', import.meta.url), 'utf8'))
    .replace(/^import .*;\r?\n/gm, '').replace(/^export /gm, '');

function element(tag = 'div') {
    return {tag, children: [], dataset: {}, listeners: {}, value: '', hidden: true, disabled: false,
        append(...nodes) { this.children.push(...nodes); },
        replaceChildren(...nodes) { this.children = nodes; },
        addEventListener(name, fn) { this.listeners[name] = fn; },
        setAttribute() {}, focus() {},
    };
}

const option = {value: 42, label: 'SO-42', warehouse: 'Main', order_date: '2026-11-01'};
const lines = [
    {id: 12, line_number: 1, product_name: 'Rice <5kg>', product_sku: 'RICE', unit_code: 'PCS', sold_quantity: '3.000000',
        returned_quantity: '1.000000', remaining_quantity: '2.000000', returnable: true},
    {id: 13, line_number: 2, product_name: 'Other', product_sku: 'OTHER', unit_code: 'PCS', sold_quantity: '1.000000',
        returned_quantity: '0.000000', remaining_quantity: '1.000000', returnable: false},
];
const original = {id: 8, sales_order: 42, return_date: '2026-11-02', notes: 'Draft notes', is_completed: false,
    items: [{sales_order_item: 12, quantity: '1.250000'}]};
const selectorKey = selector => selector.match(/^\[data-return-(.+)\]$/)?.[1];
const submit = form => form.listeners.submit({preventDefault() {}});

async function setup({editing = false, failOptions = false, stale = false} = {}) {
    const form = element('form');
    const parts = Object.fromEntries(['status', 'retry', 'fields', 'rows', 'submit', 'items', 'warehouse'].map(key => [key, element()]));
    const fields = {sales_order: element('select'), return_date: element('input'), notes: element('textarea'),
        csrfmiddlewaretoken: {value: 'csrf'}};
    fields.return_date.value = '2026-11-03';
    form.querySelector = selector => parts[selectorKey(selector)];
    form.elements = {namedItem: name => fields[name]};
    form.action = editing ? '/sale/api/sales-returns/8/update/' : '/sale/api/sales-returns/';
    form.dataset = {method: editing ? 'PATCH' : 'POST', detailUrl: editing ? '/detail/' : '',
        optionsUrl: '/options/', selectedOrder: '42', successUrl: '/sale/sales-returns/'};
    const redirects = [];
    const requests = [];
    let loadFailed = failOptions;
    let completed = false;
    const context = vm.createContext({
        testForm: form, URL, document: {querySelector: () => null, createElement: element},
        window: {location: {origin: 'https://crm.test', assign: url => redirects.push(url)}},
        getJSON: async url => {
            if (url === '/detail/') return {...original, is_completed: completed};
            if (loadFailed) { loadFailed = false; throw Error('Network error'); }
            if (editing || new URL(url, 'https://crm.test').searchParams.get('sales_order') === '42') return {order: option, lines};
            return {orders: [option]};
        },
        postJSON: async (url, data, token) => { requests.push({url, data, token}); },
        patchJSON: async (url, data, token) => {
            requests.push({url, data, token});
            if (stale) {
                completed = true;
                throw Object.assign(Error('Conflict'), {status: 409});
            }
        },
    });
    vm.runInContext(source, context);
    await vm.runInContext('initializeReturnForm(testForm)', context);
    return {form, parts, fields, redirects, requests,
        quantity: index => parts.rows.children[index].children[6].children[0]};
}

const draft = await setup({failOptions: true});
assert.equal(draft.parts.submit.disabled, true);
assert.equal(draft.parts.retry.hidden, false);
await draft.parts.retry.listeners.click();
assert.equal(draft.fields.sales_order.value, '42');
assert.equal(draft.quantity(0).max, '2.000000');
assert.equal(draft.quantity(1).disabled, true);
assert.equal(draft.parts.warehouse.textContent, 'Restocking warehouse: Main');
await submit(draft.form);
assert.equal(draft.requests.length, 0);
draft.quantity(0).value = '0.125001';
await submit(draft.form);
assert.equal(draft.requests[0].token, 'csrf');
assert.equal(draft.requests[0].data.sales_order, '42');
assert.equal(draft.requests[0].data.items[0].quantity, '0.125001');
assert.deepEqual(draft.redirects, ['/sale/sales-returns/']);

const editor = await setup({editing: true, stale: true});
assert.equal(editor.fields.sales_order.disabled, true);
assert.equal(editor.quantity(0).value, '1.250000');
await submit(editor.form);
assert.equal(editor.requests[0].data.sales_order, undefined);
assert.equal(editor.parts.submit.disabled, true);
assert.match(editor.parts.status.textContent, /completed/);
assert.deepEqual(editor.redirects, []);

const panel = element();
const complete = element('form');
const completeButton = element('button');
const edit = element('a');
const detailRows = element('tbody');
const status = element('p');
const detailCalls = [];
panel.querySelector = selector => ({edit, complete, lines: detailRows})[selectorKey(selector)];
complete.querySelector = selector => selector === 'button[type="submit"]' ? completeButton : status;
complete.elements = {namedItem: () => ({value: 'csrf'})};
complete.action = '/sale/api/sales-returns/complete/';
const context = vm.createContext({panel, document: {querySelector: () => null, createElement: element},
    detailValue: value => String(value), initializeRecordDetail: (_, callback) => callback({...original, items: [{...lines[0], quantity: '1'}]}),
    postJSON: async (url, payload, token) => {
        detailCalls.push({url, payload, token});
        return {sales_return: {id: 8, is_completed: true}};
    },
    window: {location: {reload: () => detailCalls.push('reloaded')}},
});
vm.runInContext(source, context);
vm.runInContext('initializeReturnDetail(panel)', context);
assert.equal(edit.hidden, false);
assert.equal(detailRows.children[0].children[1].textContent, 'Rice <5kg>');
await Promise.all([submit(complete), submit(complete)]);
assert.equal(detailCalls.length, 2);
assert.equal(detailCalls[0].payload.return_id, 8);
assert.equal(detailCalls[0].payload.is_completed, true);
assert.equal(detailCalls[0].token, 'csrf');
assert.equal(detailCalls[1], 'reloaded');
console.log('Sales return UI passed: options retry, exact quantities, draft redirect, completed edit lock and once-only submission.');
