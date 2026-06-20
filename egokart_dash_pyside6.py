import math
import sys
import time
from pathlib import Path

from PySide6.QtCore import QObject, Property, QTimer, Signal, Slot, QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine

# ==================================================
# CONFIG
# ==================================================
USE_HARDWARE = True  # True for ADS1115 + PyVESC, False for mock mode
MAX_SPEED_MPH = 45
MAX_VOLTAGE = 75

USE_STEER_UART = True
STEER_UART_PORT = "/dev/ttyAMA0"
STEER_UART_BAUD = 115200
STEER_UART_CAL_SECONDS = 4.0

STEER_SMOOTH_ALPHA = 0.25
MOTOR_SMOOTH_ALPHA = 0.18
INVERT_STEER_GAUGE = True

AURA_TOL_NORM = 0.02
AURA_MAX = 1.0
AURA_ALPHA = 0.35

# ==================================================
# HARDWARE INITIALIZATION
# ==================================================
adc = None
adc_channel_steer = None
adc_channel_accel = None
adc_channel_regen = None
vesc = None

steer_uart = None
steer_uart_ok = False
latest_steer_uart = 16384
latest_wheel_uart = 16384
_uart_rx_buf = b""

steer_min = 0
steer_max = 32767
steer_filtered = 16384
motor_filtered = 16384
aura_level = 0.0

if USE_HARDWARE:
    try:
        from Adafruit_ADS1x15 import ADS1115
        adc = ADS1115(busnum=1)
        adc_channel_steer = 1
        adc_channel_accel = 2
        adc_channel_regen = 0
        print("ADS1115 initialized successfully.")
    except Exception as e:
        print("ADS1115 init failed:", e)
        USE_HARDWARE = False

    if USE_HARDWARE:
        try:
            from pyvesc import VESC
            vesc = VESC(serial_port="/dev/ttyUSB0")
            print("pyvesc initialized successfully.")
        except Exception as e:
            print("VESC init failed:", e)
            vesc = None

if USE_STEER_UART:
    try:
        import serial
        steer_uart = serial.Serial(STEER_UART_PORT, STEER_UART_BAUD, timeout=0)
        steer_uart_ok = True
        print(f"Steering UART opened: {STEER_UART_PORT} @ {STEER_UART_BAUD}")
    except Exception as e:
        print("Steering UART init failed:", e)
        steer_uart = None
        steer_uart_ok = False

# ==================================================
# MOCK DATA
# ==================================================
mock_steer = 16384
mock_accel = 0
mock_regen = 0
current_song = {
    "title": "No Track",
    "artist": "Unknown",
    "album": "-",
    "duration": 180.0,
    "pos": 0.0,
    "playing": False,
}


def clamp01(x: float) -> float:
    return 0.0 if x < 0.0 else 1.0 if x > 1.0 else x


def read_adc(channel):
    if USE_HARDWARE and adc:
        try:
            return adc.read_adc(channel, gain=1)
        except Exception:
            return 0
    global mock_steer, mock_accel, mock_regen
    if channel == adc_channel_steer:
        return mock_steer
    if channel == adc_channel_accel:
        return mock_accel
    if channel == adc_channel_regen:
        return mock_regen
    return 0


def poll_steer_uart():
    global _uart_rx_buf, latest_steer_uart, latest_wheel_uart

    if not steer_uart_ok or steer_uart is None:
        return

    try:
        n = steer_uart.in_waiting
        if n <= 0:
            return
        data = steer_uart.read(n)
        if not data:
            return
        _uart_rx_buf += data

        if len(_uart_rx_buf) > 4096:
            _uart_rx_buf = _uart_rx_buf[-1024:]

        while b"\n" in _uart_rx_buf:
            line, _uart_rx_buf = _uart_rx_buf.split(b"\n", 1)
            line = line.strip()
            if not line:
                continue

            if line.startswith(b"S,"):
                try:
                    val = int(line.split(b",", 1)[1])
                    latest_steer_uart = max(0, min(32767, val))
                except Exception:
                    pass
            elif line.startswith(b"W,"):
                try:
                    val = int(line.split(b",", 1)[1])
                    latest_wheel_uart = max(0, min(32767, val))
                except Exception:
                    pass
    except Exception:
        pass


