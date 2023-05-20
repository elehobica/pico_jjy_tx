# NTP to JJY
# ------------------------------------------------------
# Copyright (c) 2023, Elehobica
# ------------------------------------------------------

# please refer to the following url for JJY and its protocol
# https://jjy.nict.go.jp/jjy/trans/index.html
# https://jjy.nict.go.jp/jjy/trans/timecode1.html
# https://jjy.nict.go.jp/jjy/trans/timecode2.html

import machine
import rp2
import time 
import utime
import network
import ntptime
from micropython import const

# write ssid and password as 'secrets' dict in secrets.py
from secrets import secrets

# JST offset
TZ_JST_OFS = 9

# Pin Configuration
PIN_MOD = const(2)   # modulation (output)
PIN_CTRL = const(3)  # control (output)

def connectWifi():
  ssid = secrets['ssid']
  password = secrets['password']
  wlan = network.WLAN(network.STA_IF)
  wlan.active(True)
  wlan.connect(ssid, password)
  print('Waiting for WiFi connection...')
  for t in range(10):  # timeout 10 sec
    if wlan.isconnected():
      print('WiFi connected')
      break
    time.sleep(1)
  else:
    print('WiFi not connected')
    return False
  return True

def disconnectWifi():
  wlan = network.WLAN(network.STA_IF)
  wlan.deinit()

# LocalTime class for NTC and RTC
class LocalTime:
  # utility to handle time tuple
  class TimeTuple:
    def __init__(self, timeTuple: tuple):
      self.year, self.month, self.mday, self.hour, self.minute, self.second, self.weekday, self.yearday = timeTuple
    def __str__(self):
      wday = ('Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun')[self.weekday]
      return f'{self.year:04d}/{self.month:02d}/{self.mday:02d} {wday} {self.hour:02d}:{self.minute:02d}:{self.second:02d}'
  def __init__(self, offsetHour: int):
    self.ntpTime = self.__setNtpTime(offsetHour)
    print(f'NTP: {self.ntpTime}')
    self.rtcTime = self.__setRtc(self.ntpTime)
    print(f'RTC: {self.rtcTime}')
  def __setNtpTime(self, offsetHour: int) -> TimeTuple:
    time.sleep(1)
    ntptime.settime()
    return self.TimeTuple(utime.localtime(utime.mktime(utime.localtime()) + offsetHour*3600))
  def __setRtc(self, t: TimeTuple) -> TimeTuple:
    machine.RTC().datetime((t.year, t.month, t.mday, t.weekday+1, t.hour, t.minute, t.second, 0))
    time.sleep(1)  # wait to be reflected
    return self.TimeTuple(time.localtime())
  def now(self, offset: int = 0) -> TimeTuple:
    if offset == 0:
      return self.TimeTuple(time.localtime())
    else:
      return self.TimeTuple(time.localtime(time.time() + offset))

# PIO program
@rp2.asm_pio(set_init = rp2.PIO.OUT_LOW)
def oscillator():
    wrap_target()
    wait(1, gpio, PIN_CTRL)
    set(pins, 1) [1]
    set(pins, 0)
    wrap()

