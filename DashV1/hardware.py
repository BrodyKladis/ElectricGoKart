import math
import time
import threading
from dataclasses import dataclass, field
from typing import Optional

from config import DashConfig


@dataclass
class SongState:
    title: str = "No Track"
    artist: str = "Unknown"
    album: str = "-"
    duration: float = 180.0
    pos: float = 0.0
    playing: bool = False


@dataclass
class TelemetryState:
    speed_mph: float = 0.0
    voltage_v: float = 0.0
    current_a: float = 0.0
    steer_angle_deg: float = -135.0
    wheel_angle_deg: float = -135.0
    accel_percent: float = 0.0
    regen_percent: float = 0.0
    aura_level: float = 0.0
    song: SongState = field(default_factory=SongState)


class BluetoothMediaMonitor:
    def __init__(self):
        self.available = False
        self.title = "No Track"
        self.artist = "Unknown"
        self.album = "-"
        self.duration = 180.0
        self.pos = 0.0
        self.playing = False

        self._lock = threading.Lock()
        self._loop = None
        self._bus = None

        try:
            from dbus_next.aio import MessageBus
            from dbus_next import BusType
            import asyncio

            self._MessageBus = MessageBus
            self._BusType = BusType
            self._asyncio = asyncio

            self._loop = asyncio.new_event_loop()
            self._thread = threading.Thread(target=self._run_loop, daemon=True)
            self._thread.start()
            self.available = True
            print("BluetoothMediaMonitor started.")
        except Exception as exc:
            print("Bluetooth media monitor unavailable:", exc)

    def _run_loop(self):
        self._asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self._main())

    async def _main(self):
        self._bus = await self._MessageBus(bus_type=self._BusType.SYSTEM).connect()

        introspection = await self._bus.introspect("org.bluez", "/")
        obj = self._bus.get_proxy_object("org.bluez", "/", introspection)
        om = obj.get_interface("org.freedesktop.DBus.ObjectManager")

        await self._scan_players(om)

        om.on_interfaces_added(self._interfaces_added)
        om.on_interfaces_removed(self._interfaces_removed)

        await self._asyncio.Future()

    async def _scan_players(self, om):
        objects = await om.call_get_managed_objects()
        for path, ifaces in objects.items():
            if "org.bluez.MediaPlayer1" in ifaces:
                await self._attach_player(path)

    async def _attach_player(self, path: str):
        try:
            introspection = await self._bus.introspect("org.bluez", path)
            obj = self._bus.get_proxy_object("org.bluez", path, introspection)
            props = obj.get_interface("org.freedesktop.DBus.Properties")

            values = await props.call_get_all("org.bluez.MediaPlayer1")
            self._apply_props(values)

            props.on_properties_changed(self._props_changed)
            print(f"Attached BlueZ media player: {path}")
        except Exception as exc:
            print(f"Failed to attach player {path}: {exc}")

    def _interfaces_added(self, path, interfaces):
        if "org.bluez.MediaPlayer1" in interfaces and self._loop is not None:
            self._asyncio.run_coroutine_threadsafe(self._attach_player(path), self._loop)

    def _interfaces_removed(self, path, interfaces):
        if "org.bluez.MediaPlayer1" in interfaces:
            with self._lock:
                self.title = "No Track"
                self.artist = "Unknown"
                self.album = "-"
                self.duration = 180.0
                self.pos = 0.0
                self.playing = False

    def _props_changed(self, interface_name, changed, invalidated):
        if interface_name == "org.bluez.MediaPlayer1":
            self._apply_props(changed)

    @staticmethod
    def _variant_value(item, default=None):
        try:
            return item.value
        except Exception:
            return default

    def _apply_props(self, props):
        with self._lock:
            status = props.get("Status")
            if status is not None:
                self.playing = str(self._variant_value(status, "")).lower() == "playing"

            position = props.get("Position")
            if position is not None:
                try:
                    # BlueZ Position is typically in milliseconds
                    self.pos = max(0.0, float(self._variant_value(position, 0)) / 1000.0)
                except Exception:
                    pass

            track = props.get("Track")
            if track is not None:
                track_dict = self._variant_value(track, {})

                title = track_dict.get("Title")
                artist = track_dict.get("Artist")
                album = track_dict.get("Album")
                duration = track_dict.get("Duration")

                if title is not None:
                    self.title = str(self._variant_value(title, "No Track"))

                if artist is not None:
                    artist_val = self._variant_value(artist, "Unknown")
                    if isinstance(artist_val, (list, tuple)):
                        self.artist = ", ".join(str(x) for x in artist_val) if artist_val else "Unknown"
                    else:
                        self.artist = str(artist_val)

                if album is not None:
                    self.album = str(self._variant_value(album, "-"))

                if duration is not None:
                    try:
                        # BlueZ Duration is typically in milliseconds
                        self.duration = max(1.0, float(self._variant_value(duration, 180000)) / 1000.0)
                    except Exception:
                        pass

            if self.pos > self.duration:
                self.pos = self.duration

    def get_song_state(self) -> SongState:
        with self._lock:
            return SongState(
                title=self.title,
                artist=self.artist,
                album=self.album,
                duration=self.duration,
                pos=self.pos,
                playing=self.playing,
            )


