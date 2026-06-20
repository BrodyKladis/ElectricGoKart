from PySide6.QtCore import QObject, Property, QTimer, Signal, Slot

from config import DashConfig
from hardware import HardwareInterface


class DashboardBackend(QObject):
    dataChanged = Signal()

    def __init__(self, hardware: HardwareInterface, config: DashConfig):
        super().__init__()
        self.hardware = hardware
        self.config = config

        self._speed = 0.0
        self._voltage = 0.0
        self._current = 0.0
        self._steer_angle = -135.0
        self._wheel_angle = -135.0
        self._accel_percent = 0.0
        self._regen_percent = 0.0
        self._aura = 0.0
        self._song_title = hardware.song.title
        self._song_artist = hardware.song.artist
        self._song_album = hardware.song.album
        self._song_playing = hardware.song.playing
        self._song_progress = 0.0

        self.hardware.calibrate_steering_uart(config.steer_uart_cal_seconds)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self.update_dashboard)
        self._timer.start(config.update_interval_ms)

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

    @Slot()
    def mockLeft(self):
        self.hardware.mock_left()

    @Slot()
    def mockRight(self):
        self.hardware.mock_right()

    @Slot()
    def mockAccelUp(self):
        self.hardware.mock_accel_up()

    @Slot()
    def mockAccelDown(self):
        self.hardware.mock_accel_down()

    @Slot()
    def mockRegenUp(self):
        self.hardware.mock_regen_up()

    @Slot()
    def mockRegenDown(self):
        self.hardware.mock_regen_down()

    @Slot()
    def togglePlayback(self):
        self.hardware.toggle_playback()

    @Slot()
    def update_dashboard(self):
        state = self.hardware.step()

        self._speed = state.speed_mph
        self._voltage = state.voltage_v
        self._current = state.current_a
        self._steer_angle = state.steer_angle_deg
        self._wheel_angle = state.wheel_angle_deg
        self._accel_percent = state.accel_percent
        self._regen_percent = state.regen_percent
        self._aura = state.aura_level
        self._song_title = state.song.title
        self._song_artist = state.song.artist
        self._song_album = state.song.album
        self._song_playing = state.song.playing

        duration = max(1.0, float(state.song.duration))
        pos = max(0.0, min(duration, float(state.song.pos)))
        self._song_progress = pos / duration

        self.dataChanged.emit()
