import { initializeRecordDetail } from "../../js/record_detail.js";

const panel = document.querySelector("[data-record-detail]");
if (panel) {
    const edit = panel.querySelector("[data-expense-edit]");
    initializeRecordDetail(panel, record => {
        if (edit) edit.hidden = record.is_completed !== false;
    });
}
