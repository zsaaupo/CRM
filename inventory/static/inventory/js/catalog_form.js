import { APIError, getJSON, postJSON, postFormData, patchJSON, patchFormData } from "../../js/api.js";

const form = document.querySelector("[data-catalog-form]");
if (form) {
    const status = form.querySelector("[data-catalog-form-status]");
    const submit = form.querySelector("[data-catalog-submit]");
    const label = form.querySelector("[data-submit-label]");
    const originalLabel = label.textContent;
    const spinner = form.querySelector("[data-submit-spinner]");
    const fields = [...form.querySelectorAll("input, select, textarea")]
        .filter((input) => input.name && input.name !== "csrfmiddlewaretoken");
    const controls = fields.filter((input) => !input.disabled);
    const csrf = form.elements.namedItem("csrfmiddlewaretoken").value;
    const retry = form.querySelector("[data-catalog-load-retry]");
    let ready = !form.dataset.optionsUrl && !form.dataset.detailUrl;
    let loading = false;

    function setDisabled(disabled) {
        for (const input of [...controls, submit]) input.disabled = disabled;
    }

    async function initialize() {
        if (loading) return;
        loading = true;
        ready = false;
        setDisabled(true);
        retry.hidden = true;
        status.dataset.tone = "info";
        status.textContent = "Loading details...";
        status.hidden = false;
        form.setAttribute("aria-busy", "true");
        try {
            const [options, record] = await Promise.all([
                form.dataset.optionsUrl ? getJSON(form.dataset.optionsUrl) : null,
                form.dataset.detailUrl ? getJSON(form.dataset.detailUrl) : null,
            ]);
            if (form.dataset.optionsUrl) {
                const selects = [...form.querySelectorAll("[data-options-field]")];
                for (const select of selects) {
                    if (!Array.isArray(options[select.name])) throw new Error("Could not load catalog choices. Please try again.");
                }
                const missing = [];
                for (const select of selects) {
                    const placeholder = document.createElement("option");
                    placeholder.value = "";
                    placeholder.textContent = select.required ? `Select ${select.dataset.label.toLowerCase()}` : "None";
                    select.replaceChildren(placeholder);
                    for (const record of options[select.name]) {
                        const option = document.createElement("option");
                        option.value = String(record.value);
                        option.textContent = record.label;
                        select.append(option);
                    }
                    const value = select.dataset.default;
                    if (value && options[select.name].some((item) => String(item.value) === value)) select.value = value;
                    if (select.required && options[select.name].length === 0) missing.push(select.dataset.label.toLowerCase());
                }
                if (missing.length) throw new Error(`No ${missing.join(", ")} records are available. Add the missing records or ask your administrator, then try loading again.`);
            }
            if (form.dataset.detailUrl) {
                if (!record || typeof record !== "object" || !record.id) throw new Error("Could not load this record. Please try again.");
                for (const input of fields) {
                    if (!(input.name in record) || input.type === "file") continue;
                    if (input.type === "checkbox") input.checked = record[input.name];
                    else input.value = input.dataset.jsonField ? JSON.stringify(record[input.name] ?? {}) : record[input.name] ?? "";
                }
                const image = form.querySelector("[data-current-image-preview]");
                if (image && record.image) {
                    image.src = record.image;
                    form.querySelector("[data-current-image]").hidden = false;
                }
            }
            ready = true;
            status.hidden = true;
            setDisabled(false);
        } catch (error) {
            status.dataset.tone = "error";
            status.textContent = error.status === 404 ? "This record could not be found. Return to the list."
                : error.status === 403 ? "Your session or permissions have changed. Refresh this page and try again."
                : error.message || "Could not load details. Please try again.";
            retry.hidden = false;
        } finally {
            loading = false;
            form.setAttribute("aria-busy", "false");
        }
    }

    retry.addEventListener("click", initialize);
    if (!ready) initialize();

    function fieldError(name, message) {
        const input = form.elements.namedItem(name);
        const box = [...form.querySelectorAll("[data-error-for]")].find((element) => element.dataset.errorFor === name);
        if (!input || !box) return false;
        input.setAttribute("aria-invalid", "true");
        box.textContent = message;
        box.hidden = false;
        return true;
    }

    form.addEventListener("input", (event) => {
        event.target.removeAttribute("aria-invalid");
        const error = [...form.querySelectorAll("[data-error-for]")].find((box) => box.dataset.errorFor === event.target.name);
        if (error) error.hidden = true;
    });

    form.addEventListener("submit", async (event) => {
        event.preventDefault();
        if (!ready || form.dataset.busy === "true" || submit.disabled) return;
        status.hidden = true;
        for (const box of form.querySelectorAll("[data-error-for]")) box.hidden = true;
        let invalid = null;
        const payload = {};
        for (const input of controls) {
            input.removeAttribute("aria-invalid");
            if (!input.checkValidity()) {
                fieldError(input.name, input.validationMessage);
                invalid ??= input;
                continue;
            }
            if (input.type === "file") continue;
            if (input.dataset.jsonField) {
                try {
                    const value = JSON.parse(input.value.trim() || "{}");
                    if (value === null || Array.isArray(value) || typeof value !== "object") throw new Error();
                    payload[input.name] = value;
                } catch {
                    fieldError(input.name, 'Enter specifications as an object, e.g. {"color":"blue"}.');
                    invalid ??= input;
                }
            } else {
                payload[input.name] = input.type === "checkbox" ? input.checked
                    : input.value === "" && (input.dataset.nullableField || input.type === "number") ? null
                    : input.value;
            }
        }
        if (invalid) {
            status.dataset.tone = "error";
            status.textContent = "Please check the highlighted fields.";
            status.hidden = false;
            invalid.focus();
            return;
        }
        const fileInputs = controls.filter((input) => input.type === "file");
        const isPatch = form.dataset.method === "PATCH";
        const multipart = fileInputs.some((input) => input.files[0]) || (!isPatch && fileInputs.length > 0);
        let data = payload;
        if (multipart) {
            data = new FormData();
            for (const [name, value] of Object.entries(payload)) {
                if (value === null) {
                    if (isPatch) data.append(name, "");
                } else data.append(name, typeof value === "object" ? JSON.stringify(value) : String(value));
            }
            for (const input of fileInputs) if (input.files[0]) data.append(input.name, input.files[0]);
        }
        form.dataset.busy = "true";
        form.setAttribute("aria-busy", "true");
        for (const input of [...controls, submit]) input.disabled = true;
        label.textContent = "Saving...";
        spinner.hidden = false;
        try {
            if (isPatch && multipart) await patchFormData(form.action, data, csrf);
            else if (isPatch) await patchJSON(form.action, payload, csrf);
            else if (multipart) await postFormData(form.action, data, csrf);
            else await postJSON(form.action, payload, csrf);
            status.dataset.tone = "success";
            status.textContent = "Saved. Opening the list...";
            status.hidden = false;
            window.location.assign(form.dataset.successUrl);
        } catch (error) {
            const general = [];
            if (error instanceof APIError) {
                for (const [name, value] of Object.entries(error.errors)) {
                    const message = Array.isArray(value) ? value.join(" ") : String(value);
                    if (!fieldError(name, message)) general.push(message);
                }
            }
            status.dataset.tone = "error";
            status.textContent = error.status === 403
                ? "Your session or permissions have changed. Refresh this page and try again."
                : general.join(" ") || "Could not save. Check the highlighted fields and try again.";
            status.hidden = false;
            (form.querySelector('[aria-invalid="true"]') || status).focus();
        } finally {
            form.dataset.busy = "false";
            form.setAttribute("aria-busy", "false");
            for (const input of [...controls, submit]) input.disabled = false;
            label.textContent = originalLabel;
            spinner.hidden = true;
        }
    });
}
