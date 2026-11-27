import { patchJSON, postJSON } from "../../js/api.js";

for (const form of document.querySelectorAll("[data-delivery-form]")) {
    const feedback = form.querySelector("[data-delivery-feedback]");
    const button = form.querySelector('button[type="submit"]');
    const fields = [...form.querySelectorAll("[data-error-for]")];
    const courier = form.elements.namedItem("courier");
    const tracking = form.elements.namedItem("tracking_number");
    if (courier && tracking && form.dataset.method === "PATCH") {
        courier.addEventListener("change", () => { tracking.value = ""; });
    }
    const paymentAccount = form.elements.namedItem("payment_account");
    const deliveryStatus = form.elements.namedItem("status");
    if (paymentAccount && deliveryStatus) {
        const paymentField = form.querySelector("[data-delivery-payment-field]");
        const updatePaymentField = () => {
            const delivered = deliveryStatus.value === "delivered";
            paymentField.hidden = paymentAccount.disabled = !delivered;
            paymentAccount.required = delivered;
        };
        deliveryStatus.addEventListener("change", updatePaymentField);
        updatePaymentField();
    }

    form.addEventListener("submit", async (event) => {
        event.preventDefault();
        if (button.disabled) return;
        feedback.hidden = true;
        for (const field of fields) {
            field.hidden = true;
            form.elements.namedItem(field.dataset.errorFor).removeAttribute("aria-invalid");
        }
        const payload = Object.fromEntries(new FormData(form));
        const csrf = payload.csrfmiddlewaretoken;
        delete payload.csrfmiddlewaretoken;
        button.disabled = true;
        form.setAttribute("aria-busy", "true");
        try {
            const save = form.dataset.method === "PATCH" ? patchJSON : postJSON;
            const result = await save(form.action, payload, csrf);
            if (form.dataset.successUrlTemplate) {
                const next = form.dataset.successUrlTemplate.replace("/0/", `/${encodeURIComponent(result.delivery.id)}/`);
                window.location.assign(next);
            } else {
                window.location.reload();
            }
        } catch (error) {
            const messages = [];
            let invalid;
            for (const [name, value] of Object.entries(error.errors || {})) {
                const message = Array.isArray(value) ? value.join(" ") : String(value);
                const field = fields.find((element) => element.dataset.errorFor === name);
                if (field) {
                    field.textContent = message;
                    field.hidden = false;
                    const input = form.elements.namedItem(name);
                    input.setAttribute("aria-invalid", "true");
                    invalid ??= input;
                } else {
                    messages.push(message);
                }
            }
            feedback.textContent = messages.join(" ") || (invalid ? "Check the highlighted fields." : error.message);
            feedback.dataset.tone = "error";
            feedback.hidden = false;
            (invalid || feedback).focus();
            button.disabled = false;
            form.setAttribute("aria-busy", "false");
        }
    });
}
