import { getJSON, postJSON, postFormData } from "../../js/api.js";

const byId = (id) => document.getElementById(id);
const csrf = document.querySelector("input[name=csrfmiddlewaretoken]")?.value;
const notice = byId("notice");

function feedback(message, tone = "error") {
    notice.textContent = message;
    notice.dataset.tone = tone;
    notice.hidden = !message;
}

function showError(error) {
    const entries = Object.entries(error.errors || {});
    feedback(entries.length ? entries.map(([field, value]) =>
        /^\d+$/.test(field) || field === "detail" ? String(value) : field + ": " + value).join(" · ") : error.message);
}

function bindSubmit(form, save) {
    form.addEventListener("submit", async (event) => {
        event.preventDefault();
        const button = form.querySelector("button[type=submit], button:not([type])");
        if (button.disabled) return;
        button.disabled = true;
        feedback("");
        try { await save(new FormData(form)); }
        catch (error) { showError(error); }
        finally { button.disabled = false; }
    });
}

for (const form of document.querySelectorAll("[data-card-action]")) {
    bindSubmit(form, async (data) => {
        await postFormData(form.action, data, csrf);
        location.reload();
    });
}

function setup(root) {
    const api = root.dataset.api;
    const isCard = root.dataset.purpose === "gift_card";
    const sortButtons = [...root.querySelectorAll("[data-sort]")];
    const columns = sortButtons.map(button => button.dataset.sort);
    const query = new URLSearchParams({page: "1", per_page: "10", sort_by: "id", sort_dir: "desc"});
    let generation = 0;
    let last;
    const selected = () => [...root.querySelectorAll("[data-record]:checked")].map(el => el.value);

    function updateSelection() {
        const total = byId("rows").querySelectorAll("[data-record]").length;
        const count = selected().length;
        const all = byId("select-all");
        all.disabled = total === 0;
        all.checked = total > 0 && count === total;
        all.indeterminate = count > 0 && count < total;
        if (byId("deactivate")) byId("deactivate").disabled = count === 0;
    }

    function cell(row, value) {
        const td = row.insertCell();
        td.textContent = value;
        return td;
    }

    function display() {
        const body = byId("rows");
        body.replaceChildren();
        for (const record of last.data) {
            const tr = body.insertRow();
            const box = document.createElement("input");
            box.type = "checkbox";
            box.value = record.id;
            box.dataset.record = "";
            box.setAttribute("aria-label", "Select " + (record.code || record.card_no));
            tr.insertCell().append(box);
            for (const key of columns) {
                const td = cell(tr, record[key]);
                if (isCard && key === "card_no") {
                    const link = document.createElement("a");
                    link.href = root.dataset.detail.replace("/0/", "/" + record.id + "/");
                    link.className = "text-button";
                    link.textContent = record.card_no;
                    td.replaceChildren(link);
                } else if (key === "status") {
                    const badge = document.createElement("span");
                    badge.className = "user-status-badge " + (record.status === "Active" ? "is-active" : "is-inactive");
                    badge.textContent = record.status;
                    td.replaceChildren(badge);
                } else if (!isCard && key === "value") {
                    td.textContent = record.kind === "percentage" ? record.value + "%" : last.currency + " " + record.value;
                }
            }
        }
        if (!last.data.length) cell(body.insertRow(), "No matching records.").colSpan = columns.length + 1;
        updateSelection();
        byId("count").textContent = last.meta.total_entries;
        byId("count").hidden = false;
        for (const button of sortButtons) {
            const heading = button.closest("th");
            if (button.dataset.sort === query.get("sort_by")) {
                heading.setAttribute("aria-sort", query.get("sort_dir") === "asc" ? "ascending" : "descending");
            } else {
                heading.removeAttribute("aria-sort");
            }
        }
        byId("totals").replaceChildren();
        if (isCard) {
            const row = byId("totals").insertRow();
            cell(row, "Total (" + last.currency + ")");
            for (const key of columns) cell(row, last.totals[key] ?? "");
        }
        byId("page").textContent = "Page " + last.meta.current_page + " of " + last.meta.pages + " · " + last.meta.total_entries + " records";
        byId("prev").disabled = last.meta.current_page === 1;
        byId("next").disabled = last.meta.current_page >= last.meta.pages;
    }

    async function load() {
        const version = ++generation;
        try {
            const result = await getJSON(api + "?" + query);
            if (version !== generation) return;
            last = result;
            display();
        } catch (error) { if (version === generation) showError(error); }
    }

    byId("select-all").onchange = event => {
        for (const box of root.querySelectorAll("[data-record]")) box.checked = event.target.checked;
        updateSelection();
    };
    byId("rows").addEventListener("change", updateSelection);
    let timer;
    byId("search").oninput = event => {
        clearTimeout(timer);
        query.set("search", event.target.value);
        query.set("page", 1);
        timer = setTimeout(load, 180);
    };
    byId("limit").onchange = event => { query.set("per_page", event.target.value); query.set("page", 1); load(); };
    for (const button of sortButtons) button.onclick = () => {
        const key = button.dataset.sort;
        query.set("sort_dir", query.get("sort_by") === key && query.get("sort_dir") === "asc" ? "desc" : "asc");
        query.set("sort_by", key);
        query.set("page", 1);
        load();
    };
    for (const [id, delta] of [["prev", -1], ["next", 1]]) byId(id).onclick = () => {
        query.set("page", last.meta.current_page + delta);
        load();
    };
    for (const button of root.querySelectorAll("[data-export]")) button.onclick = () => {
        const params = new URLSearchParams(query);
        params.set("export", button.dataset.export);
        selected().forEach(id => params.append("selected", id));
        location.assign(api + "?" + params);
    };
    byId("print").onclick = () => window.print();
    if (byId("deactivate")) byId("deactivate").onclick = async () => {
        const ids = selected();
        if (!ids.length) return;
        if (!window.confirm("Deactivate " + ids.length + " selected record(s)?")) return;
        try {
            await postJSON(api + "deactivate/", {ids}, csrf);
            feedback("Selection deactivated.", "success");
            await load();
        } catch (error) { showError(error); }
    };
    const form = byId("create");
    if (form) {
        function toggleForm(open) {
            form.hidden = !open;
            byId("add").setAttribute("aria-expanded", String(open));
            if (open) form.querySelector("input:not([type=hidden]), select")?.focus();
            else byId("add").focus();
        }
        byId("add").onclick = () => toggleForm(form.hidden);
        byId("cancel").onclick = () => { form.reset(); toggleForm(false); feedback(""); };
        byId("generate").onclick = async () => {
            try {
                const result = await postJSON(root.dataset.generator, {purpose: root.dataset.purpose}, csrf);
                form.elements[isCard ? "card_no" : "code"].value = result.code;
            } catch (error) { showError(error); }
        };
        bindSubmit(form, async data => {
            if (!isCard && !data.get("code").trim()) {
                const result = await postJSON(root.dataset.generator, {purpose: "coupon"}, csrf);
                form.elements.code.value = result.code;
                data.set("code", result.code);
            }
            const record = await postFormData(api, data, csrf);
            if (isCard) { location.assign(root.dataset.detail.replace("/0/", "/" + record.id + "/")); return; }
            form.reset();
            toggleForm(false);
            feedback("Created coupon " + record.code + ".", "success");
            query.set("page", 1);
            await load();
        });
    }
    load();
}

if (byId("instruments")) setup(byId("instruments"));

