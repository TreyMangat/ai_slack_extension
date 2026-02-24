import { useState } from "react";

const acceptanceCriteria = ["skip"];

export default function App() {
  const [clickCount, setClickCount] = useState(0);

  return (
    <main className="page">
      <h1>Can you add a button to your page</h1>
      <p className="problem">I want you to create a new button</p>

      <section className="button-demo">
        <h2>Button Demo</h2>
        <button
          type="button"
          className="primary-button"
          onClick={() => setClickCount((count) => count + 1)}
        >
          Click me
        </button>
        <p className="click-output">Button clicks: {clickCount}</p>
      </section>

      <section>
        <h2>Acceptance Criteria</h2>
        <ul>
          {acceptanceCriteria.map((item) => (
            <li key={item}>{item}</li>
          ))}
        </ul>
      </section>
    </main>
  );
}
