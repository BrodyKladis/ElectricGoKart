from Adafruit_ADS1x15 import ADS1115
import time

adc = ADS1115(busnum=1)

while True:
    print(adc.read_adc(1, gain=1))
    time.sleep(0.2)

