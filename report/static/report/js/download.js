export async function downloadReport(url, trigger, feedback) {
    if (trigger.dataset.reportBusy === 'true') return;
    trigger.dataset.reportBusy = 'true'; trigger.disabled = true;
    trigger.setAttribute('aria-busy', 'true'); trigger.setAttribute('aria-disabled', 'true');
    const label = trigger.querySelector('[data-report-label]');
    const original = label?.textContent;
    if (label) label.textContent = 'Generating PDF...';
    const icon = trigger.querySelector('svg');
    let spinner;
    if (icon) {
        icon.style.display = 'none';
        spinner = document.createElement('span'); spinner.className = 'button-spinner';
        spinner.setAttribute('aria-hidden', 'true'); trigger.append(spinner);
    }
    const controller = new AbortController();
    const timer = window.setTimeout(() => controller.abort(), 60000);
    feedback.hidden = true;
    try {
        const response = await fetch(url, {method: 'GET', credentials: 'same-origin', mode: 'same-origin',
            cache: 'no-store', headers: {Accept: 'application/pdf, application/json'}, signal: controller.signal});
        if (!response.ok) {
            const data = await response.json().catch(() => ({}));
            throw new Error(response.status === 403 ? 'You do not have permission to download this document, or your session has changed.'
                : response.status === 404 ? 'This document is unavailable. Refresh the list.'
                : typeof data.detail === 'string' ? data.detail : 'Could not generate the PDF. Please try again.');
        }
        if (!(response.headers.get('Content-Type') || '').includes('application/pdf')) throw new Error('The server did not return a PDF. Refresh the page and try again.');
        const blob = await response.blob();
        if (!blob.size) throw new Error('The generated PDF was empty. Please try again.');
        const disposition = response.headers.get('Content-Disposition') || '';
        const match = disposition.match(/filename="([^"]+)"/);
        const filename = (match?.[1] || 'document.pdf').replace(/[\\/\x00-\x1f]/g, '_');
        const objectUrl = URL.createObjectURL(blob);
        const link = document.createElement('a'); link.href = objectUrl; link.download = filename;
        document.body.append(link); link.click(); link.remove();
        window.setTimeout(() => URL.revokeObjectURL(objectUrl), 1000);
    } catch (error) {
        feedback.textContent = error.name === 'AbortError' ? 'PDF generation took too long. Please try again.'
            : error.message || 'Unable to download the PDF. Check your connection and try again.';
        feedback.dataset.tone = 'error'; feedback.hidden = false; feedback.focus();
    } finally {
        window.clearTimeout(timer); delete trigger.dataset.reportBusy; trigger.disabled = false;
        trigger.removeAttribute('aria-busy'); trigger.removeAttribute('aria-disabled');
        if (label) label.textContent = original;
        spinner?.remove();
        if (icon) icon.style.display = '';
    }
}

export function addReportActions(panel) {
    const template = panel.dataset.reportUrlTemplate;
    if (!template) return;
    for (const row of panel.querySelector('[data-catalog-rows]').children) {
        if (!row.dataset.recordId || row.querySelector('[data-report-download]')) continue;
        const actions = row.lastElementChild?.firstElementChild;
        if (!actions) continue;
        const link = document.createElement('a'); link.className = 'icon-button';
        link.href = template.replace('/0/', `/${encodeURIComponent(row.dataset.recordId)}/`);
        link.dataset.reportDownload = ''; link.dataset.reportUrl = link.href;
        link.title = 'Download PDF'; link.setAttribute('aria-label', 'Download PDF');
        const icon = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
        for (const [name, value] of Object.entries({width: '18', height: '18', viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', 'stroke-width': '1.7', 'aria-hidden': 'true'})) icon.setAttribute(name, value);
        const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
        path.setAttribute('d', 'M14 2H6v20h12V6l-4-4ZM14 2v5h5M12 10v7m-3-3 3 3 3-3');
        path.setAttribute('stroke-linecap', 'round'); path.setAttribute('stroke-linejoin', 'round');
        icon.append(path); link.append(icon); actions.append(link);
    }
}

const panel = document.querySelector('[data-inventory-list][data-report-url-template]');
if (panel?.dataset.reportUrlTemplate) {
    addReportActions(panel);
    new MutationObserver(() => addReportActions(panel)).observe(panel.querySelector('[data-catalog-rows]'), {childList: true, subtree: true});
}
document.addEventListener('click', event => {
    const trigger = event.target.closest('[data-report-download]');
    if (!trigger) return;
    event.preventDefault();
    const workspace = trigger.closest('.workspace-content') || document.body;
    let feedback = workspace.querySelector('[data-report-feedback]');
    if (!feedback) {
        feedback = document.createElement('p'); feedback.dataset.reportFeedback = '';
        feedback.className = 'form-status mb-5'; feedback.setAttribute('role', 'alert'); feedback.tabIndex = -1;
        const heading = workspace.querySelector('.page-heading');
        if (heading) heading.insertAdjacentElement('afterend', feedback); else workspace.append(feedback);
    }
    downloadReport(trigger.dataset.reportUrl || trigger.href, trigger, feedback);
});
