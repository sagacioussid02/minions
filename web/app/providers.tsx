"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState } from "react";

/**
 * Client-side root. Wraps every page in a TanStack Query client with a
 * 3-second poll on stale data — Sprint 6 will replace polling with SSE.
 */
export function Providers({ children }: { children: React.ReactNode }) {
  const [client] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            // Initial load is RSC; this only takes over after hydration.
            staleTime: 2_500,
            refetchInterval: 3_000,
            refetchOnWindowFocus: false,
          },
        },
      }),
  );
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}
