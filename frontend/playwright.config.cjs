const { defineConfig } = require('@playwright/test');
module.exports = defineConfig({
  testDir: './tests',
  workers: 1,
  timeout: 15000,
  use: { baseURL: process.env.BENCHMARK_URL || 'http://127.0.0.1:8763', headless: true },
  webServer: process.env.BENCHMARK_URL ? undefined : {
    command: `${process.env.PYTHON || (process.platform === 'win32' ? 'python' : 'python3')} serve.py --port 8763`,
    url: 'http://127.0.0.1:8763', reuseExistingServer: false
  }
});
