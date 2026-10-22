import React from "react";
import { STATUS_LABELS } from "./api.js";

// React escapes all text by default; we never use dangerouslySetInnerHTML, so review text is safe to render.
export function Badge({ status }) {
  return <span className={`badge b-${status}`}>{STATUS_LABELS[status] || status}</span>;
}

export function Stars({ n }) {
  return <span title={`${n} of 5`}>{"★".repeat(n)}{"☆".repeat(5 - n)}</span>;
}

export function Filters({ filters, onChange }) {
  const set = (k, v) => onChange({ ...filters, [k]: v });
  return (
    <div className="filters">
      <select value={filters.status} onChange={(e) => set("status", e.target.value)} aria-label="Status filter">
        <option value="">All statuses</option>
        {Object.entries(STATUS_LABELS).map(([k, v]) => (
          <option key={k} value={k}>{v}</option>
        ))}
      </select>
      <select value={filters.rating} onChange={(e) => set("rating", e.target.value)} aria-label="Rating filter">
        <option value="">All ratings</option>
        {[5, 4, 3, 2, 1].map((n) => (
          <option key={n} value={n}>{n} stars</option>
        ))}
      </select>
      <input
        placeholder="Review ID (e.g. rev_1001)"
        value={filters.review_id}
        maxLength={64}
        onChange={(e) => set("review_id", e.target.value.replace(/[^A-Za-z0-9_-]/g, ""))}
      />
    </div>
  );
}

export function Notice({ notice, onClose }) {
  if (!notice) return null;
  return (
    <div className={`notice n-${notice.kind}`} role="status">
      <span>{notice.text}</span>
      <button className="link" onClick={onClose}>dismiss</button>
    </div>
  );
}
