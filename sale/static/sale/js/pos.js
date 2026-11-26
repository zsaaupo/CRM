import {getJSON, postJSON} from "../../js/api.js";

const $ = id => document.getElementById(id);
const root = $("pos");
const base = new URL("../", new URL(root.dataset.base, location.origin));
const csrf = root.querySelector("[name=csrfmiddlewaretoken]").value;
const currency = root.dataset.currency;
const recovery = "pos-last-checkout-" + root.dataset.cashier;
const get = (route, params = {}) => getJSON(new URL(route, base) + "?" + new URLSearchParams(params));
const post = (route, payload) => postJSON(new URL(route, base), payload, csrf);
const money = value => currency + " " + value;
function newKey() {
    if (crypto.randomUUID) return crypto.randomUUID();
    const bytes = crypto.getRandomValues(new Uint8Array(16));
    bytes[6] = (bytes[6] & 15) | 64;
    bytes[8] = (bytes[8] & 63) | 128;
    const hex = Array.from(bytes, b => b.toString(16).padStart(2, "0")).join("");
    return [hex.slice(0,8), hex.slice(8,12), hex.slice(12,16), hex.slice(16,20), hex.slice(20)].join("-");
}
let key = newKey(), revision = 0, customer = null, lines = [], quote = null;
let catalogPage = 1, catalogVersion = 0, quoteVersion = 0, customerVersion = 0;
let saving = false, quoteTimer, searchTimer, pendingScans = 0, historyPage = 1;

function node(tag, text, cls) {
    const el = document.createElement(tag);
    if (text !== undefined) el.textContent = text;
    if (cls) el.className = cls;
    return el;
}
function message(text = "", success = false) {
    const dialog = root.querySelector("dialog[open]");
    let box = $("pos-notice");
    if (dialog) {
        box = dialog.querySelector("[data-notice]");
        if (!box) { box = node("p", "", "form-status mb-4"); box.dataset.notice = ""; box.setAttribute("role","status"); dialog.prepend(box); }
    }
    box.textContent = text;
    box.hidden = !text;
    box.dataset.tone = success ? "success" : "error";
}
function fail(error) {
    const values = Object.values(error.errors || {}).flat(Infinity).filter(x => typeof x === "string");
    message(values.join(" · ") || error.message);
}
function modal(id) {
    const dialog = $(id);
    const notice = dialog.querySelector("[data-notice]");
    if (notice) notice.hidden = true;
    dialog.showModal();
}
for (const button of root.querySelectorAll("[data-close]")) button.onclick = () => {
    if (!saving) button.closest("dialog").close();
};
for (const dialog of root.querySelectorAll("dialog")) dialog.oncancel = e => { if (saving) e.preventDefault(); };

