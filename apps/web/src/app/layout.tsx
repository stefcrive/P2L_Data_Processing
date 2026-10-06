import type { Metadata } from "next";
import { Geist_Mono, Manrope, Space_Grotesk } from "next/font/google";
import "./globals.css";

import { BackToTopButton } from "@/components/layout/back-to-top-button";
import { QueryProvider } from "@/components/layout/query-provider";
import { LanguageProvider } from "@/components/layout/language-provider";

export const metadata: Metadata = {
  title: "IRMS Metrology Station",
  description: "Metrological qualification, client sessions and traceable IRMS results",
};

const sans = Manrope({
  subsets: ["latin"],
  variable: "--font-sans",
  display: "swap",
});

const display = Space_Grotesk({
  subsets: ["latin"],
  variable: "--font-display",
  display: "swap",
});

const mono = Geist_Mono({
  subsets: ["latin"],
  variable: "--font-mono",
  display: "swap",
});

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className={`${sans.variable} ${display.variable} ${mono.variable}`}>
        <LanguageProvider>
          <QueryProvider>{children}</QueryProvider>
          <BackToTopButton />
        </LanguageProvider>
      </body>
    </html>
  );
}
