const form = document.getElementById("target-form");
const calculate = document.getElementById("calculate");
const results = document.getElementById("results");
const empty = document.getElementById("empty-result");
const error = document.getElementById("error");

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!form.reportValidity()) return;
  const f = new FormData(form);
  const payload = Object.fromEntries(f.entries());
  payload.symbol = payload.symbol.trim().toUpperCase();
  payload.quantity = Number(payload.quantity);
  payload.multiplier = Number(payload.multiplier);
  calculate.disabled = true;
  calculate.firstChild.textContent = "CALCULATING… ";
  error.hidden = true;
  try {
    const response = await fetch("/api/target", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(payload)
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Target calculation failed");
    document.getElementById("target-bid").textContent = "$" + data.candidate_bid_target;
    document.getElementById("initial-debit").textContent = "$" + data.initial_debit_usd;
    document.getElementById("net-pnl").textContent = "$" + data.estimated_net_pnl_usd;
    document.getElementById("raw-bid").textContent = "$" + data.minimum_theoretical_bid;
    document.getElementById("quantity").textContent = data.quantity;
    empty.hidden = true;
    results.hidden = false;
  } catch (ex) {
    results.hidden = true;
    error.textContent = ex.message || "The calculation could not be completed";
    error.hidden = false;
  } finally {
    calculate.disabled = false;
    calculate.firstChild.textContent = "CALCULATE TARGET ";
  }
});
