import { getJSON, postJSON } from "../../js/api.js";
import { renderPayrollOperations } from "./payroll_operations.js";

function node(tag, text) {
    const element = document.createElement(tag);
    if (text != null) element.textContent = text;
    return element;
}

function amount(value, currency) {
    return value == null ? "—" : `${currency} ${value}`;
}

function breakdown(slip, currency) {
    const details = node("details");
    const issues = (slip.blockers?.length || 0) + (slip.warnings?.length || 0);
    details.open = Boolean(slip.blockers?.length);
    details.append(node("summary", issues ? `View calculation and review flags (${issues})` : "View calculation"));
    for (const blocker of slip.blockers || []) {
        const message = node("p", blocker);
        message.className = "form-error mt-2";
        details.append(message);
    }
    for (const warning of slip.warnings || []) {
        details.append(node("p", `${warning.message} on ${warning.dates.join(", ")}`));
    }
    const attendance = slip.calculation_inputs?.attendance_summary;
    if (attendance) {
        details.append(node("p", `${attendance.working_days} working days · ${attendance.late_marks} late marks · ` +
            `${attendance.late_penalty_days} late penalty days · ${attendance.early_penalty_days} early penalty days · ` +
            `${attendance.covered_days} days covered by leave · ${attendance.unpaid_days} unpaid days`));
    }
    for (const line of slip.lines) {
        const label = line.type === "deduction" ? "Deduction" : "Earning";
        details.append(node("p", `${line.name} · ${label} · ${amount(line.amount, currency)}`));
        const inputs = node("ul");
        inputs.className = "mb-2 pl-4 text-xs text-stone-500";
        for (const input of line.calculation_inputs) {
            const text = input.date ? (input.days ? `${input.date} · ${input.days} unpaid days` : input.date) :
                `${input.from} to ${input.to} · ${input.days} of ${input.calendar_days} days · ${amount(input.monthly_amount, currency)} monthly`;
            inputs.append(node("li", text));
        }
        details.append(inputs);
    }
    return details;
}

export function initializePayroll(workspace) {
    const status = workspace.querySelector("[data-payroll-status]");
    const results = workspace.querySelector("[data-payroll-results]");
    const blockers = workspace.querySelector("[data-payroll-blockers]");
    const rows = workspace.querySelector("[data-payroll-rows]");
    const form = workspace.querySelector("[data-payroll-form]");

    function feedback(message, tone = "info") {
        status.textContent = message;
        status.dataset.tone = tone;
        status.hidden = !message;
    }

    function show(data) {
        const errors = data.blockers || [];
        blockers.replaceChildren(...errors.map(error => node("li", error)));
        blockers.hidden = !errors.length;
        rows.replaceChildren();
        for (const slip of data.payslips) {
            const row = node("tr");
            const name = node("td", `${slip.employee_name} (${slip.employee_code})`);
            const cell = node("td");
            cell.append(breakdown(slip, data.currency));
            if (data.published && slip.id && workspace.dataset.payslipPdfTemplate) {
                const link = node("a", "Download payslip PDF");
                link.className = "text-button mt-2";
                link.href = workspace.dataset.payslipPdfTemplate.replace(/\/0\//, `/${slip.id}/`);
                cell.append(link);
            }
            row.append(name, node("td", amount(slip.gross_pay, data.currency)),
                node("td", amount(slip.total_deductions, data.currency)),
                node("td", amount(slip.net_pay, data.currency)), cell);
            rows.append(row);
        }
        results.hidden = false;
    }

    if (!form) {
        const retry = workspace.querySelector("[data-payroll-detail-retry]");
        async function loadRun() {
            retry.hidden = true;
            feedback("Loading payslips…");
            try {
                const data = await getJSON(workspace.dataset.detailUrl);
                show(data);
                if (workspace.querySelector('[data-payroll-operations]')) renderPayrollOperations(workspace, data);
                feedback("");
            } catch (error) {
                feedback(error.message || "Could not load this payroll run.", "error");
                retry.hidden = false;
            }
        }
        retry.addEventListener("click", loadRun);
        return loadRun();
    }

    const month = form.elements.namedItem("month");
    const currency = form.elements.namedItem("currency");
    const csrf = form.elements.namedItem("csrfmiddlewaretoken").value;
    const previewButton = form.querySelector("[data-payroll-preview]");
    const saveButton = form.querySelector("[data-payroll-save]");
    const retry = form.querySelector("[data-payroll-retry]");
    let currenciesReady = false;
    let busy = false;
    let preview = null;

    function updateControls() {
        month.disabled = busy;
        currency.disabled = busy || !currenciesReady;
        previewButton.disabled = busy || !currenciesReady;
        saveButton.disabled = busy || !preview?.can_calculate;
        form.setAttribute("aria-busy", String(busy));
    }

    async function loadCurrencies() {
        retry.hidden = true;
        try {
            const data = await getJSON(form.dataset.optionsUrl);
            currency.replaceChildren(node("option", "Select currency"));
            currency.firstElementChild.value = "";
            for (const choice of data.currency) {
                const option = node("option", choice.label);
                option.value = choice.value;
                currency.append(option);
            }
            if (data.currency.some(choice => choice.value === form.dataset.initialCurrency)) {
                currency.value = form.dataset.initialCurrency;
            }
            currenciesReady = data.currency.length > 0;
            feedback(currenciesReady ? "" : "Add an active currency before calculating payroll.", currenciesReady ? "info" : "error");
            retry.hidden = currenciesReady;
        } catch (error) {
            feedback(error.message || "Could not load currencies.", "error");
            retry.hidden = false;
        }
        updateControls();
    }

    retry.addEventListener("click", loadCurrencies);
    form.addEventListener("input", () => {
        preview = null;
        results.hidden = true;
        feedback("");
        updateControls();
    });
    form.addEventListener("submit", async event => {
        event.preventDefault();
        const saving = event.submitter === saveButton;
        if (busy || !currenciesReady || (saving && !preview?.can_calculate)) return;
        if (!/^\d{4}-\d{2}$/.test(month.value) || !currency.value) {
            feedback("Select a payroll month and currency.", "error");
            return;
        }
        const payload = {month: `${month.value}-01`, currency: currency.value};
        if (saving && (preview.month !== payload.month || preview.currency !== payload.currency)) {
            preview = null;
            updateControls();
            feedback("Preview the selected month and currency before saving.", "error");
            return;
        }
        if (saving) payload.preview_token = preview.preview_token;
        preview = null;
        busy = true;
        updateControls();
        if (!saving) results.hidden = true;
        feedback(saving ? "Saving payroll…" : "Calculating preview…");
        try {
            const data = await postJSON(saving ? form.action : form.dataset.previewUrl, payload, csrf);
            if (saving) {
                window.location.assign(form.dataset.detailTemplate.replace(/\/0\/$/, `/${data.id}/`));
            } else {
                preview = data;
                show(data);
                feedback(data.can_calculate ? "Preview ready. Review the results before saving." :
                    "Resolve the listed blockers before saving payroll.", data.can_calculate ? "info" : "error");
            }
        } catch (error) {
            if (error.errors?.preview) show(error.errors.preview);
            const fieldErrors = [...(error.errors?.month || []), ...(error.errors?.currency || [])];
            feedback(fieldErrors.join(" ") || error.message || "Could not calculate payroll.", "error");
            status.focus();
        } finally {
            busy = false;
            updateControls();
        }
    });
    return loadCurrencies();
}

const workspace = document.querySelector("[data-payroll-workspace]");
if (workspace) initializePayroll(workspace);
