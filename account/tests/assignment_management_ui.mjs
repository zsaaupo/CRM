// Run with Node: node account/tests/assignment_management_ui.mjs
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import vm from 'node:vm';

class Element {
    constructor() { this.children = []; this.dataset = {}; this.listeners = {}; this.attributes = {};
        this.disabled = false; this.hidden = false; this.value = ''; this.textContent = '';
        this.classList = {add() {}, remove() {}}; }
    append(...nodes) { this.children.push(...nodes); }
    replaceChildren(...nodes) { this.children = nodes; }
    addEventListener(name, fn) { this.listeners[name] = fn; }
    setAttribute(name, value) { this.attributes[name] = value; }
    focus() {}
    querySelectorAll() { return this.children.flatMap(node => [
        ...(node.name === 'permissions' ? [node] : []), ...node.querySelectorAll(),
    ]); }
}

const source = (await readFile(new URL('../static/account/js/permissions.js', import.meta.url), 'utf8'))
    .replace(/^import .*;\r?\n/gm, '').replace(/^export /gm, '')
    .replace(/    loadPermissions\(\);\r?\n\}/, '    globalThis.ready = loadPermissions();\n}');

async function permissionEditor(checked) {
    const panel = new Element(); panel.dataset = {apiUrl: '/permissions/', roleUrl: '/role/7/'};
    const form = new Element(); form.action = '/role/7/permissions/'; form.dataset.successUrl = '/roles/';
    form.elements = {namedItem: () => ({value: 'csrf-token'})};
    const parts = Object.fromEntries(['permissions-save', 'permissions-reset', 'permission-fields', 'permissions-retry',
        'permissions-save-label', 'permissions-save-spinner', 'selection-count', 'permissions-status', 'permissions-table',
        'permissions-empty', 'permissions-error', 'permissions-feedback', 'permissions-role-name',
        'custom-permissions-heading', 'permission-rows', 'permissions-error-message'].map(name => [name, new Element()]));
    form.append(parts['permission-rows']);
    panel.querySelector = selector => selector === '[data-permissions-form]' ? form : parts[selector.slice(6, -1)];
    const calls = []; const redirects = [];
    const permission = {id: 45, content_type: 8, app_label: 'warehouse', model: 'rolewarehouseaccess',
        codename: 'manage_warehouse_access', name: 'Manage warehouse assignments'};
    const context = vm.createContext({document: {querySelector: () => panel, createElement: () => new Element()},
        getJSON: async url => url === '/permissions/' ? [permission] : {name: 'Warehouse managers', permissions: checked ? [45] : []},
        putJSON: async (url, payload, csrf) => { calls.push({url, payload: structuredClone(payload), csrf}); return payload; },
        window: {location: {assign: url => redirects.push(url)}},
    });
    vm.runInContext(source, context); await context.ready;
    const checkbox = form.querySelectorAll()[0];
    assert.equal(checkbox.name, 'permissions');
    assert.equal(checkbox.title, 'Manage warehouse assignments');
    assert.equal(parts['custom-permissions-heading'].hidden, false);
    return {checkbox, form, parts, calls, redirects};
}

const event = {preventDefault() {}};
const grant = await permissionEditor(false);
assert.equal(grant.checkbox.checked, false);
grant.checkbox.checked = true;
const saving = grant.form.listeners.submit(event);
await grant.form.listeners.submit(event); await saving;
assert.deepEqual(JSON.parse(JSON.stringify(grant.calls)), [{url: '/role/7/permissions/', payload: {permissions: [45]}, csrf: 'csrf-token'}]);
const revoke = await permissionEditor(true);
assert.equal(revoke.checkbox.checked, true);
revoke.checkbox.checked = false;
revoke.parts['permissions-reset'].listeners.click();
assert.equal(revoke.checkbox.checked, true);
revoke.checkbox.checked = false;
await revoke.form.listeners.submit(event);
assert.deepEqual(JSON.parse(JSON.stringify(revoke.calls[0].payload)), {permissions: []});

console.log('Management checkbox load, grant, revoke, reset and single save passed.');
