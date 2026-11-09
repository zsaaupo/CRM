import { getJSON, patchJSON, deleteJSON } from "../../js/api.js";

const directory = document.querySelector("[data-role-list]");

if (directory) {
    const find = (name) => directory.querySelector(`[data-${name}]`);
    const editDialog = find("edit-dialog");
    const deleteDialog = find("delete-dialog");
    const editForm = find("edit-role-form");
    const deleteForm = find("delete-role-form");
    const nameInput = editForm.elements.namedItem("name");
    const csrfToken = editForm.elements.namedItem("csrfmiddlewaretoken").value;
    let activeRole = null;
    let loadVersion = 0;

    function roleUrl(template, id) { return template.replace("/0/", `/${id}/`); }
    function sessionExpired(error) {
        if (error.status !== 401 && error.status !== 403) return false;
        window.location.replace(directory.dataset.loginUrl);
        return true;
    }
    function feedback(message) {
        const box = find("role-feedback");
        box.dataset.tone = "success";
        box.textContent = message;
        box.hidden = false;
        box.focus();
    }
    function busy(form, value, label) {
        form.dataset.busy = String(value);
        form.setAttribute("aria-busy", String(value));
        form.querySelectorAll("button").forEach((button) => { button.disabled = value; });
        form.querySelector("[data-dialog-submit]").textContent = label;
    }
    function openDialog(role, mode) {
        activeRole = role;
        if (mode === "edit") {
            nameInput.value = role.name;
            nameInput.removeAttribute("aria-invalid");
            nameInput.setCustomValidity("");
            find("edit-role-error").hidden = true;
            editDialog.showModal();
            nameInput.focus();
        } else {
            find("delete-role-name").textContent = role.name;
            find("delete-role-error").hidden = true;
            deleteDialog.showModal();
            deleteDialog.querySelector("[data-dialog-close]").focus();
        }
    }
    function rowFor(role) {
        const row = document.createElement("tr");
        const name = document.createElement("td");
        name.className = "role-name-cell";
        name.textContent = role.name;
        const actions = document.createElement("td");
        const controls = document.getElementById("role-actions-template").content.cloneNode(true);
        const edit = controls.querySelector("[data-role-edit]");
        const manage = controls.querySelector("[data-role-manage]");
        const remove = controls.querySelector("[data-role-delete]");
        edit.setAttribute("aria-label", `Edit ${role.name}`);
        manage.setAttribute("aria-label", `Manage permissions for ${role.name}`);
        remove.setAttribute("aria-label", `Delete ${role.name}`);
        manage.href = roleUrl(directory.dataset.permissionsUrl, role.id);
        edit.addEventListener("click", () => openDialog(role, "edit"));
        remove.addEventListener("click", () => openDialog(role, "delete"));
        actions.append(controls);
        row.append(name, actions);
        return row;
    }
    async function loadRoles() {
        const version = ++loadVersion;
        directory.setAttribute("aria-busy", "true");
        find("role-status").hidden = false;
        find("role-status").classList.remove("sr-only");
        find("role-status").textContent = "Loading roles...";
        find("role-error").hidden = true;
        find("role-table").hidden = true;
        find("role-empty").hidden = true;
        find("role-count").hidden = true;
        try {
            const roles = await getJSON(directory.dataset.apiUrl);
            if (version !== loadVersion) return;
            find("role-rows").replaceChildren(...roles.map(rowFor));
            find("role-count").textContent = String(roles.length);
            find("role-count").hidden = false;
            find("role-empty").hidden = roles.length !== 0;
            find("role-table").hidden = roles.length === 0;
            find("role-status").textContent = `${roles.length} roles loaded.`;
            find("role-status").classList.add("sr-only");
        } catch (error) {
            if (version !== loadVersion || sessionExpired(error)) return;
            find("role-status").hidden = true;
            find("role-error-message").textContent = error.message || "Could not load roles.";
            find("role-error").hidden = false;
        } finally {
            if (version === loadVersion) directory.setAttribute("aria-busy", "false");
        }
    }
    [editDialog, deleteDialog].forEach((dialog) => {
        dialog.querySelector("[data-dialog-close]").addEventListener("click", () => dialog.close());
        dialog.addEventListener("cancel", (event) => {
            if (dialog.querySelector("form").dataset.busy === "true") event.preventDefault();
        });
    });
    nameInput.addEventListener("input", () => {
        nameInput.setCustomValidity("");
        nameInput.removeAttribute("aria-invalid");
        find("edit-role-error").hidden = true;
    });
    editForm.addEventListener("submit", async (event) => {
        event.preventDefault();
        if (editForm.dataset.busy === "true") return;
        nameInput.setCustomValidity(nameInput.value.trim() ? "" : "Enter a role name.");
        const errorBox = find("edit-role-error");
        errorBox.hidden = true;
        if (!nameInput.checkValidity()) {
            errorBox.textContent = nameInput.validationMessage;
            errorBox.hidden = false;
            nameInput.setAttribute("aria-invalid", "true");
            nameInput.focus();
            return;
        }
        busy(editForm, true, "Saving...");
        try {
            await patchJSON(roleUrl(directory.dataset.detailUrl, activeRole.id), {name: nameInput.value.trim()}, csrfToken);
            editDialog.close();
            feedback("Role name updated.");
            await loadRoles();
        } catch (error) {
            if (sessionExpired(error)) return;
            errorBox.textContent = error.errors?.name?.join(" ") || error.message || "Could not update this role.";
            errorBox.hidden = false;
            nameInput.setAttribute("aria-invalid", "true");
            nameInput.focus();
        } finally {
            busy(editForm, false, "Save name");
        }
    });
    deleteForm.addEventListener("submit", async (event) => {
        event.preventDefault();
        if (deleteForm.dataset.busy === "true") return;
        find("delete-role-error").hidden = true;
        busy(deleteForm, true, "Deleting...");
        try {
            await deleteJSON(roleUrl(directory.dataset.detailUrl, activeRole.id), csrfToken);
            deleteDialog.close();
            feedback("Role deleted.");
            await loadRoles();
        } catch (error) {
            if (sessionExpired(error)) return;
            find("delete-role-error").textContent = error.message || "Could not delete this role.";
            find("delete-role-error").hidden = false;
        } finally {
            busy(deleteForm, false, "Delete role");
        }
    });
    find("role-retry").addEventListener("click", loadRoles);
    loadRoles();
}
