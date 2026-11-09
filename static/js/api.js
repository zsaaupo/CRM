export class APIError extends Error {
    constructor(message, status = 0, errors = {}) {
        super(message);
        this.name = "APIError";
        this.status = status;
        this.errors = errors;
    }
}

export async function postJSON(url, data, csrfToken) {
    return requestJSON(url, {
        method: "POST",
        headers: {
            "Content-Type": "application/json",
            "X-CSRFToken": csrfToken,
        },
        body: JSON.stringify(data),
    });
}

export async function getJSON(url) {
    return requestJSON(url, { method: "GET" });
}

export async function patchJSON(url, data, csrfToken) {
    return requestJSON(url, {
        method: "PATCH",
        headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken },
        body: JSON.stringify(data),
    });
}

export async function putJSON(url, data, csrfToken) {
    return requestJSON(url, {
        method: "PUT",
        headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken },
        body: JSON.stringify(data),
    });
}

export async function deleteJSON(url, csrfToken) {
    return requestJSON(url, {
        method: "DELETE",
        headers: { "X-CSRFToken": csrfToken },
    });
}

async function requestJSON(url, options) {
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), 20000);

    try {
        const response = await fetch(url, {
            ...options,
            mode: "same-origin",
            credentials: "same-origin",
            headers: {
                "Accept": "application/json",
                ...options.headers,
            },
            signal: controller.signal,
        });

        if (response.status === 204 && response.ok) return null;

        let result;
        try {
            result = await response.json();
        } catch {
            const message = response.status === 403
                ? "Your session has changed. Refresh this page and try again."
                : "The server returned an unexpected response. Please try again.";
            throw new APIError(message, response.status);
        }

        if (!response.ok) {
            const errors = result && typeof result === "object" ? result : {};
            const message = typeof errors.detail === "string"
                ? errors.detail
                : "We couldn’t complete your request. Please try again.";
            throw new APIError(message, response.status, errors);
        }

        return result;
    } catch (error) {
        if (error instanceof APIError) throw error;
        if (error.name === "AbortError") {
            throw new APIError("The request took too long. Please check before trying again.");
        }
        throw new APIError("Unable to connect. Check your connection and try again.");
    } finally {
        window.clearTimeout(timeout);
    }
}
