import { defineConfig } from '@playwright/test'
export default defineConfig({ testDir:'./e2e', fullyParallel:false, workers:1, timeout:60000,
 use:{baseURL:'http://localhost:5173',trace:'retain-on-failure',screenshot:'only-on-failure'},
 webServer:[
  {command:'SWARMGUARD_E2E=1 .venv/bin/python -m tests.console_e2e_server',cwd:'..',url:'http://127.0.0.1:8000/api/health',reuseExistingServer:false,timeout:30000},
  {command:process.env.SWARMGUARD_PREVIEW === '1' ? 'npm run preview' : 'npm run dev',url:'http://127.0.0.1:5173',reuseExistingServer:false,timeout:30000},
 ] })
