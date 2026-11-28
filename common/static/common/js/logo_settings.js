import { deleteJSON, getJSON, patchFormData } from "../../js/api.js";
import { showERPLogo } from "../../js/branding.js";

const panel = document.querySelector("[data-logo-settings]");
const url = panel.dataset.apiUrl;
const description = panel.querySelector("[data-logo-description]");
const status = panel.querySelector("[data-logo-status]");
const retry = panel.querySelector("[data-logo-retry]");
const form = panel.querySelector("[data-logo-form]");
const input = form?.elements.namedItem("logo");
const csrf = form?.elements.namedItem("csrfmiddlewaretoken").value;
const save = form?.querySelector("[type=submit]");
const reset = form?.querySelector("[data-logo-reset]");
let current = null;
let busy = false;

function updateControls() {
    if (!form) return;
    input.disabled = save.disabled = busy || !current;
    reset.disabled = busy || !current?.is_custom;
    form.setAttribute("aria-busy", String(busy));
}

function report(message, tone = "info") {
    status.textContent = message;
    status.dataset.tone = tone;
    status.hidden = !message;
}

async function request(action, loading, success = "") {
    if (busy) return;
    busy = true;
    updateControls();
    retry.hidden = true;
    report(loading);
    try {
        current = await action();
        showERPLogo(current.logo_url, current.sidebar_logo_url);
        description.textContent = current.is_custom ? "Using the uploaded logo." : "Using the default ERP logo.";
        form?.reset();
        report(success, "success");
    } catch (error) {
        const messages = Object.values(error.errors ?? {}).flat().filter(value => typeof value === "string");
        report(messages.join(" ") || error.message, "error");
        retry.hidden = Boolean(current);
    } finally {
        busy = false;
        updateControls();
    }
}

function loadLogo() {
    return request(() => getJSON(url), "Loading logo…");
}

retry.addEventListener("click", loadLogo);
if (form) {
    form.addEventListener("submit", event => {
        event.preventDefault();
        if (save.disabled || !input.files.length) return;
        const upload = new FormData();
        upload.append("logo", input.files[0]);
        request(() => patchFormData(url, upload, csrf), "Saving logo…", "Logo saved.");
    });
    reset.addEventListener("click", () => {
        if (reset.disabled || !window.confirm("Restore the default ERP logo?")) return;
        request(() => deleteJSON(url, csrf), "Restoring default…", "Default logo restored.");
    });
}
loadLogo();
