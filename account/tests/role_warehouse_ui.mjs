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


const scopeSource = (await readFile(new URL('../static/account/js/role_warehouse_access.js', import.meta.url), 'utf8'))
    .replace(/^import .*;\r?\n/gm, '').replace(/^export /gm, '');
const normalized = value => JSON.parse(JSON.stringify(value));
const event = {preventDefault() {}};
async function setup({existing = false, delayedLoad = false, failLoad = false, failSave = false,
                      delayedSave = false, restricted = false, wrongRole = false} = {}) {
    const panel = new Element(); panel.dataset = {apiUrl: '/permissions/', roleUrl: '/role/7/'};
    const form = new Element(); form.action = '/role/7/permissions/'; form.dataset.successUrl = '/roles/';
    form.elements = {namedItem: () => ({value: 'csrf-token'})};
    const parts = Object.fromEntries(['permissions-save', 'permissions-reset', 'permission-fields', 'permissions-retry',
        'permissions-save-label', 'permissions-save-spinner', 'selection-count', 'permissions-status', 'permissions-table',
        'permissions-empty', 'permissions-error', 'permissions-feedback', 'permissions-role-name',
        'custom-permissions-heading', 'permission-rows', 'permissions-error-message'].map(name => [name, new Element()]));
    const scopePanel = new Element(); scopePanel.dataset = {roleId: '7', optionsUrl: '/scope-options/?role=7'};
    const scopeParts = Object.fromEntries(['mode', 'select', 'fields', 'retry', 'select-wrap', 'all', 'feedback']
        .map(name => [name, new Element()]));
    scopePanel.querySelector = selector => scopeParts[selector.match(/^\[data-warehouse-(.*)\]$/)?.[1]];
    form.append(parts['permission-rows']);
    panel.querySelector = selector => selector === '[data-permissions-form]' ? form
        : selector === '[data-role-warehouse-access]' ? scopePanel : parts[selector.slice(6, -1)];
    const calls = []; const redirects = []; let releaseLoad, releaseSave;
    const loadWait = delayedLoad ? new Promise(resolve => {releaseLoad = resolve;}) : Promise.resolve();
    const saveWait = delayedSave ? new Promise(resolve => {releaseSave = resolve;}) : Promise.resolve();
    const context = vm.createContext({document: {querySelector: () => panel, createElement: () => new Element()},
        getJSON: async url => {
            if (url === '/permissions/') return [{id: 45, content_type: 8, app_label: 'warehouse', model: 'stock', codename: 'view_stock', name: 'Can view stock'}];
            if (url === '/role/7/') return {name: 'Warehouse readers', permissions: [45]};
            await loadWait;
            if (failLoad) {failLoad = false; throw new Error('Connection failed');}
            return {role: wrongRole ? 8 : 7, assignment: existing ? {id: 91, role: 7, warehouse: 1, all_warehouses: false} : null,
                warehouse: [{value: 1, label: '<UTT>'}], can_create: true, can_change: true, can_assign_all: !restricted};
        },
        putJSON: async (url, payload, csrf) => {
            calls.push(normalized({url, payload, csrf})); await saveWait;
            if (failSave) throw {status: 400, errors: {warehouse_access: {warehouse: ['Choose an allowed warehouse.']}}};
            return {permissions: payload.permissions, warehouse_access: {id: 91, role: 7, ...payload.warehouse_access}};
        },
        window: {location: {assign: url => redirects.push(url)}},
    });
    vm.runInContext(scopeSource, context);
    const initialize = context.initializeRoleWarehouseAccess;
    context.initializeRoleWarehouseAccess = (...args) => {
        const controller = initialize(...args); context.controller = controller; return controller;
    };
    vm.runInContext(source, context); await context.ready;
    if (!delayedLoad) await context.controller.ready;
    return {form, parts, scopeParts, calls, redirects, releaseLoad, releaseSave, context,
        checkbox: form.querySelectorAll()[0]};
}
const initial = await setup({delayedLoad: true});
assert.equal(initial.parts['permissions-save'].disabled, true);
await initial.form.listeners.submit(event); assert.equal(initial.calls.length, 0);
initial.releaseLoad(); await initial.context.controller.ready;
assert.equal(initial.parts['permissions-save'].disabled, false);
assert.equal(initial.scopeParts.feedback.hidden, true);
assert.equal(initial.scopeParts.select.children[1].textContent, '<UTT>');
initial.scopeParts.mode.value = 'specific'; initial.scopeParts.mode.listeners.change();
await initial.form.listeners.submit(event); assert.equal(initial.calls.length, 0);
initial.scopeParts.select.value = '1'; await initial.form.listeners.submit(event);
assert.deepEqual(initial.calls, [{url: '/role/7/permissions/', payload: {permissions: [45],
    warehouse_access: {warehouse: 1, all_warehouses: false}}, csrf: 'csrf-token'}]);
assert.deepEqual(initial.redirects, ['/roles/']);
const update = await setup({existing: true, delayedSave: true});
update.scopeParts.mode.value = 'all'; update.checkbox.checked = false;
const pending = update.form.listeners.submit(event);
assert.equal(update.parts['permissions-save'].disabled, true);
assert.equal(update.scopeParts.fields.disabled, true);
await update.form.listeners.submit(event); assert.equal(update.calls.length, 1);
update.releaseSave(); await pending;
assert.deepEqual(update.calls[0].payload, {permissions: [], warehouse_access: {warehouse: null, all_warehouses: true}});
const reset = await setup({existing: true});
reset.checkbox.checked = false; reset.scopeParts.mode.value = 'none';
reset.parts['permissions-reset'].listeners.click();
assert.equal(reset.checkbox.checked, true); assert.equal(reset.scopeParts.mode.value, 'specific');
assert.equal(reset.scopeParts.select.value, '1');
const failed = await setup({failSave: true});
await failed.form.listeners.submit(event);
assert.equal(failed.redirects.length, 0); assert.equal(failed.parts['permissions-save'].disabled, false);
assert.match(failed.parts['permissions-feedback'].textContent, /allowed warehouse/);
const retry = await setup({failLoad: true});
assert.equal(retry.parts['permissions-save'].disabled, true);
await retry.scopeParts.retry.listeners.click(); assert.equal(retry.parts['permissions-save'].disabled, false);
const restricted = await setup({restricted: true});
restricted.scopeParts.mode.value = 'all'; await restricted.form.listeners.submit(event);
assert.equal(restricted.calls.length, 0);
const wrong = await setup({wrongRole: true}); assert.equal(wrong.parts['permissions-save'].disabled, true);
console.log('Combined permissions UI passed: one PUT, CSRF, loading, validation, reset, double-submit, failures, retry and warehouse limits.');
