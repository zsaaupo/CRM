import { APIError, postJSON } from "../../js/api.js";

const emailStorageKey = "emanager.pendingEmail";

function rememberEmail(email) {
    try { sessionStorage.setItem(emailStorageKey, email); } catch { /* Manual entry still works. */ }
}

function clearFeedback(form) {
    form.querySelectorAll("[data-error-for]").forEach((element) => {
        element.hidden = true;
        element.textContent = "";
    });
    form.querySelectorAll("[aria-invalid]").forEach((input) => input.removeAttribute("aria-invalid"));
    const status = form.querySelector("[data-form-status]");
    status.hidden = true;
    status.textContent = "";
}

function setStatus(form, message, success = false) {
    const element = form.querySelector("[data-form-status]");
    element.dataset.tone = success ? "success" : "error";
    element.textContent = message;
    element.hidden = false;
}

function setFieldError(form, name, message) {
    const input = form.elements.namedItem(name);
    const error = [...form.querySelectorAll("[data-error-for]")].find((element) => element.dataset.errorFor === name);
    if (!input || !error) return false;
    input.setAttribute("aria-invalid", "true");
    error.textContent = message;
    error.hidden = false;
    return true;
}

function validateInputs(form, names) {
    let firstInvalid = null;
    for (const name of names) {
        const input = form.elements.namedItem(name);
        input.setCustomValidity("");
        if (input.required && !["password", "password_confirm"].includes(name) && !input.value.trim()) {
            input.setCustomValidity("This field is required.");
        }
        if (!input.checkValidity()) {
            setFieldError(form, name, input.validity.patternMismatch
                ? "Enter the six-character code from your email."
                : input.validationMessage);
            firstInvalid ??= input;
        }
    }
    if (firstInvalid) {
        setStatus(form, "Please check the highlighted fields.");
        firstInvalid.focus();
    }
    return !firstInvalid;
}

function showRequestError(form, error) {
    const general = [];
    let hasFieldErrors = false;
    if (error instanceof APIError) {
        for (const [name, value] of Object.entries(error.errors)) {
            const message = Array.isArray(value) ? value.join(" ") : String(value);
            if (setFieldError(form, name, message)) hasFieldErrors = true;
            else general.push(message);
        }
    }
    setStatus(form, general.join(" ") || (hasFieldErrors
        ? "Please check the highlighted fields."
        : error.message || "Something went wrong. Please try again."));
    const target = form.querySelector('[aria-invalid="true"]') || form.querySelector("[data-form-status]");
    target.focus();
}

function setBusy(form, activeButton, busy) {
    form.dataset.busy = String(busy);
    form.setAttribute("aria-busy", String(busy));
    form.querySelectorAll("button").forEach((button) => { button.disabled = busy; });
    const label = activeButton.querySelector("[data-button-label]");
    if (label) {
        if (busy) {
            activeButton.dataset.originalLabel = label.textContent;
            label.textContent = activeButton.dataset.loadingLabel;
        } else {
            label.textContent = activeButton.dataset.originalLabel;
        }
    }
    const spinner = activeButton.querySelector("[data-button-spinner]");
    const arrow = activeButton.querySelector("[data-button-arrow]");
    if (spinner) spinner.hidden = !busy;
    if (arrow) arrow.hidden = busy;
}

document.querySelectorAll("[data-password-toggle]").forEach((button) => {
    button.addEventListener("click", () => {
        const input = document.getElementById(button.dataset.passwordToggle);
        const showing = input.type === "password";
        input.type = showing ? "text" : "password";
        button.textContent = showing ? "Hide" : "Show";
        button.setAttribute("aria-pressed", String(showing));
        button.setAttribute("aria-label", `${showing ? "Hide" : "Show"} ${input.name === "password_confirm" ? "password confirmation" : "password"}`);
    });
});

