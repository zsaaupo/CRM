// Run with Node: node hrm/tests/hrm_ui.mjs
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import vm from 'node:vm';

const formSource = (await readFile(new URL('../../inventory/static/inventory/js/catalog_form.js', import.meta.url), 'utf8'))
    .replace(/^import .*;\r?\n/gm, '');
const listSource = (await readFile(new URL('../../inventory/static/inventory/js/catalog_list.js', import.meta.url), 'utf8'))
    .replace(/^import .*;\r?\n/gm, '').replace(/^export /gm, '');

class Element {
    constructor(tag = 'div') {
        this.tagName = tag.toUpperCase(); this.dataset = {}; this.children = []; this.listeners = {};
        this.attributes = {}; this.hidden = false; this.disabled = false; this.type = 'text'; this.files = [];
        this.textContent = ''; this.value = ''; this.valid = true;
    }
    set value(value) { this._value = String(value); }
    get value() { return this._value; }
    append(...nodes) { for (const node of nodes) { node.parent = this; this.children.push(node); } }
    replaceChildren(...nodes) { this.children = []; this.append(...nodes); }
    setAttribute(name, value) { this.attributes[name] = value; }
    removeAttribute(name) { delete this.attributes[name]; }
    addEventListener(name, callback) { this.listeners[name] = callback; }
    checkValidity() { return this.valid; }
    focus() { this.focused = true; }
    remove() { this.parent.children = this.parent.children.filter(node => node !== this); }
}

class Payload {
    constructor() { this.entries = new Map(); }
    append(name, value) { this.entries.set(name, value); }
    get(name) { return this.entries.get(name); }
    has(name) { return this.entries.has(name); }
}

class APIError extends Error {
    constructor(status, errors = {}) { super('Request failed'); this.status = status; this.errors = errors; }
}

