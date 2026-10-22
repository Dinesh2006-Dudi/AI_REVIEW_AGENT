import React, { useCallback, useEffect, useState } from "react";
import { api } from "../api.js";
import { Badge, Filters, Stars } from "../components.jsx";

function Table({ title, rows, columns, onOpen, empty }) {
  return (
    <div className="panel">
      <h3>{title} ({rows.length})</h3>
      <div className="table-wrap">
        <table>
          <thead><tr><th>Review ID</th><th>Rating</th>{columns.map((c) => <th key={c.label}>{c.label}</th>)}<th></th></tr></thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.id}>
                <td><code>{r.id}</code></td>
                <td><Stars n={r.rating} /></td>
                {columns.map((c) => <td key={c.label} className="wrap">{c.render(r)}</td>)}
                <td><button className="secondary" onClick={() => onOpen(r.id)}>Open</button></td>
              </tr>
            ))}
            {rows.length === 0 && <tr><td colSpan={columns.length + 3}>{empty}</td></tr>}
          </tbody>
        </table>
      </div>
    </div>
  );
}

const draftText = (r) => (r.latest_draft ? r.latest_draft.text : "—");
const approval = (r) => (r.decision ? r.decision.decision : "none");

export default function Results({ onOpen, notify }) {
  const [data, setData] = useState(null);
  const [filters, setFilters] = useState({ status: "", rating: "", review_id: "" });

  const load = useCallback(() => {
    api.results(filters).then(setData).catch((e) => notify("error", e.message));
  }, [filters]);

  useEffect(() => { load(); }, [load]);

  if (!data) return <p>Loading…</p>;
  return (
    <section>
      <h2>Results &amp; verification</h2>
      <Filters filters={filters} onChange={setFilters} />
      <Table title="Replied (posted and verified)" rows={data.replied} onOpen={onOpen} empty="Nothing verified yet."
        columns={[
          { label: "Approved reply", render: (r) => (r.verification ? r.verification.observed_reply_text : draftText(r)) },
          { label: "Posted at", render: (r) => (r.attempt ? r.attempt.updated_at : "—") },
          { label: "Verification", render: (r) => (r.verification && r.verification.verified ? "Verified ✔" : "—") },
        ]} />
      <Table title="Needs verification (not confirmed)" rows={data.verification_pending} onOpen={onOpen} empty="None."
        columns={[
          { label: "Reply", render: draftText },
          { label: "Detail", render: (r) => (r.verification ? r.verification.detail : r.status_reason || "—") },
        ]} />
      <Table title="Posting failures" rows={data.failed} onOpen={onOpen} empty="None."
        columns={[
          { label: "Draft", render: draftText },
          { label: "Error", render: (r) => (r.attempt ? r.attempt.error : "—") },
        ]} />
      <Table title="Drafted only" rows={data.drafted_only} onOpen={onOpen} empty="No drafts waiting."
        columns={[
          { label: "Current draft", render: draftText },
          { label: "Approval", render: approval },
          { label: "Status", render: (r) => <Badge status={r.status} /> },
        ]} />
      <Table title="Skipped / rejected / already answered / needs attention" rows={data.skipped} onOpen={onOpen} empty="None."
        columns={[
          { label: "Status", render: (r) => <Badge status={r.status} /> },
          { label: "Reason", render: (r) => r.status_reason || (r.existing_reply ? "Dashboard already has a reply" : "—") },
        ]} />
    </section>
  );
}
