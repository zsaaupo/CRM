// Run with Node: node warehouse/tests/transfer_complete_ui.mjs
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import vm from 'node:vm';

const source = (await readFile(new URL('../../inventory/static/inventory/js/catalog_list.js', import.meta.url), 'utf8'))
    .replace(/\r\n/g, '\n').replace(/^import .*;\n/gm, '').replace(/^export /gm, '')
    .replace(/\n    load\(\);\n\}/, '\n    globalThis.listReady = load();\n}');

class Element {
    constructor(tag = 'div') {
        this.tagName = tag.toUpperCase(); this.children = []; this.dataset = {}; this.listeners = {};
        this.attributes = {}; this.disabled = false; this.hidden = false; this.textContent = '';
        this.classList = { add() {}, remove() {} };
    }
    append(...nodes) { for (const node of nodes) { node.parent = this; this.children.push(node); } }
    replaceChildren(...nodes) { for (const node of this.children) node.parent = null; this.children = []; this.append(...nodes); }
    replaceWith(node) { const parent = this.parent; parent.children[parent.children.indexOf(this)] = node; node.parent = parent; this.parent = null; }
    get isConnected() { return this.connected || Boolean(this.parent?.isConnected); }
    closest(tag) { return this.tagName.toLowerCase() === tag ? this : this.parent?.closest(tag) || null; }
    setAttribute(name, value) { this.attributes[name] = value; }
    removeAttribute(name) { delete this.attributes[name]; }
    addEventListener(name, fn) { this.listeners[name] = fn; }
    focus() { this.focused = true; }
    querySelectorAll(selector) {
        const key = selector.match(/^\[data-([^\]]+)\]$/)?.[1]?.replace(/-([a-z])/g, (_, letter) => letter.toUpperCase());
        return this.children.flatMap(child => [...(key && Object.hasOwn(child.dataset, key) ? [child] : []), ...child.querySelectorAll(selector)]);
    }
    querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
}

async function setup({ permitted = true, fail = false, delayed = false, alreadyCompleted = false } = {}) {
    const panel = new Element(); panel.connected = true;
    panel.dataset = { apiUrl: '/list/', viewUrlTemplate: '/warehouse/transfers/0/', editUrlTemplate: permitted ? '/warehouse/transfers/0/edit/' : '',
        editDisabledField: 'is_completed', completeUrl: permitted ? '/complete/' : '', loginUrl: '/login/',
        deleteUrlTemplate: permitted ? '/warehouse/transfers/0/' : '', deleteDisabledField: 'is_completed', completeIdField: 'transfer_id', completeResultKey: 'warehouse_transfer', completeLabel: 'transfer' };
    const parts = Object.fromEntries(['rows', 'status', 'table', 'empty', 'error', 'pagination', 'count', 'previous', 'next',
        'range', 'error-message', 'feedback', 'retry', 'search'].map(name => [name, new Element()]));
    parts['delete-dialog'] = new Element('dialog');
    parts.search.elements = { namedItem: () => ({ value: 'SO' }) };
    const completeForm = new Element('form'); completeForm.elements = { namedItem: () => ({ value: 'csrf-token' }) };
    panel.append(...Object.values(parts), completeForm);
    panel.querySelector = selector => {
        const key = selector.match(/^\[data-catalog-(.*)\]$/)?.[1];
        return key === 'complete-form' ? permitted ? completeForm : null : parts[key] || null;
    };
    const records = [{ id: 7, transfer_number: 'WT-7', is_completed: false }, { id: 8, transfer_number: 'WT-8', is_completed: true }];
    const posts = []; let release;
    const wait = delayed ? new Promise(resolve => { release = resolve; }) : Promise.resolve();
    const context = vm.createContext({
        document: { querySelector: () => panel, createElement: tag => new Element(tag), createElementNS: (ns, tag) => new Element(tag),
            getElementById: () => ({ textContent: JSON.stringify([{ key: 'transfer_number', type: 'text' }, { key: 'actions', type: 'edit' }]) }) },
        window: { location: { origin: 'https://crm.test', replace() {} } }, URL,
        getJSON: async () => ({ count: 2, next: null, previous: null, results: structuredClone(records) }),
        postJSON: async (url, payload, csrf) => {
            posts.push({ url, payload: structuredClone(payload), csrf });
            await wait;
            if (fail) throw Object.assign(new Error('Failed'), { status: 400, errors: { quantity: ['Insufficient stock.'] } });
            records[0].is_completed = true;
            return { message: alreadyCompleted ? 'Transfer was already completed.' : 'Transfer completed and stock updated.',
                completed_now: !alreadyCompleted, warehouse_transfer: structuredClone(records[0]) };
        },
    });
    vm.runInContext(source, context);
    await context.listReady;
    assert.equal(parts.error.hidden, true, parts['error-message'].textContent);
    return { panel, parts, posts, release };
}

