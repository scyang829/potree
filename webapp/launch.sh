#!/bin/bash
# Launches the Lane Digitize Tool as an app-like desktop window.
# Starts the backend (if not already running) and opens it in Chrome's
# --app mode (no address bar, its own window/taskbar entry).

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="python3"
PORT=8080
URL="http://localhost:${PORT}/examples/lane_digitize.html"
LOG_FILE="/tmp/lane_digitize_server.log"

if ! curl -s -o /dev/null "$URL"; then
	echo "Starting backend server..."
	cd "$REPO_ROOT" || exit 1
	nohup "$PYTHON_BIN" webapp/server.py > "$LOG_FILE" 2>&1 &

	for i in $(seq 1 60); do
		if curl -s -o /dev/null "$URL"; then
			break
		fi
		sleep 0.5
	done
fi

google-chrome --app="$URL" --window-size=1400,900 --new-window > /dev/null 2>&1 &
