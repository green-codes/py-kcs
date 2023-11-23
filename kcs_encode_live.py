#!/usr/bin/env python3
# kcs_encode.py
#
# Author : David Beazley (http://www.dabeaz.com)
# Copyright (C) 2010
#
# Requires Python 3.1.2 or newer
#
# Updated 2022: Greg Strike (https://www.gregorystrike.com)

"""
Takes the contents of any file and encodes it into a Kansas
City Standard WAV file, that when played will upload data via the
cassette tape input on various vintage home computers. See
http://en.wikipedia.org/wiki/Kansas_City_standard
"""

from time import sleep
import sys
import optparse
import math

from queue import Queue
from threading import Thread

import pyaudio

# A few global parameters related to the encoding

FORMAT = pyaudio.paUInt8  # must be signed integer type
CHANNELS = 1
FRAMERATE = 44100
CHUNK = 1024  # sweetspot, don't touch
ONES_FREQ = 2400  # Hz (per KCS)
ZERO_FREQ = 1200  # Hz (per KCS)
AMPLITUDE = 120  # Amplitude of generated waves
CENTER = 128  # Center point of generated waves

# init PyAudio
pa = pyaudio.PyAudio()


# create a single sine wave cycle of a given frequency
def make_sin_wave(freq, framerate):
    n = int(round(framerate / freq))
    y = [math.sin(2 * math.pi * e / n) for e in range(n)]
    return bytes([int((CENTER + AMPLITUDE * e)) for e in y])


# Take a single byte value and turn it into a bytearray representing
# the associated waveform along with the required start and stop bits.
def kcs_encode_byte(byteval, one_pulse, zero_pulse, cuts):
    bitmasks = [0x1, 0x2, 0x4, 0x8, 0x10, 0x20, 0x40, 0x80]
    # The start bit (0)
    encoded = bytearray(zero_pulse)
    # 8 data bits
    for mask in bitmasks:
        if cuts and (mask == 0x80):
            encoded.extend(one_pulse)  # CUTS encoding uses 3 stop bits
        else:
            encoded.extend(one_pulse if (byteval & mask) else zero_pulse)
    # Two stop bits (1)
    encoded.extend(one_pulse)
    encoded.extend(one_pulse)
    return bytes(encoded)


def monitor_output(monitor_device, buffer_q):
    monitor_stream = pa.open(
        format=FORMAT,
        channels=CHANNELS,
        rate=FRAMERATE,
        output=True,
        output_device_index=monitor_device,
        frames_per_buffer=1024,
    )
    while True:
        monitor_stream.write(buffer_q.get())


if __name__ == "__main__":
    parser = optparse.OptionParser()
    parser.add_option(
        "-l",
        "--list-devices",
        action="store_true",
        default=False,
        dest="list_devices",
        help="list audio input devices and exit",
    )
    parser.add_option(
        "-d",
        "--device",
        dest="device",
        type="int",
        default=-1,
        help="audio input device id (system default if none)",
    )
    parser.add_option(
        "-m",
        "--monitor-device",
        dest="monitor_device",
        type="int",
        default=-1,
        help="audio output device id (no monitor if none)",
    )
    parser.add_option(
        "-s",
        "--speed",
        type="int",
        default=0,
        dest="speed_mode",
        help="0 for 300 baud, 1 for 1200 baud, 2 for 2400 baud",
    )
    parser.add_option(
        "-L",
        "--leader",
        type="int",
        default=1,
        dest="leader",
        help="length of leader in seconds",
    )
    parser.add_option(
        "-T",
        "--trailer",
        type="int",
        default=1,
        dest="trailer",
        help="length of trailer in seconds",
    )
    parser.add_option(
        "-a",
        "--ascii",
        action="store_true",
        default=False,
        dest="cuts",
        help="ASCII only w/CUTS encoding (7 data bits, 3 stop bits)",
    )
    parser.add_option(
        "-e",
        "--echo",
        action="store_true",
        default=False,
        dest="echo",
        help="echo source file to stdout",
    )
    opts, args = parser.parse_args()

    # if req'd, list possible input devices
    if opts.list_devices:
        info = pa.get_host_api_info_by_index(0)
        numdevices = info.get("deviceCount")
        for i in range(0, numdevices):
            d = pa.get_device_info_by_host_api_device_index(0, i)
            name = d["name"]
            in_mark = "[IN]" if d["maxInputChannels"] > 0 else ""
            out_mark = "[OUT]" if d["maxOutputChannels"] > 0 else ""
            if in_mark or out_mark:
                print(
                    f"Device id {i} - {name} {in_mark}{out_mark} ",
                )
        exit(0)

    if len(args) != 1:
        input_f = sys.stdin.buffer.raw
    else:
        # load input file
        input_f = open(args[0], "rb")

    # if device not specified, use system default
    if opts.device < 0:
        device = pa.get_default_input_device_info()["index"]
    else:
        device = opts.device

    # Create the wave patterns that encode 1s and 0s
    if opts.speed_mode == 2:
        FRAMERATE *= 2
        ONES_FREQ *= 2
        ZERO_FREQ *= 2
    HIGHSPEED = opts.speed_mode > 0
    one_pulse = make_sin_wave(ONES_FREQ, FRAMERATE) * (2 if HIGHSPEED else 8)
    zero_pulse = make_sin_wave(ZERO_FREQ, FRAMERATE) * (1 if HIGHSPEED else 4)

    # start outputting
    stream = pa.open(
        format=FORMAT,
        channels=CHANNELS,
        rate=FRAMERATE,
        output=True,
        output_device_index=device,
        frames_per_buffer=CHUNK,
    )
    stdout = sys.stdout.buffer.raw

    if opts.monitor_device >= 0:
        buffer_q = Queue()
        monitor_thread = Thread(
            target=monitor_output,
            kwargs=dict(monitor_device=opts.monitor_device, buffer_q=buffer_q),
        )
        monitor_thread.daemon = True
        monitor_thread.start()

    leader = one_pulse * int(FRAMERATE / len(one_pulse)) * opts.leader
    if opts.monitor_device >= 0:
        buffer_q.put(leader)
    stream.write(leader, exception_on_underflow=True)

    for byteval in input_f.read():
        encoded_data = kcs_encode_byte(byteval, one_pulse, zero_pulse, opts.cuts)
        if opts.monitor_device >= 0:
            buffer_q.put(encoded_data)
        stream.write(encoded_data, exception_on_underflow=True)
        if opts.echo:
            stdout.write(bytes([byteval]))
            stdout.flush()

    trailer = one_pulse * int(FRAMERATE / len(one_pulse)) * opts.trailer
    if opts.monitor_device >= 0:
        buffer_q.put(trailer)
    stream.write(trailer, exception_on_underflow=True)

    sleep(1)
