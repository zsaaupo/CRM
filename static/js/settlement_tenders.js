const form = document.querySelector("[data-settlement-form]");
if (form) {
    const list = form.querySelector("[data-tender-list]");
    const add = form.querySelector("[data-add-tender]");
    const prototype = list.firstElementChild.cloneNode(true);
    add.hidden = false;
    function update() {
        for (const button of list.querySelectorAll("[data-remove-tender]")) {
            button.hidden = list.childElementCount === 1;
        }
        add.disabled = list.childElementCount >= 100;
    }
    add.addEventListener("click", () => {
        const row = prototype.cloneNode(true);
        list.append(row);
        update();
        row.querySelector("input").focus();
    });
    list.addEventListener("click", event => {
        const button = event.target.closest("[data-remove-tender]");
        if (button && list.childElementCount > 1) {
            button.closest("[data-tender]").remove();
            update();
            add.focus();
        }
    });
}
