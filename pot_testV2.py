import tkinter as tk
import math
import time
from Adafruit_ADS1x15 import ADS1115

# ===============================
# ADS1115 SETUP
# ===============================
adc = ADS1115(busnum=1)
GAIN = 1
ADC_CHANNEL = 1  # <-- A1

def read_adc(channel=ADC_CHANNEL):
    """
    Read ADS1115 ADC channel (0–3)
    Returns value 0–32767
    """
    return adc.read_adc(channel, gain=GAIN)

# ===============================
# CALIBRATION
# ===============================
print("Calibration starting...")
print("Turn potentiometer FULL LEFT, then FULL RIGHT.")
print("Calibration will run for 5 seconds...")

start_time = time.time()
adc_min = 32767
adc_max = 0

while time.time() - start_time < 5:
    val = read_adc()
    adc_min = min(adc_min, val)
    adc_max = max(adc_max, val)
    time.sleep(0.01)

if adc_max - adc_min < 50:
    raise RuntimeError("Calibration failed: pot not moved enough")

print(f"Calibration complete: min={adc_min}, max={adc_max}")

# ===============================
# TKINTER GUI
# ===============================
WIDTH = 400
HEIGHT = 400
CENTER = WIDTH // 2
RADIUS = 150

root = tk.Tk()
root.title("Potentiometer Position")

canvas = tk.Canvas(root, width=WIDTH, height=HEIGHT, bg="white")
canvas.pack()

# Draw circle
canvas.create_oval(
    CENTER - RADIUS,
    CENTER - RADIUS,
    CENTER + RADIUS,
    CENTER + RADIUS,
    width=2
)

# Draw initial line
line = canvas.create_line(
    CENTER, CENTER,
    CENTER, CENTER - RADIUS,
    width=3,
    fill="red"
)

# ===============================
# UPDATE LOOP
# ===============================
def update():
    raw = read_adc()

    # Normalize 0.0–1.0
    norm = (raw - adc_min) / (adc_max - adc_min)
    norm = max(0.0, min(1.0, norm))

    # Map to angle (-135° to +135°)
    angle = math.radians(-135 + norm * 270)

    x = CENTER + RADIUS * math.cos(angle)
    y = CENTER - RADIUS * math.sin(angle)

    canvas.coords(line, CENTER, CENTER, x, y)

    root.after(20, update)  # ~50 Hz refresh

update()
root.mainloop()