function cart() {
    return {warehouse: $("warehouse").value, customer: customer?.id ?? null, date: $("sale-date").value,
        items: lines.map(({product, quantity, unit_discount}) => ({product, quantity, unit_discount})),
        discount_type: $("discount-type").value, discount: $("discount").value || "0", coupon: $("coupon").value.trim(),
        tax_rate: $("tax-rate").value || null, shipping: $("shipping").value || "0"};
}
function changed() {
    const version = ++quoteVersion;
    clearTimeout(quoteTimer);
    quote = null;
    $("park").disabled = $("pay").disabled = true;
    $("warehouse").disabled = Boolean(lines.length || revision);
    for (const id of ["grand","subtotal","discount-total","coupon-total","tax","shipping-total"]) $(id).textContent = "—";
    for (const cell of root.querySelectorAll("[data-line-total]")) cell.textContent = "—";
    if (!lines.length) return;
    quoteTimer = setTimeout(async () => {
        try {
            const result = await post("quote/", cart());
            if (version !== quoteVersion) return;
            quote = result;
            $("pay").disabled = $("park").disabled = false;
            for (const [id, field] of Object.entries({subtotal:"subtotal","discount-total":"discount","coupon-total":"coupon",
                tax:"tax","shipping-total":"shipping",grand:"total"})) $(id).textContent = money(result[field]);
            for (const item of result.items) {
                const row = $("cart-lines").querySelector('[data-product="' + item.product + '"]');
                row.querySelector("[data-line-total]").textContent = item.subtotal;
                row.querySelector("[data-price]").textContent = item.unit_price;
                row.querySelector("strong").textContent = item.name;
                row.querySelector("small").textContent = item.sku;
            }
            $("item-count").textContent = lines.length + " SKU · " + quantitySum(lines) + " units";
            message();
        } catch (error) { if (version === quoteVersion) fail(error); }
    }, 140);
}
function quantitySum(items) {
    const units = items.reduce((sum, i) => sum + Math.round(Number(i.quantity) * 1e6), 0);
    return units / 1e6;
}
function renderCart() {
    const body = $("cart-lines");
    body.replaceChildren();
    $("item-count").textContent = lines.length + " SKU";
    if (!lines.length) {
        const cell = body.insertRow().insertCell();
        cell.colSpan = 6; cell.textContent = "Scan a barcode or select a product.";
    }
    for (const line of lines) {
        const tr = body.insertRow(); tr.dataset.product = line.product;
        const name = tr.insertCell();
        name.append(node("strong", line.name || "Product #" + line.product), node("br"), node("small", line.sku || "", "pos-muted"));
        tr.insertCell().dataset.price = "";
        for (const field of ["quantity","unit_discount"]) {
            const input = node("input", undefined, "form-input");
            input.type = "number"; input.step = field === "quantity" ? ".000001" : ".0001";
            input.min = field === "quantity" ? ".000001" : "0"; input.value = line[field];
            input.setAttribute("aria-label", (field === "quantity" ? "Quantity" : "Discount per unit") + " for " + (line.name || line.product));
            input.oninput = () => { line[field] = input.value; changed(); };
            tr.insertCell().append(input);
        }
        tr.insertCell().dataset.lineTotal = "";
        const remove = node("button","Remove","text-button");
        remove.onclick = () => { lines = lines.filter(i => i !== line); renderCart(); };
        tr.insertCell().append(remove);
    }
    changed();
}
function addProduct(product) {
    const line = lines.find(i => i.product === product.id);
    if (line) line.quantity = String((Math.round(Number(line.quantity || 0) * 1e6) + 1e6) / 1e6);
    else lines.push({product:product.id, name:product.name, sku:product.sku, quantity:"1", unit_discount:"0"});
    renderCart();
}
async function catalog() {
    const version = ++catalogVersion;
    const warehouse = $("warehouse").value;
    $("tiles").replaceChildren();
    if (!warehouse) { $("tiles").textContent = "An active warehouse is required."; return; }
    try {
        const q = $("lookup").value.trim();
        const result = await get("catalog/", {warehouse, date:$("sale-date").value, q:q.length >= 2 ? q : "",
            category:$("category").value, brand:$("brand").value, featured:$("featured").checked ? "1" : "0", page:catalogPage});
        if (version !== catalogVersion) return;
        for (const p of result.results) {
            const button = node("button",undefined,"pos-tile");
            if (p.image) { const image = node("img"); image.src = p.image; image.alt = ""; image.loading = "lazy"; button.append(image); }
            button.append(node("strong",p.name), node("small",p.sku + " · " + p.unit), node("span",money(p.price)));
            button.onclick = () => addProduct(p);
            $("tiles").append(button);
        }
        if (!result.results.length) $("tiles").textContent = "No stocked products match this selection.";
        catalogPage = result.page;
        $("catalog-page").textContent = result.page + " / " + result.pages;
        $("catalog-prev").disabled = result.page <= 1; $("catalog-next").disabled = result.page >= result.pages;
    } catch (error) { if (version === catalogVersion) fail(error); }
}
$("lookup").oninput = () => { clearTimeout(searchTimer); catalogPage = 1; searchTimer = setTimeout(() => catalog(),180); };
$("lookup").onkeydown = async event => {
    const scan = event.target.value.trim();
    if (event.key !== "Enter" || !scan) return;
    event.preventDefault(); clearTimeout(searchTimer);
    event.target.value = "";
    catalogPage = 1;
    const ticket = key, warehouse = $("warehouse").value;
    pendingScans++;
    try {
        const result = await get("catalog/", {warehouse, date:$("sale-date").value, scan});
        if (ticket !== key || warehouse !== $("warehouse").value) return;
        if (result.results.length === 1) addProduct(result.results[0]);
        else message("No stocked product matches that barcode or SKU.");
        if (!$("lookup").value) catalog();
    } catch (error) { fail(error); }
    finally { pendingScans--; }
};
for (const id of ["category","brand","featured","warehouse","sale-date"]) $(id).onchange = () => {
    catalogPage = 1; catalog(); changed();
};
$("catalog-prev").onclick = () => { catalogPage--; catalog(); };
$("catalog-next").onclick = () => { catalogPage++; catalog(); };
for (const id of ["discount-type","discount","coupon","tax-rate","shipping"]) $(id).oninput = changed;

