import type { Metadata } from "next";
import "./globals.css";

import { BackToTopButton } from "@/components/layout/back-to-top-button";
import { QueryProvider } from "@/components/layout/query-provider";

export const metadata: Metadata = {
  title: "IRMS Dashboard",
  description: "Next.js dashboard for the IRMS analyzer refactor",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <QueryProvider>{children}</QueryProvider>
        <BackToTopButton />
      </body>
    </html>
  );
}
