import { postJSON } from "../../js/api.js";

const form = document.querySelector("[data-leave-review]");
if (form) {
    const status = form.querySelector("[data-leave-review-status]");
    const buttons = [...form.querySelectorAll("button[type=submit]")];
    const csrf = form.elements.namedItem("csrfmiddlewaretoken").value;

    form.addEventListener("submit", async (event) => {
        event.preventDefault();
        if (form.getAttribute("aria-busy") === "true" || !event.submitter) return;
        const decision = event.submitter.value;
        form.setAttribute("aria-busy", "true");
        buttons.forEach(button => { button.disabled = true; });
        status.hidden = true;
        try {
            await postJSON(form.action, {status: decision}, csrf);
            window.location.reload();
        } catch (error) {
            const messages = Object.values(error.errors ?? {}).flat().filter(value => typeof value === "string");
            status.dataset.tone = "error";
            status.textContent = messages.join(" ") || error.message;
            status.hidden = false;
            status.focus();
            form.setAttribute("aria-busy", "false");
            buttons.forEach(button => { button.disabled = false; });
        }
    });
}
