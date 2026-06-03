const { app, BrowserWindow, dialog, shell } = require('electron');
const fs = require('fs');
const http = require('http');
const path = require('path');
const { spawn } = require('child_process');

const DJANGO_HOST = process.env.ICHAT_HOST || '127.0.0.1';
const DJANGO_PORT = process.env.ICHAT_PORT || '8000';
const DJANGO_ORIGIN = `http://${DJANGO_HOST}:${DJANGO_PORT}`;
const DJANGO_READY_URL = `${DJANGO_ORIGIN}/login/`;
const DJANGO_APP_URL = DJANGO_ORIGIN;
const PROJECT_ROOT = path.resolve(__dirname, '..');
const IS_DEV = process.argv.includes('--dev');

let djangoProcess = null;
let mainWindow = null;

function localPythonCandidates() {
  const candidates = [];
  if (process.env.ICHAT_PYTHON) {
    candidates.push(process.env.ICHAT_PYTHON);
  }
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
    if (path.isAbsolute(candidate) && fs.existsSync(candidate)) {
      return candidate;
    }
    if (!path.isAbsolute(candidate)) {
      return candidate;
    }
  }
  return 'python';
}

function startDjangoServer() {
  const pythonExecutable = resolvePythonExecutable();
  djangoProcess = spawn(
    pythonExecutable,
    ['manage.py', 'runserver', `${DJANGO_HOST}:${DJANGO_PORT}`],
    {
      cwd: PROJECT_ROOT,
      env: {
        ...process.env,
        PYTHONUNBUFFERED: '1',
      },
      stdio: ['ignore', 'pipe', 'pipe'],
      windowsHide: true,
    },
  );

  djangoProcess.stdout.on('data', (data) => {
    console.log(`[Django] ${data.toString().trim()}`);
  });
  djangoProcess.stderr.on('data', (data) => {
    console.error(`[Django] ${data.toString().trim()}`);
  });
  djangoProcess.on('error', (error) => {
    console.error(`[Django] Failed to start: ${error.message}`);
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
        if (response.statusCode >= 200 && response.statusCode < 500) {
          resolve();
          return;
        }
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

function isAllowedExternalUrl(rawUrl) {
  try {
    const parsed = new URL(rawUrl);
    return ['https:', 'http:', 'mailto:'].includes(parsed.protocol);
  } catch {
    return false;
  }
}

function isDjangoOrigin(rawUrl) {
  try {
    return new URL(rawUrl).origin === DJANGO_ORIGIN;
  } catch {
    return false;
  }
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
    if (isAllowedExternalUrl(url)) {
      shell.openExternal(url);
    }
    return { action: 'deny' };
  });

  mainWindow.webContents.on('will-navigate', (event, url) => {
    if (!isDjangoOrigin(url)) {
      event.preventDefault();
      if (isAllowedExternalUrl(url)) {
        shell.openExternal(url);
      }
    }
  });

  mainWindow.loadURL(DJANGO_APP_URL);

  if (IS_DEV) {
    mainWindow.webContents.openDevTools();
  }

  mainWindow.on('closed', () => {
    mainWindow = null;
  });
}

app.whenReady().then(async () => {
  if (process.env.ICHAT_SKIP_DJANGO !== '1') {
    startDjangoServer();
  }

  try {
    await waitForDjangoReady(DJANGO_READY_URL);
  } catch (error) {
    dialog.showErrorBox(
      'iChat Pro backend unavailable',
      `${error.message}\n\nStart Django manually or set ICHAT_HOST / ICHAT_PORT.`,
    );
    app.quit();
    return;
  }

  createWindow();

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      createWindow();
    }
  });
});

app.on('before-quit', stopDjangoServer);

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') {
    app.quit();
  }
});
