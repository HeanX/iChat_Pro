const { app, BrowserWindow, shell } = require('electron');
const path = require('path');
const { spawn } = require('child_process');
const http = require('http');

// ---------------------------------------------------------------------------
// Configuration
// ---------------------------------------------------------------------------

const DJANGO_HOST = process.env.ICHAT_HOST || '127.0.0.1';
const DJANGO_PORT = process.env.ICHAT_PORT || '8000';
const DJANGO_URL = `http://${DJANGO_HOST}:${DJANGO_PORT}`;
const DJANGO_READY_TIMEOUT = parseInt(process.env.ICHAT_READY_TIMEOUT || '30', 10);

const ALLOWED_EXTERNAL_PROTOCOLS = new Set(['https:', 'http:', 'mailto:']);

const WINDOW_CONFIG = {
  width: 1280,
  height: 800,
  minWidth: 900,
  minHeight: 600,
  title: 'iChat Pro',
  webPreferences: {
    nodeIntegration: false,
    contextIsolation: true,
    preload: path.join(__dirname, 'preload.js'),
  },
};

// ---------------------------------------------------------------------------
// Python resolution — prefer the project .venv
// ---------------------------------------------------------------------------

function resolvePython() {
  const projectRoot = path.join(__dirname, '..');
  if (process.platform === 'win32') {
    const venvPython = path.join(projectRoot, '.venv', 'Scripts', 'python.exe');
    return venvPython;
  }
  const venvPython = path.join(projectRoot, '.venv', 'bin', 'python');
  return venvPython;
}

// ---------------------------------------------------------------------------
// Django server management
// ---------------------------------------------------------------------------

let djangoProcess = null;

function startDjangoServer() {
  const projectRoot = path.join(__dirname, '..');
  const pythonExe = resolvePython();
  const isWindows = process.platform === 'win32';

  console.log(`[Electron] Using Python: ${pythonExe}`);

  djangoProcess = spawn(
    pythonExe,
    ['manage.py', 'runserver', `${DJANGO_HOST}:${DJANGO_PORT}`],
    {
      cwd: projectRoot,
      stdio: 'pipe',
      shell: isWindows,
    }
  );

  djangoProcess.stdout.on('data', (data) => {
    console.log(`[Django] ${data.toString().trim()}`);
  });

  djangoProcess.stderr.on('data', (data) => {
    console.log(`[Django] ${data.toString().trim()}`);
  });

  djangoProcess.on('error', (err) => {
    console.error('[Django] Failed to start:', err.message);
  });

  djangoProcess.on('exit', (code) => {
    console.log(`[Django] Server exited (code ${code})`);
    djangoProcess = null;
  });

  console.log(`[Electron] Starting Django server at ${DJANGO_URL} ...`);
}

function stopDjangoServer() {
  if (djangoProcess) {
    console.log('[Electron] Stopping Django server ...');
    if (process.platform === 'win32') {
      spawn('taskkill', ['/pid', djangoProcess.pid.toString(), '/f', '/t']);
    } else {
      djangoProcess.kill('SIGTERM');
    }
    djangoProcess = null;
  }
}

// ---------------------------------------------------------------------------
// Django readiness polling
// ---------------------------------------------------------------------------

function waitForDjango(timeoutSeconds = DJANGO_READY_TIMEOUT) {
  const startTime = Date.now();
  const deadline = startTime + timeoutSeconds * 1000;

  return new Promise((resolve, reject) => {
    function poll() {
      if (Date.now() > deadline) {
        return reject(
          new Error(
            `Django did not become ready within ${timeoutSeconds} seconds`
          )
        );
      }

      const req = http.get(`${DJANGO_URL}/login/`, (res) => {
        // Any HTTP response (even 4xx/5xx) means the server is listening
        res.resume();
        console.log(`[Electron] Django ready (HTTP ${res.statusCode})`);
        resolve();
      });

      req.on('error', () => {
        // Server not listening yet — try again soon
        setTimeout(poll, 500);
      });

      req.setTimeout(3000, () => {
        req.destroy();
        setTimeout(poll, 500);
      });
    }

    poll();
  });
}

// ---------------------------------------------------------------------------
// Window management
// ---------------------------------------------------------------------------

let mainWindow = null;

function createWindow() {
  mainWindow = new BrowserWindow(WINDOW_CONFIG);

  mainWindow.loadURL(DJANGO_URL);

  // Only allow safe external protocols
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    try {
      const parsed = new URL(url);
      if (ALLOWED_EXTERNAL_PROTOCOLS.has(parsed.protocol)) {
        shell.openExternal(url);
      } else {
        console.warn(`[Electron] Blocked external URL: ${url}`);
      }
    } catch {
      console.warn(`[Electron] Blocked malformed URL: ${url}`);
    }
    return { action: 'deny' };
  });

  mainWindow.on('closed', () => {
    mainWindow = null;
  });

  if (process.argv.includes('--dev')) {
    mainWindow.webContents.openDevTools();
  }
}

// ---------------------------------------------------------------------------
// App lifecycle
// ---------------------------------------------------------------------------

app.whenReady().then(async () => {
  if (!process.env.ICHAT_SKIP_DJANGO) {
    startDjangoServer();
    try {
      await waitForDjango();
    } catch (err) {
      console.error(`[Electron] ${err.message}`);
      // Still try to open the window — user may start Django manually
    }
  }

  createWindow();

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      createWindow();
    }
  });
});

app.on('window-all-closed', () => {
  stopDjangoServer();
  if (process.platform !== 'darwin') {
    app.quit();
  }
});

app.on('before-quit', () => {
  stopDjangoServer();
});
