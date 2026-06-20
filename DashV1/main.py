import sys
from pathlib import Path

from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtCore import QUrl

from backend import DashboardBackend
from config import CONFIG
from hardware import HardwareInterface


def main() -> int:
    app = QGuiApplication(sys.argv)
    engine = QQmlApplicationEngine()

    hardware = HardwareInterface(CONFIG)
    backend = DashboardBackend(hardware, CONFIG)
    engine.rootContext().setContextProperty("backend", backend)

    qml_path = Path(__file__).with_name("Dashboard.qml")
    engine.load(QUrl.fromLocalFile(str(qml_path.resolve())))

    if not engine.rootObjects():
        return 1
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
