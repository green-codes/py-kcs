#

# Decodes Kansas City Format (KCS) encoded data from a live audio source
# - 300 baud mode only for now
# - Original code: https://github.com/gstrike/py-kcs
# - Original-original code: http://www.dabeaz.com/py-kcs

import sys
import optparse
from collections import deque
from itertools import islice

import pyaudio

# audio I/O settings
FORMAT = pyaudio.paInt16  # must be signed integer type
CHANNELS = 2
FRAMERATE = 44100
CHUNK = 1024
MSB_HI_THRES = 0x10  # MSB sign-change thresholds
MSB_LO_THRES = 0xFF - MSB_HI_THRES  # symmetric
SMPL_ALN_FRAC = 3  # fraction by which to advance sample on start bit

# init PyAudio
pa = pyaudio.PyAudio()


# Generate a sequence representing sign changes
def generate_wav_sign_change_bits(device, monitor_device):
    samplewidth = pa.get_sample_size(FORMAT)

    # start Recording
    stream = pa.open(
        format=FORMAT,
        channels=CHANNELS,
        rate=FRAMERATE,
        input=True,
        input_device_index=device,
        frames_per_buffer=CHUNK,
    )

    if monitor_device >= 0:
        stream2 = pa.open(
            format=FORMAT,
            channels=CHANNELS,
            rate=FRAMERATE,
            output=True,
            output_device_index=monitor_device,
            frames_per_buffer=CHUNK,
        )

    # yield one sign-change bit for each sample
    previous = 0  # init to low
    while True:
        # obtain samples
        frames = stream.read(CHUNK, exception_on_overflow=False)
        if not frames:
            stream.close()
            break
        if monitor_device >= 0:
            stream2.write(frames, exception_on_underflow=False)
        # Extract most significant bytes from left-most audio channel
        msbytes = bytearray(frames[samplewidth - 1 :: samplewidth * CHANNELS])
        # Emit a stream of sign-change bits
        for byte in msbytes:
            # error tolerance: only flip pos if over threshold (either side)
            if previous == 0:  # flip high if sample > 0 and over thres
                pos = 1 if ((byte < 0x80) and (byte > MSB_HI_THRES)) else 0
            else:  # flip low if sample < 0 and under thres
                pos = 0 if ((byte > 0x80) and (byte < MSB_LO_THRES)) else 1
            # XOR with previous pos to get change bit
            yield 1 if (pos ^ previous) else 0
            previous = pos


# Generate a sequence of data bytes by sampling the stream of sign change bits
def generate_bytes(bitstream, framerate, kcs_base_freq, highspeed_mode):
    bitmasks = [0x1, 0x2, 0x4, 0x8, 0x10, 0x20, 0x40, 0x80]

    # set speed mode
    if highspeed_mode:  # 1200 baud
        fpb_mult = 2  # 2 cycles for a one bit
        thres_0_hi = 2
        thres_1_lo = 4
    else:  # 300 baud
        fpb_mult = 8  # 8 cycles for a one bit
        thres_0_hi = 9
        thres_1_lo = 12

    # Compute the number of audio frames used to encode a single data bit
    frames_per_bit = int(round(float(framerate) * fpb_mult / kcs_base_freq))

    # Queue of sampled sign bits
    sample = deque(maxlen=frames_per_bit)

    # Fill the sample buffer with an initial set of data
    sample.extend(islice(bitstream, frames_per_bit - 1))
    sign_changes = sum(sample)

    # Look for the start bit
    prev_changes = sign_changes
    for val in bitstream:
        if val:
            sign_changes += 1
        if sample.popleft():
            sign_changes -= 1
        sample.append(val)

        # If a start bit is detected, sample the next 8 data bits
        # NOTE: enforce start bit to be 1-to-0; also re-aligns byte position
        if (sign_changes < prev_changes) and (sign_changes <= thres_0_hi):
            # align sample by advancing one third of a cycle
            sample.extend(islice(bitstream, frames_per_bit // SMPL_ALN_FRAC))
            # obtain eight bits (least significant first)
            byteval = 0
            for mask in bitmasks:
                if sum(islice(bitstream, frames_per_bit)) >= thres_1_lo:
                    byteval |= mask
            yield byteval
            # fill the sample buffer with the first stop bit
            sample.extend(islice(bitstream, frames_per_bit + 1))
            sign_changes = sum(sample)

        prev_changes = sign_changes


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
        "-f",
        "--kcs-base-freq",
        dest="kcs_base_freq",
        type="int",
        default=2400,
        help="KCS base frequency (speed adjust; default 2400)",
    )
    parser.add_option(
        "-H",
        "--high-speed",
        action="store_true",
        default=False,
        dest="high_speed",
        help="use 1200 baud mode",
    )
    parser.add_option(
        "-b",
        "--binary",
        action="store_true",
        default=False,
        dest="binary",
        help="output in binary",
    )
    parser.add_option(
        "-z",
        "--record-null",
        action="store_true",
        default=False,
        dest="record_null",
        help="record NULL bytes from input stream",
    )
    opts, args = parser.parse_args()

    # if req'd, list possible input devices
    if opts.list_devices:
        info = pa.get_host_api_info_by_index(0)
        numdevices = info.get("deviceCount")
        for i in range(0, numdevices):
            print(
                "Device id ",
                i,
                " - ",
                pa.get_device_info_by_host_api_device_index(0, i).get("name"),
            )
        exit(0)

    # if not specified, use system default
    if opts.device < 0:
        device = pa.get_default_input_device_info()["index"]
    else:
        device = opts.device

    # create generators
    sign_changes = generate_wav_sign_change_bits(device, opts.monitor_device)
    byte_stream = generate_bytes(
        sign_changes, FRAMERATE, opts.kcs_base_freq, opts.high_speed
    )

    if opts.binary:
        outf = sys.stdout.buffer.raw
        for b in byte_stream:
            if not opts.record_null and b == 0:
                continue
            outf.write(bytes([b]))
            outf.flush()
    else:  # ASCII mode
        for b in byte_stream:
            if not opts.record_null and b == 0:
                continue
            s = bytes([b]).decode("ascii", errors="backslashreplace")
            print(s, end="", flush=True)
