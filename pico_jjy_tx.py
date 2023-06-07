# JJY transmitter for Raspberry Pi Pico W
# ------------------------------------------------------
# Copyright (c) 2023, Elehobica
#
# This software is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This software is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this software.  If not, see <http://www.gnu.org/licenses/>.
#
# As for the libraries which are used in this software, they can have
# different license policies, look at the subdirectories of lib directory.
# ------------------------------------------------------

# please refer to the following url for JJY and its protocol
# https://jjy.nict.go.jp/jjy/trans/index.html
# https://jjy.nict.go.jp/jjy/trans/timecode1.html
# https://jjy.nict.go.jp/jjy/trans/timecode2.html

import machine
import rp2
import utime
import network
import ntptime

# write ssid and password as 'secrets' dict in secrets.py
from secrets import secrets

# JST offset
TZ_JST_OFS = 9

# JJY carrier frequency 40000 or 60000 Hz
JJY_CARRIER_FREQ = 40000

# Pin Configuration
PIN_MOD_BASE = 2   # modulation P (output for PIO), modulation N for succeeding pin
PIN_CTRL = 4  # control (output for GPIO, input for PIO)

# Seconds to run until re-sync with NTP
# infinite if SEC_TO_RUN == 0
SEC_TO_RUN = 60 * 60 * 24 * 7 // 2  # half week is the maximum

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
    utime.sleep(1)
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
    utime.sleep(1)
    try:
      ntptime.settime()
    except OSError as e:
      if e.args[0] == 110:
        # reset when OSError: [Errno 110] ETIMEDOUT
        print(e)
        utime.sleep(5)
        machine.reset()
    return self.TimeTuple(utime.localtime(utime.mktime(utime.localtime()) + offsetHour*3600))
  def __setRtc(self, t: TimeTuple) -> TimeTuple:
    machine.RTC().datetime((t.year, t.month, t.mday, t.weekday+1, t.hour, t.minute, t.second, 0))
    utime.sleep(1)  # wait to be reflected
    return self.TimeTuple(utime.localtime())
  def now(self, offset: int = 0) -> TimeTuple:
    return self.TimeTuple(utime.localtime(utime.time() + offset))
  def alignSecondEdge(self):
    t = self.now()
    while t.second == self.now().second:
      utime.sleep_ms(1)

# PIO program
@rp2.asm_pio(sideset_init = (rp2.PIO.OUT_LOW, rp2.PIO.OUT_LOW))
def oscillatorPioAsm():
  # generate 1/2 frequency pulse against PIO clock with enable control
  #  jmp pin from jmp_pin
  #  sideset pin from sideset_base

  P = 0b01  # drive +
  N = 0b10  # drive -
  Z = 0b00  # drive zero

  #                         # addr
  label('loop')
  nop()           .side(P)  # 29
  jmp(pin, 'loop').side(N)  # 30
  wrap_target()
  label('entryPoint')
  jmp(pin, 'loop').side(Z)  # 31
  wrap()

