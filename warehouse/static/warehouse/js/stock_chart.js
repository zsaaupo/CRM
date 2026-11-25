import { getJSON } from "../../js/api.js";

const page = document.querySelector("[data-stock-chart]");
if (page) {
    const warehouse = page.querySelector("#stock-chart-warehouse");
    const status = page.querySelector("[data-chart-status]");
    const scope = page.querySelector("[data-chart-scope]");
    const warning = page.querySelector("[data-chart-warning]");
    const chart = page.querySelector("[data-stock-chart-svg]");
    const plot = page.querySelector("[data-chart-plot]");
    const legend = page.querySelector("[data-stock-chart-legend]");
    const empty = page.querySelector("[data-chart-empty]");
    const retry = page.querySelector("[data-chart-retry]");
    const totalItems = page.querySelector("[data-total-items]");
    const totalQuantity = page.querySelector("[data-total-quantity]");
    const quantityUnits = page.querySelector("[data-quantity-units]");
    const metrics = [
        { key: "stock_value_by_price", label: "Stock Value by Price", color: "#07838b" },
        { key: "stock_value_by_cost", label: "Stock Value by Cost", color: "#ff8753" },
        { key: "estimated_profit", label: "Estimate Profit", color: "#89918b" },
    ];
    let responseData = null;
    let requestVersion = 0;

    function formatMoney(value) {
        return new Intl.NumberFormat(undefined, {
            style: "currency",
            currency: page.dataset.currency,
            maximumFractionDigits: 2,
        }).format(Number(value));
    }

    function svgElement(name, attributes) {
        const element = document.createElementNS("http://www.w3.org/2000/svg", name);
        for (const [key, value] of Object.entries(attributes)) element.setAttribute(key, value);
        return element;
    }

    function renderLegend(values, total) {
        legend.replaceChildren();
        for (const metric of metrics) {
            const value = Number(values[metric.key]);
            const entry = document.createElement("li");
            entry.className = "flex items-center gap-2 text-sm text-stone-600";
            const swatch = document.createElement("span");
            swatch.className = "h-3 w-8 shrink-0";
            swatch.style.backgroundColor = metric.color;
            const label = document.createElement("span");
            label.textContent = `${metric.label} · ${formatMoney(value)}`;
            if (value > 0 && total > 0) {
                label.textContent += ` (${(value / total * 100).toFixed(1)}%)`;
            } else if (metric.key === "estimated_profit") {
                label.textContent += " (no positive slice)";
            }
            entry.append(swatch, label);
            legend.append(entry);
        }
    }

    function renderPie(slices, total) {
        chart.replaceChildren();
        chart.setAttribute("viewBox", "0 0 320 320");
        if (slices.length === 1) {
            chart.append(svgElement("circle", {
                cx: "160", cy: "160", r: "145", fill: slices[0].color,
            }));
            return;
        }
        let angle = -Math.PI / 2;
        for (const slice of slices) {
            const nextAngle = angle + slice.value / total * Math.PI * 2;
            const x1 = 160 + 145 * Math.cos(angle);
            const y1 = 160 + 145 * Math.sin(angle);
            const x2 = 160 + 145 * Math.cos(nextAngle);
            const y2 = 160 + 145 * Math.sin(nextAngle);
            const path = svgElement("path", {
                d: `M 160 160 L ${x1} ${y1} A 145 145 0 ${nextAngle - angle > Math.PI ? 1 : 0} 1 ${x2} ${y2} Z`,
                fill: slice.color,
                stroke: "#ffffff",
                "stroke-width": "1",
            });
            const title = svgElement("title", {});
            title.textContent = `${slice.label}: ${formatMoney(slice.value)}`;
            path.append(title);
            chart.append(path);
            angle = nextAngle;
        }
    }

    function render() {
        if (!responseData) return;
        const values = responseData.totals;
        const slices = metrics
            .map(metric => ({ ...metric, value: Number(values[metric.key]) }))
            .filter(metric => metric.value > 0);
        const pieTotal = slices.reduce((sum, slice) => sum + slice.value, 0);
        const warnings = [];
        if (responseData.missing_exchange_rate_currencies.length) {
            warnings.push(`Stock values in ${responseData.missing_exchange_rate_currencies.join(", ")} were excluded because their exchange rates are missing.`);
        }
        if (Number(values.estimated_profit) <= 0) {
            warnings.push("Estimated profit is not positive, so it is excluded from the pie.");
        }
        warning.textContent = warnings.join(" ");
        warning.hidden = warnings.length === 0;
        scope.textContent = `Showing ${responseData.warehouse_label} · amounts in ${responseData.currency}`;
        totalItems.textContent = new Intl.NumberFormat().format(responseData.total_items);
        totalQuantity.textContent = new Intl.NumberFormat(undefined, {
            minimumFractionDigits: 2,
            maximumFractionDigits: 6,
        }).format(Number(responseData.total_quantity));
        quantityUnits.textContent = responseData.quantity_unit_count > 1
            ? `Combined across ${responseData.quantity_unit_count} unit types`
            : responseData.quantity_unit_count === 1 ? "1 unit type" : "No stock units";
        renderLegend(values, pieTotal);
        plot.hidden = slices.length === 0;
        empty.hidden = slices.length > 0;
        if (slices.length) renderPie(slices, pieTotal);
        status.textContent = slices.length
            ? `${responseData.warehouse_label} · ${slices.length} stock value measures`
            : "No positive stock values to chart for this selection.";
    }

    async function load() {
        const current = ++requestVersion;
        responseData = null;
        totalItems.textContent = "—";
        totalQuantity.textContent = "—";
        quantityUnits.textContent = "";
        plot.hidden = true;
        chart.replaceChildren();
        empty.hidden = true;
        legend.replaceChildren();
        scope.textContent = "";
        warning.hidden = true;
        retry.hidden = true;
        status.textContent = "Loading stock values...";
        const url = new URL(page.dataset.apiUrl, location.origin);
        if (warehouse?.value) url.searchParams.set("warehouse", warehouse.value);
        try {
            const data = await getJSON(url);
            if (current !== requestVersion) return;
            responseData = data;
            render();
        } catch (error) {
            if (current !== requestVersion) return;
            responseData = null;
            status.textContent = error.message || "Could not load stock values.";
            retry.hidden = false;
        }
    }

    warehouse?.addEventListener("change", load);
    retry.addEventListener("click", load);
    load();
}