function chooseCustomer(record) {
    customerVersion++;
    customer = record;
    $("customer-search").value = record ? record.name + " · " + record.code : "";
    $("customer-results").replaceChildren();
    changed();
}
$("customer-search").oninput = async event => {
    const q = event.target.value.trim();
    const version = ++customerVersion;
    customer = null; changed();
    $("customer-results").replaceChildren();
    if (!q) return;
    try {
        const rows = await get("customers/", {q});
        if (version !== customerVersion) return;
        for (const row of rows) {
            const button = node("button",row.name + " · " + row.code,"pos-customer-choice");
            button.onclick = () => chooseCustomer(row); $("customer-results").append(button);
        }
        if (!rows.length) $("customer-results").textContent = "No matching customer.";
    } catch (error) { if (version === customerVersion) fail(error); }
};
if ($("add-customer")) {
    $("add-customer").onclick = () => modal("new-customer");
    $("customer-form").onsubmit = async event => {
        event.preventDefault();
        const button = event.submitter;
        if (button.disabled) return;
        button.disabled = true;
        try {
            const response = await postJSON(root.dataset.people,{...Object.fromEntries(new FormData(event.target)), is_customer:true},csrf);
            chooseCustomer(response.party); $("new-customer").close(); event.target.reset();
        } catch (error) { fail(error); } finally { button.disabled = false; }
    };
}

function reset() {
    key = newKey(); revision = 0; customer = null; lines = []; customerVersion++;
    $("customer-search").value = ""; $("customer-results").replaceChildren();
    $("coupon").value = $("tax-rate").value = ""; $("discount").value = $("shipping").value = "0";
    $("discount-type").value = "flat"; $("sale-date").value = $("sale-date").max;
    renderCart(); catalog();
}
async function savingOnce(task) {
    if (saving) return;
    saving = true;
    const surface = root.querySelector("dialog[open]") || root;
    surface.inert = true;
    try { await task(); } catch (error) { fail(error); }
    finally { surface.inert = false; saving = false; }
}
$("park").onclick = () => savingOnce(async () => {
    if (pendingScans) throw Error("Wait for the barcode lookup to finish.");
    if ($("customer-search").value.trim() && !customer) throw Error("Select a customer or clear the search for walk-in.");
    await post("draft/", {key,revision,cart:cart()});
    reset(); message("Draft saved.",true);
});
$("cancel-cart").onclick = () => savingOnce(async () => {
    if ((lines.length || revision) && !confirm("Discard this cart?")) return;
    if (revision) await post("tickets/" + key + "/",{revision});
    reset(); message();
});

