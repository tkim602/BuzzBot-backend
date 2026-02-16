import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./src/**/*.{js,ts,jsx,tsx,mdx}"],
  theme: {
    extend: {
      colors: {
        gt: {
          gold: "#B3A369",
          navy: "#003057",
          white: "#FFFFFF",
          blue: "#004F9F",
        },
      },
    },
  },
  plugins: [],
};
export default config;
