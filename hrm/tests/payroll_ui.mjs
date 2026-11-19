// Run with Node: node hrm/tests/payroll_ui.mjs
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import vm from 'node:vm';

const source = (await readFile(new URL('../static/hrm/js/payroll.js', import.meta.url), 'utf8'))
    .replace(/^import .*;\r?\n/gm, '').replace(/^export /gm, '');

function element() {
    return {
        children: [], dataset: {}, listeners: {}, hidden: true, value: '', textContent: '', disabled: false,
        get firstElementChild() { return this.children[0]; },
        append(...items) { this.children.push(...items); },
        replaceChildren(...items) { this.children = items; },
        addEventListener(name, listener) { this.listeners[name] = listener; },
        setAttribute() {},
        focus() {},
    };
}

const slip = {
    employee_name: 'Payroll <employee>', employee_code: 'E-1', gross_pay: '30000.0000',
    total_deductions: '0.0000', net_pay: '30000.0000', warnings: [],
    lines: [{code: 'basic', name: 'Basic salary', type: 'earning', amount: '30000.0000', calculation_inputs: []}],
};
const elements = names => Object.fromEntries(names.map(name => [name, element()]));
const panels = elements(['status', 'results', 'blockers', 'rows', 'form', 'detail-retry']);
const buttons = elements(['preview', 'save', 'retry']);
const fields = {...elements(['month', 'currency']), csrfmiddlewaretoken: {value: 'csrf'}};
const form = panels.form;
const testWorkspace = element();
const selectorKey = selector => selector.match(/^\[data-payroll-(.+)\]$/)?.[1];
testWorkspace.querySelector = selector => panels[selectorKey(selector)];
form.querySelector = selector => buttons[selectorKey(selector)];
form.elements = {namedItem: name => fields[name]};
form.dataset = {optionsUrl: '/options/', previewUrl: '/preview/', detailTemplate: '/hrm/payroll-runs/0/', initialCurrency: 'BDT'};
form.action = '/calculate/';
fields.month.value = '2026-11';

const requests = [];
const redirects = [];
let blocked = true;
let stale = false;
let loadingFails = true;
const context = vm.createContext({
    document: {createElement: element, querySelector: () => null}, testWorkspace,
    getJSON: async url => {
        if (url === '/options/') {
            if (loadingFails) throw new Error('Connection unavailable');
            return {currency: [{value: 'BDT', label: 'Taka'}]};
        }
        assert.equal(url, '/detail/');
        return {currency: 'BDT', payslips: [slip]};
    },
    postJSON: async (url, data, csrf) => {
        assert.equal(csrf, 'csrf');
        requests.push({url, data});
        if (url === '/preview/') return {
            month: data.month, currency: data.currency, payslips: [slip], blockers: blocked ? ['Missing compensation'] : [],
            preview_token: blocked ? null : 'token', can_calculate: !blocked,
        };
        assert.equal(url, '/calculate/');
        if (stale) throw new Error('Payroll inputs have changed.');
        return {id: 4};
    },
    window: {location: {assign: url => redirects.push(url)}},
});
vm.runInContext(source, context);
await vm.runInContext('initializePayroll(testWorkspace)', context);
assert.equal(buttons.preview.disabled, true);
assert.equal(buttons.retry.hidden, false);
loadingFails = false;
await buttons.retry.listeners.click();
assert.equal(fields.currency.value, 'BDT');

const submit = button => form.listeners.submit({submitter: button, preventDefault() {}});
await submit(buttons.preview);
assert.equal(buttons.save.disabled, true);
assert.equal(panels.blockers.children[0].textContent, 'Missing compensation');
await submit(buttons.save);
assert.equal(requests.length, 1);
blocked = false;
await submit(buttons.preview);
assert.equal(buttons.save.disabled, false);
assert.equal(panels.rows.children[0].children[0].textContent, 'Payroll <employee> (E-1)');

fields.month.value = '2026-12';
form.listeners.input();
assert.equal(buttons.save.disabled, true);
await submit(buttons.preview);
stale = true;
await submit(buttons.save);
assert.equal(requests.at(-1).data.preview_token, 'token');
assert.equal(buttons.save.disabled, true);
assert.match(panels.status.textContent, /changed/);
stale = false;
await submit(buttons.preview);
await submit(buttons.save);
assert.deepEqual(redirects, ['/hrm/payroll-runs/4/']);

panels.form = null;
testWorkspace.dataset.detailUrl = '/detail/';
await vm.runInContext('initializePayroll(testWorkspace)', context);
assert.equal(panels.rows.children[0].children[3].textContent, 'BDT 30000.0000');
console.log('Payroll UI passed: currency retry, blockers, stale preview, save redirect, and saved payslip display.');
