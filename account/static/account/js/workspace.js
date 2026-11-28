import { postJSON } from "../../js/api.js";

document.querySelectorAll("[data-avatar-image]").forEach((image) => {
    const fallback = () => image.remove();
    image.addEventListener("error", fallback, { once: true });
    if (image.complete && image.naturalWidth === 0) fallback();
});

const userMenu = document.querySelector("[data-user-menu]");
const menuToggle = userMenu?.querySelector("[data-user-menu-toggle]");
const dropdown = userMenu?.querySelector("[data-user-dropdown]");

function closeUserMenu(returnFocus = false) {
    if (!dropdown) return;
    dropdown.hidden = true;
    menuToggle.setAttribute("aria-expanded", "false");
    if (returnFocus) menuToggle.focus();
}

menuToggle?.addEventListener("click", () => {
    dropdown.hidden = !dropdown.hidden;
    menuToggle.setAttribute("aria-expanded", String(!dropdown.hidden));
});

menuToggle?.addEventListener("keydown", (event) => {
    if (event.key !== "ArrowDown") return;
    event.preventDefault();
    dropdown.hidden = false;
    menuToggle.setAttribute("aria-expanded", "true");
    dropdown.querySelector("a, button").focus();
});

document.addEventListener("click", (event) => {
    if (userMenu && !userMenu.contains(event.target)) closeUserMenu();
});

document.addEventListener("focusin", (event) => {
    if (userMenu && !userMenu.contains(event.target)) closeUserMenu();
});

const sidebar = document.querySelector("[data-sidebar]");
const sidebarToggle = document.querySelector("[data-sidebar-toggle]");
const sidebarClose = document.querySelector("[data-sidebar-close]");
const sidebarOverlay = document.querySelector("[data-sidebar-overlay]");
const desktop = window.matchMedia("(min-width: 960px)");

document.querySelectorAll("[data-nav-toggle]").forEach((toggle) => {
    const submenu = document.getElementById(toggle.getAttribute("aria-controls"));
    toggle.addEventListener("click", () => {
        submenu.hidden = !submenu.hidden;
        toggle.setAttribute("aria-expanded", String(!submenu.hidden));
    });
});

function setSidebar(open, returnFocus = false) {
    if (!sidebar) return;
    const isOpen = open && !desktop.matches;
    sidebar.classList.toggle("is-open", isOpen);
    sidebar.inert = !desktop.matches && !isOpen;
    sidebar.setAttribute("aria-hidden", String(!desktop.matches && !isOpen));
    sidebarOverlay.hidden = !isOpen;
    sidebarToggle.setAttribute("aria-expanded", String(isOpen));
    document.body.classList.toggle("navigation-open", isOpen);
    if (isOpen) sidebarClose.focus();
    else if (returnFocus) sidebarToggle.focus();
}

if (sidebar) {
    setSidebar(false);
    sidebarToggle.addEventListener("click", () => setSidebar(true));
    sidebarClose.addEventListener("click", () => setSidebar(false, true));
    sidebarOverlay.addEventListener("click", () => setSidebar(false, true));
    desktop.addEventListener("change", () => setSidebar(false));
}

const moduleNavigation = document.querySelector("[data-module-navigation]");
if (moduleNavigation && sidebar) {
    const current = [...sidebar.querySelectorAll("a[data-nav-list]")].find((link) => {
        return link.pathname === window.location.pathname;
    });
    const submenu = current?.closest(".sidebar-submenu");
    const siblings = submenu?.querySelectorAll(":scope > a[data-nav-list]");
    if (siblings?.length > 1) {
        const links = [...siblings].map((item) => {
            const link = document.createElement("a");
            link.href = item.href;
            link.textContent = item.textContent.trim();
            if (item === current) link.setAttribute("aria-current", "page");
            return link;
        });
        moduleNavigation.replaceChildren(...links);
        moduleNavigation.hidden = false;
    }
}

