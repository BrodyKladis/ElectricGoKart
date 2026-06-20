import serial
import struct
import time

COMM_GET_VALUES = 4  # VESC command id

def crc16_ccitt_false(data: bytes) -> int:
    """CRC-16/CCITT-FALSE (poly 0x1021, init 0xFFFF)."""
    crc = 0xFFFF
    for b in data:
        crc ^= (b << 8)
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ 0x1021) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return crc

def build_short_frame(payload: bytes) -> bytes:
    if len(payload) > 255:
        raise ValueError("payload too long")
    crc = crc16_ccitt_false(payload)
    return bytes([0x02, len(payload)]) + payload + struct.pack(">H", crc) + bytes([0x03])

def parse_short_frame(buf: bytearray):
    """Return payload bytes if a valid frame is found; consume bytes from buffer."""
    while len(buf) >= 5:
        if buf[0] != 0x02:
            del buf[0]
            continue

        length = buf[1]
        frame_len = 2 + length + 2 + 1
        if len(buf) < frame_len:
            return None

        if buf[frame_len - 1] != 0x03:
            del buf[0]
            continue

        payload = bytes(buf[2:2 + length])
        crc_recv = struct.unpack(">H", bytes(buf[2 + length:2 + length + 2]))[0]
        crc_calc = crc16_ccitt_false(payload)

        if crc_recv != crc_calc:
            del buf[0]
            continue

        del buf[:frame_len]
        return payload

    return None

def parse_comm_get_values(payload: bytes) -> dict:
    """Decode a subset of COMM_GET_VALUES response (common/stable fields)."""
    if not payload or payload[0] != COMM_GET_VALUES:
        raise ValueError("Not COMM_GET_VALUES response")

    i = 1

    def i16(scale):
        nonlocal i
        v = struct.unpack(">h", payload[i:i+2])[0]
        i += 2
        return v / scale

    def i32(scale):
        nonlocal i
        v = struct.unpack(">i", payload[i:i+4])[0]
        i += 4
        return v / scale

    out = {}
    out["temp_fet_C"]       = i16(10.0)
    out["temp_motor_C"]     = i16(10.0)
    out["motor_current_A"]  = i32(100.0)
    out["input_current_A"]  = i32(100.0)
    out["id_current_A"]     = i32(100.0)
    out["iq_current_A"]     = i32(100.0)
    out["duty"]             = i16(1000.0)
    out["rpm"]              = i32(1.0)
    out["v_in_V"]           = i16(10.0)

    return out

class VescUart:
    def __init__(self, port: str, baud: int = 115200):
        self.ser = serial.Serial(port, baudrate=baud, timeout=0.05)
        self.buf = bytearray()

    def get_values(self, timeout_s: float = 0.25) -> dict:
        req = build_short_frame(bytes([COMM_GET_VALUES]))

        # Clear stale bytes then request
        try:
            self.ser.reset_input_buffer()
        except Exception:
            pass

        self.ser.write(req)

        t_end = time.time() + timeout_s
        while time.time() < t_end:
            n = self.ser.in_waiting
            if n:
                self.buf += self.ser.read(n)
                payload = parse_short_frame(self.buf)
                if payload is not None:
                    return parse_comm_get_values(payload)

        raise TimeoutError("No valid VESC response (timeout/CRC/framing)")
