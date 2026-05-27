import { useEffect, useState } from "react";
import { InboxView } from "./inbox/InboxView";
import { PRDetailView } from "./detail/PRDetailView";
import { LoginView } from "./login/LoginView";
import { useStore } from "./lib/store";
import { api } from "./lib/api";

type AuthState = "checking" | "authenticated" | "needs_login";

export function App() {
  const activePrUrl = useStore((s) => s.activePrUrl);
  const [auth, setAuth] = useState<AuthState>("checking");

  // Probe whoami on mount. Any error (including AuthMissingError) → show login.
  useEffect(() => {
    api
      .whoami()
      .then(() => setAuth("authenticated"))
      .catch(() => setAuth("needs_login"));
  }, []);

  return (
    <div className="flex h-screen w-screen flex-col">
      <header className="app-drag flex h-12 shrink-0 items-center justify-between border-b border-zinc-800 px-4">
        <h1 className="text-sm font-medium tracking-tight text-zinc-300">
          Code Review Wizard
        </h1>
        <span className="text-xs text-zinc-600">v0.1.0</span>
      </header>
      <main className="flex-1 overflow-hidden">
        {auth === "checking" && <CheckingAuth />}
        {auth === "needs_login" && <LoginView onSuccess={() => setAuth("authenticated")} />}
        {auth === "authenticated" && (activePrUrl ? <PRDetailView /> : <InboxView />)}
      </main>
    </div>
  );
}

function CheckingAuth() {
  return (
    <div className="flex h-full items-center justify-center">
      <p className="text-sm text-zinc-500">Checking authentication…</p>
    </div>
  );
}