async function setup({ edit = false, missingOptions = false, failLoad = false, saveError = false, linkedUser = false } = {}) {
    const form = new Element('form');
    form.action = edit ? '/hrm/api/employees/7/update/' : '/hrm/api/employees/';
    form.dataset = { optionsUrl: '/options/', detailUrl: edit ? '/detail/' : '',
        method: edit ? 'PATCH' : 'POST', successUrl: '/hrm/employees/' };
    const names = ['employee_code', 'full_name', 'image', 'work_email', 'work_phone', 'joining_date', 'leaving_date',
        'department', 'designation', 'employment_type', 'status', 'reports_to', 'warehouse', 'workplace'];
    if (linkedUser) names.push('user');
    const fields = Object.fromEntries(names.map(name => {
        const field = new Element('input'); field.name = name;
        return [name, field];
    }));
    fields.employee_code.value = 'EMP-7'; fields.full_name.value = 'Employee Name';
    fields.joining_date.type = 'date'; fields.joining_date.value = '2026-11-17';
    fields.leaving_date.type = 'date'; fields.leaving_date.dataset.nullableField = 'true';
    fields.image.type = 'file';
    for (const name of ['department', 'designation', 'employment_type', 'status', 'reports_to', 'warehouse', ...(linkedUser ? ['user'] : [])]) {
        fields[name].tagName = 'SELECT'; fields[name].dataset.label = name;
    }
    fields.employment_type.required = true; fields.employment_type.dataset.default = 'full-time';
    fields.status.required = true; fields.status.dataset.default = 'active';
    const status = new Element(); const submit = new Element('button'); const label = new Element();
    label.textContent = edit ? 'Save changes' : 'Add employee';
    const spinner = new Element(); const retry = new Element('button'); const image = new Element('img'); const preview = new Element();
    const errors = Object.fromEntries(names.map(name => {
        const error = new Element(); error.dataset.errorFor = name; return [name, error];
    }));
    const csrf = {value: 'csrf'};
    form.elements = {namedItem: name => name === 'csrfmiddlewaretoken' ? csrf : fields[name]};
    const parts = {
        '[data-catalog-form-status]': status, '[data-catalog-submit]': submit, '[data-submit-label]': label,
        '[data-submit-spinner]': spinner, '[data-catalog-load-retry]': retry,
        '[data-current-image-preview]': image, '[data-current-image]': preview,
    };
    form.querySelector = selector => parts[selector] || Object.values(fields).find(field => field.attributes['aria-invalid'] === 'true') || null;
    form.querySelectorAll = selector => selector === 'input, select, textarea' ? Object.values(fields)
        : selector === '[data-options-field]' ? Object.values(fields).filter(field => field.tagName === 'SELECT')
        : selector === '[data-error-for]' ? Object.values(errors) : [];
    const choices = {department: [], designation: [], warehouse: [], user: [],
        reports_to: [{value: 88, label: 'Archived employee (existing)'}],
        employment_type: missingOptions ? [] : [{value: 'full-time', label: 'Full-time'}],
        status: [{value: 'active', label: 'Active'}]};
    const record = {id: 7, employee_code: 'EMP-7', full_name: 'Existing Employee', employment_type: 'full-time', status: 'active',
        joining_date: '2026-01-01', leaving_date: null, reports_to: 88, image: '/media/employee_pictures/person.png'};
    const saved = []; const redirects = []; let loadFailed = failLoad;
    const context = vm.createContext({
        document: {querySelector: () => form, createElement: tag => new Element(tag)}, APIError, FormData: Payload,
        window: {location: {assign: url => redirects.push(url)}},
        getJSON: async url => {
            if (loadFailed) { loadFailed = false; throw new APIError(403); }
            return structuredClone(url === '/options/' ? choices : record);
        },
        postFormData: async (url, data, token) => { assert.equal(token, 'csrf'); saved.push({method: 'POST', url, data}); },
        postJSON: async () => { throw new Error('Employee creation should send multipart data'); },
        patchFormData: async (url, data, token) => { assert.equal(token, 'csrf'); saved.push({method: 'PATCH-FILE', url, data}); },
        patchJSON: async (url, data, token) => {
            assert.equal(token, 'csrf');
            if (saveError) throw new APIError(400, {full_name: ['Invalid employee name.']});
            saved.push({method: 'PATCH', url, data});
        },
    });
    vm.runInContext(formSource, context);
    await new Promise(resolve => setImmediate(resolve));
    return {form, fields, saved, redirects, status, submit, retry, errors, image, preview};
}

const event = {preventDefault() {}};
const creator = await setup();
assert.equal(creator.submit.disabled, false, creator.status.textContent);
assert.equal(creator.fields.employment_type.value, 'full-time');
assert.equal(creator.fields.status.value, 'active');
creator.fields.image.files = [{name: 'new.png'}];
await creator.form.listeners.submit(event);
assert.equal(creator.saved[0].method, 'POST');
assert.equal(creator.saved[0].data.get('image').name, 'new.png');
assert.equal(creator.saved[0].data.has('leaving_date'), false);
assert.equal(creator.saved[0].data.has('user'), false);
assert.deepEqual(creator.redirects, ['/hrm/employees/']);

const editor = await setup({edit: true});
assert.equal(editor.fields.full_name.value, 'Existing Employee');
assert.equal(editor.fields.reports_to.value, '88');
assert.equal(editor.fields.leaving_date.value, '');
assert.equal(editor.image.src, '/media/employee_pictures/person.png');
assert.equal(editor.preview.hidden, false);
await editor.form.listeners.submit(event);
assert.equal(editor.saved[0].method, 'PATCH');
assert.equal(editor.saved[0].data.leaving_date, null);
assert.equal(editor.saved[0].data.reports_to, '88');
assert.deepEqual(editor.redirects, ['/hrm/employees/']);

const failed = await setup({failLoad: true});
assert.equal(failed.submit.disabled, true);
await failed.form.listeners.submit(event);
assert.equal(failed.saved.length, 0);
await failed.retry.listeners.click();
assert.equal(failed.submit.disabled, false);

const missing = await setup({missingOptions: true});
assert.equal(missing.submit.disabled, true);
assert.match(missing.status.textContent, /No employment_type/);

