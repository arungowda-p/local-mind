/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,ts,tsx}"],
  theme: {
    extend: {
      colors: {
        bg: "#0b0f19",
        surface: "#131825",
        panel: "#1a2035",
        line: "#262f45",
        muted: "#7b8ba5",
        accent: "#6c8cff",
        ok: "#34d399",
        warn: "#fbbf24",
        danger: "#f87171",
      },
      fontFamily: {
        sans: ['"Inter"', "system-ui", "sans-serif"],
        mono: ['"JetBrains Mono"', "ui-monospace", "monospace"],
      },
    },
  },
  plugins: [],
};
