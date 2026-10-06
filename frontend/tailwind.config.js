/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        // Forest-green primary (replaces cyan)
        primary: {
          50:  '#f3f8f5',
          100: '#e2f0e7',
          200: '#c2dfcd',
          300: '#9bc8ad',
          400: '#6fae89',
          500: '#4a8568',
          600: '#2d6a4f',
          700: '#235740',
          800: '#1c4634',
          900: '#143329',
        },
        // Paper / forest tokens
        geo: {
          // legacy names retained for backwards compatibility, retargeted
          dark:      '#faf8f2', // was deep navy; now paper bg
          darker:    '#f1ede2',
          accent:    '#2d6a4f',
          highlight: '#2d6a4f',
          coral:     '#b54a36',
          glow:      '#4a8568',
          // new tokens
          bg:        '#faf8f2',
          'bg-2':    '#f1ede2',
          surface:   '#ffffff',
          'surface-2': '#f5f1e6',
          'surface-3': '#e8e3d4',
          border:    '#e5dfce',
          'border-strong': '#c8c1ad',
          text:      '#1f2a23',
          'text-dim':'#5a6359',
          'text-mute':'#8b9087',
          ok:        '#3f8a5f',
          warn:      '#b07a1c',
          danger:    '#b54a36',
          info:      '#3d6a9e',
        },
        status: {
          info:    '#3d6a9e',
          success: '#3f8a5f',
          warning: '#b07a1c',
          error:   '#b54a36',
        },
      },
      spacing: {
        'panel-collapsed': '56px',
        'panel-left':      '280px',
        'chat-medium':     '400px',
        'chat-expanded':   '460px',
        'topbar':          '44px',
        'rail':            '56px',
        'statusbar':       '32px',
      },
      borderRadius: {
        panel: '10px',
        card:  '10px',
      },
      fontFamily: {
        sans: ['Geist', 'Inter Tight', 'system-ui', '-apple-system', 'BlinkMacSystemFont', 'Segoe UI', 'Roboto', 'sans-serif'],
        mono: ['JetBrains Mono', 'IBM Plex Mono', 'ui-monospace', 'SFMono-Regular', 'Menlo', 'monospace'],
      },
      transitionDuration: { panel: '200ms' },
      width: {
        'panel-collapsed': '56px',
        'panel-left':      '280px',
        'chat-medium':     '400px',
        'chat-expanded':   '460px',
        'rail':            '56px',
      },
      minWidth: { 'panel-collapsed': '56px' },
      maxWidth: { 'chat-expanded': '460px' },
      boxShadow: {
        glow:        '0 2px 10px rgba(31, 42, 35, 0.06)',
        'glow-sm':   '0 1px 4px rgba(31, 42, 35, 0.04)',
        'inner-glow':'inset 0 0 0 1px rgba(45, 106, 79, 0.10)',
      },
      backdropBlur: { xs: '2px' },
      animation: {
        'pulse-slow': 'pulse 3s cubic-bezier(0.4, 0, 0.6, 1) infinite',
      },
    },
  },
  plugins: [],
}
