import { getJSON } from "../../js/api.js";

const dashboard = document.querySelector("[data-dashboard]");
const formatCount = new Intl.NumberFormat();
const formatDay = new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeZone: "UTC" });
const formatMonth = new Intl.DateTimeFormat(undefined, { month: "short", timeZone: "UTC" });
const formatCompact = new Intl.NumberFormat(undefined, { notation: "compact", maximumFractionDigits: 1 });

function money(value, currency) {
    return new Intl.NumberFormat(undefined, {
        style: "currency",
        currency,
        currencyDisplay: "code",
        maximumFractionDigits: 2,
    }).format(value);
}

function metric(name, value) {
    dashboard.querySelectorAll(`[data-dashboard-metric="${name}"]`).forEach((item) => {
        item.textContent = value;
    });
}

function panel(name) {
    const wrapper = dashboard.querySelector(`[data-dashboard-panel="${name}"]`);
    const content = wrapper.querySelector("[data-dashboard-content]");
    const status = wrapper.querySelector("[data-dashboard-status]");
    return {
        wrapper,
        content,
        show() {
            status.hidden = true;
            content.hidden = false;
        },
        message(text) {
            status.textContent = text;
        },
    };
}

function element(tag, text, className) {
    const node = document.createElement(tag);
    if (text) node.textContent = text;
    if (className) node.className = className;
    return node;
}

async function loadCount(name, url) {
    try {
        const { count } = await getJSON(url);
        metric(name, formatCount.format(count));
    } catch {
        metric(name, "Unavailable");
    }
}

async function loadSales(url) {
    const sales = panel("sales");
    const breakdown = panel("breakdown");
    try {
        const { count, results } = await getJSON(url);
        metric("sales", formatCount.format(count));
        if (!results.length) {
            sales.message("No sales orders yet.");
            breakdown.message("Order status will appear when sales are recorded.");
            return;
        }

        const rows = results.slice(0, 4).map((order) => {
            const row = element("tr");
            const orderCell = element("td");
            const link = element("a", order.order_number, "dashboard-order-link");
            link.href = dashboard.dataset.saleDetailUrl.replace(/\/0\/$/, `/${order.id}/`);
            const date = element("time", formatDay.format(new Date(`${order.order_date}T00:00:00Z`)));
            date.dateTime = order.order_date;
            orderCell.append(link, date);

            const statusCell = element("td");
            const status = element("span", order.status, "user-status-badge");
            status.classList.add(order.is_completed ? "is-active" : "is-inactive");
            statusCell.append(status);
            row.append(orderCell, element("td", order.customer_name || "Walk-in customer"),
                element("td", money(order.grand_total, order.currency)), statusCell);
            return row;
        });
        sales.content.querySelector("[data-recent-sales]").replaceChildren(...rows);
        sales.show();

        const completed = results.filter((order) => order.is_completed).length;
        const share = completed / results.length * 100;
        breakdown.content.querySelector("[data-sales-ring]").style.setProperty("--completed-share", `${share}%`);
        breakdown.content.querySelector("[data-sales-sample]").textContent = results.length;
        breakdown.content.querySelector("[data-sales-completed]").textContent = completed;
        breakdown.content.querySelector("[data-sales-incomplete]").textContent = results.length - completed;
        breakdown.show();
    } catch {
        metric("sales", "Unavailable");
        sales.message("Recent sales could not be loaded.");
        breakdown.message("The sales breakdown could not be loaded.");
    }
}

async function loadActivity(url) {
    const activity = panel("activity");
    const icon = activity.wrapper.querySelector("[data-activity-icon]");
    try {
        const { results } = await getJSON(url);
        if (!results.length) {
            activity.message("No recent activity.");
            return;
        }
        const entries = results.slice(0, 4).map((event) => {
            const item = element("li");
            const marker = element("span", null, "dashboard-activity-icon");
            marker.append(icon.content.cloneNode(true));
            const detail = element("div");
            detail.append(element("strong", `${event.action_label} · ${event.resource_label}`),
                element("span", [event.reference, event.actor].filter(Boolean).join(" · ")));
            const date = element("time", formatDay.format(new Date(event.occurred_at)));
            date.dateTime = event.occurred_at;
            item.append(marker, detail, date);
            return item;
        });
        activity.content.replaceChildren(...entries);
        activity.show();
    } catch {
        activity.message("Recent activity could not be loaded.");
    }
}

