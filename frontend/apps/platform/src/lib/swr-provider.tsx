"use client";

import { SWRConfig } from "swr";
import type { ReactNode } from "react";
import { fetcher } from "@/lib/fetcher";

export function SWRProvider({ children }: { children: ReactNode }) {
  return (
    <SWRConfig
      value={{
        fetcher,
        revalidateOnFocus: false,
        errorRetryCount: 2,
      }}
    >
      {children}
    </SWRConfig>
  );
}