def read_steer_value_0_to_32767():
    if steer_uart_ok:
        poll_steer_uart()
        return latest_steer_uart

    raw = read_adc(adc_channel_steer)
    return max(0, min(32767, raw))


def calibrate_steering_uart(duration_s=4.0):
    global steer_min, steer_max, steer_filtered, motor_filtered

    if not steer_uart_ok:
        steer_min, steer_max = 0, 32767
        steer_filtered = 16384
        motor_filtered = 16384
        return

    print(f"\n=== CALIBRATING STEERING (UART) {duration_s:.1f}s ===")
    print("Sweep steering fully LEFT then fully RIGHT now...\n")

    t_end = time.time() + duration_s
    mn = 32767
    mx = 0

    for _ in range(10):
        poll_steer_uart()
        time.sleep(0.01)

    while time.time() < t_end:
        poll_steer_uart()
        v = latest_steer_uart
        mn = min(mn, v)
        mx = max(mx, v)
        time.sleep(0.01)

    if mx - mn < 200:
        print("UART calibration invalid (no sweep detected). Using default 0..32767.\n")
        steer_min, steer_max = 0, 32767
    else:
        steer_min, steer_max = mn, mx
        print(f"Steer UART calibration: [{steer_min}, {steer_max}]\n")

    steer_filtered = max(steer_min, min(steer_max, latest_steer_uart))
    motor_filtered = max(0, min(32767, latest_wheel_uart))


