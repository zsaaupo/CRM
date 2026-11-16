import { getJSON } from "../../js/api.js";

export function initializeRoleWarehouseAccess(panel, onChange = () => {}) {
    const find = name => panel.querySelector(`[data-warehouse-${name}]`);
    const mode = find('mode');
    const warehouse = find('select');
    const feedback = find('feedback');
    const roleId = Number(panel.dataset.roleId);
    let assignment = null;
    let loaded = false;
    let busy = false;
    let loading = false;
    let canSave = false;
    let canChange = false;
    let canAssignAll = false;

    function message(text, tone = 'info') {
        feedback.textContent = text;
        feedback.dataset.tone = tone;
        feedback.hidden = false;
    }

    function controls() {
        const disabled = !loaded || busy || !canSave;
        find('fields').disabled = disabled;
        find('retry').disabled = busy || loading;
        find('select-wrap').hidden = mode.value !== 'specific';
        warehouse.disabled = disabled || mode.value !== 'specific';
        warehouse.required = mode.value === 'specific';
        find('all').disabled = !canAssignAll;
        panel.setAttribute('aria-busy', String(busy || loading));
    }

    function restore() {
        mode.value = assignment?.all_warehouses ? 'all' : assignment?.warehouse ? 'specific' : 'none';
        warehouse.value = assignment?.warehouse ? String(assignment.warehouse) : '';
        controls();
    }

    async function load() {
        if (busy || loading) return;
        loading = true;
        loaded = false;
        find('retry').hidden = true;
        controls();
        message('Loading warehouse access...');
        try {
            const data = await getJSON(panel.dataset.optionsUrl);
            if (Number(data.role) !== roleId || !Array.isArray(data.warehouse)
                || (data.assignment && Number(data.assignment.role) !== roleId)) {
                throw new Error('Could not load warehouse access for this role.');
            }
            assignment = data.assignment;
            canChange = data.can_change === true;
            canSave = assignment ? canChange : data.can_create === true;
            canAssignAll = data.can_assign_all === true;
            warehouse.replaceChildren();
            const placeholder = document.createElement('option');
            placeholder.value = '';
            placeholder.textContent = 'Select a warehouse';
            warehouse.append(placeholder);
            for (const row of data.warehouse) {
                const option = document.createElement('option');
                option.value = String(row.value);
                option.textContent = row.label;
                warehouse.append(option);
            }
            loaded = true;
            restore();
            if (canSave) feedback.hidden = true;
            else message('You can view this setting but do not have permission to change it.');
        } catch (error) {
            message(error.status === 403 ? 'Your permissions do not allow managing warehouse access for this role.'
                : error.status === 404 ? 'This role or its warehouse access is unavailable.'
                : error.message || 'Could not load warehouse access.', 'error');
            find('retry').hidden = false;
        } finally {
            loading = false;
            controls();
            onChange();
        }
    }

    function currentPayload() {
        return {warehouse: mode.value === 'specific' ? Number(warehouse.value) || null : null,
                all_warehouses: mode.value === 'all'};
    }
    function getPayload() {
        if (!loaded || !canSave) throw new Error('Warehouse access settings are unavailable. Reload before saving.');
        if (!['none', 'specific', 'all'].includes(mode.value)
            || (mode.value === 'all' && !canAssignAll)
            || (mode.value === 'specific' && !warehouse.value)) {
            message('Select an available warehouse access setting.', 'error');
            feedback.focus();
            throw new Error('Select an available warehouse access setting.');
        }
        return currentPayload();
    }
    mode.addEventListener('change', () => { controls(); onChange(); });
    find('retry').addEventListener('click', load);
    return {
        ready: load(),
        get readyToSave() { return loaded && canSave; },
        getPayload,
        setBusy(value) { busy = value; controls(); },
        reset() { if (loaded && !busy) { restore(); onChange(); } },
        hasChanges() { return loaded && JSON.stringify(currentPayload()) !== JSON.stringify({
            warehouse: assignment?.warehouse ?? null, all_warehouses: assignment?.all_warehouses ?? false}); },
        acceptSaved(record) {
            if (!record?.id || Number(record.role) !== roleId) throw new Error('Could not confirm the saved warehouse setting. Reload the page.');
            assignment = record; restore();
        },
    };
}

