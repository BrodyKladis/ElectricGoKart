from vesc_uart import VescUart

PORT = "/dev/ttyAMA3"
BAUD = 115200  # try 57600 if this times out

v = VescUart(PORT, BAUD)

vals = v.get_values()
print("VESC OK:", vals)
