import { APIError, postJSON, postFormData } from "../../js/api.js";

const form = document.querySelector("[data-catalog-form]");
if (form) {
    const status = form.querySelector("[data-catalog-form-status]");
    const submit = form.querySelector("[data-catalog-submit]");
    const label = form.querySelector("[data-submit-label]");
    const originalLabel = label.textContent;
    const spinner = form.querySelector("[data-submit-spinner]");
    const controls = [...form.querySelectorAll("input, select, textarea")]
        .filter((input) => input.name && input.name !== "csrfmiddlewaretoken");
    const csrf = form.elements.namedItem("csrfmiddlewaretoken").value;

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
        if (form.dataset.busy === "true" || submit.disabled) return;
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
        let data = payload;
        if (fileInputs.length) {
            data = new FormData();
            for (const [name, value] of Object.entries(payload)) {
                if (value !== null) data.append(name, typeof value === "object" ? JSON.stringify(value) : String(value));
            }
            for (const input of fileInputs) if (input.files[0]) data.append(input.name, input.files[0]);
        }
        form.dataset.busy = "true";
        form.setAttribute("aria-busy", "true");
        for (const input of [...controls, submit]) input.disabled = true;
        label.textContent = "Saving...";
        spinner.hidden = false;
        try {
            if (fileInputs.length) await postFormData(form.action, data, csrf);
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
