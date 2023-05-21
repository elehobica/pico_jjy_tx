# NTP to JJY
#  for Raspberry Pi Pico W
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

# write ssid and password as 'secrets' dict in secrets.py
from secrets import secrets

# JST offset
TZ_JST_OFS = 9

# Pin Configuration
PIN_MOD = 2   # modulation (output for PIO)
PIN_CTRL = 3  # control (output for GPIO, input for PIO)

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

# LocalTime class for NTP and RTC
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
    try:
      ntptime.settime()
    except OSError as e:
      if e.args[0] == 110:
        # reset when OSError: [Errno 110] ETIMEDOUT
        print(e)
        time.sleep(5)
        machine.reset()
    return self.TimeTuple(utime.localtime(utime.mktime(utime.localtime()) + offsetHour*3600))
  def __setRtc(self, t: TimeTuple) -> TimeTuple:
    machine.RTC().datetime((t.year, t.month, t.mday, t.weekday+1, t.hour, t.minute, t.second, 0))
    time.sleep(1)  # wait to be reflected
    return self.TimeTuple(time.localtime())
  def now(self, offset: int = 0) -> TimeTuple:
    return self.TimeTuple(time.localtime(time.time() + offset))
  def alignSecondEdge(self):
    t = self.now()
    while t.second == self.now().second:
      time.sleep_ms(1)

# PIO program
@rp2.asm_pio(sideset_init = rp2.PIO.OUT_LOW)
def oscillatorPioAsm():
  # generate 1/2 frequency pulse against PIO clock with enable control
  #  wait pin from in_base
  #  sideset pin from sideset_base
  wrap_target()
  wait(1, pin, 0).side(0)
  nop()          .side(1)
  wrap()