class DashboardBackend(QObject):
    dataChanged = Signal()

    def __init__(self):
        super().__init__()
        self._speed = 0.0
        self._voltage = 0.0
        self._current = 0.0
        self._steer_angle = -135.0
        self._wheel_angle = -135.0
        self._accel_percent = 0.0
        self._regen_percent = 0.0
        self._aura = 0.0
        self._song_title = current_song["title"]
        self._song_artist = current_song["artist"]
        self._song_album = current_song["album"]
        self._song_playing = current_song["playing"]
        self._song_progress = 0.0

        calibrate_steering_uart(STEER_UART_CAL_SECONDS)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self.update_dashboard)
        self._timer.start(50)

    @Property(float, notify=dataChanged)
    def speed(self):
        return self._speed

    @Property(float, notify=dataChanged)
    def voltage(self):
        return self._voltage

    @Property(float, notify=dataChanged)
    def currentDraw(self):
        return self._current

    @Property(float, notify=dataChanged)
    def steerAngle(self):
        return self._steer_angle

    @Property(float, notify=dataChanged)
    def wheelAngle(self):
        return self._wheel_angle

    @Property(float, notify=dataChanged)
    def accelPercent(self):
        return self._accel_percent

    @Property(float, notify=dataChanged)
    def regenPercent(self):
        return self._regen_percent

    @Property(float, notify=dataChanged)
    def auraLevel(self):
        return self._aura

    @Property(str, notify=dataChanged)
    def songTitle(self):
        return self._song_title

    @Property(str, notify=dataChanged)
    def songArtist(self):
        return self._song_artist

    @Property(str, notify=dataChanged)
    def songAlbum(self):
        return self._song_album

    @Property(bool, notify=dataChanged)
    def songPlaying(self):
        return self._song_playing

    @Property(float, notify=dataChanged)
    def songProgress(self):
        return self._song_progress

    def _map_sweep_deg(self, value_norm: float) -> float:
        return -135.0 + 270.0 * clamp01(value_norm)

    @Slot()
    def mockLeft(self):
        global mock_steer
        if not USE_HARDWARE:
            mock_steer = max(0, mock_steer - 800)

    @Slot()
    def mockRight(self):
        global mock_steer
        if not USE_HARDWARE:
            mock_steer = min(32767, mock_steer + 800)

    @Slot()
    def mockAccelUp(self):
        global mock_accel
        if not USE_HARDWARE:
            mock_accel = min(32767, mock_accel + 1000)

    @Slot()
    def mockAccelDown(self):
        global mock_accel
        if not USE_HARDWARE:
            mock_accel = max(0, mock_accel - 1000)

    @Slot()
    def mockRegenUp(self):
        global mock_regen
        if not USE_HARDWARE:
            mock_regen = min(32767, mock_regen + 1000)

    @Slot()
    def mockRegenDown(self):
        global mock_regen
        if not USE_HARDWARE:
            mock_regen = max(0, mock_regen - 1000)

    @Slot()
    def togglePlayback(self):
        current_song["playing"] = not current_song.get("playing", False)

    def update_dashboard(self):
        global steer_filtered, motor_filtered, aura_level
        global current_song

        raw = read_steer_value_0_to_32767()
        steer_filtered = int(steer_filtered + STEER_SMOOTH_ALPHA * (raw - steer_filtered))

        v = max(steer_min, min(steer_max, steer_filtered))
        span = max(1, (steer_max - steer_min))
        norm = clamp01((v - steer_min) / span)
        if INVERT_STEER_GAUGE:
            norm = 1.0 - norm
        self._steer_angle = self._map_sweep_deg(norm)

        if steer_uart_ok:
            poll_steer_uart()

        motor_filtered = int(motor_filtered + MOTOR_SMOOTH_ALPHA * (latest_wheel_uart - motor_filtered))
        wheel_norm = clamp01(motor_filtered / 32767.0)
        if INVERT_STEER_GAUGE:
            wheel_norm = 1.0 - wheel_norm
        self._wheel_angle = self._map_sweep_deg(wheel_norm)

        err = abs(norm - wheel_norm)
        target = clamp01(1.0 - (err / max(1e-6, AURA_TOL_NORM))) * AURA_MAX
        aura_level = aura_level + AURA_ALPHA * (target - aura_level)
        self._aura = clamp01(aura_level)

        raw_acc = read_adc(adc_channel_accel)
        raw_reg = read_adc(adc_channel_regen)
        self._accel_percent = clamp01(raw_acc / 32767.0)
        self._regen_percent = clamp01(raw_reg / 32767.0)

        if not USE_HARDWARE and current_song.get("playing"):
            current_song["pos"] += 0.05
            if current_song["pos"] >= current_song["duration"]:
                current_song["pos"] = current_song["duration"]
                current_song["playing"] = False

        self._song_title = current_song.get("title", "-")
        self._song_artist = current_song.get("artist", "-")
        self._song_album = current_song.get("album", "-")
        self._song_playing = current_song.get("playing", False)
        duration = max(1.0, float(current_song.get("duration", 1.0)))
        pos = max(0.0, min(duration, float(current_song.get("pos", 0.0))))
        self._song_progress = pos / duration

        if USE_HARDWARE and vesc:
            try:
                values = vesc.get_values()
                self._speed = values.speed * 2.23694
                self._voltage = values.v_in
                self._current = values.avg_motor_current
            except Exception:
                self._speed = (math.sin(time.time()) + 1) / 2 * MAX_SPEED_MPH
                self._voltage = (math.cos(time.time()) + 1) / 2 * MAX_VOLTAGE
                self._current = 0.0
        else:
            self._speed = (math.sin(time.time()) + 1) / 2 * MAX_SPEED_MPH
            self._voltage = (math.cos(time.time()) + 1) / 2 * MAX_VOLTAGE
            self._current = 0.0

        self.dataChanged.emit()


def main():
    app = QGuiApplication(sys.argv)
    engine = QQmlApplicationEngine()

    backend = DashboardBackend()
    engine.rootContext().setContextProperty("backend", backend)

    qml_path = Path(__file__).with_name("Dashboard.qml")
    engine.load(QUrl.fromLocalFile(str(qml_path.resolve())))

    if not engine.rootObjects():
        sys.exit(1)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
