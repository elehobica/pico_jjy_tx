# NTP to JJY for Raspberry Pi Pico W

## Overview
This project is for JJY signal emulator for Raspberry Pi Pico W

This project supports:
* Fetch JST time by NTP
* Generate JJY signal
* JJY carrier frequency is configurable for 40 KHz or 60 KHz

About JJY, please refer to [https://jjy.nict.go.jp/jjy/trans/index.html](https://jjy.nict.go.jp/jjy/trans/index.html)

## Supported Board
* Raspberry Pi Pico W

## Pin Assignment

| Pico Pin # | Pin Name | Function | Note |
----|----|----|----
|  3 | GND | GND | Ground |
|  4 | GP2 | PIN_MOD | JJY Modulation Output |
|  5 | GP3 | PIN_CTRL | Control Output (option for external carrier generator) |

## Connection Diagram
![Connection Diagram](doc/pico_ntp2jjy_connection.png)

## How to make the program work
* Use [Thonny](https://thonny.org/) or similar MicroPython IDE (Confirmed with Thonny 4.0.2, Python 3.10.9)
* Install MicroPython (Raspberry Pi Pico) interpreter firmware on Raspberry Pi Pico W by Thonny
* Add `secrets.py` to include your WiFi configuration and put it on the storage of Raspberry Pi Pico W
```
# secrets.py
secrets = {
  "ssid": 'xxxx',
  "password": 'xxxx',
}
```
* Excecute `pico_ntp2jjy.py` from Thonny
* For stand-alone application, please rename `pico_ntp2jjy.py` as `main.py` and store on the storage of Raspberry Pi Pico W

## Tips for emitting JJY
* The easiest way to make the clock detect JJY emulated singal in small room environment, is just to connect wired earphone between PIN_MOD and GND pins and put the clock near the earphone cable. (This could damage the earphone. please try with cheaper one.)
* For the more proper way, round ferrite rod bar antenna with appropriate driver would be suitable.
