const acceptanceCriteria = [
  "Landing page renders headline and CTA",
  "CTA button is visible and clickable"
];

export default function App() {
  return (
    <main className="page">
      <h1>UI feature: add onboarding landing page</h1>
      <p className="problem">Create a frontend page with button, layout, and styling for onboarding flow.</p>
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
