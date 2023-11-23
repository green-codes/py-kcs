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

import sys
import optparse
import math
import wave

# A few global parameters related to the encoding

FRAMERATE = 9600  # Hz
ONES_FREQ = 2400  # Hz (per KCS)
ZERO_FREQ = 1200  # Hz (per KCS)


from kcs_encode_live import make_sin_wave, kcs_encode_byte


# Write a WAV file with encoded data. leader and trailer specify the
# number of seconds of carrier signal to encode before and after the data
def kcs_write_wav(filename, data, leader, trailer, cuts):
    w = wave.open(filename, "wb")
    w.setnchannels(1)
    w.setsampwidth(1)
    w.setframerate(FRAMERATE)

    # Write the leader
    w.writeframes(one_pulse * (int(FRAMERATE / len(one_pulse)) * leader))

    # Encode the actual data
    for byteval in data:
        w.writeframes(kcs_encode_byte(byteval, one_pulse, zero_pulse, cuts))

    # Write the trailer
    w.writeframes(one_pulse * (int(FRAMERATE / len(one_pulse)) * trailer))
    w.close()


if __name__ == "__main__":
    parser = optparse.OptionParser()
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
    opts, args = parser.parse_args()

    if len(args) != 2:
        print("Usage : %s [options] infile outfile" % sys.argv[0], file=sys.stderr)
        raise SystemExit(1)

    # Create the wave patterns that encode 1s and 0s
    if opts.speed_mode == 2:
        FRAMERATE *= 2
        ONES_FREQ *= 2
        ZERO_FREQ *= 2
    HIGHSPEED = opts.speed_mode > 0
    one_pulse = make_sin_wave(ONES_FREQ, FRAMERATE) * (2 if HIGHSPEED else 8)
    zero_pulse = make_sin_wave(ZERO_FREQ, FRAMERATE) * (1 if HIGHSPEED else 4)

    in_filename = args[0]
    out_filename = args[1]
    data = open(in_filename, "rb").read()
    # data = data.replace('\n','\r\n')         # Fix line endings
    rawdata = bytearray(data)
    kcs_write_wav(out_filename, rawdata, opts.leader, opts.trailer, opts.cuts)
