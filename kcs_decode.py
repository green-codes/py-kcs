#!/usr/bin/env python3
# kcs_decode.py
#
# Author : David Beazley (http://www.dabeaz.com)
# Copyright (C) 2010
#
# Requires Python 3.1.2 or newer

"""
Converts a WAV file containing Kansas City Standard data and
extracts text data from it. See:

http://en.wikipedia.org/wiki/Kansas_City_standard
"""

import sys
import optparse
from itertools import islice
import wave

from kcs_decode_live import generate_bytes


# Generate a sequence representing sign bits
def generate_wav_sign_change_bits(wavefile):
    samplewidth = wavefile.getsampwidth()
    nchannels = wavefile.getnchannels()
    previous = 0
    while True:
        frames = wavefile.readframes(8192)
        if not frames:
            break

        # Extract most significant bytes from left-most audio channel
        msbytes = bytearray(frames[samplewidth - 1 :: samplewidth * nchannels])

        # Emit a stream of sign-change bits
        for byte in msbytes:
            signbit = byte & 0x80
            yield 1 if (signbit ^ previous) else 0
            previous = signbit


if __name__ == "__main__":
    parser = optparse.OptionParser()
    parser.add_option(
        "-f",
        "--kcs-base-adj",
        dest="kcs_base_adj",
        type="int",
        default=0,
        help="KCS base frequency adjustment",
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
        "-a",
        "--ascii",
        action="store_true",
        default=False,
        dest="cuts",
        help="ASCII only w/CUTS encoding (7 data bits, 3 stop bits)",
    )

    opts, args = parser.parse_args()
    if len(args) != 1:
        print("Usage: %s [options] infile" % sys.argv[0], file=sys.stderr)
        raise SystemExit(1)

    wf = wave.open(args[0])
    sign_changes = generate_wav_sign_change_bits(wf)
    byte_stream = generate_bytes(
        sign_changes, wf.getframerate(), opts.kcs_base_adj, opts.speed_mode, opts.cuts
    )

    # Output the byte stream in 80-byte chunks (optionally to file)
    stdout = sys.stdout.buffer.raw
    while True:
        buffer = bytes(islice(byte_stream, 80))
        if not buffer:
            break
        stdout.write(buffer)
        stdout.flush()
