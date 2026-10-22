import React, { useCallback, useEffect, useState } from "react";
import { api } from "../api.js";
import { Badge, Stars } from "../components.jsx";

const LOCKED = ["replied", "already_replied", "verification_pending"];

export default function Editor({ reviewId, onPick, notify }) {
  const [review, setReview] = useState(null);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);

  const apply = (r) => {
    setReview(r);
    setText(r.latest_draft ? r.latest_draft.text : "");
  };

  const load = useCallback(async () => {
    if (!reviewId) return;
    try {
      apply(await api.review(reviewId));
    } catch (e) {
      notify("error", e.message);
    }
  }, [reviewId]);

  useEffect(() => {
    load();
  }, [load]);

  if (!reviewId) {
    return (
      <section>
        <h2>Reply editor</h2>
        <p>Pick a review first.</p>
        <button onClick={onPick}>Choose a review</button>
      </section>
    );
  }
  if (!review) return <p>Loading…</p>;

  const draft = review.latest_draft;
  const locked = LOCKED.includes(review.status);
  const dirty = !!draft && text.trim() !== draft.text;
  const version = draft ? draft.version : 0;

  const run = async (fn, okMessage) => {
    setBusy(true);
    try {
      const out = await fn();
      if (okMessage) notify("ok", okMessage);
      return out;
    } catch (e) {
      notify("error", e.message);
      await load();
      return null;
    } finally {
      setBusy(false);
    }
  };

  // Saves the textarea as a new draft version if it differs; returns the version to act on.
  const ensureSaved = async () => {
    if (!draft && !text.trim()) throw new Error("Write or generate a reply first.");
    if (draft && !dirty) return draft.version;
    const saved = await api.edit(reviewId, text, version);
    return saved.version;
  };

  const generate = () => run(async () => { await api.generate(reviewId); await load(); }, "New AI draft created.");
  const save = () => run(async () => { await ensureSaved(); await load(); }, "Edit saved as a new draft version. It needs approval before posting.");

  const approvePost = () =>
    run(async () => {
      const v = await ensureSaved();
      const shown = text.trim();
      if (!window.confirm(`Post this exact reply to ${reviewId}?\n\n${shown}`)) { await load(); return; }
      const res = await api.approveAndPost(reviewId, v);
      apply(res.review);
      notify(res.outcome === "verified" ? "ok" : "warn", res.message);
    });

  const draftOnly = () =>
    run(async () => {
      const v = await ensureSaved();
      await api.decide(reviewId, "draft_only", v);
      await load();
    }, "Saved as draft only. It will not be posted.");

  const reject = () =>
    run(async () => {
      if (!draft) throw new Error("There is no draft to reject.");
      await api.decide(reviewId, "reject", draft.version);
      await load();
    }, "Draft rejected. It will not be posted.");

  const skip = () => {
    const reason = window.prompt("Reason for skipping (optional):", "");
    if (reason === null) return;
    return run(async () => { await api.decide(reviewId, "skip", null, reason); await load(); }, "Review skipped.");
  };

  const verify = () =>
    run(async () => {
      const res = await api.verify(reviewId);
      apply(res.review);
      notify(res.outcome === "verified" ? "ok" : "warn", res.message);
    });

  const att = review.attempt;
  const ver = review.verification;

  return (
    <section>
      <h2>Reply editor — <code>{review.id}</code> <Badge status={review.status} /></h2>
      <div className="grid2">
        <div className="panel">
          <h3>Original review</h3>
          <p><strong>{review.reviewer_name}</strong> · <Stars n={review.rating} /> · {review.review_date || "no date"}</p>
          <p className="quote">{review.text}</p>
          {review.existing_reply && <p className="muted">Existing reply: {review.existing_reply}</p>}
          {review.status_reason && <p className="muted">Note: {review.status_reason}</p>}
        </div>
        <div className="panel">
          <h3>Proposed reply {draft && <span className="muted">(for <code>{review.id}</code>, draft v{draft.version}, {draft.source === "ai" ? "AI" : "edited by you"})</span>}</h3>
          {draft && draft.flagged ? <p className="warn-box">Needs your attention: {draft.flag_reason}</p> : null}
          <textarea value={text} onChange={(e) => setText(e.target.value)} rows={8} maxLength={1500} disabled={locked || busy} placeholder="Generate a draft or write your own reply…" />
          <div className="actions">
            <button className="secondary" onClick={generate} disabled={locked || busy}>{draft ? "Regenerate with AI" : "Generate AI draft"}</button>
            <button className="secondary" onClick={save} disabled={locked || busy || !dirty}>Save edit</button>
            <button className="secondary" onClick={draftOnly} disabled={locked || busy || (!draft && !text.trim())}>Save as draft only</button>
            <button className="danger" onClick={reject} disabled={locked || busy || !draft}>Reject</button>
            <button className="secondary" onClick={skip} disabled={locked || busy}>Skip</button>
            <button className="primary" onClick={approvePost} disabled={locked || busy || (!draft && !text.trim())}>Approve and Post</button>
          </div>
          <p className="muted">“Save as draft only” never publishes. “Approve and Post” approves exactly the text shown above and posts it; if you changed the text it is saved as a new version first.</p>
          {review.status === "verification_pending" && <button onClick={verify} disabled={busy}>Verify on dashboard now</button>}
        </div>
      </div>
      {(att || ver) && (
        <div className="panel">
          <h3>Posting &amp; verification</h3>
          {att && <p>Posting attempt: <strong>{att.status}</strong> (tries: {att.attempt_count}){att.dashboard_reply_id ? ` · reply ID ${att.dashboard_reply_id}` : ""}{att.error ? ` · ${att.error}` : ""}</p>}
          {ver && <p>Last verification: <strong>{ver.verified ? "passed" : "NOT verified"}</strong> at {ver.created_at}. {ver.detail}</p>}
        </div>
      )}
    </section>
  );
}
