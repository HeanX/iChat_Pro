/**
 * iChat Pro desktop shell (P4 T14/T15).
 *
 * Two modes:
 * - CLOUD (production): ICHAT_SERVER_URL holds the full HTTPS origin of the
 *   public service (e.g. https://sub.example.com:8443). Django is NEVER
 *   spawned; an offline page with automatic recovery is shown when the
 *   service is unreachable.
 * - DEV (developer machines only): no ICHAT_SERVER_URL - a local Django
 *   runserver is spawned on ICHAT_HOST/ICHAT_PORT (default 127.0.0.1:8000).
 *   This branch is intentionally the only one that spawns Python.
 */
const { app, BrowserWindow, shell } = require('electron');
const fs = require('fs');
const http = require('http');
const https = require('https');
const path = require('path');
const { spawn } = require('child_process');

const { normalizeOrigin, resolveAppConfig } = require('./app-config');

const APP_CONFIG = resolveAppConfig(process.env, process.argv);
const CLOUD_MODE = APP_CONFIG.mode === 'cloud';
const DEV_MODE = APP_CONFIG.mode === 'dev';
const APP_ORIGIN = APP_CONFIG.origin || '';
const APP_URL = APP_ORIGIN + '/login/';
const PROJECT_ROOT = path.resolve(__dirname, '..');

let djangoProcess = null;
let mainWindow = null;
let offlinePollTimer = null;

function localPythonCandidates() {
  const candidates = [];
  if (process.env.ICHAT_PYTHON) candidates.push(process.env.ICHAT_PYTHON);
  if (process.platform === 'win32') {
    candidates.push(path.join(PROJECT_ROOT, '.venv', 'Scripts', 'python.exe'));
    candidates.push('python');
  } else {
    candidates.push(path.join(PROJECT_ROOT, '.venv', 'bin', 'python'));
    candidates.push('python3');
    candidates.push('python');
  }
  return candidates;
}

function resolvePythonExecutable() {
  for (const candidate of localPythonCandidates()) {
    if (path.isAbsolute(candidate) && fs.existsSync(candidate)) return candidate;
    if (!path.isAbsolute(candidate)) return candidate;
  }
  return 'python';
}

function startDjangoServer() {
  if (CLOUD_MODE || !DEV_MODE) return; // T14: Django only in explicit dev mode.
  const pythonExecutable = resolvePythonExecutable();
  djangoProcess = spawn(
    pythonExecutable,
    ['manage.py', 'runserver', `${DJANGO_HOST}:${DJANGO_PORT}`],
    {
      cwd: PROJECT_ROOT,
      env: { ...process.env, PYTHONUNBUFFERED: '1' },
      stdio: ['ignore', 'pipe', 'pipe'],
      windowsHide: true,
    },
  );
  djangoProcess.stdout.on('data', (data) => console.log(`[Django] ${data.toString().trim()}`));
  djangoProcess.stderr.on('data', (data) => console.error(`[Django] ${data.toString().trim()}`));
  djangoProcess.on('error', (error) => {
    // e.g. Python not installed in dev mode - surface via offline page.
    console.error(`[Django] Failed to start: ${error.message}`);
    djangoProcess = null;
    if (mainWindow) mainWindow.webContents.loadURL(APP_URL).catch(() => {});
  });
  djangoProcess.on('exit', (code) => {
    console.log(`[Django] Server exited with code ${code}`);
    djangoProcess = null;
  });
}

function stopDjangoServer() {
  if (!djangoProcess) return;
  const processToStop = djangoProcess;
  djangoProcess = null;
  if (process.platform === 'win32') {
    spawn('taskkill', ['/pid', String(processToStop.pid), '/f', '/t'], {
      windowsHide: true,
      stdio: 'ignore',
    });
  } else {
    processToStop.kill('SIGTERM');
  }
}

function waitForDjangoReady(url, attempts = 40, delayMs = 500) {
  return new Promise((resolve, reject) => {
    let remaining = attempts;
    const tryRequest = () => {
      const request = http.get(url, (response) => {
        response.resume();
        if (response.statusCode >= 200 && response.statusCode < 500) return resolve();
        retry();
      });
      request.on('error', retry);
      request.setTimeout(2000, () => {
        request.destroy();
        retry();
      });
    };
    const retry = () => {
      remaining -= 1;
      if (remaining <= 0) {
        reject(new Error(`Django did not become ready at ${url}`));
        return;
      }
      setTimeout(tryRequest, delayMs);
    };
    tryRequest();
  });
}

