import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { RouterProvider } from "react-router-dom";
import "./styles/globals.css";
import "./i18n";
import { getToken } from "./api/token";
import { ClientsConfirmDialog } from "./components/app/ClientsConfirm";
import { Toaster } from "./components/ui/sonner";
import { TooltipProvider } from "./components/ui/tooltip";
import "./lib/theme";
import { router } from "./router";

getToken(); // capture ?token= before any route renders and strip it from the address bar

const queryClient = new QueryClient({
  defaultOptions: { queries: { refetchOnWindowFocus: false, retry: 1 } },
});

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <TooltipProvider delayDuration={400}>
        <RouterProvider router={router} />
        <Toaster position="bottom-right" closeButton />
        <ClientsConfirmDialog />
      </TooltipProvider>
    </QueryClientProvider>
  </StrictMode>,
);