function renderCashFlow(cashFlow, rows, currency) {
    const get = (selector) => cashFlow.content.querySelector(selector);
    const chart = get("[data-cashflow-chart]");
    const maxAmount = Math.max(1, ...rows.flatMap(({ inflow, outflow }) => [inflow, outflow]));
    const magnitude = 10 ** Math.floor(Math.log10(maxAmount));
    const ceiling = Math.ceil(maxAmount / magnitude) * magnitude;
    const points = (field) => rows.map((row, index) => {
        const x = index * 550 / (rows.length - 1);
        const y = 139 - row[field] * 132 / ceiling;
        return `${x},${y}`;
    }).join(" L");
    const inflows = points("inflow");
    get("[data-chart-in]").setAttribute("d", `M${inflows}`);
    get("[data-chart-out]").setAttribute("d", `M${points("outflow")}`);
    get("[data-chart-fill]").setAttribute("d", `M${inflows} L550,140 L0,140 Z`);
    get("[data-chart-values]").replaceChildren(...[1, .75, .5, .25, 0].map((fraction) => {
        return element("span", formatCompact.format(ceiling * fraction));
    }));
    get("[data-chart-months]").replaceChildren(...rows.map(({ month }) => element("span", month)));
    get("[data-cashflow-total]").textContent = money(rows.reduce((net, row) => net + row.net, 0), currency);
    chart.setAttribute("aria-label", rows.map(({ month, inflow, outflow }) => {
        return `${month}: ${money(inflow, currency)} in, ${money(outflow, currency)} out`;
    }).join(". "));
}

async function loadCashFlow(url) {
    const cashFlow = panel("cashflow");
    const period = cashFlow.wrapper.querySelector("[data-cashflow-period]");
    const today = new Date(`${dashboard.dataset.today}T00:00:00Z`);
    const year = today.getUTCFullYear();
    const month = today.getUTCMonth();
    const toDate = (date) => date.toISOString().slice(0, 10);

    try {
        const months = await Promise.all(Array.from({ length: 6 }, async (_, index) => {
            const start = new Date(Date.UTC(year, month - 5 + index, 1));
            const end = index === 5 ? today : new Date(Date.UTC(year, month - 4 + index, 0));
            const request = new URL(url, window.location.href);
            request.searchParams.set("start_date", toDate(start));
            request.searchParams.set("end_date", toDate(end));
            const monthData = await getJSON(request);
            return {
                ...monthData,
                month: formatMonth.format(start),
                inflow: Number(monthData.inflow),
                outflow: Number(monthData.outflow),
                net: Number(monthData.net),
            };
        }));
        const current = months.at(-1);
        metric("cashflow", money(current.net, current.currency));
        const update = () => renderCashFlow(cashFlow, months.slice(-Number(period.value)), current.currency);
        update();
        const hero = dashboard.querySelector("[data-hero-cashflow]");
        const maxInflow = Math.max(1, ...months.map(({ inflow }) => inflow));
        const bars = months.map(({ inflow }) => {
            const bar = element("i");
            bar.style.height = `${inflow / maxInflow * 100}%`;
            return bar;
        });
        hero.querySelector("[data-hero-cashflow-bars]").replaceChildren(...bars);
        hero.hidden = false;
        period.addEventListener("change", update);
        period.hidden = false;
        cashFlow.show();
    } catch {
        metric("cashflow", "Unavailable");
        cashFlow.message("Cash flow could not be loaded.");
    }
}

if (dashboard) {
    const { daybookUrl, salesUrl, stockUrl, employeesUrl, auditUrl } = dashboard.dataset;
    if (daybookUrl) loadCashFlow(daybookUrl);
    if (salesUrl) loadSales(salesUrl);
    if (stockUrl) loadCount("stock", stockUrl);
    if (employeesUrl) loadCount("employees", employeesUrl);
    if (auditUrl) loadActivity(auditUrl);
}