class HardwareInterface:
    def __init__(self, config: DashConfig):
        self.config = config
        self.use_hardware = config.use_hardware

        self.adc = None
        self.vesc = None
        self.steer_uart = None
        self.steer_uart_ok = False
        self._uart_rx_buf = b""

        self.latest_steer_uart = 16384
        self.latest_wheel_uart = 16384
        self.steer_min = 0
        self.steer_max = 32767
        self.steer_filtered = 16384
        self.motor_filtered = 16384
        self.aura_level = 0.0

        self.mock_steer = 16384
        self.mock_accel = 0
        self.mock_regen = 0
        self.song = SongState()
        self.bt_media = BluetoothMediaMonitor()

        self._init_ads1115()
        self._init_vesc()
        self._init_uart()

    @staticmethod
    def clamp01(value: float) -> float:
        return max(0.0, min(1.0, value))

    def _map_sweep_deg(self, value_norm: float) -> float:
        return -135.0 + 270.0 * self.clamp01(value_norm)

    def _init_ads1115(self) -> None:
        if not self.use_hardware:
            return
        try:
            from Adafruit_ADS1x15 import ADS1115

            self.adc = ADS1115(busnum=1)
            print("ADS1115 initialized successfully.")
        except Exception as exc:
            print("ADS1115 init failed:", exc)
            self.use_hardware = False

    def _init_vesc(self) -> None:
        if not self.use_hardware:
            return
        try:
            from pyvesc import VESC

            self.vesc = VESC(serial_port=self.config.vesc_port)
            print("pyvesc initialized successfully.")
        except Exception as exc:
            print("VESC init failed:", exc)
            self.vesc = None

    def _init_uart(self) -> None:
        if not self.config.use_steer_uart:
            return
        try:
            import serial

            self.steer_uart = serial.Serial(
                self.config.steer_uart_port,
                self.config.steer_uart_baud,
                timeout=0,
            )
            self.steer_uart_ok = True
            print(
                f"Steering UART opened: {self.config.steer_uart_port} @ "
                f"{self.config.steer_uart_baud}"
            )
        except Exception as exc:
            print("Steering UART init failed:", exc)
            self.steer_uart = None
            self.steer_uart_ok = False

    def read_adc(self, channel: Optional[int]) -> int:
        if channel is None:
            return 0
        if self.use_hardware and self.adc is not None:
            try:
                return int(self.adc.read_adc(channel, gain=1))
            except Exception:
                return 0

        if channel == self.config.adc_steer_channel:
            return self.mock_steer
        if channel == self.config.adc_accel_channel:
            return self.mock_accel
        if channel == self.config.adc_regen_channel:
            return self.mock_regen
        return 0

    def poll_steer_uart(self) -> None:
        if not self.steer_uart_ok or self.steer_uart is None:
            return

        try:
            waiting = self.steer_uart.in_waiting
            if waiting <= 0:
                return

            data = self.steer_uart.read(waiting)
            if not data:
                return

            self._uart_rx_buf += data
            if len(self._uart_rx_buf) > 4096:
                self._uart_rx_buf = self._uart_rx_buf[-1024:]

            while b"\n" in self._uart_rx_buf:
                line, self._uart_rx_buf = self._uart_rx_buf.split(b"\n", 1)
                line = line.strip()
                if not line:
                    continue

                if line.startswith(b"S,"):
                    try:
                        val = int(line.split(b",", 1)[1])
                        self.latest_steer_uart = max(0, min(32767, val))
                    except Exception:
                        pass
                elif line.startswith(b"W,"):
                    try:
                        val = int(line.split(b",", 1)[1])
                        self.latest_wheel_uart = max(0, min(32767, val))
                    except Exception:
                        pass
        except Exception:
            pass

    def read_steer_value(self) -> int:
        if self.steer_uart_ok:
            self.poll_steer_uart()
            return self.latest_steer_uart
        return max(0, min(32767, self.read_adc(self.config.adc_steer_channel)))

    def calibrate_steering_uart(self, duration_s: Optional[float] = None) -> None:
        duration_s = self.config.steer_uart_cal_seconds if duration_s is None else duration_s

        if not self.steer_uart_ok:
            self.steer_min, self.steer_max = 0, 32767
            self.steer_filtered = 16384
            self.motor_filtered = 16384
            return

        print(f"\n=== CALIBRATING STEERING (UART) {duration_s:.1f}s ===")
        print("Sweep steering fully LEFT then fully RIGHT now...\n")

        t_end = time.time() + duration_s
        mn = 32767
        mx = 0

        for _ in range(10):
            self.poll_steer_uart()
            time.sleep(0.01)

        while time.time() < t_end:
            self.poll_steer_uart()
            value = self.latest_steer_uart
            mn = min(mn, value)
            mx = max(mx, value)
            time.sleep(0.01)

        if mx - mn < 200:
            print("UART calibration invalid (no sweep detected). Using default 0..32767.\n")
            self.steer_min, self.steer_max = 0, 32767
        else:
            self.steer_min, self.steer_max = mn, mx
            print(f"Steer UART calibration: [{self.steer_min}, {self.steer_max}]\n")

        self.steer_filtered = max(self.steer_min, min(self.steer_max, self.latest_steer_uart))
        self.motor_filtered = max(0, min(32767, self.latest_wheel_uart))

    def update_mock_song(self) -> None:
        if self.bt_media.available:
            self.song = self.bt_media.get_song_state()
            return

        if not self.use_hardware and self.song.playing:
            self.song.pos += self.config.update_interval_ms / 1000.0
            if self.song.pos >= self.song.duration:
                self.song.pos = self.song.duration
                self.song.playing = False

    def read_powertrain(self) -> tuple[float, float, float]:
        if self.use_hardware and self.vesc is not None:
            try:
                values = self.vesc.get_values()
                return values.speed * 2.23694, values.v_in, values.avg_motor_current
            except Exception:
                pass

        now = time.time()
        speed = (math.sin(now) + 1.0) / 2.0 * self.config.max_speed_mph
        voltage = (math.cos(now) + 1.0) / 2.0 * self.config.max_voltage
        return speed, voltage, 0.0

    def step(self) -> TelemetryState:
        raw = self.read_steer_value()
        self.steer_filtered = int(
            self.steer_filtered
            + self.config.steer_smooth_alpha * (raw - self.steer_filtered)
        )

        steer_value = max(self.steer_min, min(self.steer_max, self.steer_filtered))
        span = max(1, self.steer_max - self.steer_min)
        steer_norm = self.clamp01((steer_value - self.steer_min) / span)
        if self.config.invert_steer_gauge:
            steer_norm = 1.0 - steer_norm
        steer_angle_deg = self._map_sweep_deg(steer_norm)

        if self.steer_uart_ok:
            self.poll_steer_uart()

        self.motor_filtered = int(
            self.motor_filtered
            + self.config.motor_smooth_alpha * (self.latest_wheel_uart - self.motor_filtered)
        )
        wheel_norm = self.clamp01(self.motor_filtered / 32767.0)
        if self.config.invert_steer_gauge:
            wheel_norm = 1.0 - wheel_norm
        wheel_angle_deg = self._map_sweep_deg(wheel_norm)

        err = abs(steer_norm - wheel_norm)
        target = self.clamp01(1.0 - err / max(1e-6, self.config.aura_tol_norm)) * self.config.aura_max
        self.aura_level += self.config.aura_alpha * (target - self.aura_level)

        accel_percent = self.clamp01(self.read_adc(self.config.adc_accel_channel) / 32767.0)
        regen_percent = self.clamp01(self.read_adc(self.config.adc_regen_channel) / 32767.0)

        self.update_mock_song()
        speed_mph, voltage_v, current_a = self.read_powertrain()

        return TelemetryState(
            speed_mph=speed_mph,
            voltage_v=voltage_v,
            current_a=current_a,
            steer_angle_deg=steer_angle_deg,
            wheel_angle_deg=wheel_angle_deg,
            accel_percent=accel_percent,
            regen_percent=regen_percent,
            aura_level=self.clamp01(self.aura_level),
            song=self.song,
        )

    def mock_left(self) -> None:
        if not self.use_hardware:
            self.mock_steer = max(0, self.mock_steer - 800)

    def mock_right(self) -> None:
        if not self.use_hardware:
            self.mock_steer = min(32767, self.mock_steer + 800)

    def mock_accel_up(self) -> None:
        if not self.use_hardware:
            self.mock_accel = min(32767, self.mock_accel + 1000)

    def mock_accel_down(self) -> None:
        if not self.use_hardware:
            self.mock_accel = max(0, self.mock_accel - 1000)

    def mock_regen_up(self) -> None:
        if not self.use_hardware:
            self.mock_regen = min(32767, self.mock_regen + 1000)

    def mock_regen_down(self) -> None:
        if not self.use_hardware:
            self.mock_regen = max(0, self.mock_regen - 1000)

    def toggle_playback(self) -> None:
        self.song.playing = not self.song.playing
