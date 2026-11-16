// Run with Node: node account/tests/edit_user_ui.mjs
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import vm from 'node:vm';
import {URL} from 'node:url';

const source = (await readFile(new URL('../static/account/js/edit_user.js', import.meta.url), 'utf8'))
    .replace(/^import .*;\r?\n/gm, '').replace('    loadUser();\n}', '    globalThis.ready = loadUser();\n}');
class Element {
    constructor() { this.listeners = {}; this.dataset = {}; this.attributes = {}; this.value = '';
        this.hidden = false; this.disabled = false; this.textContent = ''; this.files = []; }
    addEventListener(name, callback) { this.listeners[name] = callback; }
    setAttribute(name, value) { this.attributes[name] = value; }
    removeAttribute(name) { delete this.attributes[name]; }
    setCustomValidity(message) { this.validationMessage = message; }
    checkValidity() { return !this.validationMessage; }
    focus() { this.focused = true; }
}
class TestFormData {
    constructor() { this.fields = new Map(); }
    append(name, value) { this.fields.set(name, value); }
    get(name) { return this.fields.get(name); }
    has(name) { return this.fields.has(name); }
}
async function setup({failLoad = false, failSave = false, delayed = false, image = null} = {}) {
    const form = new Element(); form.action = '/api/users/7/update/'; form.dataset.successUrl = '/users/';
    const fields = Object.fromEntries(['full_name', 'email', 'phone_number', 'designation', 'date_of_birth', 'gender',
        'address', 'city', 'state', 'postal_code', 'country', 'image', 'csrfmiddlewaretoken'].map(name => {
            const element = new Element(); element.name = name; return [name, element];
        }));
    fields.full_name.required = true; fields.email.required = true;
    fields.csrfmiddlewaretoken.value = 'csrf-token';
    form.elements = {namedItem: name => fields[name]};
    const parts = Object.fromEntries(['edit-fields', 'edit-save', 'edit-retry', 'button-label', 'button-spinner',
        'button-arrow', 'form-status', 'edit-image', 'edit-initials'].map(name => [name, new Element()]));
    const errors = Object.keys(fields).map(name => { const element = new Element(); element.dataset.errorFor = name; return element; });
    form.querySelector = selector => parts[selector.slice(6, -1)];
    form.querySelectorAll = selector => selector === '[data-error-for]' ? errors : [];
    const calls = [], redirects = []; let release;
    const wait = delayed ? new Promise(resolve => {release = resolve;}) : Promise.resolve();
    const context = vm.createContext({FormData: TestFormData, document: {querySelector: () => form},
        getJSON: async () => { if (failLoad) {failLoad = false; throw {status: 404};}
            return {full_name: 'Avery Morgan', email: 'avery@example.com', phone_number: '123', city: 'Dhaka', image}; },
        patchFormData: async (url, data, csrf) => { calls.push({url, data, csrf}); await wait;
            if (failSave) throw {status: 400, errors: {email: ['Already in use.']}}; return {}; },
        window: {location: {assign: url => redirects.push(url)}},
    });
    vm.runInContext(source, context); await context.ready;
    return {form, fields, parts, calls, redirects, errors, release};
}
const event = {preventDefault() {}};
const edit = await setup({delayed: true, image: '/media/avatar.png'});
assert.equal(edit.fields.full_name.value, 'Avery Morgan');
assert.equal(edit.fields.date_of_birth.value, '');
assert.equal(edit.parts['edit-save'].disabled, false);
assert.equal(edit.parts['edit-image'].src, '/media/avatar.png');
edit.parts['edit-image'].listeners.error(); assert.equal(edit.parts['edit-image'].hidden, true);
assert.equal(edit.parts['edit-initials'].textContent, 'AM');
edit.fields.full_name.value = 'Edited User';
const saving = edit.form.listeners.submit(event);
assert.equal(edit.parts['edit-fields'].disabled, true);
await edit.form.listeners.submit(event); assert.equal(edit.calls.length, 1);
edit.release(); await saving;
assert.equal(edit.calls[0].url, '/api/users/7/update/');
assert.equal(edit.calls[0].csrf, 'csrf-token');
assert.equal(edit.calls[0].data.get('full_name'), 'Edited User');
assert.equal(edit.calls[0].data.get('date_of_birth'), '');
assert.equal(edit.calls[0].data.has('image'), false);
assert.equal(edit.calls[0].data.has('password'), false);
assert.deepEqual(edit.redirects, ['/users/']);
const invalid = await setup(); invalid.fields.full_name.value = '   ';
await invalid.form.listeners.submit(event); assert.equal(invalid.calls.length, 0);
assert.equal(invalid.fields.full_name.focused, true);
const failed = await setup({failSave: true});
await failed.form.listeners.submit(event); assert.equal(failed.redirects.length, 0);
assert.equal(failed.parts['edit-save'].disabled, false);
assert.equal(failed.fields.email.attributes['aria-invalid'], 'true');
assert.match(failed.errors.find(error => error.dataset.errorFor === 'email').textContent, /Already in use/);
const retry = await setup({failLoad: true});
assert.equal(retry.parts['edit-save'].disabled, true);
await retry.form.listeners.submit(event); assert.equal(retry.calls.length, 0);
assert.match(retry.parts['form-status'].textContent, /no longer exists/);
await retry.parts['edit-retry'].listeners.click(); assert.equal(retry.parts['edit-save'].disabled, false);
const picture = await setup(); picture.fields.image.files = [{name: 'avatar.png', type: 'image/png'}];
await picture.form.listeners.submit(event); assert.equal(picture.calls[0].data.has('image'), true);
console.log('Edit user UI passed: API loading, profile values, multipart/CSRF, image fallback, validation, duplicate submit, errors, retry and redirect.');

