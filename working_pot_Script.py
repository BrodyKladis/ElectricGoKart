
import tkinter as tk
import math
import time

# ==================================================
# CONFIG
# ==================================================
USE_HARDWARE = True  # True for ADS1115 + PyVESC, False for mock mode
MAX_SPEED_MPH = 45
MAX_VOLTAGE = 75

# Steering UART (Pico -> Pi GPIO UART)
USE_STEER_UART = True
STEER_UART_PORT = "/dev/ttyAMA0"   # <-- GPIO header UART (Pi 5: NOT /dev/serial0)
STEER_UART_BAUD = 115200

# Steering calibration time (sweep full left/right during this time)
STEER_UART_CAL_SECONDS = 4.0

# Optional smoothing (for POT/command line)
STEER_SMOOTH_ALPHA = 0.25  # 0..1 (higher = less smoothing)

# Optional smoothing (for MOTOR/wheel line)
MOTOR_SMOOTH_ALPHA = 0.18  # 0..1

# Flip the steering gauge direction
INVERT_STEER_GAUGE = True

# --- Aura (alignment glow) ---
AURA_TOL_NORM = 0.02   # tolerance in normalized units (0..1). 0.02 ≈ 2% of sweep
AURA_MAX = 1.0         # max intensity
AURA_ALPHA = 0.35      # smoothing for fade in/out (0..1)

# ==================================================
# HARDWARE INITIALIZATION
# ==================================================
adc = None
adc_channel_steer = None
adc_channel_accel = None
adc_channel_regen = None
vesc = None

# UART steering state
steer_uart = None
steer_uart_ok = False
latest_steer_uart = 16384  # S,0..32767 (pot input)
latest_wheel_uart = 16384  # W,0..32767 (wheel/motor position estimate)
_uart_rx_buf = b""

# Steering calibration bounds (set by UART calibration step)
steer_min = 0
steer_max = 32767

# filtered values for gauge
steer_filtered = 16384
motor_filtered = 16384

# aura state
aura_level = 0.0

if USE_HARDWARE:
    # Attempt ADS1115 import
    try:
        from Adafruit_ADS1x15 import ADS1115
        adc = ADS1115(busnum=1)
        adc_channel_steer = 1   # only used if UART is not available
        adc_channel_accel = 2
        adc_channel_regen = 0
        print("ADS1115 initialized successfully.")
    except Exception as e:
        print("ADS1115 init failed:", e)
        USE_HARDWARE = False

    # Attempt pyvesc import
    if USE_HARDWARE:
        try:
            from pyvesc import VESC
            vesc = VESC(serial_port="/dev/ttyUSB0")
            print("pyvesc initialized successfully.")
        except Exception as e:
            print("VESC init failed:", e)
            vesc = None

# Attempt steering UART init (works in hardware or mock mode)
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
# MOCK DATA (used when not using hardware)
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

def read_adc(channel):
    """Read ADC value from hardware or mock."""
    if USE_HARDWARE and adc:
        try:
            return adc.read_adc(channel, gain=1)
        except Exception:
            return 0
    else:
        global mock_steer, mock_accel, mock_regen
        if channel == adc_channel_steer:
            return mock_steer
        if channel == adc_channel_accel:
            return mock_accel
        if channel == adc_channel_regen:
            return mock_regen
        return 0

# ==================================================
# STEERING UART PARSER
# ==================================================
def poll_steer_uart():
    """
    Non-blocking UART read.
    Expected lines:
      "S,<0..32767>\n"  (pot/command)
      "W,<0..32767>\n"  (wheel/motor estimated position)
    """
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

        # prevent runaway buffer if noise
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
                    val = max(0, min(32767, val))
                    latest_steer_uart = val
                except Exception:
                    pass

            elif line.startswith(b"W,"):
                try:
                    val = int(line.split(b",", 1)[1])
                    val = max(0, min(32767, val))
                    latest_wheel_uart = val
                except Exception:
                    pass
    except Exception:
        pass

def read_steer_value_0_to_32767():
    """
    Priority:
      1) UART steering (Pico)
      2) ADS1115 steering channel
      3) Mock steering
    Returns 0..32767
    """
    if steer_uart_ok:
        poll_steer_uart()
        return latest_steer_uart

    raw = read_adc(adc_channel_steer)
    if raw < 0:
        raw = 0
    elif raw > 32767:
        raw = 32767
    return raw

