import { getJSON, postJSON } from "../../js/api.js";

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

// Topbar controls: Quick Create, Notifications, Language, Theme, Fullscreen
const topbar = document.querySelector("[data-topbar]");
const csrfTokenGlobal = topbar?.dataset.csrf || document.querySelector("input[name=csrfmiddlewaretoken]")?.value || "";

const qcRoot = document.querySelector("[data-quick-create]");
const qcToggle = qcRoot?.querySelector("[data-quick-create-toggle]");
const qcMenu = qcRoot?.querySelector("[data-quick-create-menu]");
qcToggle?.addEventListener("click", () => {
    qcMenu.hidden = !qcMenu.hidden;
    qcToggle.setAttribute("aria-expanded", String(!qcMenu.hidden));
});

const notifRoot = document.querySelector("[data-notifications]");
const notifToggle = notifRoot?.querySelector("[data-notifications-toggle]");
const notifDropdown = notifRoot?.querySelector("[data-notifications-dropdown]");
const notifList = notifRoot?.querySelector("[data-notifications-list]");
const notifBadge = notifRoot?.querySelector("[data-notifications-badge]");

async function loadNotifications() {
    if (!notifToggle || !notifList) return;
    try {
        const data = await getJSON(notifToggle.dataset.url);
        if (notifBadge) notifBadge.textContent = String(data.unread_count ?? 0);
        if (!data.results?.length) {
            notifList.innerHTML = '<p class="text-stone-400 py-2">No notifications.</p>';
            return;
        }
        notifList.innerHTML = data.results.map((n) => `
            <div class="border-b border-stone-100 py-2 flex items-start justify-between gap-2">
                <div>
                    <div class="font-medium text-stone-800">${escapeHtml(n.title)}</div>
                    <div class="text-stone-500">${escapeHtml(n.body)}</div>
                </div>
                ${n.read_at ? '<span class="text-[10px] text-stone-400">Read</span>' : `<button type="button" class="text-[11px] text-emerald-700 font-semibold" data-read-id="${n.id}">Mark read</button>`}
            </div>
        `).join("");
        notifList.querySelectorAll("[data-read-id]").forEach((btn) => {
            btn.addEventListener("click", async () => {
                await postJSON(`/settings/api/notifications/${btn.dataset.readId}/read/`, {}, csrfTokenGlobal);
                loadNotifications();
            });
        });
    } catch {
        notifList.textContent = "Unable to load notifications.";
    }
}

notifToggle?.addEventListener("click", () => {
    notifDropdown.hidden = !notifDropdown.hidden;
    notifToggle.setAttribute("aria-expanded", String(!notifDropdown.hidden));
    if (!notifDropdown.hidden) loadNotifications();
});

document.addEventListener("click", (e) => {
    if (qcRoot && !qcRoot.contains(e.target) && qcMenu) qcMenu.hidden = true;
    if (notifRoot && !notifRoot.contains(e.target) && notifDropdown) notifDropdown.hidden = true;
});

document.querySelector("[data-theme-toggle]")?.addEventListener("click", () => {
    const isDark = document.documentElement.classList.toggle("theme-dark");
    localStorage.setItem("em_theme", isDark ? "dark" : "light");
});

document.querySelector("[data-fullscreen-toggle]")?.addEventListener("click", () => {
    if (!document.fullscreenElement) document.documentElement.requestFullscreen?.();
    else document.exitFullscreen?.();
});

document.querySelector("[data-language-switch]")?.addEventListener("change", async (e) => {
    const select = e.currentTarget;
    try {
        await postJSON(select.dataset.url, { code: select.value }, csrfTokenGlobal);
        window.location.reload();
    } catch {
        // Ignore if session expired
    }
});

function escapeHtml(text) {
    return String(text ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