const invalid = await setup({edit: true, saveError: true});
await invalid.form.listeners.submit(event);
assert.equal(invalid.errors.full_name.textContent, 'Invalid employee name.');
assert.equal(invalid.fields.full_name.attributes['aria-invalid'], 'true');
assert.equal(invalid.submit.disabled, false);
assert.equal(invalid.redirects.length, 0);

const listContext = vm.createContext({document: {
    querySelector: () => null, createElement: tag => new Element(tag), createElementNS: (ns, tag) => new Element(tag),
}});
vm.runInContext(listSource, listContext);
listContext.person = {id: 7, full_name: 'Employee Name', employee_code: 'EMP-7', image: '/picture.png'};
const row = vm.runInContext("catalogRow(person, [{key:'full_name',type:'identity'},{key:'actions'}], '/hrm/employees/0/edit/', () => {})", listContext);
const identity = row.children[0].children[0];
assert.equal(identity.children[0].textContent, 'E');
assert.equal(identity.children[1].textContent, 'Employee Name');
const actions = row.children[1].children[0].children;
assert.equal(actions[0].href, '/hrm/employees/7/edit/');
assert.equal(actions[0].title, 'Edit Employee Name');
assert.equal(actions[1].title, 'Delete Employee Name');
assert.equal(actions[1].className, 'icon-button inventory-delete-button');
console.log('HRM UI passed: API choices/defaults, photo upload, nullable dates, preserved manager, edit/redirect, load retry, validation and employee row actions.');

const viewer = vm.runInContext("catalogRow(person, [{key:'actions'}], '', null, '/hrm/employees/0/')", listContext);
const viewActions = viewer.children[0].children[0].children;
assert.equal(viewActions.length, 1);
assert.equal(viewActions[0].href, '/hrm/employees/7/');
assert.equal(viewActions[0].title, 'View Employee Name');
assert.equal(viewActions[0].children[0].tagName, 'SVG');
const combined = vm.runInContext("catalogRow(person, [{key:'actions'}], '/hrm/employees/0/edit/', () => {}, '/hrm/employees/0/')", listContext);
assert.equal(combined.children[0].children[0].children.length, 3);

const attendanceRow = vm.runInContext(`catalogRow({id: 8, check_in: '2026-11-18T22:15:00+06:00',
    check_out: '2026-11-19T00:00:00+06:00', missing: null},
    ['check_in', 'check_out', 'missing'].map(key => ({key, type: 'datetime'})))`, listContext);
assert.deepEqual(attendanceRow.children.map(cell => cell.textContent),
    ['2026-11-18 10:15 PM', '2026-11-19 12:00 AM', '\u2014']);

async function attendanceForm(fields, groups, record = null) {
    const form = new Element('form');
    form.action = record ? '/update/' : '/create/';
    form.dataset = {detailUrl: record ? '/detail/' : '', method: record ? 'PATCH' : 'POST', successUrl: '/list/'};
    const error = new Element();
    error.dataset.errorFor = 'working_days';
    const parts = Object.fromEntries(['form-status', 'submit', 'load-retry']
        .map(name => [`[data-catalog-${name}]`, new Element()]));
    parts['[data-submit-label]'] = new Element();
    parts['[data-submit-spinner]'] = new Element();
    form.querySelector = selector => parts[selector];
    form.querySelectorAll = selector => ({
        'input, select, textarea': fields, '[data-number-checkbox-group]': groups, '[data-error-for]': [error],
    })[selector] || [];
    form.elements = {namedItem: name => name === 'csrfmiddlewaretoken' ? {value: 'csrf'} : null};
    const requests = [];
    vm.runInNewContext(formSource, {
        document: {querySelector: () => form}, APIError,
        getJSON: async () => record,
        postJSON: async (url, data) => requests.push(structuredClone(data)),
        patchJSON: async (url, data) => requests.push(structuredClone(data)),
        window: {location: {assign() {}}},
    });
    await new Promise(resolve => setImmediate(resolve));
    return {form, error, requests};
}

