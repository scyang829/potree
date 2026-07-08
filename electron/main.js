const electron = require('electron')
const app = electron.app
const BrowserWindow = electron.BrowserWindow

const { spawn } = require('child_process');
const http = require('http');
const path = require('path');

const REPO_ROOT = path.resolve(__dirname, '..');
const PORT = 8080;
const ENTRY_URL = `http://localhost:${PORT}/examples/lane_digitize.html`;

app.commandLine.appendSwitch('no-sandbox');
app.commandLine.appendSwitch('disable-dev-shm-usage');

let mainWindow;
let backendProcess = null;

function startBackend() {
	backendProcess = spawn('python3', ['webapp/server.py'], { cwd: REPO_ROOT });

	backendProcess.stdout.on('data', (data) => process.stdout.write(`[backend] ${data}`));
	backendProcess.stderr.on('data', (data) => process.stderr.write(`[backend] ${data}`));

	backendProcess.on('error', (err) => {
		console.error('Failed to start backend (it may already be running):', err.message);
	});
}

function waitForServer(url, timeoutMs, callback) {
	const start = Date.now();

	const tryOnce = () => {
		http.get(url, (res) => {
			res.resume();
			callback(null);
		}).on('error', () => {
			if (Date.now() - start > timeoutMs) {
				callback(new Error(`Server did not become ready within ${timeoutMs}ms`));
				return;
			}
			setTimeout(tryOnce, 300);
		});
	};

	tryOnce();
}

function createWindow () {
	mainWindow = new BrowserWindow({
		width: 1600,
		height: 1200,
		title: 'Lane Digitize Tool',
	})

	mainWindow.loadURL(ENTRY_URL);

	mainWindow.on('closed', function () {
		mainWindow = null
	})
}

app.on('ready', () => {
	startBackend();

	waitForServer(ENTRY_URL, 30000, (err) => {
		if (err) {
			console.error(err.message);
		}
		createWindow();
	});
});

app.on('window-all-closed', function () {
	if (backendProcess) {
		backendProcess.kill();
	}
	if (process.platform !== 'darwin') {
		app.quit()
	}
})

app.on('before-quit', () => {
	if (backendProcess) {
		backendProcess.kill();
	}
});

app.on('activate', function () {
	if (mainWindow === null) {
		createWindow()
	}
})
