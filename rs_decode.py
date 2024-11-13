#

# reed-solomon decoder

import sys
import select
import optparse

from reedsolo import RSCodec, ReedSolomonError


if __name__ == "__main__":
    parser = optparse.OptionParser()
    parser.add_option(
        "-n",
        "--codeword-size",
        type="int",
        default=8,
        dest="n",
        help="codeword size (message+ecc)",
    )
    parser.add_option(
        "-k",
        "--message-size",
        type="int",
        default=4,
        dest="k",
        help="message size",
    )
    opts, args = parser.parse_args()

    # determine I/O files
    if len(args) != 1:
        input_f = sys.stdin.buffer.raw
    else:
        # load input file
        input_f = open(args[0], "rb")
    stdout = sys.stdout.buffer.raw
    
    # initialize reed-solomon encoder
    rsc = RSCodec(opts.n - opts.k)

    # encode and output
    buffer = []
    while True:
        try:
            read_ready, _, _ = select.select([sys.stdin.buffer.raw],[],[],1.)
            if len(read_ready) > 0:
                    buffer.append(read_ready[0].read(1))  # blocks until byte avail
            if len(buffer) == opts.n:
                try:
                    stdout.write(rsc.decode(b''.join(buffer))[0])
                except ReedSolomonError as e:
                    sys.stderr.buffer.raw.write(f"{repr(e)}".encode('utf-8'))
                stdout.flush()
                buffer = []
        except KeyboardInterrupt:
            break  # may hang on last few bytes, interrupt to jump out
    if len(buffer) > 0:
        try:
            stdout.write(rsc.decode(b''.join(buffer))[0])
        except ReedSolomonError as e:
            sys.stderr.buffer.raw.write(f"{repr(e)}".encode('utf-8'))
        stdout.flush()


