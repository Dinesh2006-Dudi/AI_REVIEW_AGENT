import React, { useEffect, useState } from "react";
import { api, STATUS_LABELS } from "../api.js";

export default function Home({ onNavigate, notify }) {
  const [data, setData] = useState(null);

  useEffect(() => {
    api.summary().then(setData).catch((e) => notify("error", e.message));
  }, []);

  if (!data) return <p>Loading…</p>;
  return (
    <section>
      <h2>Overview</h2>
      <p>{data.total} reviews. Nothing is ever posted without your explicit approval.</p>
      <div className="cards">
        {Object.entries(data.counts).map(([k, n]) => (
          <div className="card" key={k}>
            <div className="big">{n}</div>
            <div>{STATUS_LABELS[k]}</div>
          </div>
        ))}
      </div>
      <p>
        <button onClick={() => onNavigate("reviews")}>Go to reviews</button>{" "}
        <button className="secondary" onClick={() => onNavigate("results")}>See results</button>
      </p>
    </section>
  );
}
