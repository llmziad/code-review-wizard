/**
 * Placeholder app shell. The real inbox / PR detail / settings views land in
 * follow-up tasks; this scaffold just verifies the Electron + React + Tailwind
 * pipeline compiles and renders.
 */
export function App() {
  return (
    <div className="flex h-screen w-screen flex-col">
      <header className="app-drag flex h-12 items-center justify-between border-b border-zinc-800 px-4">
        <h1 className="text-sm font-medium tracking-tight text-zinc-300">
          Code Review Wizard
        </h1>
        <span className="text-xs text-zinc-500">scaffold v0.1.0</span>
      </header>

      <main className="flex flex-1 items-center justify-center">
        <div className="max-w-md text-center">
          <p className="text-lg font-medium text-zinc-200">
            Electron shell is alive.
          </p>
          <p className="mt-2 text-sm text-zinc-500">
            The sidecar bridge, inbox view, and triage workflow land in the
            next commits.
          </p>
        </div>
      </main>
    </div>
  );
}
