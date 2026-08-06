import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      fontFamily: {
        sans: ["var(--font-sans)", '"Segoe UI"', "sans-serif"],
        display: ["var(--font-display)", '"Segoe UI"', "sans-serif"],
        mono: ["var(--font-mono)", '"Cascadia Mono"', "monospace"],
      },
      colors: {
        canvas: "var(--canvas)",
      },
    },
  },
  plugins: [],
};

export default config;
