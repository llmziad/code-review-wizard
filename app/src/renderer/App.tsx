import { InboxView } from "./inbox/InboxView";
import { PRDetailView } from "./detail/PRDetailView";
import { useStore } from "./lib/store";

export function App() {
  const activePrUrl = useStore((s) => s.activePrUrl);

  return (
    <div className="flex h-screen w-screen flex-col">
      <header className="app-drag flex h-12 shrink-0 items-center justify-between border-b border-zinc-800 px-4">
        <h1 className="text-sm font-medium tracking-tight text-zinc-300">
          Code Review Wizard
        </h1>
        <span className="text-xs text-zinc-600">v0.1.0</span>
      </header>
      <main className="flex-1 overflow-hidden">
        {activePrUrl ? <PRDetailView /> : <InboxView />}
      </main>
    </div>
  );
}