const workingDays = new Element('fieldset');
workingDays.dataset.numberCheckboxGroup = 'working_days';
const days = Array.from({length: 7}, (_, day) => Object.assign(new Element('input'), {value: day, checked: false}));
workingDays.querySelectorAll = selector => selector.endsWith(':checked') ? days.filter(day => day.checked) : days;
const scheduleForm = await attendanceForm([], [workingDays]);
await scheduleForm.form.listeners.submit(event);
assert.equal(scheduleForm.requests.length, 0);
assert.equal(workingDays.attributes['aria-invalid'], 'true');
assert.equal(scheduleForm.error.hidden, false);
days[0].checked = days[6].checked = true;
await scheduleForm.form.listeners.submit(event);
assert.deepEqual(scheduleForm.requests[0].working_days, [0, 6]);
const scheduleEdit = await attendanceForm([], [workingDays], {id: 1, working_days: [2, 5]});
assert.deepEqual(days.filter(day => day.checked).map(day => Number(day.value)), [2, 5]);
await scheduleEdit.form.listeners.submit(event);
assert.deepEqual(scheduleEdit.requests[0].working_days, [2, 5]);

const checkIn = Object.assign(new Element('input'), {name: 'check_in', type: 'datetime-local'});
const checkOut = Object.assign(new Element('input'), {name: 'check_out', type: 'datetime-local'});
checkOut.dataset.nullableField = 'true';
const attendanceEdit = await attendanceForm([checkIn, checkOut], [], {
    id: 1, check_in: '2026-11-18T22:00:30.456000+06:00', check_out: '2026-11-19T06:00:00+06:00',
});
assert.equal(checkIn.value, '2026-11-18T22:00:30');
assert.equal(checkOut.value, '2026-11-19T06:00:00');
checkOut.value = '';
await attendanceEdit.form.listeners.submit(event);
assert.deepEqual(attendanceEdit.requests[0], {check_out: null});
console.log('Attendance forms passed: numeric weekdays, edit values, overnight timestamps and clearing checkout.');

const detailSource = (await readFile(new URL('../../static/js/record_detail.js', import.meta.url), 'utf8'))
    .replace(/^import .*;\r?\n/gm, '').replace(/^export /gm, '');
const detailPanel = new Element();
detailPanel.dataset = {apiUrl: '/hrm/api/employees/7/', titleField: 'full_name', loginUrl: '/account/login/'};
const detailParts = Object.fromEntries(['retry', 'image', 'image-wrapper', 'content', 'error', 'error-message', 'status', 'title']
    .map(name => [name, new Element()]));
const detailFields = ['full_name', 'joining_date', 'leaving_date', 'reports_to_name'].map(name => {
    const field = new Element(); field.dataset.detailField = name; return field;
});
detailPanel.querySelector = selector => detailParts[selector.match(/^\[data-record-(.*)\]$/)?.[1]];
detailPanel.querySelectorAll = () => detailFields;
let detailError = null;
const detailContext = vm.createContext({getJSON: async () => {
    if (detailError) throw detailError;
    return {id: 7, full_name: 'Employee <Name>', joining_date: '2026-01-01', leaving_date: null,
        reports_to_name: null, image: '/employee.png'};
}});
vm.runInContext(detailSource, detailContext);
detailContext.panel = detailPanel;
await vm.runInContext('initializeRecordDetail(panel).ready', detailContext);
assert.equal(detailParts.title.textContent, 'Employee <Name>');
assert.equal(detailParts.image.alt, 'Employee <Name>');
assert.equal(detailFields[2].textContent, '\u2014');
assert.equal(detailParts.content.hidden, false);
detailParts.image.listeners.error();
assert.equal(detailParts['image-wrapper'].hidden, true);
detailError = {status: 404};
await detailParts.retry.listeners.click();
assert.equal(detailParts.content.hidden, true);
assert.match(detailParts['error-message'].textContent, /could not be found/);
detailError = {status: 403};
await detailParts.retry.listeners.click();
assert.match(detailParts['error-message'].textContent, /permission/);
console.log('Employee detail UI passed: view icon/link, read-only row actions, text-safe data, image fallback, null values and access errors.');
