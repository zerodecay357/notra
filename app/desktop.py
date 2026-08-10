"""Native desktop entrypoint — runs Notra as a windowed app instead of a
browser tab.

This starts the same FastAPI app (app.main:app) via uvicorn in a background
thread, then opens a Qt WebEngine window pointed at it. Closing the window
shuts the server down cleanly, including any in-flight ffmpeg/tectonic
subprocess the pipeline may have started.

This is the entrypoint PyInstaller packages (see notra.spec), not run.sh —
run.sh (plain browser tab) still works unchanged for development.
"""

from __future__ import annotations

import socket
import sys
import threading

import uvicorn

from . import config

APP_TITLE = "Notra"
WINDOW_SIZE = (1280, 860)


def _free_port(preferred: int = 8000) -> int:
    """Preferred port if free, otherwise let the OS pick one."""
    for port in (preferred, 0):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            try:
                probe.bind(("127.0.0.1", port))
            except OSError:
                continue
            return probe.getsockname()[1]
    raise RuntimeError("could not find a free port")


class _ServerThread(threading.Thread):
    """Runs uvicorn in the background and can be told to stop cleanly."""

    def __init__(self, port: int) -> None:
        super().__init__(daemon=True)
        # Pass the app object directly rather than the "app.main:app" import
        # string uvicorn would otherwise resolve at startup — that string
        # form fails inside a PyInstaller-frozen build.
        from .main import app as asgi_app

        config_ = uvicorn.Config(asgi_app, host="127.0.0.1", port=port, log_level="warning")
        self.server = uvicorn.Server(config_)

    def run(self) -> None:
        self.server.run()

    def stop(self) -> None:
        self.server.should_exit = True


def main() -> int:
    # Imported lazily: only needed for the desktop build, not run.sh/tests.
    from PySide6.QtCore import QUrl
    from PySide6.QtGui import QCloseEvent, QDesktopServices
    from PySide6.QtWebEngineCore import QWebEnginePage
    from PySide6.QtWebEngineWidgets import QWebEngineView
    from PySide6.QtWidgets import QApplication, QMainWindow

    port = _free_port()
    server = _ServerThread(port)
    server.start()

    app = QApplication(sys.argv)
    app.setApplicationName(APP_TITLE)

    class Window(QMainWindow):
        def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 (Qt override)
            server.stop()
            server.join(timeout=5)
            event.accept()

    window = Window()
    window.setWindowTitle(APP_TITLE)
    window.resize(*WINDOW_SIZE)

    class Page(QWebEnginePage):
        """Keeps the app itself in this window, but hands anything pointing
        elsewhere (the bug tracker, the API-key consoles) to the user's real
        browser — an embedded view has no tabs, back button or password
        manager, so it is the wrong place to land on an external site."""

        def acceptNavigationRequest(self, url, nav_type, is_main_frame):  # noqa: N802
            if url.host() not in ("127.0.0.1", "localhost"):
                QDesktopServices.openUrl(url)
                return False
            return super().acceptNavigationRequest(url, nav_type, is_main_frame)

        def createWindow(self, _window_type):  # noqa: N802
            # target="_blank" asks for a new window; route it externally too.
            popup = QWebEnginePage(self)
            popup.urlChanged.connect(
                lambda url: (QDesktopServices.openUrl(url), popup.deleteLater())
            )
            return popup

    view = QWebEngineView()
    view.setPage(Page(view))
    view.load(QUrl(f"http://127.0.0.1:{port}"))
    window.setCentralWidget(view)
    window.show()

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