function probeAppOrigin(timeoutMs = 4000) {
  return new Promise((resolve) => {
    const url = new URL(APP_ORIGIN + '/health/live/');
    const transport = url.protocol === 'https:' ? https : http;
    const request = transport.get(
      url,
      { rejectUnauthorized: false, timeout: timeoutMs },
      (response) => {
        response.resume();
        resolve(response.statusCode >= 200 && response.statusCode < 500);
      },
    );
    request.on('error', () => resolve(false));
    request.on('timeout', () => {
      request.destroy();
      resolve(false);
    });
  });
}

// T14: poll the cloud origin while the offline page is shown; reload the
// app automatically once the service answers again.
function startOfflinePolling() {
  if (offlinePollTimer) return;
  offlinePollTimer = setInterval(async () => {
    if (await probeAppOrigin(3000)) {
      clearInterval(offlinePollTimer);
      offlinePollTimer = null;
      connectivityWatchTimer = null;
      if (mainWindow) mainWindow.loadURL(APP_URL).catch(() => startOfflinePolling());
    }
  }, 5000);
}

let connectivityWatchTimer = null;
// T14 review: runtime request failures do not trigger did-fail-load - a
// slow watch probes the origin and flips to the offline page when down.
function startConnectivityWatch() {
  if (!CLOUD_MODE || connectivityWatchTimer) return;
  connectivityWatchTimer = setInterval(async () => {
    if (!offlinePollTimer && mainWindow && !(await probeAppOrigin(3000))) {
      showOfflinePage();
    }
  }, 30000);
}

function isAllowedExternalUrl(rawUrl) {
  try {
    return ['https:', 'http:', 'mailto:'].includes(new URL(rawUrl).protocol);
  } catch {
    return false;
  }
}

function isAppOrigin(rawUrl) {
  return normalizeOrigin(rawUrl) === APP_ORIGIN;
}

function showOfflinePage() {
  if (!mainWindow) return;
  mainWindow.loadFile(path.join(__dirname, 'offline.html'), {
    query: { origin: APP_ORIGIN || '', message: APP_CONFIG.message || '' },
  });
  startOfflinePolling();
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1280,
    height: 800,
    minWidth: 900,
    minHeight: 600,
    title: 'iChat Pro',
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true,
      sandbox: true,
      preload: path.join(__dirname, 'preload.js'),
    },
  });

  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    if (isAllowedExternalUrl(url)) shell.openExternal(url);
    return { action: 'deny' };
  });

  const guardNavigation = (event, url) => {
    if (!isAppOrigin(url)) {
      event.preventDefault();
      if (isAllowedExternalUrl(url)) shell.openExternal(url);
    }
  };
  mainWindow.webContents.on('will-navigate', guardNavigation);
  // T14 review: server redirects (302) bypass will-navigate - guard them too.
  mainWindow.webContents.on('will-redirect', guardNavigation);

  // T14: network failures while loading the cloud origin show the offline
  // page (with automatic recovery) instead of a dead window.
  mainWindow.webContents.on('did-fail-load', (event, code, desc, url, isMainFrame) => {
    if (isMainFrame && CLOUD_MODE && url.startsWith(APP_ORIGIN)) {
      showOfflinePage();
    }
  });

  mainWindow.loadURL(APP_URL).catch(() => {
    if (CLOUD_MODE) showOfflinePage();
  });
  startConnectivityWatch();

  if (IS_DEV) mainWindow.webContents.openDevTools();

  mainWindow.on('closed', () => {
    mainWindow = null;
  });
}

app.whenReady().then(async () => {
  startDjangoServer(); // dev mode only

  if (APP_CONFIG.mode === 'unconfigured') {
    createWindow();
    showOfflinePage();
    return;
  }

  if (DEV_MODE) {
    try {
      await waitForDjangoReady(`${APP_ORIGIN}/login/`);
    } catch (error) {
      createWindow();
      showOfflinePage();
      return;
    }
  } else {
    // Fail fast into the offline page when the service is down at launch.
    const reachable = await probeAppOrigin();
    if (!reachable) {
      createWindow();
      showOfflinePage();
      return;
    }
  }

  createWindow();
  startConnectivityWatch();

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on('before-quit', () => {
  if (offlinePollTimer) clearInterval(offlinePollTimer);
  stopDjangoServer();
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit();
});
