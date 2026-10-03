async function loadKpis() {
    const res = await fetch("/api/dashboard/kpis");
    if (!res.ok) return;
    const data = await res.json();
    const keys = [
        ["Vehicles", data.vehicles],
        ["Customers", data.customers],
        ["Fuel Spend", `${data.currency} ${data.fuel_spend_kes}`],
        ["Payments", `${data.currency} ${data.payments_kes}`]
    ];
    document.getElementById("kpiCards").innerHTML = keys.map(([k, v]) => `
        <div class="col-md-3"><div class="card"><div class="card-body">
            <div class="text-muted small">${k}</div><h5>${v}</h5>
        </div></div></div>
    `).join("");
}

document.getElementById("themeToggle").addEventListener("click", () => {
    const html = document.documentElement;
    html.setAttribute("data-bs-theme", html.getAttribute("data-bs-theme") === "dark" ? "light" : "dark");
});

loadKpis();
