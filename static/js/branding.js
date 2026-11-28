function showLogo(brand, url) {
    const image = brand.querySelector("[data-erp-logo-image]");
    if (url) image.setAttribute("src", url);
    else image.removeAttribute("src");
    image.hidden = !url;
    brand.querySelector("[data-erp-logo-default]").hidden = Boolean(url);
}

export function showERPLogo(url, sidebarUrl) {
    document.querySelectorAll("[data-erp-brand]").forEach(brand => {
        showLogo(brand, brand.hasAttribute("data-erp-logo-sidebar") ? sidebarUrl : url);
    });
}

document.addEventListener("error", event => {
    if (event.target.matches?.("[data-erp-logo-image]")) {
        showLogo(event.target.closest("[data-erp-brand]"), null);
    }
}, true);

document.querySelectorAll("[data-erp-logo-image][src]").forEach(image => {
    if (image.complete && !image.naturalWidth) showLogo(image.closest("[data-erp-brand]"), null);
});
