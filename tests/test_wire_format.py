"""Tests for the wire format in messaging.py."""
import random

import pytest

from messaging import (
    EMPTY, X, O,
    FLAG_X_TO_MOVE, FLAG_O_TO_MOVE, FLAG_X_WIN, FLAG_O_WIN, FLAG_TIE, FLAG_ERROR,
    MAX_MESSAGE_CHARS,
    encode_message, decode_message, encode_game_state, decode_game_state,
)


def test_flag_bit_positions():
    # Bit 0 is the most significant of the 14 flag bits.
    assert FLAG_X_TO_MOVE == 0b10000000000000
    assert FLAG_O_TO_MOVE == 0b01000000000000
    assert FLAG_X_WIN == 0b00100000000000
    assert FLAG_O_WIN == 0b00010000000000
    assert FLAG_TIE == 0b00001000000000
    assert FLAG_ERROR == 0b00000100000000


def test_game_state_square_zero_is_leftmost():
    board = [X, O] + [EMPTY] * 7
    assert encode_game_state(board) == 0b01_10_00_00_00_00_00_00_00


def test_game_state_round_trip_random_boards():
    rng = random.Random(1)
    for _ in range(200):
        board = [rng.choice((EMPTY, X, O)) for _ in range(9)]
        assert decode_game_state(encode_game_state(board)) == board


def test_decode_game_state_rejects_unused_square_value():
    with pytest.raises(ValueError):
        decode_game_state(0b11_00_00_00_00_00_00_00_00)


def test_encode_game_state_rejects_bad_boards():
    with pytest.raises(ValueError):
        encode_game_state([EMPTY] * 8)
    with pytest.raises(ValueError):
        encode_game_state([EMPTY] * 8 + [3])


def test_encode_message_exact_bytes():
    state = encode_game_state([X, O] + [EMPTY] * 7)
    packet = encode_message(0xABCDEF, 0x12, FLAG_X_TO_MOVE, state, "hi")
    assert packet == bytes.fromhex("ABCDEF12 80018000".replace(" ", "")) + b"hi"


def test_message_round_trip_with_unicode_text():
    packet = encode_message(0x123456, 255, FLAG_O_WIN | FLAG_ERROR, 0x2AAAA, "héllo ✓")
    assert decode_message(packet) == (0x123456, 255, FLAG_O_WIN | FLAG_ERROR, 0x2AAAA, "héllo ✓")


def test_text_is_truncated_to_250_characters():
    packet = encode_message(1, 1, 0, 0, "x" * 400)
    assert len(decode_message(packet)[4]) == MAX_MESSAGE_CHARS


def test_decode_message_rejects_short_packet():
    with pytest.raises(ValueError):
        decode_message(b"\x00" * 7)


def test_decode_message_tolerates_invalid_utf8():
    packet = encode_message(1, 1, 0, 0, "") + b"\xff\xfe"
    assert isinstance(decode_message(packet)[4], str)


@pytest.mark.parametrize("args", [
    (1 << 24, 0, 0, 0),   # game ID too big
    (0, 256, 0, 0),       # message ID too big
    (0, 0, 1 << 14, 0),   # flags too big
    (0, 0, 0, 1 << 18),   # state too big
    (-1, 0, 0, 0),        # negative
])
def test_encode_message_rejects_out_of_range_fields(args):
    with pytest.raises(ValueError):
        encode_message(*args, "")
