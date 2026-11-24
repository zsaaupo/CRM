import { getJSON } from "../../js/api.js";
import { catalogRow } from "../../inventory/js/catalog_list.js";

const reports = JSON.parse(document.getElementById("activity-reports").textContent);
const warehouse = document.getElementById("report-warehouse");
const activity = document.getElementById("report-activity");
const startDate = document.getElementById("report-start");
const endDate = document.getElementById("report-end");
const title = document.getElementById("report-title");
const status = document.getElementById("report-status");
const table = document.getElementById("report-table");
const pages = document.getElementById("report-pages");
const prev = document.getElementById("report-prev");
const next = document.getElementById("report-next");
const retry = document.getElementById("report-retry");
let version = 0;
let page = 1;

async function load(requestedPage = 1) {
    const current = ++version;
    page = requestedPage;
    const report = reports[activity.value];
    title.textContent = report.label;
    pages.hidden = table.hidden = retry.hidden = true;
    endDate.min = startDate.value;
    if (!startDate.validity.valid || !endDate.validity.valid) {
        status.textContent = endDate.validity.rangeUnderflow
            ? "Choose an end date on or after the start date." : "Enter a valid date.";
        return;
    }
    if (!warehouse.value) {
        status.textContent = "Select a warehouse to view activity.";
        return;
    }
    status.textContent = "Loading records…";
    const url = new URL(report.url.replace("/0/", "/" + warehouse.value + "/"), location.origin);
    url.searchParams.set("page", page);
    if (startDate.value) url.searchParams.set("start_date", startDate.value);
    if (endDate.value) url.searchParams.set("end_date", endDate.value);
    try {
        const data = await getJSON(url);
        if (current !== version) return;
        table.querySelector("thead tr").replaceChildren(...report.columns.map(column => {
            const heading = document.createElement("th");
            heading.scope = "col";
            heading.textContent = column.label;
            return heading;
        }));
        table.querySelector("tbody").replaceChildren(...data.results.map(record => catalogRow(record, report.columns)));
        table.hidden = !data.results.length;
        status.textContent = data.count ? `${data.count} records · Page ${page}` : "No records for this warehouse.";
        prev.disabled = !data.previous;
        next.disabled = !data.next;
        pages.hidden = !data.previous && !data.next;
    } catch (error) {
        if (current !== version) return;
        status.textContent = error.message;
        retry.hidden = false;
    }
}

warehouse.addEventListener("change", () => load());
activity.addEventListener("change", () => load());
startDate.addEventListener("change", () => load());
endDate.addEventListener("change", () => load());
prev.addEventListener("click", () => load(page - 1));
next.addEventListener("click", () => load(page + 1));
retry.addEventListener("click", () => load(page));
load();