function cents(value) {
    const s = String(value || "0");
    if (!/^\d+(\.\d{0,2})?$/.test(s)) throw Error("Use amounts with at most two decimal places.");
    const [whole, fraction = ""] = s.split(".");
    return BigInt(whole) * 100n + BigInt(fraction.padEnd(2,"0"));
}
function decimal(n) {
    if (n < 0n) return "-" + decimal(-n);
    return String(n / 100n) + "." + String(n % 100n).padStart(2,"0");
}
const tenderRows = () => [...$("tenders").querySelectorAll("fieldset")];
const field = (row, name) => row.querySelector('[name="' + name + '"]');
function due(except = null) {
    return cents(quote.total) - tenderRows().filter(r => r !== except).reduce((n,r) => n + cents(field(r,"amount").value),0n);
}
function updateDue() {
    try {
        const remaining = due();
        let change = 0n;
        for (const row of tenderRows()) {
            if (field(row,"method").value === "cash" && field(row,"received").value) {
                const excess = cents(field(row,"received").value) - cents(field(row,"amount").value);
                if (excess < 0n) throw Error("Cash received is less than the cash tender.");
                change += excess;
            }
        }
        $("remaining").textContent = "Remaining " + money(decimal(remaining)) + " · Cash change " + money(decimal(change));
        $("complete").disabled = remaining !== 0n;
    } catch (error) { $("remaining").textContent = error.message; $("complete").disabled = true; }
}
function addTender(amount) {
    const row = $("tender-template").content.firstElementChild.cloneNode(true);
    const method = field(row,"method"), account = field(row,"account");
    function methodChanged() {
        for (const name of ["account","received","reference","card_no"]) {
            const visible = {account:["cash","card","cheque"],received:["cash"],reference:["card","cheque"],card_no:["gift_card"]}[name].includes(method.value);
            field(row,name).closest("label").hidden = !visible;
        }
        for (const option of account.options) {
            option.disabled = option.hidden = option.dataset.type !== (method.value === "cash" ? "cash" : "bank")
                || Boolean(option.dataset.warehouse && option.dataset.warehouse !== $("warehouse").value);
        }
        account.selectedIndex = [...account.options].findIndex(o => !o.disabled);
        if (!account.value && ["cash","card","cheque"].includes(method.value)) {
            message("This warehouse needs an active " + (method.value === "cash" ? "cash" : "bank") + " account for this tender.");
        } else {
            message();
        }
        row.querySelector("[data-balance]").hidden = method.value !== "gift_card";
        if (method.value === "deposit") {
            const balance = cents(quote.deposit_balance);
            const needed = due(row);
            field(row,"amount").value = decimal(needed < 0n ? 0n : needed < balance ? needed : balance);
            message("Customer deposit balance " + money(quote.deposit_balance), true);
        }
        updateDue();
    }
    row.querySelector("[data-balance]").onclick = async () => {
        try {
            const needed = due(row);
            if (needed <= 0n) throw Error("Enter the other tenders first to calculate the gift-card portion.");
            const info = await postJSON(root.dataset.card,{card_no:field(row,"card_no").value,customer_id:customer?.id,payable_amount:decimal(needed)},csrf);
            field(row,"amount").value = info.tender_amount;
            message("Gift card balance " + money(info.balance),true); updateDue();
        } catch (error) { fail(error); }
    };
    row.querySelector("[data-remove]").onclick = () => { row.remove(); updateDue(); };
    row.oninput = updateDue;
    method.onchange = methodChanged;
    field(row,"amount").value = decimal(amount);
    $("tenders").append(row); methodChanged();
}
$("pay").onclick = () => {
    if (!quote) return;
    if (pendingScans) { message("Wait for the barcode lookup to finish."); return; }
    if ($("customer-search").value.trim() && !customer) { message("Select a customer result or clear the field for walk-in."); return; }
    $("tenders").replaceChildren();
    $("amount-due").textContent = "Grand total " + money(quote.total);
    modal("payment");
    if (cents(quote.total) > 0n) addTender(cents(quote.total));
    updateDue();
};
$("split").onclick = () => { try { const n = due(); addTender(n > 0n ? n : 0n); } catch(error) { fail(error); } };
function tenderData(row) {
    const value = name => field(row,name).value;
    const method = value("method");
    const tender = {method, amount:value("amount")};
    if (["cash","card","cheque"].includes(method)) {
        if (!value("account")) throw Error("Select a compatible financial account for this warehouse.");
        tender.account = value("account");
        if (method !== "cash") tender.reference = value("reference");
        if (method === "cash" && value("received")) tender.received = value("received");
    }
    if (method === "gift_card") tender.card_no = value("card_no");
    return tender;
}
function receipt(data) {
    const body = $("receipt-body"); body.replaceChildren(node("h2","Sales receipt","section-title"),node("p",data.number,"pos-muted"));
    function pair(label, value) { const line = node("div",undefined,"pos-receipt-line"); line.append(node("span",label),node("strong",value)); body.append(line); }
    pair(data.warehouse,data.date); pair(data.customer,data.cashier);
    for (const i of data.items) pair(i.name + " · " + i.quantity + " × " + i.unit_price, i.subtotal);
    pair("Subtotal",data.subtotal); pair("Discount",data.discount); pair("Coupon " + data.coupon_code,data.coupon);
    pair("Tax",data.tax); pair("Shipping",data.shipping); pair("Grand total",money(data.total));
    for (const p of data.tenders) {
        pair(p.method.replace("_"," ") + (p.reference ? " · " + p.reference : ""),money(p.amount));
        if (p.received) { pair("Cash received",money(p.received)); pair("Change",money(p.change)); }
    }
    modal("receipt");
}
$("complete").onclick = () => savingOnce(async () => {
    const payload = {key,revision,cart:cart(),quote:quote.quote,tenders:tenderRows().map(tenderData)};
    sessionStorage.setItem(recovery,key);
    try {
        const result = await post("checkout/",payload);
        sessionStorage.removeItem(recovery);
        $("payment").close(); reset(); receipt(result.receipt);
    } catch (error) {
        if (error.status === 409) {
            const result = await get("tickets/" + key + "/").catch(() => null);
            if (result?.status === "completed") { sessionStorage.removeItem(recovery); $("payment").close(); reset(); receipt(result.receipt); return; }
            $("payment").close(); changed();
        }
        throw error;
    }
});
$("print-receipt").onclick = () => window.print();

