import { getJSON } from "../../js/api.js";

const directory = document.querySelector("[data-user-list]");

if (directory) {
    const searchForm = directory.querySelector("[data-user-search]");
    const rows = directory.querySelector("[data-user-rows]");
    const table = directory.querySelector("[data-list-table]");
    const status = directory.querySelector("[data-list-status]");
    const errorBox = directory.querySelector("[data-list-error]");
    const empty = directory.querySelector("[data-list-empty]");
    const pagination = directory.querySelector("[data-list-pagination]");
    const previous = directory.querySelector("[data-list-previous]");
    const next = directory.querySelector("[data-list-next]");
    const count = directory.querySelector("[data-user-count]");
    let currentPage = 1;
    let currentQuery = "";
    let requestVersion = 0;
    let hasNext = false;
    let hasPrevious = false;

    function cell(text, className = "") {
        const element = document.createElement("td");
        element.textContent = text || "\u2014";
        element.className = className;
        return element;
    }

    function userRow(user) {
        const row = document.createElement("tr");
        const name = cell("");
        const identity = document.createElement("div");
        identity.className = "user-table-identity";
        const avatar = document.createElement("span");
        avatar.className = "user-avatar";
        avatar.setAttribute("aria-hidden", "true");
        avatar.textContent = (user.full_name || user.email || "U").trim().split(/\s+/).slice(0, 2).map((word) => word[0]).join("").toUpperCase() || "U";
        const label = document.createElement("span");
        label.textContent = user.full_name || "Unnamed user";
        name.textContent = "";
        identity.append(avatar, label);
        name.append(identity);
        const state = cell("");
        state.textContent = "";
        const badge = document.createElement("span");
        badge.className = `user-status-badge ${user.is_active ? "is-active" : "is-inactive"}`;
        badge.textContent = user.is_active ? "Active" : "Inactive";
        state.append(badge);
        const date = new Date(user.date_joined);
        const joined = Number.isNaN(date.getTime()) ? "" : date.toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
        row.append(name, cell(user.email), cell(user.phone_number), cell(user.designation), state, cell(joined, "user-joined"));
        const actions = cell("");
        actions.textContent = "";
        const controls = document.getElementById("user-actions-template").content.cloneNode(true);
        const assign = controls.querySelector("[data-assign-role]");
        assign.href = directory.dataset.assignRoleUrl.replace("/0/", `/${user.id}/`);
        assign.setAttribute("aria-label", `Assign role to ${user.full_name || user.email || "user"}`);
        actions.append(controls);
        row.append(actions);
        return row;
    }

    async function loadUsers(page = 1) {
        const version = ++requestVersion;
        currentPage = page;
        const url = new URL(directory.dataset.apiUrl, window.location.origin);
        url.searchParams.set("page", String(page));
        if (currentQuery) url.searchParams.set("q", currentQuery);
        directory.setAttribute("aria-busy", "true");
        status.textContent = "Loading users...";
        status.classList.remove("sr-only");
        status.hidden = false;
        errorBox.hidden = true;
        empty.hidden = true;
        table.hidden = true;
        pagination.hidden = true;
        count.hidden = true;
        previous.disabled = true;
        next.disabled = true;

        try {
            const data = await getJSON(url.href);
            if (version !== requestVersion) return;
            rows.replaceChildren(...data.results.map(userRow));
            count.textContent = String(data.count);
            count.hidden = false;
            table.hidden = data.results.length === 0;
            empty.hidden = data.results.length !== 0;
            status.textContent = `${data.count} ${data.count === 1 ? "user" : "users"} found.`;
            status.classList.add("sr-only");
            hasPrevious = Boolean(data.previous);
            hasNext = Boolean(data.next);
            previous.disabled = !hasPrevious;
            next.disabled = !hasNext;
            pagination.hidden = data.results.length === 0;
            // The endpoint uses a fixed page size; derive the final range from returned rows.
            const start = (page - 1) * 20 + 1;
            directory.querySelector("[data-list-range]").textContent = `Showing ${start}\u2013${start + data.results.length - 1} of ${data.count}`;
        } catch (error) {
            if (version !== requestVersion) return;
            if (error.status === 401 || error.status === 403) {
                window.location.replace(directory.dataset.loginUrl);
                return;
            }
            status.hidden = true;
            directory.querySelector("[data-list-error-message]").textContent = error.message || "Could not load users. Please try again.";
            errorBox.hidden = false;
        } finally {
            if (version === requestVersion) directory.setAttribute("aria-busy", "false");
        }
    }

    searchForm.addEventListener("submit", (event) => {
        event.preventDefault();
        currentQuery = searchForm.elements.namedItem("q").value.trim();
        status.classList.remove("sr-only");
        loadUsers(1);
    });
    previous.addEventListener("click", () => { if (hasPrevious) loadUsers(currentPage - 1); });
    next.addEventListener("click", () => { if (hasNext) loadUsers(currentPage + 1); });
    directory.querySelector("[data-list-retry]").addEventListener("click", () => loadUsers(currentPage));
    loadUsers();
}