document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
        if (dropdown && !dropdown.hidden) closeUserMenu(true);
        if (sidebar?.classList.contains("is-open")) setSidebar(false, true);
    }
    if (event.key === "Tab" && sidebar?.classList.contains("is-open")) {
        const items = [...sidebar.querySelectorAll('a[href], button:not([disabled])')]
            .filter((element) => element.getClientRects().length);
        const first = items[0];
        const last = items[items.length - 1];
        if (event.shiftKey && document.activeElement === first) {
            event.preventDefault();
            last.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
            event.preventDefault();
            first.focus();
        }
    }
});

const search = document.getElementById("workspace-search");
const searchToggle = document.querySelector("[data-workspace-search-open]");

if (search && searchToggle) {
    const input = search.querySelector('input[type="search"]');
    const results = search.querySelector("[data-workspace-search-results]");
    const empty = search.querySelector("[data-workspace-search-empty]");
    const pages = [...sidebar.querySelectorAll("a[data-nav-item]")].map((link) => {
        const group = link.closest(".sidebar-submenu");
        const label = link.textContent.trim();
        const groupName = group?.previousElementSibling?.querySelector("span")?.textContent.trim() || "Workspace";
        return { label, groupName, href: link.href };
    });

    function renderSearch() {
        const terms = input.value.trim().toLowerCase().split(/\s+/);
        const matches = pages.filter(({ label, groupName }) => {
            const text = `${label} ${groupName}`.toLowerCase();
            return terms.every((term) => text.includes(term));
        });
        const items = matches.slice(0, input.value.trim() ? 30 : 8).map(({ label, groupName, href }) => {
            const item = document.createElement("li");
            const link = document.createElement("a");
            const title = document.createElement("strong");
            const context = document.createElement("small");
            link.href = href;
            title.textContent = label;
            context.textContent = groupName;
            link.append(title, context);
            item.append(link);
            return item;
        });
        results.replaceChildren(...items);
        empty.hidden = Boolean(items.length);
    }

    function openSearch() {
        if (document.querySelector("dialog[open]")) return;
        setSidebar(false);
        closeUserMenu();
        input.value = "";
        renderSearch();
        search.showModal();
        input.focus();
    }

    searchToggle.addEventListener("click", openSearch);
    input.addEventListener("input", renderSearch);
    input.addEventListener("keydown", (event) => {
        const first = results.querySelector("a");
        if (event.key === "ArrowDown" && first) {
            event.preventDefault();
            first.focus();
        } else if (event.key === "Enter" && first && input.value.trim()) {
            first.click();
        }
    });
    search.addEventListener("click", (event) => {
        if (event.target !== search) return;
        const { top, bottom, left, right } = search.getBoundingClientRect();
        if (event.clientY < top || event.clientY > bottom || event.clientX < left || event.clientX > right) search.close();
    });
    search.addEventListener("close", () => searchToggle.focus());
    document.addEventListener("keydown", (event) => {
        if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k" && !document.querySelector("dialog[open]")) {
            event.preventDefault();
            openSearch();
        }
    });
}

document.querySelectorAll("[data-logout-form]").forEach((form) => {
    form.addEventListener("submit", async (event) => {
        event.preventDefault();
        if (form.dataset.busy === "true") return;
        const button = form.querySelector("[data-logout-button]");
        const label = form.querySelector("[data-logout-label]");
        const errorBox = form.querySelector("[data-logout-error]");
        const csrfToken = form.elements.namedItem("csrfmiddlewaretoken").value;
        form.dataset.busy = "true";
        form.setAttribute("aria-busy", "true");
        button.disabled = true;
        label.textContent = "Logging out…";
        errorBox.hidden = true;
        try {
            await postJSON(form.action, {}, csrfToken);
            window.location.replace(form.dataset.redirectUrl);
        } catch (error) {
            errorBox.textContent = error.message || "Could not log out. Please try again.";
            errorBox.hidden = false;
        } finally {
            button.disabled = false;
            form.dataset.busy = "false";
            form.setAttribute("aria-busy", "false");
            label.textContent = "Logout";
        }
    });
});

// Recheck server authentication when restoring a page from browser history.
window.addEventListener("pageshow", (event) => {
    if (event.persisted) window.location.reload();
});