# JJY class
class Jjy:
  def __init__(self, lcTime: LocalTime, freq: int, ctrlPins: tuple(machine.Pin), modOutPinBase: machine.Pin, pioAsm: Callable):
    self.lcTime = lcTime
    self.freq = freq # 40000 or 60000 (Hz)
    self.ctrlPins = ctrlPins  # for pulse control output (could be multiple, but PIO accepts only [0] as control input)
    self.modOutPinBase = modOutPinBase  # for modulation output
    self.pioAsm = pioAsm
    # dummy toggle because it spends much time (more than 0.6 sec) at first time
    self.__control(False)
    self.__control(True)
    self.__control(False)
  def __control(self, enable: bool) -> None:
    for ctrlPin in self.ctrlPins:
      ctrlPin.value(enable)
  def run(self, secToRun: int = 0):
    def marker(**kwargs: dict) -> list:
      return [2]
    def bcd(value: int, numDigits: int = 4, **kwargs: dict) -> list:
      return [((value % 10) >> bitPos) & 0b1 for bitPos in range(numDigits-1, -1, -1)]
    def bin(value: int, count: int = 1, **kwargs: dict) -> list:
      return [value & 0b1] * count
    def genTimecode(t: LocalTime.TimeTuple) -> list:
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

      vector = []
      # 0 ~ 9
      vector += marker(name='M') + bcd(t.minute // 10, 3) + bin(0) + bcd(t.minute) + marker(name='P1')
      # 10 ~ 19
      vector += bin(0, 2) + bcd(t.hour // 10, 2) + bin(0) + bcd(t.hour) + marker(name='P2')
      # 20 ~ 29
      vector += bin(0, 2) + bcd(t.yearday // 100, 2) + bin(0) + bcd(t.yearday // 10) + marker(name='P3')
      # Parity
      pa1 = sum(vector[12:14] + vector[15:19])
      pa2 = sum(vector[1:4] + vector[5:9])
      if not (t.minute == 15 or t.minute == 45):
        # 30 ~ 39
        vector += bcd(t.yearday) + bin(0, 2) + bin(pa1) + bin(pa2) + bin(0, name='SU1') + marker(name='P4')
        # 40 ~ 49
        vector += bin(0, name='SU2') + bcd(t.year // 10) + bcd(t.year) + marker(name='P5')
        # 50 ~ 59
        vector += bcd((t.weekday + 1) % 7, 3) + bin(0, name='LS1') + bin(0, name='LS2') + bin(0, 4) + marker(name='P0')
      else:
        # 30 ~ 39
        vector += bcd(t.yearday) + bin(0, 2) + bin(pa1) + bin(pa2) + bin(0) + marker(name='P4')
        # 40 ~ 49
        vector += bin(0, 9, name='Call') + marker(name='P5')
        # 50 ~ 59
        vector += bin(0, 6, name='ST1-ST6') + bin(0, 3) + marker(name='P0')
      return vector
    def sendTimecode(vector: list) -> None:
      for value in vector:
        self.lcTime.alignSecondEdge()
        self.__control(True)
        if value == 0:  # bit 0
          pulseWidth = 0.8
        elif value == 1:  # bit 1
          pulseWidth = 0.5
        else:  # marker
          pulseWidth = 0.2
        utime.sleep(pulseWidth)
        self.__control(False)
    # run
    ticksTimeout = utime.ticks_add(utime.ticks_ms(), secToRun * 1000)
    # start PIO
    sm = rp2.StateMachine(0, self.pioAsm, freq = self.freq*2, jmp_pin = self.ctrlPins[0], sideset_base = self.modOutPinBase)
    sm.active(False)
    entryPoint = 31
    sm.exec(f'set(y, {entryPoint})')
    sm.exec('mov(pc, y)')
    sm.active(True)
    # start modulation
    print(f'start JJY emission at {self.freq} Hz')
    self.lcTime.alignSecondEdge()
    utime.sleep(0.2)  # to make same condition as marker P0
    while True:
      t = self.lcTime.now(1)  # time for next second
      vector = genTimecode(t)
      print(f'Timecode: {t}')
      sendTimecode(vector[t.second:])  # apply offset (should be only for the first time)
      if secToRun > 0 and utime.ticks_diff(utime.ticks_ms(), ticksTimeout) > 0:
        print(f'Finished {secToRun}+ sec.')
        break

def main() -> bool:
  machine.freq(96000000)  # recommend multiplier of 40000*2 and 60000*2 to avoid jitter
  led = machine.Pin("LED", machine.Pin.OUT)
  led.off()
  modOutP = machine.Pin(PIN_MOD_BASE, machine.Pin.OUT)
  modOutN = machine.Pin(PIN_MOD_BASE + 1, machine.Pin.OUT)
  # connect WiFi
  if not connectWifi():
    return False
  # LED sign for WiFi connection
  for i in range(2 * 3):
    utime.sleep(0.1)
    led.toggle()
  # NTP/RTC setting
  lcTime = LocalTime(TZ_JST_OFS)
  # disconnect WiFi
  disconnectWifi()
  # JJY
  jjy = Jjy(
    lcTime = lcTime,
    freq = JJY_CARRIER_FREQ,
    ctrlPins = (machine.Pin(PIN_CTRL, machine.Pin.OUT), led),
    modOutPinBase = modOutP,
    pioAsm = oscillatorPioAsm,
  )
  jjy.run(SEC_TO_RUN)
  print('System reset to sync NTP again')
  utime.sleep(5)
  machine.reset()
  return True

if __name__ == '__main__':
  main()
