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
CHANNELS = 1
FRAMERATE = 44100
CHUNK = 1024  # sweetspot, don't touch
KCS_BASE_FREQ = 2400
MSB_HI_THRES = 0x7F // 8  # MSB sign-change thresholds
MSB_LO_THRES = 0xFF - MSB_HI_THRES  # symmetric
ALGN_FRAC = 0.45  # fraction by which to advance sample on start bit

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
            stream2.write(frames)

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
def generate_bytes(bitstream, framerate, kcs_base_adj, speed_mode, cuts):
    bitmasks = [0x1, 0x2, 0x4, 0x8, 0x10, 0x20, 0x40, 0x80]
    if cuts:  # CUTS encoding (1-7-3), ignore the highest bit in the byte
        bitmasks[-1] = 0x0

    # calculate adjusted KCS base frequency
    kcs_base_freq = KCS_BASE_FREQ
    if speed_mode == 2:  # double for 2400 baud
        kcs_base_freq *= 2
    kcs_base_freq += kcs_base_adj

    # set speed mode
    if speed_mode > 0:  # 1200/2400 baud
        fpb_mult = 2  # 2 cycles for a one bit
        thres_0_hi = 2
        thres_1_lo = 3
    else:  # 300 baud
        fpb_mult = 8  # 8 cycles for a one bit
        thres_0_hi = 11
        thres_1_lo = 13

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
            _ = list(islice(bitstream, int(frames_per_bit * ALGN_FRAC)))
            # obtain eight bits (least significant first)
            byteval = 0
            for mask in bitmasks:
                bit_sample = list(islice(bitstream, frames_per_bit))
                # use partial bit sample for better error tolerance
                bit_sample = bit_sample[: int(len(bit_sample) * 7 / 8)]
                if sum(bit_sample) >= thres_1_lo:
                    byteval |= mask
            # only emit byte if the first stop bit is detected
            sample.extend(islice(bitstream, frames_per_bit + 1))
            sign_changes = sum(sample)
            if sign_changes >= thres_1_lo:
                yield byteval

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
    parser.add_option(
        "-o",
        "--output-file",
        dest="output_file",
        help="output file to write to",
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

    # if device not specified, use system default
    if opts.device < 0:
        device = pa.get_default_input_device_info()["index"]
    else:
        device = opts.device

    # create generators
    sign_changes = generate_wav_sign_change_bits(device, opts.monitor_device)
    byte_stream = generate_bytes(
        sign_changes, FRAMERATE, opts.kcs_base_adj, opts.speed_mode, opts.cuts
    )

    # consume audio source and write to stdout (optionally to file)
    if opts.output_file:
        outf = open(opts.output_file, "wb")
    else:
        outf = sys.stdout.buffer.raw
    for b in byte_stream:
        outf.write(bytes([b]))
        outf.flush()
