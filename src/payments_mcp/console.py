"""Static approvals console (served same-origin at /console) for the human-in-the-loop mandate flow."""

APPROVALS_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Payments — Approvals</title>
<style>
  body { font: 15px/1.5 system-ui, sans-serif; max-width: 820px; margin: 2rem auto; padding: 0 1rem; color: #1a1a1a; }
  h1 { font-size: 1.3rem; } h2 { font-size: 1rem; margin-top: 2rem; }
  table { width: 100%; border-collapse: collapse; margin-top: .5rem; }
  th, td { text-align: left; padding: .5rem .6rem; border-bottom: 1px solid #eee; }
  button { cursor: pointer; border: 1px solid #ccc; border-radius: 6px; padding: .35rem .7rem; background: #fff; }
  button.approve { border-color: #1a7f37; color: #1a7f37; } button.reject { border-color: #b42318; color: #b42318; }
  form { display: flex; gap: .5rem; flex-wrap: wrap; margin-top: .5rem; }
  input { padding: .4rem; border: 1px solid #ccc; border-radius: 6px; }
  .mandate { font-family: ui-monospace, monospace; font-size: 12px; word-break: break-all; background: #f6f8fa; padding: .6rem; border-radius: 6px; margin-top: .5rem; }
  .empty { color: #777; }
</style>
</head>
<body>
<h1>Payments — Approval console</h1>
<p>Human-in-the-loop: approve a pending payment to mint a signed mandate the agent can then execute.</p>

<h2>New approval request</h2>
<form id="new">
  <input name="payer" placeholder="payer (acct-A)" value="acct-A" required>
  <input name="payee" placeholder="payee (acct-B)" value="acct-B" required>
  <input name="amount_minor" type="number" min="1" placeholder="amount (minor)" value="5000" required>
  <input name="currency" placeholder="INR" value="INR" required>
  <button type="submit">Create</button>
</form>

<h2>Pending</h2>
<table><thead><tr><th>id</th><th>payer</th><th>payee</th><th>amount</th><th>cur</th><th></th></tr></thead>
<tbody id="rows"><tr><td class="empty" colspan="6">loading…</td></tr></tbody></table>
<div id="mandate"></div>

<script>
async function load() {
  const rows = document.getElementById('rows');
  const res = await fetch('/approvals');
  const items = await res.json();
  if (!items.length) { rows.innerHTML = '<tr><td class="empty" colspan="6">no pending approvals</td></tr>'; return; }
  rows.innerHTML = '';
  for (const a of items) {
    const tr = document.createElement('tr');
    tr.innerHTML = `<td>${a.approval_id}</td><td>${a.payer}</td><td>${a.payee}</td>` +
      `<td>${a.amount_minor}</td><td>${a.currency}</td>` +
      `<td><button class="approve" data-id="${a.approval_id}">Approve</button> ` +
      `<button class="reject" data-id="${a.approval_id}">Reject</button></td>`;
    rows.appendChild(tr);
  }
}
async function approve(id) {
  const res = await fetch(`/approvals/${id}/approve`, { method: 'POST' });
  const out = await res.json();
  const box = document.getElementById('mandate');
  box.innerHTML = res.ok
    ? `<p>Approved <b>${id}</b> — signed mandate:</p><div class="mandate">${out.mandate}</div>`
    : `<p style="color:#b42318">Error: ${out.error || res.status}</p>`;
  load();
}
async function reject(id) { await fetch(`/approvals/${id}/reject`, { method: 'POST' }); load(); }
document.getElementById('rows').addEventListener('click', e => {
  const id = e.target.dataset.id; if (!id) return;
  (e.target.classList.contains('approve') ? approve : reject)(id);
});
document.getElementById('new').addEventListener('submit', async e => {
  e.preventDefault();
  const f = new FormData(e.target);
  await fetch('/approvals', {
    method: 'POST', headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ payer: f.get('payer'), payee: f.get('payee'),
      amount_minor: Number(f.get('amount_minor')), currency: f.get('currency') }),
  });
  load();
});
load();
</script>
</body>
</html>
"""