# JJY class
class Jjy:
  def __init__(self, lcTime: LocalTime, freq: int, ctrlPins: tuple(machine.Pin), modOutPin: machine.Pin):
    self.lcTime = lcTime
    self.freq = freq # 40000 or 60000 (Hz)
    self.ctrlPins = ctrlPins  # for pulse control (could be multiple)
    self.modOutPin = modOutPin  # for modulation output
    self.millisOffset = 0
    for ctrlPin in self.ctrlPins:
      ctrlPin.off()
    sm = rp2.StateMachine(0, oscillator, freq=self.freq*4, set_base=self.modOutPin)
    sm.active(True)
  def __sendTimecode1(self, now: LocalTime.TimeTuple) -> None: # exept for 15, 45 min
    # 00: marker M
    # 01 ~ 08: Minite BCD 40, 20, 10, "0", 8, 4, 2, 1
    # 09: marker P1
    # 10 ~ 18: Hour BCD "0", "0", 20, 10, "0", 8, 4, 2, 1
    # 19: marker P2
    # 20 ~ 28: Yearday BCD "0", "0", 200, 100, "0", 80, 40, 20, 10
    # 29: marker P3
    # 30 ~ 38: Yearday BCD 8, 4, 2, 1, "0", 0", PA1, PA2, SU1
    # 39: marker P4
    # 40 ~ 48: Year(2) BCD SU2, 80, 40, 20, 10, 8, 4, 2, 1
    # 49: marker P5
    # 50 ~ 58: Wday BCD 4, 2, 1, LS1, LS2, "0", "0", "0", "0"
    # 59: marker P0

    # BCD
    # 200, 100      : 100's digit
    # 80, 40, 20, 10: 10's digit
    # 8, 4, 2, 1    : 1's digit
    self.__sendMarker()  # marker M
    pa2 = self.__sendBcd(now.minute // 10, 3)
    self.__send0()
    pa2 += self.__sendBcd(now.minute)
    self.__sendMarker()  # marker P1
    self.__send0(2)
    pa1 = self.__sendBcd(now.hour // 10, 2)
    self.__send0()
    pa1 += self.__sendBcd(now.hour)
    self.__sendMarker()  # marker P2
    self.__send0(2)
    self.__sendBcd(now.yearday // 100, 2)
    self.__send0()
    self.__sendBcd(now.yearday // 10)
    self.__sendMarker()  # marker P3
    self.__sendBcd(now.yearday)
    self.__send0(2)
    self.__sendPulse(pa1 % 2) # PA1
    self.__sendPulse(pa2 % 2) # PA2
    self.__send0() # SU1
    self.__sendMarker()  # marker P4
    self.__send0() # SU2
    self.__sendBcd(now.year // 10)
    self.__sendBcd(now.year)
    self.__sendMarker()  # marker P5
    self.__sendBcd((now.weekday + 1) % 7, 3)
    self.__send0() # LS1
    self.__send0() # LS2
    self.__send0(4)
    self.__sendMarker()  # marker P0
  def __sendTimecode2(self, now: LocalTime.TimeTuple) -> None: # for 15, 45 min
    # 00: marker M
    # 01 ~ 08: Minite BCD 40, 20, 10, "0", 8, 4, 2, 1
    # 09: marker P1
    # 10 ~ 18: Hour BCD "0", "0", 20, 10, "0", 8, 4, 2, 1
    # 19: marker P2
    # 20 ~ 28: Yearday BCD "0", "0", 200, 100, "0", 80, 40, 20, 10
    # 29: marker P3
    # 30 ~ 38: Yearday BCD 8, 4, 2, 1, "0", 0", PA1, PA2, "0"
    # 39: marker P4
    # 40 ~ 48: Call Signals
    # 49: marker P5
    # 50 ~ 58: ST1, ST2, ST3, ST4, ST5, ST6, "0", "0", "0"
    # 59: marker P0
    self.__sendMarker()  # marker M
    pa2 = self.__sendBcd(now.minute // 10, 3)
    self.__send0()
    pa2 += self.__sendBcd(now.minute)
    self.__sendMarker()  # marker P1
    self.__send0(2)
    pa1 = self.__sendBcd(now.hour // 10, 2)
    self.__send0()
    pa1 += self.__sendBcd(now.hour)
    self.__sendMarker()  # marker P2
    self.__send0(2)
    self.__sendBcd(now.yearday // 100, 2)
    self.__send0()
    self.__sendBcd(now.yearday // 10)
    self.__sendMarker()  # marker P3
    self.__sendBcd(now.yearday)
    self.__send0(2)
    self.__sendPulse(pa1 % 2) # PA1
    self.__sendPulse(pa2 % 2) # PA2
    self.__send0()
    self.__sendMarker()  # marker P4
    self.__send0(9) # Call Signals
    self.__sendMarker()  # marker P5
    self.__send0(6) # ST1 ~ ST6
    self.__send0(3)
    self.__sendMarker()  # marker P0
  def __sendMarker(self) -> None:
    self.__sendPulse(2)
  def __sendBcd(self, value: int, numDigits: int = 4) -> int:
    parity = 0
    value = value % 10
    bitPos = numDigits - 1
    while bitPos >= 0:
      bit = (value >> bitPos) & 0b1
      self.__sendPulse(bit)
      parity += bit
      bitPos -= 1
    return parity
  def __send0(self, count: int = 1) -> None:
    for i in range(count):
      self.__sendPulse(0)
  def __sendPulse(self, value: int) -> None:
    millis = time.ticks_ms() - self.millisOffset
    time.sleep((1000 - millis % 1000) / 1000)  # wait modulo millis
    for ctrlPin in self.ctrlPins:
      ctrlPin.on()
    if value == 0:  # bit 0
      pulseWidth = 0.8
    elif value == 1:  # bit 1
      pulseWidth = 0.5
    else:  # marker
      pulseWidth = 0.2
    time.sleep(pulseWidth)
    for ctrlPin in self.ctrlPins:
      ctrlPin.off()
    # now time left is 200ms at minimum
  def run(self):
    secs = 60 - self.lcTime.now().second
    print(f'wait for {secs} sec. to synchronize with 0 second')
    time.sleep(secs - 1)
    self.millisOffset = time.ticks_ms() % 1000
    print('start emission')
    while True:
      now = self.lcTime.now(1)
      print(now)
      if now.minute == 15 or now.minute == 45:
        self.__sendTimecode2(now)
      else:
        self.__sendTimecode1(now)

def main() -> bool:
  machine.freq(96000000) # multiplier of 40000*4 and 60000*4
  led = machine.Pin("LED", machine.Pin.OUT)
  led.off()
  # connect WiFi
  if not connectWifi():
    return False
  # LED sign for WiFi connection
  for i in range(2 * 3):
    time.sleep(0.1)
    led.toggle()
  # NTC/RTC setting
  lcTime = LocalTime(TZ_JST_OFS)
  # disconnect WiFi
  disconnectWifi()
  # JJY
  jjy = Jjy(
    lcTime=lcTime,
    freq=40000, # 40000 or 60000
    ctrlPins=(led, machine.Pin(PIN_CTRL, machine.Pin.OUT)),
    modOutPin=machine.Pin(PIN_MOD, machine.Pin.OUT),
  )
  jjy.run()  # infinite loop
  return True

if __name__ == '__main__':
  main()
