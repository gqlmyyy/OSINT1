/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        ink: { 950: '#080a0f', 900: '#0b0e14', 850: '#0f141d', 800: '#141922', 700: '#1b212c' },
        line: { DEFAULT: '#232a37', bright: '#2f3849' },
        fg: { DEFAULT: '#e6e9ef', muted: '#8b93a7', dim: '#5f6779' },
        accent: { DEFAULT: '#5aa9ff', dim: '#2b6fbf' },
        // One colour per assertion class, used identically in the graph, panels and reports.
        observed: '#3ddc97',
        correlated: '#5aa9ff',
        inferred: '#f5b942',
        unverified: '#8b93a7',
        danger: '#ff6b6b',
      },
      fontFamily: {
        sans: ['Inter', 'ui-sans-serif', 'system-ui', '-apple-system', 'Segoe UI', 'sans-serif'],
        mono: ['ui-monospace', 'SFMono-Regular', 'Menlo', 'monospace'],
      },
      fontSize: { '2xs': ['0.6875rem', '1rem'] },
    },
  },
  plugins: [],
};
