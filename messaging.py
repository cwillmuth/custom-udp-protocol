"""Encoding and decoding of Tic-Tac-Toe UDP messages.

Wire format: an 8-byte big-endian header, followed by optional UTF-8 text.

    |<---- 24 ---->|<-- 8 -->|<----- 14 ----->|<------ 18 ------>|
    |   game ID    | msg ID  |     flags      |    game state    | text...

"Bit 0" of every field is its MOST significant (leftmost) bit, the reverse of
Python's usual numbering. The game state is nine 2-bit squares, row by row, with
square 0 (top-left) in the top two bits. A board is a list of 9 values
(EMPTY, X or O).
"""

# Square values. 0b11 is unused; the server treats it as an error.
EMPTY = 0b00
X = 0b01
O = 0b10

FLAG_BITS = 14


def _flag(bit):
    """Mask for flag bit number `bit`, counted from the left (bit 0 = 2**13)."""
    return 1 << (FLAG_BITS - 1 - bit)


# Test a flag with `flags & FLAG_TIE`: the result is non-zero if the flag is set.
FLAG_X_TO_MOVE = _flag(0)   # 10000000000000
FLAG_O_TO_MOVE = _flag(1)   # 01000000000000
FLAG_X_WIN = _flag(2)       # 00100000000000
FLAG_O_WIN = _flag(3)       # 00010000000000
FLAG_TIE = _flag(4)         # 00001000000000
FLAG_ERROR = _flag(5)       # 00000100000000 (bits 6-13 are reserved, always 0)

GAME_ID_BITS = 24
MESSAGE_ID_BITS = 8
STATE_BITS = 18
HEADER_BYTES = 8            # 24 + 8 + 14 + 18 = 64 bits
MAX_MESSAGE_CHARS = 250     # 250 characters is at most 1000 UTF-8 bytes
NUM_SQUARES = 9


def _check_range(name, value, bits):
    """Raise ValueError unless `value` is an integer that fits in `bits` bits."""
    if not isinstance(value, int) or not 0 <= value < (1 << bits):
        raise ValueError(f"{name} must be an integer that fits in {bits} bits, got {value!r}")


def encode_message(game_id, message_id, flags, game_state, message):
    """Pack the fields into bytes ready to send over UDP.

    game_state is the already-encoded 18-bit integer (see encode_game_state).
    """
    _check_range("game_id", game_id, GAME_ID_BITS)
    _check_range("message_id", message_id, MESSAGE_ID_BITS)
    _check_range("flags", flags, FLAG_BITS)
    _check_range("game_state", game_state, STATE_BITS)

    # Each field is shifted left by the total width of the fields to its right
    # (40, 32, 18 and 0 bits), then the pieces are OR-ed into one 64-bit integer.
    header = (
        (game_id << (MESSAGE_ID_BITS + FLAG_BITS + STATE_BITS))
        | (message_id << (FLAG_BITS + STATE_BITS))
        | (flags << STATE_BITS)
        | game_state
    )
    text = message[:MAX_MESSAGE_CHARS].encode("utf-8")
    return header.to_bytes(HEADER_BYTES, byteorder="big") + text


def decode_message(message):
    """Unpack bytes from the wire.

    Returns (game_id, message_id, flags, game_state, text).
    Raises ValueError if the packet is too short to contain a header.
    """
    if len(message) < HEADER_BYTES:
        raise ValueError(f"message too short: {len(message)} bytes (need at least {HEADER_BYTES})")

    header = int.from_bytes(message[:HEADER_BYTES], byteorder="big")

    # Reverse of encoding: peel fields off the right end by masking the lowest
    # N bits, then shifting them away.
    game_state = header & ((1 << STATE_BITS) - 1)
    header >>= STATE_BITS
    flags = header & ((1 << FLAG_BITS) - 1)
    header >>= FLAG_BITS
    message_id = header & ((1 << MESSAGE_ID_BITS) - 1)
    header >>= MESSAGE_ID_BITS
    game_id = header & ((1 << GAME_ID_BITS) - 1)

    # errors="replace" keeps a malformed packet from crashing the client.
    text = message[HEADER_BYTES:].decode("utf-8", errors="replace")
    return game_id, message_id, flags, game_state, text


def encode_game_state(game_state):
    """Convert a board (9 squares, each EMPTY/X/O) to an 18-bit integer."""
    if len(game_state) != NUM_SQUARES:
        raise ValueError(f"board must have {NUM_SQUARES} squares, got {len(game_state)}")

    bits = 0
    for square in game_state:
        if square not in (EMPTY, X, O):
            raise ValueError(f"invalid square value: {square!r}")
        # Make room for two bits, then add this square; square 0 ends up leftmost.
        bits = (bits << 2) | square
    return bits


def decode_game_state(game_bits):
    """Convert an 18-bit integer to a list of 9 squares (EMPTY/X/O)."""
    _check_range("game_bits", game_bits, STATE_BITS)

    board = []
    for i in range(NUM_SQUARES):
        # Square 0 sits at shift 16 and square 8 at shift 0.
        shift = 2 * (NUM_SQUARES - 1 - i)
        square = (game_bits >> shift) & 0b11
        if square == 0b11:
            raise ValueError(f"invalid game state: square {i} has value 0b11")
        board.append(square)
    return board