// Exercise the actual directory renderer so each pencil targets that row's user ID.
const directorySource = (await readFile(new URL('../static/account/js/users.js', import.meta.url), 'utf8'))
    .replace(/^import .*;\r?\n/gm, '').replace(/    loadUsers\(\);\r?\n\}/, '    globalThis.ready = loadUsers();\n}');
async function directory(superuser) {
    class Node extends Element {
        constructor() { super(); this.children = []; this.classList = {add() {}, remove() {}}; }
        append(...nodes) { this.children.push(...nodes); }
        replaceChildren(...nodes) { this.children = nodes; }
        remove() { this.removed = true; }
    }
    const root = new Node(); root.dataset = {apiUrl: '/api/users/', currentUserId: '2', superuser: String(superuser),
        editUserUrl: '/users/0/edit/', assignRoleUrl: '/users/0/assign-role/'};
    const nodes = Object.fromEntries(['user-search', 'user-rows', 'list-table', 'list-status', 'list-error', 'list-empty',
        'list-pagination', 'list-previous', 'list-next', 'user-count', 'list-range', 'list-retry', 'list-error-message']
        .map(name => [name, new Node()]));
    root.querySelector = selector => nodes[selector.slice(6, -1)];
    const links = [];
    const context = vm.createContext({URL, document: {querySelector: () => root, createElement: () => new Node(),
        getElementById: () => ({content: {cloneNode: () => {
            const edit = new Node(); links.push(edit);
            return {querySelector: selector => selector === '[data-edit-user]' ? edit : null};
        }}})},
        getJSON: async () => ({count: 2, next: null, previous: null, results: [
            {id: 7, full_name: 'Ordinary User', is_active: true, date_joined: '2026-01-01'},
            {id: 9, full_name: 'Admin User', is_superuser: true, is_active: true, date_joined: '2026-01-01'},
        ]}), window: {location: {origin: 'http://localhost'}},
    });
    vm.runInContext(directorySource, context); await context.ready; return {links, nodes};
}
const ordinary = await directory(false);
assert.equal(ordinary.links.length, 2, ordinary.nodes['list-error-message'].textContent);
assert.equal(ordinary.links[0].href, '/users/7/edit/');
assert.equal(ordinary.links[0].attributes['aria-label'], 'Edit Ordinary User');
assert.equal(ordinary.links[1].removed, true);
const admin = await directory(true); assert.equal(admin.links[1].href, '/users/9/edit/');
assert.equal(admin.links[1].removed, undefined);
console.log('User list pencil links target each user and protect superuser rows.');