async function openTicket(id) {
    const item = await get("tickets/" + id + "/");
    if (item.status === "completed") { $("history").close(); receipt(item.receipt); return; }
    if (item.status !== "draft") throw Error("This cart has been cancelled.");
    if (lines.length && !confirm("Replace the current cart with this draft?")) return;
    const c = item.cart;
    let owner = null;
    if (c.customer) {
        [owner] = await get("customers/",{id:c.customer});
        if (!owner) throw Error("The draft customer is no longer available.");
    }
    key = item.key; revision = item.revision; lines = c.items; customer = owner;
    $("customer-search").value = owner ? owner.name + " · " + owner.code : "";
    $("customer-results").replaceChildren(); customerVersion++;
    for (const [id, value] of Object.entries({warehouse:c.warehouse,"sale-date":c.date,"discount-type":c.discount_type,
        discount:c.discount,coupon:c.coupon,"tax-rate":c.tax_rate,shipping:c.shipping})) $(id).value = value ?? "";
    $("history").close(); renderCart(); catalog();
}
async function history(page) {
    try {
        const result = await get("history/",{warehouse:$("warehouse").value, page});
        const items = result.results;
        historyPage = result.page;
        $("history-prev").disabled = result.page <= 1;
        $("history-next").disabled = result.page >= result.pages;
        $("history-page").textContent = result.page + " / " + result.pages;
        const list = $("history-rows"); list.replaceChildren();
        if (!items.length) list.textContent = "No recent transactions for this till operator.";
        for (const item of items) {
            const button = node("button",(item.number || "Parked cart") + " · " + new Date(item.updated).toLocaleString());
            button.onclick = () => openTicket(item.key).catch(fail); list.append(button);
        }
        if (!$("history").open) modal("history");
    } catch (error) { fail(error); }
}
$("recent").onclick = () => history(1);
$("history-prev").onclick = () => history(historyPage - 1);
$("history-next").onclick = () => history(historyPage + 1);
renderCart(); catalog();
const previous = sessionStorage.getItem(recovery);
if (previous) get("tickets/" + previous + "/").then(ticket => {
    if (ticket.status === "completed") { receipt(ticket.receipt); sessionStorage.removeItem(recovery); }
}).catch(error => { if (error.status === 404) sessionStorage.removeItem(recovery); else fail(error); });
