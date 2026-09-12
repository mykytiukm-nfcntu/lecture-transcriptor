import type { Config } from 'tailwindcss';

const config: Config = {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        status: {
          queued: '#94a3b8',
          running: '#3b82f6',
          completed: '#22c55e',
          failed: '#ef4444',
        },
      },
    },
  },
  plugins: [],
};

export default config;
