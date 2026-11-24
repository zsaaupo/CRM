import { getJSON, postJSON } from "../../js/api.js";

const form = document.getElementById("ledger-form");
const fields = form.elements;
const generate = form.querySelector("button[type=submit]");
const retry = document.getElementById("ledger-retry");
const status = document.getElementById("ledger-status");
const report = document.getElementById("ledger-report");
const rows = report.querySelector("tbody");

function showStatus(message, tone) {
    status.textContent = message;
    status.dataset.tone = tone;
    status.hidden = !message;
}

async function loadAccounts() {
    retry.hidden = true;
    generate.disabled = true;
    showStatus("Loading accounts…", "");
    try {
        const { accounts } = await getJSON(form.action);
        fields.account.replaceChildren(new Option("Select an account", ""));
        for (const account of accounts) fields.account.add(new Option(account.name, account.id));
        generate.disabled = accounts.length === 0;
        showStatus(accounts.length ? "" : "No accounts are available.", "");
    } catch (error) {
        showStatus(error.message, "error");
        retry.hidden = false;
    }
}

function renderReport(data) {
    document.getElementById("ledger-account").textContent = data.account.name;
    document.getElementById("ledger-period").textContent =
        `${data.start_date} to ${data.end_date} · ${data.account.currency}`;
    for (const total of report.querySelectorAll("[data-total]")) {
        total.textContent = data[total.dataset.total];
    }
    const entries = document.createDocumentFragment();
    for (const entry of data.results) {
        const row = document.createElement("tr");
        const inflow = entry.direction === "inflow";
        for (const value of [entry.date, entry.source, entry.reference, inflow ? entry.amount : "",
                             inflow ? "" : entry.amount, entry.running_balance]) {
            row.insertCell().textContent = value;
        }
        entries.append(row);
    }
    rows.replaceChildren(entries);
    document.getElementById("ledger-empty").hidden = data.results.length > 0;
    report.hidden = false;
}

form.addEventListener("input", () => {
    fields.end_date.min = fields.start_date.value;
    report.hidden = true;
});

form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (generate.disabled || !form.reportValidity()) return;
    const payload = {
        account: Number(fields.account.value), start_date: fields.start_date.value, end_date: fields.end_date.value,
    };
    generate.disabled = true;
    form.querySelector("fieldset").disabled = true;
    report.hidden = true;
    showStatus("Generating ledger…", "");
    try {
        const data = await postJSON(form.action, payload, fields.csrfmiddlewaretoken.value);
        renderReport(data);
        showStatus("Ledger generated.", "success");
    } catch (error) {
        const details = Object.values(error.errors || {}).flat().join(" ");
        showStatus(details || error.message, "error");
    } finally {
        generate.disabled = false;
        form.querySelector("fieldset").disabled = false;
    }
});

// A history restoration should open the same blank form as a fresh visit.
window.addEventListener("pagehide", () => {
    report.hidden = true;
    rows.replaceChildren();
});
window.addEventListener("pageshow", (event) => {
    if (event.persisted) window.location.reload();
});
retry.addEventListener("click", loadAccounts);
loadAccounts();
