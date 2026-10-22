import React, { useCallback, useEffect, useState } from "react";
import { api } from "../api.js";
import { Badge, Filters, Stars } from "../components.jsx";

export default function Reviews({ onOpen, notify }) {
  const [rows, setRows] = useState([]);
  const [filters, setFilters] = useState({ status: "", rating: "", review_id: "" });
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    api.reviews(filters).then(setRows).catch((e) => notify("error", e.message));
  }, [filters]);

  useEffect(() => {
    load();
  }, [load]);

  const sync = async () => {
    setBusy(true);
    try {
      const r = await api.sync();
      notify("ok", `Synced from dashboard: ${r.added} new review(s), ${r.total} total.`);
      load();
    } catch (e) {
      notify("error", e.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <section>
      <h2>Reviews</h2>
      <div className="toolbar">
        <Filters filters={filters} onChange={setFilters} />
        <button onClick={sync} disabled={busy}>{busy ? "Syncing…" : "Sync from dashboard"}</button>
      </div>
      <div className="table-wrap">
        <table>
          <thead>
            <tr><th>Review ID</th><th>Reviewer</th><th>Rating</th><th>Review</th><th>Date</th><th>Status</th><th></th></tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.id}>
                <td><code>{r.id}</code></td>
                <td>{r.reviewer_name}</td>
                <td><Stars n={r.rating} /></td>
                <td className="wrap">
                  {r.text}
                  {r.existing_reply && <div className="muted">Existing reply: {r.existing_reply}</div>}
                </td>
                <td>{r.review_date || "—"}</td>
                <td><Badge status={r.status} /></td>
                <td><button onClick={() => onOpen(r.id)}>Open</button></td>
              </tr>
            ))}
            {rows.length === 0 && <tr><td colSpan="7">No reviews match.</td></tr>}
          </tbody>
        </table>
      </div>
    </section>
  );
}
