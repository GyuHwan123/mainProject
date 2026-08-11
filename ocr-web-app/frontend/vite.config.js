import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  // Keep shared Supabase settings in the project-level .env file.
  envDir: '..',
  plugins: [react()],
  server: {
    host: '0.0.0.0',
    port: 3000,
  },
});
