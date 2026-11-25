import { getJSON, postJSON } from "../../js/api.js";
import { initializeRecordDetail } from "../../js/record_detail.js";

const panel = document.querySelector("[data-record-detail]");
if (panel) {
    const csrf = panel.dataset.csrf;
    async function loadFinancePanels() {
        if (panel.dataset.dueUrl) {
            try {
                const s = await getJSON(panel.dataset.dueUrl);
                document.getElementById("party-due-box").textContent =
                    `Receivables due: ${s.receivables?.total_due ?? '0.0000'} | Payables due: ${s.payables?.total_due ?? '0.0000'}`;
            } catch {}
        }
        if (panel.dataset.depositsUrl) {
            try {
                const d = await getJSON(panel.dataset.depositsUrl);
                document.getElementById("deposit-list").innerHTML =
                    (d.results || []).slice(0, 5).map((r) => `<div>${r.kind}: ${r.amount} (${r.note || r.source})</div>`).join("") || "No deposits.";
            } catch {}
        }
        if (panel.dataset.pointsUrl) {
            try {
                const p = await getJSON(panel.dataset.pointsUrl);
                document.getElementById("points-list").innerHTML =
                    `<div><strong>Balance: ${p.reward_points} pts</strong></div>` +
                    ((p.results || []).slice(0, 5).map((r) => `<div>${r.points_delta > 0 ? '+' : ''}${r.points_delta} (${r.reason})</div>`).join("") || "No points history.");
            } catch {}
        }
    }

    initializeRecordDetail(panel, loadFinancePanels);

    document.getElementById("clear-due-form")?.addEventListener("submit", async (e) => {
        e.preventDefault();
        const fd = new FormData(e.currentTarget);
        const msg = document.getElementById("clear-due-msg");
        try {
            const payload = {
                direction: fd.get("direction"),
                amount: fd.get("amount"),
                tenders: [{ method: "cash", account: Number(fd.get("account")), amount: fd.get("amount") }],
                request_key: crypto.randomUUID(),
            };
            await postJSON(panel.dataset.clearConfirmUrl, payload, csrf);
            msg.textContent = "Due cleared.";
            loadFinancePanels();
        } catch (err) {
            msg.textContent = err.message || "Clear due failed.";
        }
    });

    document.getElementById("deposit-form")?.addEventListener("submit", async (e) => {
        e.preventDefault();
        const fd = new FormData(e.currentTarget);
        await postJSON(panel.dataset.depositsUrl, {
            account: Number(fd.get("account")), amount: fd.get("amount"), request_key: crypto.randomUUID(),
        }, csrf);
        e.currentTarget.reset();
        loadFinancePanels();
    });

    document.getElementById("create-login-btn")?.addEventListener("click", async () => {
        try {
            const res = await postJSON(panel.dataset.createLoginUrl, {}, csrf);
            document.getElementById("create-login-msg").textContent = `Portal user: ${res.username} (created=${res.created})`;
        } catch (err) {
            document.getElementById("create-login-msg").textContent = err.message;
        }
    });
}
