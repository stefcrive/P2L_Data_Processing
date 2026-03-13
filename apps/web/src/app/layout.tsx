import type { Metadata } from "next";
import { Plus_Jakarta_Sans, Space_Grotesk } from "next/font/google";
import "./globals.css";

import { BackToTopButton } from "@/components/layout/back-to-top-button";
import { QueryProvider } from "@/components/layout/query-provider";

export const metadata: Metadata = {
  title: "IRMS Dashboard",
  description: "Next.js dashboard for the IRMS analyzer refactor",
};

const sans = Plus_Jakarta_Sans({
  subsets: ["latin"],
  variable: "--font-sans",
  display: "swap",
});

const display = Space_Grotesk({
  subsets: ["latin"],
  variable: "--font-display",
  display: "swap",
});

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className={`${sans.variable} ${display.variable}`}>
        <QueryProvider>{children}</QueryProvider>
        <BackToTopButton />
      </body>
    </html>
  );
}