# JJY class
class Jjy:
  def __init__(self, lcTime: LocalTime, freq: int, ctrlPins: tuple(machine.Pin), modOutPin: machine.Pin, pioAsm: Callable):
    self.lcTime = lcTime
    self.freq = freq # 40000 or 60000 (Hz)
    self.ctrlPins = ctrlPins  # for pulse control output (could be multiple, but PIO accepts only [0] as control input)
    self.modOutPin = modOutPin  # for modulation output
    # dummy toggle because it spends much time (more than 0.6 sec) at first time
    self.__control(False)
    self.__control(True)
    self.__control(False)
    # start PIO
    sm = rp2.StateMachine(0, pioAsm, freq = self.freq*2, in_base = self.ctrlPins[0], sideset_base = self.modOutPin)
    sm.active(True)
  def __control(self, enable: bool) -> None:
    for ctrlPin in self.ctrlPins:
      if enable:
        ctrlPin.on()
      else:
        ctrlPin.off()
  def __resetVector(self):
    self.__vector = []
  def __appendVector(self, value: int):
    self.__vector.append(value)
  def __getVector(self):
    return self.__vector
  def __genTimecode(self, t: LocalTime.TimeTuple) -> list:
    ## Timecode1 (exept for 15, 45 min) ##
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

    ## Timecode2 (for 15, 45 min)
    # 00: marker M
    # 01 ~ 08: Minite BCD 40, 20, 10, "0", 8, 4, 2, 1
    # 09: marker P1
    # 10 ~ 18: Hour BCD "0", "0", 20, 10, "0", 8, 4, 2, 1
    # 19: marker P2
    # 20 ~ 28: Yearday BCD "0", "0", 200, 100, "0", 80, 40, 20, 10
    # 29: marker P3
    # 30 ~ 38: Yearday BCD 8, 4, 2, 1, "0", 0", PA1, PA2, "0" ***
    # 39: marker P4
    # 40 ~ 48: Call Signals ***
    # 49: marker P5
    # 50 ~ 58: ST1, ST2, ST3, ST4, ST5, ST6, "0", "0", "0" ***
    # 59: marker P0

    # BCD
    # 200, 100      : 100's digit
    # 80, 40, 20, 10: 10's digit
    # 8, 4, 2, 1    : 1's digit

    self.__resetVector()
    isTimecode1 = not (t.minute == 15 or t.minute == 45)
    self.__addMarker()  # marker M
    pa2 = self.__addBcd(t.minute // 10, 3)
    self.__addBin(0)
    pa2 += self.__addBcd(t.minute)
    self.__addMarker()  # marker P1
    self.__addBin(0, 2)
    pa1 = self.__addBcd(t.hour // 10, 2)
    self.__addBin(0)
    pa1 += self.__addBcd(t.hour)
    self.__addMarker()  # marker P2
    self.__addBin(0, 2)
    self.__addBcd(t.yearday // 100, 2)
    self.__addBin(0)
    self.__addBcd(t.yearday // 10)
    self.__addMarker()  # marker P3
    self.__addBcd(t.yearday)
    self.__addBin(0, 2)
    self.__addBin(pa1) # PA1
    self.__addBin(pa2) # PA2
    if isTimecode1:
      self.__addBin(0) # SU1
      self.__addMarker()  # marker P4
      self.__addBin(0) # SU2
      self.__addBcd(t.year // 10)
      self.__addBcd(t.year)
      self.__addMarker()  # marker P5
      self.__addBcd((t.weekday + 1) % 7, 3)
      self.__addBin(0) # LS1
      self.__addBin(0) # LS2
      self.__addBin(0, 4)
    else:
      self.__addBin(0)
      self.__addMarker()  # marker P4
      self.__addBin(0, 9) # Call Signals
      self.__addMarker()  # marker P5
      self.__addBin(0, 6) # ST1 ~ ST6
      self.__addBin(0, 3)
    self.__addMarker()  # marker P0
    return self.__getVector()
  def __addMarker(self) -> None:
    self.__appendVector(2)
  def __addBcd(self, value: int, numDigits: int = 4) -> int:
    parity = 0
    value = value % 10
    for bitPos in range(numDigits-1, -1, -1):
      bit = (value >> bitPos) & 0b1
      self.__appendVector(bit)
      parity += bit
    return parity
  def __addBin(self, value: int, count: int = 1) -> None:
    for i in range(count):
      self.__appendVector(value & 0b1)
  def __sendTimecode(self, vector: list) -> None:
    for value in vector:
      self.lcTime.alignSecondEdge()
      self.__control(True)
      if value == 0:  # bit 0
        pulseWidth = 0.8
      elif value == 1:  # bit 1
        pulseWidth = 0.5
      else:  # marker
        pulseWidth = 0.2
      time.sleep(pulseWidth)
      self.__control(False)
  def run(self):
    print(f'start JJY emission at {self.freq} Hz')
    self.lcTime.alignSecondEdge()
    time.sleep(0.2)  # to make same condition as marker P0
    while True:
      t = self.lcTime.now(1)  # time for next second
      vector = self.__genTimecode(t)
      print(f'Timecode: {t}')
      self.__sendTimecode(vector[t.second:])  # apply offset (should be only for the first time)

def main() -> bool:
  machine.freq(96000000)  # recommend multiplier of 40000*2 and 60000*2 to avoid jitter
  led = machine.Pin("LED", machine.Pin.OUT)
  led.off()
  # connect WiFi
  if not connectWifi():
    return False
  # LED sign for WiFi connection
  for i in range(2 * 3):
    time.sleep(0.1)
    led.toggle()
  # NTP/RTC setting
  lcTime = LocalTime(TZ_JST_OFS)
  # disconnect WiFi
  disconnectWifi()
  # JJY
  jjy = Jjy(
    lcTime = lcTime,
    freq = 40000,  # 40000 or 60000
    ctrlPins = (machine.Pin(PIN_CTRL, machine.Pin.OUT), led),
    modOutPin = machine.Pin(PIN_MOD, machine.Pin.OUT),
    pioAsm = oscillatorPioAsm,
  )
  jjy.run()  # infinite loop
  return True

if __name__ == '__main__':
  main()
