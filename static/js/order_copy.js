import { postJSON } from "./api.js";

for (const form of document.querySelectorAll("[data-copy-order]")) {
    form.addEventListener("submit", async event => {
        event.preventDefault();
        const button = form.querySelector("button");
        if (button.disabled) return;
        button.disabled = true;
        const status = form.querySelector("[role=status]");
        status.hidden = true;
        try {
            const result = await postJSON(form.action, {}, form.elements.csrfmiddlewaretoken.value);
            window.location.assign(result.url);
        } catch (error) {
            status.textContent = Object.values(error.errors || {}).flat().join(" ") || error.message;
            status.hidden = false;
            button.disabled = false;
        }
    });
}
