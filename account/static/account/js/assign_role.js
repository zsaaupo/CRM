import { postJSON } from "../../js/api.js";

const form = document.querySelector("[data-role-assignment]");

if (form) {
    const select = form.elements.namedItem("group");
    const status = form.querySelector("[data-assignment-status]");
    const errorBox = form.querySelector("[data-assignment-error]");
    const save = form.querySelector("[data-assignment-save]");
    const label = form.querySelector("[data-assignment-label]");
    const spinner = form.querySelector("[data-assignment-spinner]");

    select?.addEventListener("change", () => {
        select.removeAttribute("aria-invalid");
        errorBox.hidden = true;
        status.hidden = true;
    });

    form.addEventListener("submit", async (event) => {
        event.preventDefault();
        if (!select || form.dataset.busy === "true") return;
        status.hidden = true;
        errorBox.hidden = true;
        select.removeAttribute("aria-invalid");
        if (!select.checkValidity()) {
            errorBox.textContent = "Select a role to continue.";
            errorBox.hidden = false;
            select.setAttribute("aria-invalid", "true");
            select.focus();
            return;
        }

        form.dataset.busy = "true";
        form.setAttribute("aria-busy", "true");
        save.disabled = true;
        select.disabled = true;
        label.textContent = "Assigning...";
        spinner.hidden = false;
        try {
            await postJSON(form.action, {
                user: Number(form.dataset.userId),
                group: Number(select.value),
            }, form.elements.namedItem("csrfmiddlewaretoken").value);
            status.dataset.tone = "success";
            status.textContent = "Role assigned. Opening the user list...";
            status.hidden = false;
            window.location.assign(form.dataset.successUrl);
        } catch (error) {
            const messages = error.errors?.group;
            if (Array.isArray(messages)) {
                errorBox.textContent = messages.join(" ");
                errorBox.hidden = false;
                select.setAttribute("aria-invalid", "true");
            }
            status.dataset.tone = "error";
            status.textContent = error.status === 404
                ? "This user no longer exists. Return to the user list."
                : error.status === 401 || error.status === 403
                ? "Your session has changed. Refresh this page and try again."
                : error.message || "Could not assign this role. Please try again.";
            status.hidden = false;
            status.focus();
        } finally {
            form.dataset.busy = "false";
            form.setAttribute("aria-busy", "false");
            save.disabled = false;
            select.disabled = false;
            label.textContent = "Assign role";
            spinner.hidden = true;
        }
    });
}
