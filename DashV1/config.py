from dataclasses import dataclass


@dataclass(frozen=True)
class DashConfig:
    use_hardware: bool = True
    max_speed_mph: float = 45.0
    max_voltage: float = 75.0

    use_steer_uart: bool = True
    steer_uart_port: str = "/dev/ttyAMA0"
    steer_uart_baud: int = 115200
    steer_uart_cal_seconds: float = 4.0

    steer_smooth_alpha: float = 0.25
    motor_smooth_alpha: float = 0.18
    invert_steer_gauge: bool = True

    aura_tol_norm: float = 0.02
    aura_max: float = 1.0
    aura_alpha: float = 0.35

    update_interval_ms: int = 50
    adc_steer_channel: int = 1
    adc_accel_channel: int = 2
    adc_regen_channel: int = 0
    vesc_port: str = "/dev/ttyUSB0"


CONFIG = DashConfig()
