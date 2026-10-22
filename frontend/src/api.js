async function request(path, method = "GET", body) {
  const res = await fetch(`/api${path}`, {
    method,
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  let data = null;
  try {
    data = await res.json();
  } catch (e) {
    data = null;
  }
  if (!res.ok) {
    const d = data && data.detail;
    const msg = typeof d === "string" ? d : Array.isArray(d) ? d.map((x) => x.msg).join("; ") : `Request failed (${res.status})`;
    throw new Error(msg);
  }
  return data;
}

const qs = (f) => {
  const p = new URLSearchParams();
  Object.entries(f || {}).forEach(([k, v]) => {
    if (v !== "" && v !== null && v !== undefined) p.set(k, v);
  });
  const s = p.toString();
  return s ? `?${s}` : "";
};

export const api = {
  summary: () => request("/summary"),
  getSettings: () => request("/settings"),
  saveSettings: (b) => request("/settings", "PUT", b),
  sync: () => request("/reviews/sync", "POST"),
  reviews: (f) => request(`/reviews${qs(f)}`),
  review: (id) => request(`/reviews/${encodeURIComponent(id)}`),
  generate: (id) => request(`/reviews/${encodeURIComponent(id)}/draft`, "POST"),
  edit: (id, text, base_version) => request(`/reviews/${encodeURIComponent(id)}/draft`, "PUT", { text, base_version }),
  decide: (id, decision, version, reason) =>
    request(`/reviews/${encodeURIComponent(id)}/decision`, "POST", { decision, version, reason }),
  approveAndPost: (id, version) => request(`/reviews/${encodeURIComponent(id)}/approve-and-post`, "POST", { version }),
  verify: (id) => request(`/reviews/${encodeURIComponent(id)}/verify`, "POST"),
  results: (f) => request(`/results${qs(f)}`),
};

export const STATUS_LABELS = {
  new: "New",
  drafted: "Drafted",
  draft_only: "Draft only",
  approved: "Approved (not posted)",
  post_failed: "Posting failed",
  verification_pending: "Needs verification",
  replied: "Replied & verified",
  rejected: "Rejected",
  skipped: "Skipped",
  needs_attention: "Needs attention",
  already_replied: "Already replied",
};
