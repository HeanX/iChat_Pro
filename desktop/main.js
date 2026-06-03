const { app, BrowserWindow, shell } = require('electron');
const path = require('path');
const { spawn } = require('child_process');

// ---------------------------------------------------------------------------
// Configuration
// ---------------------------------------------------------------------------

const DJANGO_HOST = process.env.ICHAT_HOST || '127.0.0.1';
const DJANGO_PORT = process.env.ICHAT_PORT || '8000';
const DJANGO_URL = `http://${DJANGO_HOST}:${DJANGO_PORT}`;

const WINDOW_CONFIG = {
  width: 1280,
  height: 800,
  minWidth: 900,
  minHeight: 600,
  title: 'iChat Pro',
  icon: path.join(__dirname, '..', 'static', 'images', 'icon.png'),
  webPreferences: {
    nodeIntegration: false,
    contextIsolation: true,
    preload: path.join(__dirname, 'preload.js'),
  },
};

// ---------------------------------------------------------------------------
// Django server management
// ---------------------------------------------------------------------------

let djangoProcess = null;

function startDjangoServer() {
  const projectRoot = path.join(__dirname, '..');
  const isWindows = process.platform === 'win32';

  djangoProcess = spawn(
    'python',
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
// Window management
// ---------------------------------------------------------------------------

let mainWindow = null;

function createWindow() {
  mainWindow = new BrowserWindow(WINDOW_CONFIG);

  // Load the Django app
  mainWindow.loadURL(DJANGO_URL);

  // Open external links (if any) in the system browser
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url);
    return { action: 'deny' };
  });

  mainWindow.on('closed', () => {
    mainWindow = null;
  });

  // Show devtools in dev mode
  if (process.argv.includes('--dev')) {
    mainWindow.webContents.openDevTools();
  }
}

// ---------------------------------------------------------------------------
// App lifecycle
// ---------------------------------------------------------------------------

app.whenReady().then(async () => {
  // Start Django backend (skip if the user already has it running)
  if (!process.env.ICHAT_SKIP_DJANGO) {
    startDjangoServer();
    // Give Django a moment to start before loading the window
    await new Promise((resolve) => setTimeout(resolve, 2000));
  }

  createWindow();

  app.on('activate', () => {
    // macOS: re-create window when dock icon is clicked
    if (BrowserWindow.getAllWindows().length === 0) {
      createWindow();
    }
  });
});

app.on('window-all-closed', () => {
  stopDjangoServer();
  // On macOS, apps typically stay active until Cmd+Q
  if (process.platform !== 'darwin') {
    app.quit();
  }
});

app.on('before-quit', () => {
  stopDjangoServer();
});
