import { getJSON, putJSON } from "../../js/api.js";

const operations = [
    {prefix: "add", label: "Create"},
    {prefix: "view", label: "Read"},
    {prefix: "change", label: "Update"},
    {prefix: "delete", label: "Delete"},
];

export function groupPermissions(permissions) {
    const models = new Map();
    for (const permission of permissions) {
        // Content-type IDs distinguish identically named models in different apps.
        const key = permission.content_type;
        if (!models.has(key)) models.set(key, {
            id: key, app: permission.app_label, model: permission.model,
            crud: {}, custom: [],
        });
        const row = models.get(key);
        const operation = operations.find(({prefix}) => permission.codename === `${prefix}_${permission.model}`);
        if (operation) row.crud[operation.prefix] = permission;
        else row.custom.push(permission);
    }
    return [...models.values()].sort((a, b) => a.app.localeCompare(b.app) || a.model.localeCompare(b.model));
}

const panel = document.querySelector("[data-role-permissions]");
if (panel) {
    const find = (name) => panel.querySelector(`[data-${name}]`);
    const form = find("permissions-form");
    let original = new Set();
    let loadVersion = 0;
    let loaded = false;
    let saving = false;
    function updateControls() {
        find("permissions-save").disabled = !loaded || saving;
        find("permissions-reset").disabled = !loaded || saving;
        find("permission-fields").disabled = !loaded || saving;
        find("permissions-retry").disabled = saving;
        find("permissions-save-label").textContent = saving ? "Saving..." : "Save permissions";
        find("permissions-save-spinner").hidden = !saving;
        form.setAttribute("aria-busy", String(saving));
    }
    function updateCount() {
        const checkboxes = [...form.querySelectorAll('input[name="permissions"]')];
        const selected = checkboxes.filter((input) => input.checked).length;
        const changed = checkboxes.some((input) => input.checked !== original.has(Number(input.value)));
        find("selection-count").textContent = `${selected} permissions selected${changed ? " \u00b7 Unsaved changes" : ""}`;
    }
    function checkbox(permission, withLabel = false) {
        const label = document.createElement("label");
        label.className = "permission-checkbox-label";
        const input = document.createElement("input");
        input.type = "checkbox";
        input.name = "permissions";
        input.value = String(permission.id);
        input.id = `permission-${permission.id}`;
        input.className = "permission-checkbox";
        input.checked = original.has(permission.id);
        input.setAttribute("aria-label", `${permission.name} (${permission.app_label}.${permission.model})`);
        input.title = permission.name;
        label.title = permission.name;
        label.append(input);
        if (withLabel) {
            const text = document.createElement("span");
            text.textContent = permission.name;
            label.append(text);
        }
        return label;
    }
    function modelRow(model, hasCustom) {
        const row = document.createElement("tr");
        const heading = document.createElement("th");
        heading.scope = "row";
        const name = document.createElement("span");
        name.className = "permission-model-name";
        name.textContent = model.model.replaceAll("_", " ");
        const app = document.createElement("span");
        app.className = "permission-app-name";
        app.textContent = model.app;
        heading.append(name, app);
        row.append(heading);
        for (const operation of operations) {
            const cell = document.createElement("td");
            if (model.crud[operation.prefix]) cell.append(checkbox(model.crud[operation.prefix]));
            else {
                const missing = document.createElement("span");
                missing.textContent = "\u2014";
                missing.title = `${operation.label} permission is not available for this model`;
                cell.append(missing);
            }
            row.append(cell);
        }
        if (hasCustom) {
            const cell = document.createElement("td");
            cell.className = "custom-permissions-cell";
            if (!model.custom.length) cell.textContent = "\u2014";
            model.custom.forEach((permission) => cell.append(checkbox(permission, true)));
            row.append(cell);
        }
        return row;
    }
    async function loadPermissions() {
        if (saving) return;
        const version = ++loadVersion;
        loaded = false;
        updateControls();
        panel.setAttribute("aria-busy", "true");
        find("permissions-status").hidden = false;
        find("permissions-status").classList.remove("sr-only");
        find("permissions-status").textContent = "Loading permissions...";
        find("selection-count").textContent = "Loading selections...";
        find("permissions-table").hidden = true;
        find("permissions-empty").hidden = true;
        find("permissions-error").hidden = true;
        find("permissions-feedback").hidden = true;
        try {
            const [permissions, role] = await Promise.all([getJSON(panel.dataset.apiUrl), getJSON(panel.dataset.roleUrl)]);
            if (version !== loadVersion) return;
            original = new Set(role.permissions);
            find("permissions-role-name").textContent = role.name;
            const models = groupPermissions(permissions);
            const hasCustom = models.some((model) => model.custom.length);
            find("custom-permissions-heading").hidden = !hasCustom;
            find("permission-rows").replaceChildren(...models.map((model) => modelRow(model, hasCustom)));
            find("permissions-table").hidden = models.length === 0;
            find("permissions-empty").hidden = models.length !== 0;
            find("permissions-status").textContent = `${models.length} models loaded.`;
            find("permissions-status").classList.add("sr-only");
            loaded = true;
            updateCount();
        } catch (error) {
            if (version !== loadVersion) return;
            if (error.status === 401) {
                window.location.replace(panel.dataset.loginUrl);
                return;
            }
            find("permissions-status").hidden = true;
            find("selection-count").textContent = "Permissions unavailable";
            find("permissions-error-message").textContent = error.status === 404
                ? "This role no longer exists. Return to the role list."
                : error.message || "Could not load permissions.";
            find("permissions-error").hidden = false;
        } finally {
            if (version === loadVersion) {
                panel.setAttribute("aria-busy", "false");
                updateControls();
            }
        }
    }
    form.addEventListener("submit", async (event) => {
        event.preventDefault();
        if (!loaded || saving) return;
        const permissions = [...form.querySelectorAll('input[name="permissions"]')]
            .filter((input) => input.checked)
            .map((input) => Number(input.value));
        const feedback = find("permissions-feedback");
        feedback.hidden = true;
        saving = true;
        updateControls();
        try {
            const result = await putJSON(form.action, {permissions}, form.elements.namedItem("csrfmiddlewaretoken").value);
            original = new Set(result.permissions);
            updateCount();
            feedback.dataset.tone = "success";
            feedback.textContent = "Permissions saved. Opening the role list...";
            feedback.hidden = false;
            window.location.assign(form.dataset.successUrl);
        } catch (error) {
            feedback.dataset.tone = "error";
            const messages = error.errors?.permissions;
            feedback.textContent = Array.isArray(messages) ? messages.join(" ")
                : error.status === 404 ? "This role no longer exists. Return to the role list."
                : error.message || "Could not save permissions. Please try again.";
            feedback.hidden = false;
            feedback.focus();
        } finally {
            saving = false;
            updateControls();
        }
    });
    form.addEventListener("change", updateCount);
    find("permissions-reset").addEventListener("click", () => {
        if (!loaded || saving) return;
        form.querySelectorAll('input[name="permissions"]').forEach((input) => {
            input.checked = original.has(Number(input.value));
        });
        updateCount();
    });
    find("permissions-retry").addEventListener("click", loadPermissions);
    loadPermissions();
}
