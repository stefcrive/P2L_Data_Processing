import type { Metadata } from "next";
import "./metrology.css";
import { AppHeader } from "@/components/layout/sidebar";

export const metadata: Metadata = {
  title: "IRMS Metrology Station",
  description: "Instrument qualification, method validation and traceable isotope results",
};

export default function MetrologyLayout({ children }: { children: React.ReactNode }) {
  return <div className="metro"><AppHeader /><div className="pt-[var(--app-header-height,56px)]">{children}</div></div>;
}
