import { getJSON, postJSON } from "../../js/api.js";

const find = (root, attr) => root.querySelector(`[data-${attr}]`);
const field = (form, name) => form.elements.namedItem(name);
const detailURL = (url, id) => url.replace(/\/0(?=\/)/, `/${id}`);

function element(tag, text, className = "") {
    const node = document.createElement(tag);
    if (text != null) node.textContent = text;
    if (className) node.className = className;
    return node;
}

function reportStatus(panel, message, tone = "info") {
    const status = find(panel, "operation-status");
    status.textContent = message;
    status.hidden = !message;
    status.dataset.tone = tone;
}

function errorMessage(error) {
    const messages = Object.values(error.errors || {}).flat().filter(value => typeof value === "string");
    return messages.join(" ") || error.message || "Could not complete this request.";
}

function attachAction(panel, form, csrf, buildRequest) {
    form.addEventListener("submit", async event => {
        event.preventDefault();
        if (panel.dataset.busy === "true") return;
        const request = buildRequest(event.submitter);
        if (!request) return;
        panel.dataset.busy = "true";
        form.setAttribute("aria-busy", "true");
        const buttons = [...panel.querySelectorAll('button[type="submit"]')].filter(button => !button.disabled);
        buttons.forEach(button => { button.disabled = true; });
        reportStatus(panel, "Saving…");
        try {
            await postJSON(request.url, request.payload, csrf);
            window.location.reload();
        } catch (error) {
            reportStatus(panel, errorMessage(error), "error");
            find(panel, "operation-status").focus();
            buttons.forEach(button => { button.disabled = false; });
            panel.dataset.busy = "false";
            form.setAttribute("aria-busy", "false");
        }
    });
}

function paymentKey() {
    if (typeof crypto.randomUUID === "function") return crypto.randomUUID();
    const bytes = crypto.getRandomValues(new Uint8Array(16));
    bytes[6] = (bytes[6] & 0x0f) | 0x40;
    bytes[8] = (bytes[8] & 0x3f) | 0x80;
    const hex = Array.from(bytes, byte => byte.toString(16).padStart(2, "0")).join("");
    return [hex.slice(0, 8), hex.slice(8, 12), hex.slice(12, 16), hex.slice(16, 20), hex.slice(20)].join("-");
}

function table(headers, rows) {
    const scroll = element("div", null, "users-table-scroll");
    const table = element("table", null, "users-table");
    const head = element("thead");
    const headerRow = element("tr");
    for (const header of headers) {
        const cell = element("th", header);
        cell.scope = "col";
        headerRow.append(cell);
    }
    head.append(headerRow);
    const body = element("tbody");
    for (const values of rows) {
        const row = element("tr");
        for (const value of values) {
            const cell = element("td");
            if (typeof value === "object" && value !== null) cell.append(value);
            else cell.textContent = value ?? "";
            row.append(cell);
        }
        body.append(row);
    }
    table.append(head, body);
    scroll.append(table);
    return scroll;
}

function renderPayments(panel, data, csrf) {
    if (!data.payments || !data.published) return;
    find(panel, "payments").hidden = false;
    const balanceRows = data.payslips.map(item => [item.employee_name, item.net_pay, item.paid_amount,
        item.pending_amount, item.available_amount]);
    find(panel, "payment-balances").replaceChildren(table(
        ["Employee", `Net (${data.currency})`, "Paid", "Pending", "Available"], balanceRows));
    const create = find(panel, "payment-create");
    const selectable = data.payslips.filter(slip => /[1-9]/.test(slip.available_amount));
    if (data.can_pay && selectable.length) {
        const choices = field(create, "payslip");
        choices.replaceChildren();
        for (const slip of selectable) {
            const option = element("option", `${slip.employee_name} (${slip.employee_code})`);
            option.value = slip.id;
            choices.append(option);
        }
        let key;
        create.addEventListener("input", () => { key = null; });
        create.hidden = false;
        attachAction(panel, create, csrf, () => ({url: panel.dataset.paymentUrl,
            payload: {payslip: field(create, "payslip").value, amount: field(create, "amount").value,
                method: field(create, "method").value, request_key: key ||= paymentKey()}}));
    }
    const attempts = find(panel, "payment-attempts");
    attempts.replaceChildren(element("h3", "Payment attempts", "section-title mb-3"));
    if (!data.payments.length) attempts.append(element("p", "No payment attempts recorded."));
    const employees = new Map(data.payslips.map(slip => [slip.id, slip.employee_name]));
    const template = document.querySelector('[data-payment-outcome-template]');
    for (const payment of data.payments) {
        const entry = element("section", null, "border-t border-stone-200 py-3");
        entry.append(element("p", `${employees.get(payment.payslip)} · ${data.currency} ${payment.amount} · ${payment.method_name} · ${payment.status_name}`));
        const note = [payment.reference, payment.paid_on, payment.failure_reason].filter(Boolean).join(" · ");
        if (note) entry.append(element("p", note, "text-sm text-stone-500"));
        if (payment.status === "pending" && data.can_pay) {
            const outcome = template.content.firstElementChild.cloneNode(true);
            const status = field(outcome, "status");
            status.addEventListener("change", () => {
                for (const wrapper of outcome.querySelectorAll('[data-payment-success], [data-payment-failure]')) {
                    const visible = wrapper.hasAttribute("data-payment-success") === (status.value === "succeeded");
                    const input = wrapper.querySelector("input");
                    wrapper.hidden = !visible;
                    input.disabled = !visible;
                    input.required = visible;
                }
            });
            attachAction(panel, outcome, csrf, () => ({url: detailURL(panel.dataset.resolveTemplate, payment.id), payload:
                status.value === "succeeded" ? {status: status.value, reference: field(outcome, "reference").value,
                    paid_on: field(outcome, "paid_on").value} : {status: status.value, failure_reason: field(outcome, "failure_reason").value}}));
            entry.append(outcome);
        }
        attempts.append(entry);
    }
}