# ==================================================
# STEERING UART CALIBRATION
# ==================================================
def calibrate_steering_uart(duration_s=4.0):
    """
    Sweep wheel/pot fully left-right during duration.
    Captures observed S-min/max and uses those for normalization.
    """
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

    # prime a few reads
    for _ in range(10):
        poll_steer_uart()
        time.sleep(0.01)

    while time.time() < t_end:
        poll_steer_uart()
        v = latest_steer_uart
        if v < mn:
            mn = v
        if v > mx:
            mx = v
        time.sleep(0.01)

    if mx - mn < 200:
        print("UART calibration invalid (no sweep detected). Using default 0..32767.\n")
        steer_min, steer_max = 0, 32767
    else:
        steer_min, steer_max = mn, mx
        print(f"Steer UART calibration: [{steer_min}, {steer_max}]\n")

    steer_filtered = max(steer_min, min(steer_max, latest_steer_uart))
    motor_filtered = max(0, min(32767, latest_wheel_uart))

# Run UART calibration at startup if UART is enabled
calibrate_steering_uart(STEER_UART_CAL_SECONDS)

# ==================================================
# WINDOW SETUP
# ==================================================
WINDOW_WIDTH = 1200
WINDOW_HEIGHT = 675
root = tk.Tk()
root.title("Dash V1")
root.geometry(f"{WINDOW_WIDTH}x{WINDOW_HEIGHT}")
root.attributes('-fullscreen', True)

def exit_fullscreen(event=None):
    root.attributes("-fullscreen", False)

def toggle_fullscreen(event=None):
    root.attributes("-fullscreen", not root.attributes("-fullscreen"))

def quit_app(event=None):
    root.destroy()

root.bind("<Escape>", exit_fullscreen)
root.bind("<F11>", toggle_fullscreen)
root.bind("<Control-q>", quit_app)

