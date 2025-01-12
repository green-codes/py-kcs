#

# Decodes Kansas City Format (KCS) encoded data from a live audio source
# - 300 baud mode only for now
# - Original code: https://github.com/gstrike/py-kcs
# - Original-original code: http://www.dabeaz.com/py-kcs

import sys
import optparse

import numpy as np
import matplotlib.pyplot as plt
import pyaudio

# audio I/O settings
FORMAT = pyaudio.paFloat32  # must be signed integer type
CHANNELS = 1
FRAMERATE = 44100
CHUNK = 1024  # sweetspot, don't touch
KCS_BASE_FREQ = 2400

# init PyAudio
pa = pyaudio.PyAudio()


# Generate a sequence representing sign changes
def get_samples(device, monitor_device):

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
    while True:
        # obtain samples
        frames = stream.read(CHUNK, exception_on_overflow=False)
        if not frames:
            stream.close()
            break
        if monitor_device >= 0:
            stream2.write(frames)
        samples = np.frombuffer(frames, dtype=np.float32)
        yield samples


def decode_byte(bit_arr, little_endian=True):
    mask = 0x1 if little_endian else 0x80
    byte_val = np.uint8(0)
    for i in range(8):
        byte_val += bit_arr[i] * mask
        mask = (mask << 1) if little_endian else (mask >> 1)
    return byte_val


def sliding_window(arr, window_size):
    shape = (arr.size - window_size + 1, window_size)
    strides = arr.strides * 2
    return np.lib.stride_tricks.as_strided(arr, shape=shape, strides=strides)


def do_fft(sample, window_len):
    # extract dominant frequencies from sample
    sample_w = sliding_window(sample, window_len)
    sample_fft = np.abs(np.fft.fft(sample_w, axis=1))
    sample_fft = sample_fft.T[: window_len // 2]  # simple filter
    return np.argmax(sample_fft, axis=0)


def generate_freqs(sample_it, window_len, chunk_size):
    # take a stream of audio samples, emit a stream of dominant frequencies
    # NOTE: output length identical to input length
    buf_chunk_size = chunk_size + window_len - 1
    buf = np.zeros(window_len - 1)  # init w/padding
    while True:
        # ensure sample length enough for output chunk
        while len(buf) < buf_chunk_size:
            try:  # get more samples
                buf = np.concatenate([buf, next(sample_it)])
            except ValueError:  # invalid sample
                continue
            except StopIteration:  # output shorter than chunk_size
                # NOTE: # guaranteed at least window_len-1 samples
                yield do_fft(buf, window_len)
                return
        # calculate & yield dominant frequencies on sliding windows
        yield do_fft(buf[:buf_chunk_size], window_len)
        # prepare for next sample batch
        buf = buf[chunk_size:]


def generate_bytes(freq_it, symbol_len):
    # prepare items for matching codewords
    word_len = symbol_len * 11  # code word = 1+8+2 symbols
    start_kernel = -1 * np.ones(symbol_len) / symbol_len  # works on {-1,1}
    stop_kernel = 1 * np.ones(symbol_len) / symbol_len
    # consume dominant frequencies, output stream of bytes
    freq_buf = np.array([])
    while True:

        # get at least two codewords' length in buffer
        while len(freq_buf) < 2 * word_len:
            try:
                freq_buf = np.concatenate([freq_buf, next(freq_it)])
            except StopIteration:
                # TODO: handle remaining data
                return  # not enough frequency data left

        # detect signal & handle no-carrier case
        signal_on = ((freq_buf == 1) | (freq_buf == 2)).sum() / len(freq_buf) > 0.8
        if not signal_on:  # discard entire buffer
            freq_buf = np.array([])
            continue

        # cleanup signals
        freq_buf_pp = freq_buf.copy().clip(1, 2) * 2 - 3  # convert {1,2} to {-1,1}

        # try to match codeword
        # NOTE: sort-of-working self-correction on misalignment
        start_match = np.convolve(freq_buf_pp, start_kernel, mode="valid")
        start_offset = 1 * symbol_len  # move peak to start of start symbol
        start_match = np.pad(
            start_match[start_offset:],
            (len(start_kernel) - 1, start_offset),
            constant_values=0,
        )
        stop_match = np.convolve(freq_buf_pp, stop_kernel, mode="valid")
        stop_offset = (9 + 2) * symbol_len  # move peak to start of stop symbol
        stop_match = np.pad(
            stop_match[stop_offset:],
            (len(stop_kernel) - 1, stop_offset),
            constant_values=0,
        )
        match = start_match + stop_match > 1.0  # TODO
        match = np.pad(
            match[: -symbol_len // 2], (symbol_len // 2, 0), constant_values=0
        )

        # find start position of first codeword
        try:
            word_start = np.nonzero(match == True)[0][0] + symbol_len
            word_end = word_start + 8 * (symbol_len)
        except IndexError:  # didn't find start of code word
            freq_buf = freq_buf[-symbol_len:]  # keep last symbol
            continue  # skip to next loop

        # handle not enough samples in buffer
        if len(freq_buf) - word_start < word_len:
            freq_buf = freq_buf[word_start - 2 * symbol_len :]
            continue

        # plt.figure(figsize=(15,5))
        # plt.plot(freq_buf_pp, c="red")
        # plt.plot(start_match, c='blue')
        # plt.plot(stop_match, c='green')
        # plt.plot(match)
        # plt.scatter([word_start - symbol_len, word_start, word_end], [-0.5] * 3, c='black')
        # plt.show()

        # decode bits
        bits = (
            (freq_buf_pp[word_start:word_end].reshape((8, symbol_len))).mean(axis=1) > 0
        ).astype(int)
        byte_val = decode_byte(bits)
        yield byte_val

        # truncate decoded word from buffer
        freq_buf = freq_buf[word_end:]


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

    # calculate widths of base units and symbols
    window_len = int(
        (round(FRAMERATE / KCS_BASE_FREQ) * 2 + round(FRAMERATE / (KCS_BASE_FREQ // 2)))
        / 2
    )
    symbol_len = window_len * (1 if (opts.speed_mode > 0) else 4)

    # create generators
    sample_it = get_samples(device, opts.monitor_device)
    freq_it = generate_freqs(sample_it, window_len, CHUNK)
    byte_stream = generate_bytes(freq_it, symbol_len)

    # consume audio source and write to stdout (optionally to file)
    if opts.output_file:
        outf = open(opts.output_file, "wb")
    else:
        outf = sys.stdout.buffer.raw
    for b in byte_stream:
        outf.write(bytes([b]))
        outf.flush()
