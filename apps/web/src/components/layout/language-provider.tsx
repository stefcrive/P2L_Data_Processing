"use client";

import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { translate, type Language } from "@/lib/i18n/translate";
import { usePathname } from "next/navigation";

const STORAGE_KEY = "irms.language.v1";
const LanguageContext = createContext<{ language: Language; setLanguage: (language: Language) => void }>({ language: "en", setLanguage: () => {} });

export function LanguageProvider({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  // Server and first client render agree; restore the preference after hydration.
  const [language, updateLanguage] = useState<Language>("en");
  useEffect(() => {
    try { if (localStorage.getItem(STORAGE_KEY) === "pt") updateLanguage("pt"); } catch { /* Storage is optional. */ }
    const sync = (event: StorageEvent) => {
      if (event.key === STORAGE_KEY) updateLanguage(event.newValue === "pt" ? "pt" : "en");
    };
    window.addEventListener("storage", sync);
    return () => window.removeEventListener("storage", sync);
  }, []);
  useEffect(() => {
    document.documentElement.lang = language === "pt" ? "pt-BR" : "en";
    document.title = "IRMS Metrology Station";
  }, [language, pathname]);
  const setLanguage = useCallback((next: Language) => {
    updateLanguage(next);
    try { localStorage.setItem(STORAGE_KEY, next); } catch { /* Keep the selector usable. */ }
  }, []);
  return <LanguageContext.Provider value={{ language, setLanguage }}>{children}</LanguageContext.Provider>;
}

export function useLanguage() { return useContext(LanguageContext); }
export function useTranslation() {
  const { language } = useLanguage();
  return useCallback(<T,>(value: T): T => translate(value, language), [language]);
}

export function LanguageSelector() {
  const { language, setLanguage } = useLanguage();
  return <div role="group" aria-label={language === "pt" ? "Idioma" : "Language"} className="flex shrink-0 items-center rounded-md border border-slate-300 bg-slate-50 p-0.5">
    {(["en", "pt"] as const).map(option => <button key={option} type="button" lang={option === "pt" ? "pt-BR" : "en"} aria-label={option === "pt" ? "Português" : "English"} aria-pressed={language === option} onClick={() => setLanguage(option)} className={`h-7 rounded px-2 text-[11px] font-semibold uppercase transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400 ${language === option ? "bg-white text-blue-800 shadow-sm" : "text-slate-500 hover:text-slate-900"}`}>{option}</button>)}
  </div>;
}
