import { deleteJSON, getJSON, postJSON } from "../../js/api.js";

const board = document.querySelector("[data-notice-board]");
const apiUrl = board.dataset.apiUrl;
const cards = board.querySelector("[data-notice-feed]");
const template = board.querySelector("[data-notice-card]");
const feedStatus = board.querySelector("[data-notice-feed-status]");
const removalStatus = board.querySelector("[data-notice-removal-status]");
const retryFeed = board.querySelector("[data-notice-feed-retry]");
const pagination = board.querySelector("[data-notice-pagination]");
const previous = pagination.querySelector("[data-notice-previous]");
const next = pagination.querySelector("[data-notice-next]");
const csrf = board.querySelector("[name=csrfmiddlewaretoken]")?.value;
let feedRequest = 0;
let currentPage = { url: apiUrl, previous: null, next: null };

function report(element, message, tone = "info") {
    element.textContent = message;
    element.dataset.tone = tone;
    element.hidden = !message;
}

function errorMessage(error) {
    const messages = Object.values(error.errors ?? {}).flat().filter(value => typeof value === "string");
    return messages.join(" ") || error.message;
}

function noticeCard(notice) {
    const card = template.content.cloneNode(true);
    card.querySelector("[data-notice-title]").textContent = notice.title;
    card.querySelector("[data-notice-audience]").textContent = notice.audience;
    card.querySelector("[data-notice-message]").textContent = notice.message;
    const time = card.querySelector("time");
    time.dateTime = notice.created_at;
    time.textContent = notice.created_at_display;

    const remove = card.querySelector("[data-notice-remove]");
    if (remove) {
        remove.setAttribute("aria-label", `Remove ${notice.title}`);
        remove.dataset.noticeRemove = board.dataset.removeUrl.replace(/\/0(?=\/)/, `/${notice.id}`);
    }
    return card;
}

async function loadFeed(url = apiUrl, fallback = null) {
    const request = ++feedRequest;
    pagination.hidden = true;
    retryFeed.hidden = true;
    cards.replaceChildren();
    report(feedStatus, "Loading notices…");
    try {
        let data;
        try {
            data = await getJSON(url);
        } catch (error) {
            if (request !== feedRequest) return;
            if (error.status !== 404 || !fallback) throw error;
            url = fallback;
            data = await getJSON(url);
        }
        if (request !== feedRequest) return;
        currentPage = { url, previous: data.previous, next: data.next };
        cards.replaceChildren(...data.results.map(noticeCard));
        previous.disabled = !data.previous;
        next.disabled = !data.next;
        pagination.hidden = !data.previous && !data.next;
        report(feedStatus, data.results.length ? "" : "No notices to show.");
    } catch (error) {
        if (request !== feedRequest) return;
        report(feedStatus, errorMessage(error), "error");
        retryFeed.onclick = () => loadFeed(url, fallback);
        retryFeed.hidden = false;
    }
}

previous.addEventListener("click", () => loadFeed(currentPage.previous, apiUrl));
next.addEventListener("click", () => loadFeed(currentPage.next, currentPage.url));

cards.addEventListener("click", async event => {
    const button = event.target.closest("[data-notice-remove]");
    if (!button || button.disabled || !window.confirm("Remove this notice?")) return;
    button.disabled = true;
    report(removalStatus, "Removing notice…");
    try {
        await deleteJSON(button.dataset.noticeRemove, csrf);
        report(removalStatus, "Notice removed.", "success");
        await loadFeed(currentPage.url, currentPage.previous || apiUrl);
    } catch (error) {
        report(removalStatus, errorMessage(error), "error");
        if (error.status === 404) await loadFeed(currentPage.url, currentPage.previous || apiUrl);
    } finally {
        button.disabled = false;
    }
});

const form = board.querySelector("[data-notice-form]");
if (form) {
    const formStatus = form.querySelector("[data-notice-form-status]");
    const retryOptions = form.querySelector("[data-notice-options-retry]");
    const publish = form.querySelector("[type=submit]");
    const audience = form.elements.namedItem("department_id");

    async function loadAudiences() {
        retryOptions.hidden = true;
        report(formStatus, "Loading audiences…");
        try {
            const data = await getJSON(form.dataset.optionsUrl);
            for (const department of data.departments) {
                audience.add(new Option(department.label, department.value));
            }
            audience.disabled = false;
            publish.disabled = false;
            report(formStatus, "");
        } catch (error) {
            report(formStatus, errorMessage(error), "error");
            retryOptions.hidden = false;
        }
    }

    retryOptions.addEventListener("click", loadAudiences);
    form.addEventListener("submit", async event => {
        event.preventDefault();
        if (publish.disabled) return;
        const title = form.elements.namedItem("title").value.trim();
        const message = form.elements.namedItem("message").value.trim();
        if (!title || !message) {
            report(formStatus, "Enter a title and message.", "error");
            return;
        }
        publish.disabled = true;
        report(formStatus, "Publishing notice…");
        try {
            await postJSON(form.action, { title, message, department_id: audience.value || null }, csrf);
            form.reset();
            report(formStatus, "Notice published.", "success");
            await loadFeed();
        } catch (error) {
            report(formStatus, errorMessage(error), "error");
        } finally {
            publish.disabled = false;
        }
    });

    loadAudiences();
}

loadFeed();
