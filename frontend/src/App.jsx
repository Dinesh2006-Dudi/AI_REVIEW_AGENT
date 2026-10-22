import React, { useState } from "react";
import Home from "./pages/Home.jsx";
import Reviews from "./pages/Reviews.jsx";
import Editor from "./pages/Editor.jsx";
import SettingsPage from "./pages/SettingsPage.jsx";
import Results from "./pages/Results.jsx";
import { Notice } from "./components.jsx";

const TABS = [
  ["home", "Home"],
  ["reviews", "Reviews"],
  ["editor", "Reply editor"],
  ["settings", "Tone settings"],
  ["results", "Results"],
];

export default function App() {
  const [page, setPage] = useState("home");
  const [reviewId, setReviewId] = useState(null);
  const [notice, setNotice] = useState(null);

  const notify = (kind, text) => setNotice({ kind, text });
  const openReview = (id) => {
    setReviewId(id);
    setPage("editor");
  };

  return (
    <div className="app">
      <header>
        <h1>AI Restaurant Review Reply Agent</h1>
        <nav>
          {TABS.map(([key, label]) => (
            <button key={key} className={page === key ? "tab active" : "tab"} onClick={() => setPage(key)}>
              {label}
            </button>
          ))}
        </nav>
      </header>
      <Notice notice={notice} onClose={() => setNotice(null)} />
      <main>
        {page === "home" && <Home onNavigate={setPage} notify={notify} />}
        {page === "reviews" && <Reviews onOpen={openReview} notify={notify} />}
        {page === "editor" && <Editor reviewId={reviewId} onPick={() => setPage("reviews")} notify={notify} />}
        {page === "settings" && <SettingsPage notify={notify} />}
        {page === "results" && <Results onOpen={openReview} notify={notify} />}
      </main>
    </div>
  );
}
