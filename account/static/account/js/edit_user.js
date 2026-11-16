import { getJSON, patchFormData } from "../../js/api.js";

const form = document.querySelector('[data-edit-user-form]');
if (form) {
    const find = name => form.querySelector(`[data-${name}]`);
    const fields = ['full_name', 'email', 'phone_number', 'designation', 'date_of_birth',
        'gender', 'address', 'city', 'state', 'postal_code', 'country'];
    let loaded = false;
    let loading = false;
    let saving = false;

    function controls() {
        find('edit-fields').disabled = !loaded || saving;
        find('edit-save').disabled = !loaded || saving;
        find('edit-retry').disabled = loading || saving;
        find('button-label').textContent = saving ? 'Saving changes...' : 'Save changes';
        find('button-spinner').hidden = !saving;
        find('button-arrow').hidden = saving;
        form.setAttribute('aria-busy', String(loading || saving));
    }
    function status(text, tone = 'error') {
        const element = find('form-status');
        element.textContent = text;
        element.dataset.tone = tone;
        element.hidden = false;
        return element;
    }
    function clearErrors() {
        form.querySelectorAll('[data-error-for]').forEach(element => {
            element.hidden = true; element.textContent = '';
        });
        form.querySelectorAll('[aria-invalid]').forEach(element => element.removeAttribute('aria-invalid'));
        find('form-status').hidden = true;
    }
    function fieldError(name, message) {
        const input = form.elements.namedItem(name);
        const error = [...form.querySelectorAll('[data-error-for]')].find(element => element.dataset.errorFor === name);
        if (!input || !error) return false;
        input.setAttribute('aria-invalid', 'true');
        error.textContent = message; error.hidden = false;
        return true;
    }
    function showError(error) {
        const general = [];
        for (const [name, messages] of Object.entries(error.errors || {})) {
            const text = Array.isArray(messages) ? messages.join(' ') : String(messages);
            if (!fieldError(name, text)) general.push(text);
        }
        status(general.join(' ') || (error.status === 404 ? 'This user no longer exists. Return to the user list.'
            : error.message || 'Could not save the user. Please try again.')).focus();
    }
    async function loadUser() {
        if (loading || saving) return;
        loading = true; loaded = false;
        find('edit-retry').hidden = true;
        controls(); status('Loading user...', 'info');
        try {
            const user = await getJSON(form.action);
            fields.forEach(name => { form.elements.namedItem(name).value = user[name] ?? ''; });
            const image = find('edit-image');
            find('edit-initials').textContent = (user.full_name || user.email || 'U').trim().split(/\s+/)
                .slice(0, 2).map(word => word[0]).join('').toUpperCase() || 'U';
            image.hidden = true;
            if (user.image) { image.src = user.image; image.hidden = false; }
            loaded = true; find('form-status').hidden = true;
        } catch (error) {
            showError(error); find('edit-retry').hidden = false;
        } finally { loading = false; controls(); }
    }
    find('edit-image').addEventListener('error', () => { find('edit-image').hidden = true; });
    find('edit-retry').addEventListener('click', loadUser);
    form.addEventListener('input', event => {
        if (!event.target.matches('input, select, textarea')) return;
        event.target.removeAttribute('aria-invalid');
        const error = [...form.querySelectorAll('[data-error-for]')].find(element => element.dataset.errorFor === event.target.name);
        if (error) error.hidden = true;
    });
    form.addEventListener('submit', async event => {
        event.preventDefault();
        if (!loaded || saving) return;
        clearErrors();
        let invalid = null;
        for (const name of fields) {
            const input = form.elements.namedItem(name);
            input.setCustomValidity(input.required && !input.value.trim() ? 'This field is required.' : '');
            if (!input.checkValidity()) { fieldError(name, input.validationMessage); invalid ??= input; }
        }
        if (invalid) { status('Please check the highlighted fields.'); invalid.focus(); return; }
        // Build before disabling fields so multipart requests retain their values.
        const data = new FormData();
        fields.forEach(name => data.append(name, form.elements.namedItem(name).value));
        const image = form.elements.namedItem('image').files[0];
        if (image) data.append('image', image);
        saving = true; controls();
        try {
            await patchFormData(form.action, data, form.elements.namedItem('csrfmiddlewaretoken').value);
            status('User updated. Opening the user list...', 'success');
            window.location.assign(form.dataset.successUrl);
        } catch (error) { showError(error); }
        finally { saving = false; controls(); }
    });
    loadUser();
}
