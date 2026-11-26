const create = document.querySelector('[data-stock-count-create]');
if (create) {
    const type = create.querySelector('[data-count-type]');
    const filters = create.querySelectorAll('[data-partial-filter]');
    const updateScope = () => {
        for (const filter of filters) filter.hidden = type.value !== 'partial';
    };
    type.addEventListener('change', updateScope);
    updateScope();
}

const finish = document.querySelector('[data-stock-count-finalize]');
if (finish) {
    const method = finish.querySelector('[data-count-method]');
    const panels = finish.querySelectorAll('[data-count-method-panel]');
    const inline = [...finish.querySelectorAll('[data-count-sku]')];
    const updateMethod = () => {
        for (const panel of panels) panel.disabled = panel.hidden = panel.dataset.countMethodPanel !== method.value;
    };
    method.addEventListener('change', updateMethod);
    updateMethod();
    finish.addEventListener('submit', () => {
        if (method.value !== 'inline') return;
        finish.querySelector('[data-inline-counts]').value = JSON.stringify(inline.map(input => ({
            sku: input.dataset.countSku, counted_qty: input.value,
        })));
        for (const input of inline) input.removeAttribute('name');
    });
}

const review = document.querySelector('[data-stock-count-review]');
if (review && typeof review.showModal === 'function') {
    review.close();
    review.showModal();
    review.addEventListener('cancel', event => event.preventDefault());
}

const list = document.querySelector('[data-stock-count-list]');
if (list) {
    const toggles = [...list.querySelectorAll('[data-count-column-toggle]')];
    const controls = list.querySelector('[data-count-visibility]');
    const fields = list.querySelector('[data-count-selected-columns]');
    const print = list.querySelector('[data-count-print]');
    const pageSize = list.querySelector('[data-count-page-size]');
    controls.hidden = print.hidden = false;
    print.addEventListener('click', () => window.print());
    pageSize.addEventListener('change', () => pageSize.form.requestSubmit());
    controls.addEventListener('change', () => {
        fields.replaceChildren();
        for (const toggle of toggles) {
            for (const cell of list.querySelectorAll(`[data-count-column="${toggle.value}"]`)) {
                cell.hidden = !toggle.checked;
            }
            if (toggle.checked) {
                const input = document.createElement('input');
                input.type = 'hidden';
                input.name = 'column';
                input.value = toggle.value;
                fields.append(input);
            }
        }
        for (const link of list.querySelectorAll('[data-count-query]')) {
            const url = new URL(link.href);
            url.searchParams.delete('column');
            for (const input of fields.children) url.searchParams.append('column', input.value);
            link.href = url.href;
        }
    });
}