const button = row => row.querySelector('[data-catalog-complete-action]');
const editLinks = row => row.children.at(-1).children[0].children.filter(node => node.tagName === 'A' && node.href.includes('/edit/'));
const deleteButtons = row => row.children.at(-1).children[0].children.filter(node => node.className?.includes('inventory-delete-button'));
const editAction = row => row.querySelector('[data-catalog-edit-action]');
const list = await setup({ delayed: true });
assert.equal(button(list.parts.rows.children[0]).disabled, false);
assert.equal(button(list.parts.rows.children[0]).title, 'Complete transfer WT-7');
assert.equal(button(list.parts.rows.children[1]).disabled, true);
assert.equal(editLinks(list.parts.rows.children[0]).length, 1);
assert.equal(deleteButtons(list.parts.rows.children[0]).length, 1);
assert.equal(deleteButtons(list.parts.rows.children[1]).length, 1);
assert.equal(deleteButtons(list.parts.rows.children[1])[0].disabled, true);
assert.equal(editAction(list.parts.rows.children[1]).disabled, true);
assert.equal(editAction(list.parts.rows.children[1]).href, undefined);
await deleteButtons(list.parts.rows.children[1])[0].listeners.click();
const trigger = button(list.parts.rows.children[0]);
const receiving = trigger.listeners.click();
assert.equal(trigger.disabled, true);
assert.equal(trigger.attributes['aria-busy'], 'true');
await trigger.listeners.click();
assert.equal(list.posts.length, 1);
list.release();
await receiving;
assert.deepEqual(JSON.parse(JSON.stringify(list.posts[0])), { url: '/complete/', payload: { transfer_id: 7, is_completed: true }, csrf: 'csrf-token' });
assert.equal(button(list.parts.rows.children[0]).disabled, true);
assert.equal(editLinks(list.parts.rows.children[0]).length, 0);
assert.equal(deleteButtons(list.parts.rows.children[0]).length, 1);
assert.equal(deleteButtons(list.parts.rows.children[0])[0].disabled, true);
assert.equal(editAction(list.parts.rows.children[0]).disabled, true);
await deleteButtons(list.parts.rows.children[0])[0].listeners.click();
assert.equal(list.parts.feedback.dataset.tone, 'success');
await button(list.parts.rows.children[0]).listeners.click();
await button(list.parts.rows.children[1]).listeners.click();
assert.equal(list.posts.length, 1);

const failed = await setup({ fail: true });
await button(failed.parts.rows.children[0]).listeners.click();
assert.equal(button(failed.parts.rows.children[0]).disabled, false);
assert.equal(editLinks(failed.parts.rows.children[0]).length, 1);
assert.equal(deleteButtons(failed.parts.rows.children[0])[0].disabled, false);
assert.match(failed.parts.feedback.textContent, /Insufficient stock/);
assert.equal(failed.parts.feedback.dataset.tone, 'error');
const retried = await setup({ alreadyCompleted: true });
await button(retried.parts.rows.children[0]).listeners.click();
assert.equal(button(retried.parts.rows.children[0]).disabled, true);
assert.equal(editLinks(retried.parts.rows.children[0]).length, 0);
const readOnly = await setup({ permitted: false });
assert.equal(button(readOnly.parts.rows.children[0]), null);

const refreshed = await setup({ delayed: true });
const inProgress = button(refreshed.parts.rows.children[0]).listeners.click();
refreshed.parts.search.listeners.submit({ preventDefault() {}, currentTarget: refreshed.parts.search });
for (let tick = 0; tick < 10; tick++) await Promise.resolve();
assert.equal(button(refreshed.parts.rows.children[0]).disabled, true);
refreshed.release();
await inProgress;
assert.equal(button(refreshed.parts.rows.children[0]).disabled, true);
assert.equal(editLinks(refreshed.parts.rows.children[0]).length, 0);
assert.equal(editAction(refreshed.parts.rows.children[0]).disabled, true);
assert.equal(deleteButtons(refreshed.parts.rows.children[0])[0].disabled, true);
console.log('Transfer complete UI passed: permission visibility, enabled/disabled states, CSRF POST, double-click protection, success locking, errors and refresh during completion.');
