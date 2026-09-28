import type { ReactNode } from "react";

import { openExternal } from "@/lib/native";

/** A link to the web that opens in the browser, never inside the app window. */
export function ExternalLink({ href, children, className }: { href: string; children: ReactNode; className?: string }) {
  return (
    <a
      href={href}
      rel="noreferrer"
      className={className}
      onClick={(event) => {
        event.preventDefault();
        void openExternal(href);
      }}
    >
      {children}
    </a>
  );
}
