import React, { useEffect, useState } from "react";
import { api } from "../api.js";

const PRESETS = ["friendly", "professional", "warm", "apologetic", "casual"];

export default function SettingsPage({ notify }) {
  const [tone, setTone] = useState("professional");
  const [extra, setExtra] = useState("");

  useEffect(() => {
    api.getSettings().then((s) => { setTone(s.tone); setExtra(s.extra_instructions); }).catch((e) => notify("error", e.message));
  }, []);

  const save = async () => {
    try {
      await api.saveSettings({ tone, extra_instructions: extra });
      notify("ok", "Tone settings saved.");
    } catch (e) {
      notify("error", e.message);
    }
  };

  return (
    <section>
      <h2>Owner tone settings</h2>
      <label>Preferred tone
        <select value={PRESETS.includes(tone) ? tone : "custom"} onChange={(e) => e.target.value !== "custom" && setTone(e.target.value)}>
          {PRESETS.map((p) => <option key={p} value={p}>{p}</option>)}
          <option value="custom">custom…</option>
        </select>
      </label>
      <label>Or type your own tone
        <input value={tone} maxLength={40} onChange={(e) => setTone(e.target.value)} />
      </label>
      <label>Extra style guidance (optional)
        <textarea rows={3} maxLength={500} value={extra} onChange={(e) => setExtra(e.target.value)} placeholder="e.g. Sign off with “The team at Spice Garden”" />
      </label>
      <label className="check">
        <input type="checkbox" checked disabled /> Require my approval before posting (always on)
      </label>
      <button onClick={save}>Save settings</button>
    </section>
  );
}
