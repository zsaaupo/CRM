// Run with Node: node purchase/tests/purchase_receive_ui.mjs
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

async function setup({ permitted = true, fail = false, delayed = false, alreadyReceived = false } = {}) {
    const panel = new Element(); panel.connected = true;
    panel.dataset = { apiUrl: '/list/', viewUrlTemplate: '/purchase/0/', editUrlTemplate: permitted ? '/purchase/0/edit/' : '',
        editDisabledField: 'is_received', receiveUrl: permitted ? '/receive/' : '', loginUrl: '/login/',
        deleteUrlTemplate: permitted ? '/purchase/0/' : '', deleteDisabledField: 'is_received' };
    const parts = Object.fromEntries(['rows', 'status', 'table', 'empty', 'error', 'pagination', 'count', 'previous', 'next',
        'range', 'error-message', 'feedback', 'retry', 'search'].map(name => [name, new Element()]));
    parts['delete-dialog'] = new Element('dialog');
    parts.search.elements = { namedItem: () => ({ value: 'PO' }) };
    const receiveForm = new Element('form'); receiveForm.elements = { namedItem: () => ({ value: 'csrf-token' }) };
    panel.append(...Object.values(parts), receiveForm);
    panel.querySelector = selector => {
        const key = selector.match(/^\[data-catalog-(.*)\]$/)?.[1];
        return key === 'receive-form' ? permitted ? receiveForm : null : parts[key] || null;
    };
    const records = [{ id: 7, order_number: 'PO-7', is_received: false }, { id: 8, order_number: 'PO-8', is_received: true }];
    const posts = []; let release;
    const wait = delayed ? new Promise(resolve => { release = resolve; }) : Promise.resolve();
    const context = vm.createContext({
        document: { querySelector: () => panel, createElement: tag => new Element(tag), createElementNS: (ns, tag) => new Element(tag),
            getElementById: () => ({ textContent: JSON.stringify([{ key: 'order_number', type: 'text' }, { key: 'actions', type: 'edit' }]) }) },
        window: { location: { origin: 'https://crm.test', replace() {} } }, URL,
        getJSON: async () => ({ count: 2, next: null, previous: null, results: structuredClone(records) }),
        postJSON: async (url, payload, csrf) => {
            posts.push({ url, payload: structuredClone(payload), csrf });
            await wait;
            if (fail) throw Object.assign(new Error('Failed'), { status: 400, errors: { warehouse: ['Set the receiving warehouse first.'] } });
            records[0].is_received = true;
            return { message: alreadyReceived ? 'Purchase was already received.' : 'Purchase received and stock updated.',
                received_now: !alreadyReceived, purchase_order: structuredClone(records[0]) };
        },
    });
    vm.runInContext(source, context);
    await context.listReady;
    assert.equal(parts.error.hidden, true, parts['error-message'].textContent);
    return { panel, parts, posts, release };
}

const button = row => row.querySelector('[data-catalog-receive-action]');
const editLinks = row => row.children.at(-1).children[0].children.filter(node => node.tagName === 'A' && node.href.includes('/edit/'));
const editAction = row => row.querySelector('[data-catalog-edit-action]');
const deleteAction = row => row.querySelector('[data-catalog-delete-action]');
const list = await setup({ delayed: true });
assert.equal(button(list.parts.rows.children[0]).disabled, false);
assert.equal(button(list.parts.rows.children[1]).disabled, true);
assert.equal(editLinks(list.parts.rows.children[0]).length, 1);
assert.equal(deleteAction(list.parts.rows.children[0]).disabled, false);
assert.equal(editAction(list.parts.rows.children[1]).disabled, true);
assert.equal(editAction(list.parts.rows.children[1]).href, undefined);
assert.equal(deleteAction(list.parts.rows.children[1]).disabled, true);
await deleteAction(list.parts.rows.children[1]).listeners.click();
const trigger = button(list.parts.rows.children[0]);
const receiving = trigger.listeners.click();
assert.equal(trigger.disabled, true);
assert.equal(trigger.attributes['aria-busy'], 'true');
await trigger.listeners.click();
assert.equal(list.posts.length, 1);
list.release();
await receiving;
assert.deepEqual(JSON.parse(JSON.stringify(list.posts[0])), { url: '/receive/', payload: { purchase_id: 7, is_received: true }, csrf: 'csrf-token' });
assert.equal(button(list.parts.rows.children[0]).disabled, true);
assert.equal(editLinks(list.parts.rows.children[0]).length, 0);
assert.equal(editAction(list.parts.rows.children[0]).disabled, true);
assert.equal(deleteAction(list.parts.rows.children[0]).disabled, true);
await deleteAction(list.parts.rows.children[0]).listeners.click();
assert.equal(list.parts.feedback.dataset.tone, 'success');
await button(list.parts.rows.children[0]).listeners.click();
await button(list.parts.rows.children[1]).listeners.click();
assert.equal(list.posts.length, 1);

const failed = await setup({ fail: true });
await button(failed.parts.rows.children[0]).listeners.click();
assert.equal(button(failed.parts.rows.children[0]).disabled, false);
assert.equal(editLinks(failed.parts.rows.children[0]).length, 1);
assert.equal(deleteAction(failed.parts.rows.children[0]).disabled, false);
assert.match(failed.parts.feedback.textContent, /receiving warehouse/);
assert.equal(failed.parts.feedback.dataset.tone, 'error');
const retried = await setup({ alreadyReceived: true });
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
assert.equal(deleteAction(refreshed.parts.rows.children[0]).disabled, true);
console.log('Purchase receive UI passed: permission visibility, enabled/disabled states, CSRF POST, double-click protection, success locking, errors and refresh during receipt.');
