import { lazy, Suspense, useEffect, useState } from "react";
import { Redirect, Route, Router, Switch, useLocation } from "wouter";
import { useHashLocation } from "wouter/use-hash-location";

import { ApiProvider } from "@/api/provider";
import { useSetupStatus } from "@/api/queries";
import { readPreference } from "@/lib/theme";
import { Boot } from "@/shell/Boot";
import { CommandPalette } from "@/shell/CommandPalette";
import { ErrorBoundary } from "@/shell/ErrorBoundary";
import { DOMAIN_LIST, legacyRedirect } from "@/shell/routes";
import { TopBar } from "@/shell/TopBar";
import { StatusBar } from "@/shell/StatusBar";
import { SkeletonRows } from "@/ui/feedback";
import { TooltipProvider } from "@/ui/overlay";
import { ToastProvider } from "@/ui/toast";

const Inicio = lazy(() => import("@/screens/inicio/Inicio"));
const Trading = lazy(() => import("@/screens/trading/Trading"));
const Inteligencia = lazy(() => import("@/screens/inteligencia/Inteligencia"));
const Riesgo = lazy(() => import("@/screens/riesgo/Riesgo"));
const Noticias = lazy(() => import("@/screens/noticias/Noticias"));
const Ajustes = lazy(() => import("@/screens/ajustes/Ajustes"));
const Onboarding = lazy(() => import("@/screens/onboarding/Onboarding"));

function PageFallback() {
  return (
    <div className="flex flex-1 flex-col">
      <div className="h-12 border-b border-line" />
      <div className="px-6 py-5">
        <SkeletonRows rows={6} />
      </div>
    </div>
  );
}

function Workspace() {
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [location, navigate] = useLocation();
  const setup = useSetupStatus();
  const moved = legacyRedirect(location);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (!(event.metaKey || event.ctrlKey)) return;
      if (event.key.toLowerCase() === "k") {
        event.preventDefault();
        setPaletteOpen((open) => !open);
        return;
      }
      const domain = DOMAIN_LIST.find((d) => d.shortcut === event.key);
      if (domain) {
        event.preventDefault();
        navigate(domain.path);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [navigate]);

  if (setup.data && !setup.data.configured) {
    return (
      <Suspense fallback={<PageFallback />}>
        <Onboarding />
      </Suspense>
    );
  }

  return (
    <div className="flex h-full flex-col">
      <TopBar onOpenPalette={() => setPaletteOpen(true)} />
      <div className="flex min-h-0 flex-1">
        <div className="flex min-w-0 flex-1 flex-col bg-bg">
          <ErrorBoundary resetKey={location}>
          <Suspense fallback={<PageFallback />}>
            {moved ? (
              <Redirect to={moved} replace />
            ) : (
            <Switch>
              <Route path="/inicio" component={Inicio} />
              <Route path="/trading/:tab?/:symbol?" component={Trading} />
              <Route path="/inteligencia/:tab?/:item?" component={Inteligencia} />
              <Route path="/riesgo/:tab?" component={Riesgo} />
              <Route path="/noticias/:tab?" component={Noticias} />
              <Route path="/ajustes/:panel?" component={Ajustes} />
              <Route>
                <Redirect to="/inicio" replace />
              </Route>
            </Switch>
            )}
          </Suspense>
          </ErrorBoundary>
        </div>
      </div>
      <StatusBar />
      <CommandPalette open={paletteOpen} onOpenChange={setPaletteOpen} />
    </div>
  );
}

export function App() {
  useEffect(() => {
    const stored = readPreference("hyverion.theme");
    document.documentElement.dataset.theme = stored === "light" ? "light" : "dark";
  }, []);

  return (
    <ApiProvider fallback={(state) => <Boot state={state} />}>
      <TooltipProvider>
        <ToastProvider>
          <Router hook={useHashLocation}>
            <Workspace />
          </Router>
        </ToastProvider>
      </TooltipProvider>
    </ApiProvider>
  );
}