document.querySelectorAll("[data-auth-form]").forEach((form) => {
    const emailInput = form.elements.namedItem("email");
    const csrfToken = form.elements.namedItem("csrfmiddlewaretoken").value;
    const isSignup = form.dataset.authForm === "signup";
    const isLogin = form.dataset.authForm === "login";
    const isVerify = form.dataset.authForm === "verify";
    const isAddUser = form.dataset.authForm === "add-user";

    if (isVerify) {
        try { if (!emailInput.value) emailInput.value = sessionStorage.getItem(emailStorageKey) || ""; } catch { /* Email remains editable. */ }
        form.elements.namedItem("otp").addEventListener("input", (event) => {
            event.target.value = event.target.value.toUpperCase();
        });
    }

    form.addEventListener("input", (event) => {
        if (!event.target.matches("input, select, textarea")) return;
        event.target.setCustomValidity("");
        event.target.removeAttribute("aria-invalid");
        const error = [...form.querySelectorAll("[data-error-for]")].find((element) => element.dataset.errorFor === event.target.name);
        if (error) error.hidden = true;
    });

    form.addEventListener("submit", async (event) => {
        event.preventDefault();
        if (form.dataset.busy === "true") return;
        clearFeedback(form);
        const names = isAddUser
            ? ["full_name", "email", "phone_number", "designation", "date_of_birth", "gender", "address", "city", "state", "postal_code", "country", "password", "password_confirm"]
            : isSignup
            ? ["full_name", "email", "phone_number", "password", "password_confirm"]
            : isLogin ? ["email", "password"] : ["email", "otp"];
        if (!validateInputs(form, names)) return;
        if ((isSignup || isAddUser) && form.elements.password.value !== form.elements.password_confirm.value) {
            setFieldError(form, "password_confirm", "Passwords do not match.");
            setStatus(form, "Please check the highlighted fields.");
            form.elements.password_confirm.focus();
            return;
        }

        const payload = Object.fromEntries(names.map((name) => [name, form.elements.namedItem(name).value]));
        payload.email = payload.email.trim();
        if (isAddUser && !payload.date_of_birth) payload.date_of_birth = null;
        const button = form.querySelector('button[type="submit"]');
        setBusy(form, button, true);
        try {
            const result = await postJSON(form.action, payload, csrfToken);
            if (isAddUser) {
                form.reset();
                setStatus(form, "User created. Opening the user list...", true);
                window.location.assign(form.dataset.successUrl);
            } else if (isSignup) {
                rememberEmail(result.account?.email || payload.email.toLowerCase());
                form.reset();
                form.querySelectorAll("[data-password-toggle]").forEach((toggle) => {
                    document.getElementById(toggle.dataset.passwordToggle).type = "password";
                });
                setStatus(form, "Account created. Taking you to email verification…", true);
                window.location.assign(form.dataset.successUrl);
            } else if (isLogin) {
                form.reset();
                setStatus(form, "Signed in. Taking you to your dashboard…", true);
                window.location.assign(form.dataset.successUrl);
            } else if (isVerify) {
                form.reset();
                form.hidden = true;
                try { sessionStorage.removeItem(emailStorageKey); } catch { /* No stored data to clear. */ }
                const success = document.querySelector("[data-verification-success]");
                success.hidden = false;
                success.focus();
            }
        } catch (error) {
            showRequestError(form, error);
        } finally {
            setBusy(form, button, false);
        }
    });

    const resend = form.querySelector("[data-resend-otp]");
    resend?.addEventListener("click", async () => {
        if (form.dataset.busy === "true") return;
        clearFeedback(form);
        if (!validateInputs(form, ["email"])) return;
        setBusy(form, resend, true);
        try {
            await postJSON(form.dataset.resendUrl, { email: emailInput.value.trim() }, csrfToken);
            rememberEmail(emailInput.value.trim().toLowerCase());
            form.elements.namedItem("otp").value = "";
            setStatus(form, "A new code is on its way. Use the latest code in your inbox.", true);
            form.elements.namedItem("otp").focus();
        } catch (error) {
            showRequestError(form, error);
        } finally {
            setBusy(form, resend, false);
        }
    });
});