# ==================================================
# FRAME LAYOUT
# ==================================================
left_frame = tk.Frame(root, width=WINDOW_WIDTH//3, height=WINDOW_HEIGHT, bg="white")
left_frame.pack(side="left", fill="both", expand=True)

right_frame = tk.Frame(root, width=2*WINDOW_WIDTH//3, height=WINDOW_HEIGHT, bg="lightgray")
right_frame.pack(side="right", fill="both", expand=True)

gauge_frame = tk.Frame(right_frame, width=2*WINDOW_WIDTH//3, height=WINDOW_HEIGHT//2, bg="gray90")
gauge_frame.pack(side="top", fill="both", expand=True)

blank_frame = tk.Frame(right_frame, width=2*WINDOW_WIDTH//3, height=WINDOW_HEIGHT//2, bg="gray80")
blank_frame.pack(side="bottom", fill="both", expand=True)

# ==================================================
# STEERING GAUGE (LEFT FRAME)
# ==================================================
STEER_CANVAS_SIZE = 400
STEER_CENTER = STEER_CANVAS_SIZE // 2
STEER_RADIUS = 150

steer_area_label = tk.Label(left_frame, text="Steering Dynamics", font=("Courier", 16, "bold"), bg="white")
steer_area_label.pack(pady=(6,2))

steer_canvas = tk.Canvas(left_frame, width=STEER_CANVAS_SIZE, height=STEER_CANVAS_SIZE, bg="white")
steer_canvas.pack(expand=True, pady=(2, 2))

# Base circle
steer_canvas.create_oval(
    STEER_CENTER - STEER_RADIUS, STEER_CENTER - STEER_RADIUS,
    STEER_CENTER + STEER_RADIUS, STEER_CENTER + STEER_RADIUS,
    width=2
)

def clamp01(x):
    return 0.0 if x < 0.0 else 1.0 if x > 1.0 else x

def blend_hex(rgb_a, rgb_b, t):
    """Blend two RGB tuples (0..255) with t in 0..1. Returns '#RRGGBB'."""
    t = clamp01(t)
    r = int(rgb_a[0] + (rgb_b[0] - rgb_a[0]) * t)
    g = int(rgb_a[1] + (rgb_b[1] - rgb_a[1]) * t)
    b = int(rgb_a[2] + (rgb_b[2] - rgb_a[2]) * t)
    return f"#{r:02x}{g:02x}{b:02x}"

# --- Green aura rings (start invisible) ---
AURA_PAD_1 = 14
AURA_PAD_2 = 24
AURA_PAD_3 = 36

aura_ring_1 = steer_canvas.create_oval(
    STEER_CENTER - (STEER_RADIUS + AURA_PAD_1), STEER_CENTER - (STEER_RADIUS + AURA_PAD_1),
    STEER_CENTER + (STEER_RADIUS + AURA_PAD_1), STEER_CENTER + (STEER_RADIUS + AURA_PAD_1),
    width=10, outline="#ffffff"
)
aura_ring_2 = steer_canvas.create_oval(
    STEER_CENTER - (STEER_RADIUS + AURA_PAD_2), STEER_CENTER - (STEER_RADIUS + AURA_PAD_2),
    STEER_CENTER + (STEER_RADIUS + AURA_PAD_2), STEER_CENTER + (STEER_RADIUS + AURA_PAD_2),
    width=14, outline="#ffffff"
)
aura_ring_3 = steer_canvas.create_oval(
    STEER_CENTER - (STEER_RADIUS + AURA_PAD_3), STEER_CENTER - (STEER_RADIUS + AURA_PAD_3),
    STEER_CENTER + (STEER_RADIUS + AURA_PAD_3), STEER_CENTER + (STEER_RADIUS + AURA_PAD_3),
    width=18, outline="#ffffff"
)

# keep aura behind everything
steer_canvas.tag_lower(aura_ring_1)
steer_canvas.tag_lower(aura_ring_2)
steer_canvas.tag_lower(aura_ring_3)

# ---- motor chase line (blue) ----
motor_line = steer_canvas.create_line(
    STEER_CENTER, STEER_CENTER,
    STEER_CENTER, STEER_CENTER - STEER_RADIUS,
    width=3,
    fill="blue"
)

# ---- pot/command line (red) ----
steer_line = steer_canvas.create_line(
    STEER_CENTER, STEER_CENTER,
    STEER_CENTER, STEER_CENTER - STEER_RADIUS,
    width=3,
    fill="red"
)

min_tick = steer_canvas.create_line(0, 0, 0, 0, width=2, fill="black")
max_tick = steer_canvas.create_line(0, 0, 0, 0, width=2, fill="black")

# Keyboard mock control
def on_key(event):
    global mock_steer, mock_accel, mock_regen, current_song
    steer_step = 800
    accel_step = 1000
    if event.keysym == "Left":
        mock_steer = max(0, mock_steer - steer_step)
    elif event.keysym == "Right":
        mock_steer = min(32767, mock_steer + steer_step)
    elif event.keysym == "Up":
        mock_accel = min(32767, mock_accel + accel_step)
    elif event.keysym == "Down":
        mock_accel = max(0, mock_accel - accel_step)
    elif event.keysym.lower() == 'r':
        mock_regen = min(32767, mock_regen + accel_step)
    elif event.keysym.lower() == 'f':
        mock_regen = max(0, mock_regen - accel_step)
    elif event.keysym == 'space':
        current_song['playing'] = not current_song['playing']

if not USE_HARDWARE:
    root.bind_all("<Key>", on_key)

powertrain_label = tk.Label(left_frame, text="Powertrain Properties", font=("Courier", 14, "bold"), bg="white")
powertrain_label.pack(pady=(2,2))

ACCEL_HEIGHT = 48
accel_canvas = tk.Canvas(left_frame, width=STEER_CANVAS_SIZE, height=ACCEL_HEIGHT+8, bg="white", highlightthickness=0)
accel_canvas.pack()
accel_canvas.create_rectangle(10, 4, STEER_CANVAS_SIZE-10, 4+ACCEL_HEIGHT, outline="black", width=2)
accel_fill = accel_canvas.create_rectangle(12, 6, 12, 6+ACCEL_HEIGHT-4, fill="green", width=0)
accel_label = tk.Label(left_frame, text="Accel: 0%", font=("Arial", 14), bg="white")
accel_label.pack(pady=(4,10))

REGEN_HEIGHT = 48
regen_canvas = tk.Canvas(left_frame, width=STEER_CANVAS_SIZE, height=REGEN_HEIGHT+8, bg="white", highlightthickness=0)
regen_canvas.pack()
regen_canvas.create_rectangle(10, 4, STEER_CANVAS_SIZE-10, 4+REGEN_HEIGHT, outline="black", width=2)
regen_fill = regen_canvas.create_rectangle(12, 6, 12, 6+REGEN_HEIGHT-4, fill="orange", width=0)
regen_label = tk.Label(left_frame, text="Regen: 0%", font=("Arial", 14), bg="white")
regen_label.pack(pady=(4,10))

# ==================================================
# MEDIA PLAYER
# ==================================================
media_frame = tk.Frame(blank_frame, bg="gray85")
media_frame.pack(fill="both", expand=True, padx=12, pady=12)

media_area_label = tk.Label(media_frame, text="Media player", font=("Courier", 16, "bold"), bg="gray85")
media_area_label.pack(anchor="w", pady=(0,6))

song_title_label = tk.Label(media_frame, text="Title: -", font=("Arial", 18), bg="gray85")
song_title_label.pack(anchor="w")
artist_label = tk.Label(media_frame, text="Artist: -", font=("Arial", 14), bg="gray85")
artist_label.pack(anchor="w")
album_label = tk.Label(media_frame, text="Album: -", font=("Arial", 14), bg="gray85")
album_label.pack(anchor="w")

PROG_WIDTH = max(200, (2 * WINDOW_WIDTH) // 3 - 80)
PROG_HEIGHT = 24
progress_canvas = tk.Canvas(media_frame, width=PROG_WIDTH, height=PROG_HEIGHT, bg="gray85", highlightthickness=0)
progress_canvas.pack(pady=10)
progress_canvas.create_rectangle(8, 6, PROG_WIDTH-8, PROG_HEIGHT-6, outline="black", width=2)
progress_fill = progress_canvas.create_rectangle(10, 8, 10, PROG_HEIGHT-8, fill="blue", width=0)
progress_dot = progress_canvas.create_oval(6, 4, 18, 16, fill="white", outline="black", width=2)

play_label = tk.Label(media_frame, text="Paused", font=("Arial", 12), bg="gray85")
play_label.pack(anchor="w")

# ==================================================
# GAUGE CLUSTER
# ==================================================
GAUGE_SIZE = 200
CENTER = GAUGE_SIZE // 2
RADIUS = GAUGE_SIZE // 2 - 10

gauge_header_label = tk.Label(gauge_frame, text="Gauge Cluster", font=("Courier", 16, "bold"), bg="gray90")
gauge_header_label.grid(row=0, column=0, columnspan=3, pady=(6,4))

speed_canvas = tk.Canvas(gauge_frame, width=GAUGE_SIZE, height=GAUGE_SIZE, bg="white")
speed_canvas.grid(row=1, column=0, padx=20, pady=10)
speed_label = tk.Label(gauge_frame, text="Speed: 0 mph", font=("Courier", 20), bg="gray90", width=20, anchor="center")
speed_label.grid(row=2, column=0, padx=20, pady=5)

voltage_canvas = tk.Canvas(gauge_frame, width=GAUGE_SIZE, height=GAUGE_SIZE, bg="white")
voltage_canvas.grid(row=1, column=1, padx=20, pady=10)
voltage_label = tk.Label(gauge_frame, text="Voltage: 0 V", font=("Courier", 20), bg="gray90", width=20, anchor="center")
voltage_label.grid(row=2, column=1, padx=20, pady=5)

current_label = tk.Label(gauge_frame, text="Current Draw: 0 A", font=("Courier", 20), bg="gray90", width=20, anchor="center")
current_label.grid(row=1, column=2, padx=20, pady=20)

for canvas in [speed_canvas, voltage_canvas]:
    canvas.create_oval(10, 10, GAUGE_SIZE-10, GAUGE_SIZE-10, width=3)

def draw_ticks(canvas, radius, center, tick_count=10, tick_length=10):
    for i in range(tick_count + 1):
        norm = i / tick_count
        angle = -math.radians(135) + norm * math.radians(270)
        angle -= math.pi/2
        x_outer = center + radius * math.cos(angle)
        y_outer = center + radius * math.sin(angle)
        x_inner = center + (radius - tick_length) * math.cos(angle)
        y_inner = center + (radius - tick_length) * math.sin(angle)
        canvas.create_line(x_inner, y_inner, x_outer, y_outer, width=2, fill="black")

draw_ticks(speed_canvas, RADIUS, CENTER, tick_count=16, tick_length=12)
draw_ticks(voltage_canvas, RADIUS, CENTER, tick_count=16, tick_length=12)

for canvas in [speed_canvas, voltage_canvas]:
    for norm in [0.0, 1.0]:
        angle = -math.radians(135) + norm * math.radians(270)
        angle -= math.pi/2
        x_outer = CENTER + RADIUS * math.cos(angle)
        y_outer = CENTER + RADIUS * math.sin(angle)
        x_inner = CENTER + (RADIUS - 15) * math.cos(angle)
        y_inner = CENTER + (RADIUS - 15) * math.sin(angle)
        canvas.create_line(x_inner, y_inner, x_outer, y_outer, width=3, fill="black")

speed_line = speed_canvas.create_line(CENTER, CENTER, CENTER, 20, width=3, fill="red")
voltage_line = voltage_canvas.create_line(CENTER, CENTER, CENTER, 20, width=3, fill="blue")

def map_angle_ccw(value, max_value):
    value = max(0, min(value, max_value))
    sweep = math.radians(270)
    base = -math.radians(135)
    angle = base + (value / max_value) * sweep
    angle -= math.pi/2
    return angle

# ==================================================
# UPDATE LOOP
# ==================================================
speed = 0.0
voltage = 0.0
current_draw = 0

def update_dashboard():
    global speed, voltage, current_draw, current_song, steer_filtered, motor_filtered, aura_level

    # ---------- Steering gauge ----------
    raw = read_steer_value_0_to_32767()
    steer_filtered = int(steer_filtered + STEER_SMOOTH_ALPHA * (raw - steer_filtered))

    # Clamp to calibrated steering bounds
    v = max(steer_min, min(steer_max, steer_filtered))

    # Normalize using calibration limits so gauge reaches ends
    span = max(1, (steer_max - steer_min))
    norm = (v - steer_min) / span
    norm = max(0.0, min(1.0, norm))

    # invert gauge direction if requested
    if INVERT_STEER_GAUGE:
        norm = 1.0 - norm

    # Map to 270° sweep
    sweep_angle = math.radians(270)
    base_angle = -math.radians(135)
    angle = base_angle + norm * sweep_angle
    angle -= math.pi/2

    x = STEER_CENTER + STEER_RADIUS * math.cos(angle)
    y = STEER_CENTER + STEER_RADIUS * math.sin(angle)
    steer_canvas.coords(steer_line, STEER_CENTER, STEER_CENTER, x, y)

    # ---------- Motor (wheel position) line ----------
    if steer_uart_ok:
        poll_steer_uart()

    motor_filtered = int(motor_filtered + MOTOR_SMOOTH_ALPHA * (latest_wheel_uart - motor_filtered))
    wheel_norm = motor_filtered / 32767.0
    wheel_norm = max(0.0, min(1.0, wheel_norm))

    if INVERT_STEER_GAUGE:
        wheel_norm = 1.0 - wheel_norm

    wheel_angle = base_angle + wheel_norm * sweep_angle
    wheel_angle -= math.pi/2

    mx = STEER_CENTER + STEER_RADIUS * math.cos(wheel_angle)
    my = STEER_CENTER + STEER_RADIUS * math.sin(wheel_angle)
    steer_canvas.coords(motor_line, STEER_CENTER, STEER_CENTER, mx, my)

    # ---------- Aura: fade in when aligned ----------
    err = abs(norm - wheel_norm)  # 0..1
    target = 1.0 - (err / max(1e-6, AURA_TOL_NORM))
    target = clamp01(target) * AURA_MAX

    aura_level = aura_level + AURA_ALPHA * (target - aura_level)
    lvl = clamp01(aura_level)

    white = (255, 255, 255)
    green = (0, 255, 90)

    c1 = blend_hex(white, green, lvl * 0.35)
    c2 = blend_hex(white, green, lvl * 0.55)
    c3 = blend_hex(white, green, lvl * 0.80)

    steer_canvas.itemconfigure(aura_ring_1, outline=c1)
    steer_canvas.itemconfigure(aura_ring_2, outline=c2)
    steer_canvas.itemconfigure(aura_ring_3, outline=c3)

    # Gauge bound ticks
    tick_length = 10
    min_angle = base_angle - math.pi/2
    x_outer = STEER_CENTER + STEER_RADIUS * math.cos(min_angle)
    y_outer = STEER_CENTER + STEER_RADIUS * math.sin(min_angle)
    x_inner = STEER_CENTER + (STEER_RADIUS - tick_length) * math.cos(min_angle)
    y_inner = STEER_CENTER + (STEER_RADIUS - tick_length) * math.sin(min_angle)
    steer_canvas.coords(min_tick, x_inner, y_inner, x_outer, y_outer)

    max_angle = base_angle + sweep_angle - math.pi/2
    x_outer = STEER_CENTER + STEER_RADIUS * math.cos(max_angle)
    y_outer = STEER_CENTER + STEER_RADIUS * math.sin(max_angle)
    x_inner = STEER_CENTER + (STEER_RADIUS - tick_length) * math.cos(max_angle)
    y_inner = STEER_CENTER + (STEER_RADIUS - tick_length) * math.sin(max_angle)
    steer_canvas.coords(max_tick, x_inner, y_inner, x_outer, y_outer)

    # ---------- Acceleration bar ----------
    raw_acc = read_adc(adc_channel_accel)
    a_norm = raw_acc / 32767.0
    a_norm = max(0.0, min(1.0, a_norm))
    total_width = STEER_CANVAS_SIZE - 24
    fill_w = max(2, int(total_width * a_norm))
    accel_canvas.coords(accel_fill, 12, 6, 12 + fill_w, 6 + ACCEL_HEIGHT - 4)
    accel_label.config(text=f"Acceleration: {a_norm*100:.0f}%")

    # ---------- Regen bar ----------
    raw_reg = read_adc(adc_channel_regen)
    r_norm = raw_reg / 32767.0
    r_norm = max(0.0, min(1.0, r_norm))
    regen_fill_w = max(2, int(total_width * r_norm))
    regen_canvas.coords(regen_fill, 12, 6, 12 + regen_fill_w, 6 + REGEN_HEIGHT - 4)
    regen_label.config(text=f"Regen: {r_norm*100:.0f}%")

    # ---------- Media player update ----------
    try:
        if not USE_HARDWARE:
            if current_song.get('playing'):
                current_song['pos'] += 0.05
                if current_song['pos'] >= current_song['duration']:
                    current_song['pos'] = current_song['duration']
                    current_song['playing'] = False
    except Exception:
        pass

    try:
        song_title_label.config(text=f"Title: {current_song.get('title','-')}")
        artist_label.config(text=f"Artist: {current_song.get('artist','-')}")
        album_label.config(text=f"Album: {current_song.get('album','-')}")
        play_label.config(text=("Playing" if current_song.get('playing') else "Paused"))

        duration = max(1.0, float(current_song.get('duration', 1.0)))
        pos = max(0.0, min(duration, float(current_song.get('pos', 0.0))))
        prog_norm = pos / duration if duration > 0 else 0.0
        prog_inner_width = PROG_WIDTH - 20
        prog_fill_w = int(prog_inner_width * prog_norm)
        progress_canvas.coords(progress_fill, 10, 8, 10 + prog_fill_w, PROG_HEIGHT-8)

        dot_x = 10 + prog_fill_w
        dot_r = 8
        progress_canvas.coords(progress_dot, dot_x-dot_r, 8-dot_r, dot_x+dot_r, 8+dot_r)
    except Exception:
        pass

    # ---------- Gauge cluster ----------
    if USE_HARDWARE and vesc:
        try:
            values = vesc.get_values()
            speed = values.speed * 2.23694
            voltage = values.v_in
            current_draw = values.avg_motor_current
        except Exception:
            speed = (math.sin(time.time())+1)/2*MAX_SPEED_MPH
            voltage = (math.cos(time.time())+1)/2*MAX_VOLTAGE
            current_draw = 0
    else:
        speed = (math.sin(time.time())+1)/2*MAX_SPEED_MPH
        voltage = (math.cos(time.time())+1)/2*MAX_VOLTAGE
        current_draw = 0

    a = map_angle_ccw(speed, MAX_SPEED_MPH)
    x = CENTER + RADIUS * math.cos(a)
    y = CENTER + RADIUS * math.sin(a)
    speed_canvas.coords(speed_line, CENTER, CENTER, x, y)
    speed_label.config(text=f"Speed: {speed:.1f} mph")

    a = map_angle_ccw(voltage, MAX_VOLTAGE)
    x = CENTER + RADIUS * math.cos(a)
    y = CENTER + RADIUS * math.sin(a)
    voltage_canvas.coords(voltage_line, CENTER, CENTER, x, y)
    voltage_label.config(text=f"Voltage: {voltage:.1f} V")

    current_label.config(text=f"Current Draw: {current_draw:.1f} A")

    root.after(50, update_dashboard)

update_dashboard()
root.mainloop()
