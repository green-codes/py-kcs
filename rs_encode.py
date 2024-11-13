#

# reed-solomon encoder

import sys
import optparse

from reedsolo import RSCodec


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
    for b in input_f.read():
        buffer.append(b)
        if len(buffer) == opts.k:
            stdout.write(rsc.encode(buffer))
            stdout.flush()
            buffer = []
    if len(buffer) > 0:
        stdout.write(rsc.encode(buffer))
        stdout.flush()