export function renderPayrollOperations(workspace, data) {
    const panel = find(workspace, "payroll-operations");
    if (!panel) return;
    panel.hidden = false;
    const form = find(panel, "run-actions");
    const csrf = field(form, "csrfmiddlewaretoken").value;
    if (data.available_actions.length) {
        const urls = JSON.parse(document.getElementById("payroll-transition-urls").textContent);
        const labels = {review: "Mark reviewed", return: "Return to calculation", reject: "Reject run", cancel: "Cancel run", approve: "Approve payroll"};
        const buttons = find(panel, "run-buttons");
        for (const action of data.available_actions) {
            const button = element("button", labels[action], action === "approve" ? "primary-button compact-primary" : "secondary-button");
            button.type = "submit";
            button.value = action;
            buttons.append(button);
        }
        form.hidden = false;
        attachAction(panel, form, csrf, button => button && ({url: urls[button.value], payload: {note: field(form, "note").value}}));
    }
    renderPayments(panel, data, csrf);
    if (data.events.length) {
        find(panel, "payroll-history").hidden = false;
        const events = data.events.map(event => element("li", [event.created_at.slice(0, 19).replace("T", " "),
            event.action.replaceAll("_", " "), event.actor, event.note].filter(Boolean).join(" · ")));
        find(panel, "payroll-events").replaceChildren(...events);
    }
}

async function loadAdjustment(panel) {
    reportStatus(panel, "Loading payroll adjustment…");
    try {
        const data = await getJSON(panel.dataset.detailUrl);
        const details = find(panel, "adjustment-details");
        const values = [["Employee", data.employee_name], ["Source", data.source_name], ["Component", data.component_name],
            ["Effective date", data.effective_on], ["Amount", `${data.currency} ${data.amount}`], ["Status", data.status_name],
            ["Reason", data.reason], ["Reviewed by", data.reviewed_by]];
        for (const [label, value] of values) {
            details.append(element("dt", label, "form-label"), element("dd", value || "—"));
        }
        const form = find(panel, "adjustment-review");
        if (panel.dataset.reviewUrl && data.status !== "cancelled") {
            form.hidden = false;
            form.querySelector('[value="approved"]').hidden = data.status !== "draft";
            form.querySelector('[value="cancelled"]').hidden = false;
            attachAction(panel, form, field(form, "csrfmiddlewaretoken").value, button => button && (
                {url: panel.dataset.reviewUrl, payload: {status: button.value}}));
        }
        reportStatus(panel, "");
    } catch (error) {
        reportStatus(panel, errorMessage(error), "error");
    }
}

function reportSection(title, headers, rows) {
    const section = element("section", null, "users-panel");
    const toolbar = element("div", null, "users-toolbar");
    toolbar.append(element("h2", title, "section-title"));
    section.append(toolbar, rows.length ? table(headers, rows) : element("p", "No approved payroll records match these filters.", "px-6 pb-5"));
    return section;
}

function setupReport(panel) {
    const form = find(panel, "report-filters");
    const download = find(panel, "report-export");
    const output = find(panel, "report-results");
    form.addEventListener("submit", async event => {
        event.preventDefault();
        const params = new URLSearchParams(new FormData(form));
        for (const [key, value] of params) if (!value.trim()) params.delete(key);
        if (params.has("month")) params.set("month", `${params.get("month")}-01`);
        const url = `${panel.dataset.reportUrl}?${params}`;
        const button = form.querySelector('button[type="submit"]');
        if (button.disabled) return;
        button.disabled = true;
        download.hidden = true;
        output.replaceChildren();
        reportStatus(panel, "Loading payroll report…");
        try {
            const data = await getJSON(url);
            const moneyValues = item => [item.gross_pay, item.total_deductions, item.net_pay];
            const sumHeaders = ["Month", "Currency", "Employees", "Gross", "Deductions", "Net"];
            output.append(reportSection("Monthly summary", sumHeaders,
                data.summary.map(item => [item.month, item.currency, item.employee_count, ...moneyValues(item)])));
            output.append(reportSection("Department expenses", ["Department", ...sumHeaders],
                data.departments.map(item => [item.department, item.month, item.currency, item.employee_count, ...moneyValues(item)])));
            output.append(reportSection("Employee salary history", ["Month", "Employee", "Currency", "Gross", "Deductions", "Net", "Payslip"],
                data.payslips.map(item => {
                    const link = element("a", "Download PDF", "text-button");
                    link.href = detailURL(panel.dataset.payslipPdfTemplate, item.id);
                    return [item.month, `${item.employee_name} (${item.employee_code})`, item.currency, ...moneyValues(item), link];
                })));
            download.href = `${url}&export=csv`;
            download.hidden = !data.payslips.length;
            reportStatus(panel, "");
        } catch (error) {
            reportStatus(panel, errorMessage(error), "error");
        } finally {
            button.disabled = false;
        }
    });
}

const adjustmentPanel = document.querySelector('[data-payroll-adjustment]');
if (adjustmentPanel) loadAdjustment(adjustmentPanel);
const reportPanel = document.querySelector('[data-payroll-report]');
if (reportPanel) setupReport(reportPanel);
