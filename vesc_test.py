from pyvesc import VESC

PORT = "/dev/ttyAMA3"
BAUD = 115200  # try 57600 if this fails

def open_vesc():
    # pyvesc versions differ; support both constructor styles
    try:
        return VESC(serial_port=PORT, baudrate=BAUD)
    except TypeError:
        return VESC(serial_port=PORT)

vesc = open_vesc()

try:
    values = vesc.get_values()
    print("OK")
    print("VIN:", values.v_in)
    print("RPM:", getattr(values, "rpm", None))
    print("Motor current:", getattr(values, "avg_motor_current", None))
    print("Duty:", getattr(values, "duty_cycle_now", None))
except Exception as e:
    print("FAILED:", repr(e))
