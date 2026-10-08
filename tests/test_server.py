"""Tests for server.py: the protocol logic directly, then real client-to-server games over loopback."""
import builtins
import itertools
import random
import socket
import threading

import pytest

import client
import server as srv
from messaging import (
    EMPTY, X, O,
    FLAG_X_TO_MOVE, FLAG_O_TO_MOVE, FLAG_X_WIN, FLAG_O_WIN, FLAG_TIE, FLAG_ERROR,
    encode_message, decode_message, encode_game_state, decode_game_state,
)

GAME = 0xABCDEF


def send(server, message_id, board=None, game_id=GAME, text=""):
    """Send one message to the server logic and decode its reply (None if it stayed silent)."""
    state = encode_game_state(board) if board else 0
    reply = server.handle(encode_message(game_id, message_id, 0, state, text))
    return None if reply is None else decode_message(reply)


def start(client_symbol, seed=1):
    server = srv.TicTacToeServer(client_symbol=client_symbol, rng=random.Random(seed))
    return server, send(server, 0, text="Alex")


def test_new_game_as_x_gives_client_the_first_move():
    _, (game_id, message_id, flags, state, text) = start(X)
    assert (game_id, message_id) == (GAME, 1)
    assert flags == FLAG_X_TO_MOVE
    assert decode_game_state(state) == [EMPTY] * 9
    assert "Alex" in text and "X" in text


def test_new_game_as_o_has_the_server_open_with_x():
    _, (_, _, flags, state, _) = start(O)
    assert flags == FLAG_O_TO_MOVE
    assert decode_game_state(state).count(X) == 1


def test_a_move_is_answered_with_a_server_move():
    server, (_, _, _, state, _) = start(X)
    board = decode_game_state(state)
    board[0] = X
    _, message_id, flags, state, _ = send(server, 2, board)
    reply_board = decode_game_state(state)
    assert message_id == 3
    assert flags == FLAG_X_TO_MOVE
    assert reply_board.count(X) == 1 and reply_board.count(O) == 1


def test_client_win_is_reported():
    server, _ = start(X)
    server.games[GAME].board = [X, X, EMPTY, O, O, EMPTY, EMPTY, EMPTY, EMPTY]
    _, _, flags, state, _ = send(server, 2, [X, X, X, O, O, EMPTY, EMPTY, EMPTY, EMPTY])
    assert flags == FLAG_X_WIN
    assert send(server, 4, [X, X, X, O, O, X, EMPTY, EMPTY, EMPTY])[2] & FLAG_ERROR  # game is over


def test_server_win_is_reported():
    server, _ = start(X)
    server.games[GAME].board = [O, O, EMPTY, X, X, EMPTY, EMPTY, EMPTY, X]
    # Client wastes its move on square 7; the server completes the top row.
    _, _, flags, state, text = send(server, 2, [O, O, EMPTY, X, X, EMPTY, EMPTY, X, X])
    assert flags == FLAG_O_WIN
    assert decode_game_state(state)[:3] == [O, O, O]


def test_tie_is_reported():
    server, _ = start(X)
    # X O X / X O O / O X _ : X to play the last square, which completes no line.
    server.games[GAME].board = [X, O, X, X, O, O, O, X, EMPTY]
    _, _, flags, _, _ = send(server, 2, [X, O, X, X, O, O, O, X, X])
    assert flags == FLAG_TIE


def test_choose_move_wins_then_blocks_then_takes_centre():
    rng = random.Random(1)
    assert srv.choose_move([O, O, EMPTY] + [EMPTY] * 6, O, rng) == 2            # win
    assert srv.choose_move([X, X, EMPTY] + [EMPTY] * 6, O, rng) == 2            # block
    assert srv.choose_move([X] + [EMPTY] * 8, O, rng) == 4                      # centre


def test_repeated_message_gets_the_same_reply_back():
    server, (_, _, _, state, _) = start(X)
    board = decode_game_state(state)
    board[0] = X
    packet = encode_message(GAME, 2, 0, encode_game_state(board), "")
    assert server.handle(packet) == server.handle(packet)
    assert server.games[GAME].board.count(O) == 1   # the repeat did not trigger a second server move


def test_wrong_message_id_is_an_error():
    server, _ = start(X)
    reply = send(server, 7, [X] + [EMPTY] * 8)
    assert reply[2] & FLAG_ERROR and reply[4] == "Invalid Message ID"


def test_unknown_game_id_is_an_error():
    server = srv.TicTacToeServer()
    reply = send(server, 2, [X] + [EMPTY] * 8)
    assert reply[2] & FLAG_ERROR and reply[4] == "Invalid Game ID"


@pytest.mark.parametrize("board", [
    [O] + [EMPTY] * 8,           # wrong symbol
    [X, X] + [EMPTY] * 7,        # two squares changed
    [EMPTY] * 9,                 # nothing changed
])
def test_illegal_moves_are_errors(board):
    server, _ = start(X)
    reply = send(server, 2, board)
    assert reply[2] & FLAG_ERROR and reply[4] == "Invalid move"


def test_occupied_square_is_an_error():
    server, _ = start(O)
    taken = decode_game_state(encode_game_state(server.games[GAME].board)).index(X)
    board = [EMPTY] * 9
    board[taken] = O
    assert send(server, 2, board)[2] & FLAG_ERROR


def test_invalid_game_state_bits_are_an_error():
    server, _ = start(X)
    reply = server.handle(encode_message(GAME, 2, 0, 0b11 << 16, ""))
    assert decode_message(reply)[2] & FLAG_ERROR


def test_garbage_datagram_is_ignored():
    assert srv.TicTacToeServer().handle(b"\x00\x01") is None


def test_message_ids_wrap_around():
    server, _ = start(X)
    game = server.games[GAME]
    game.expected_id = 254
    _, message_id, _, _, _ = send(server, 254, [X] + [EMPTY] * 8)
    assert message_id == 255 and game.expected_id == 0


def test_names_are_cleaned_before_being_echoed():
    assert srv.clean_name("\x1b[31mevil\x07name") == "[31mevilname"   # control characters removed
    assert len(srv.clean_name("a" * 500)) == srv.MAX_NAME_CHARS
    assert srv.clean_name("\n\t") == "Player"
    _, (_, _, _, _, text) = start(X)
    assert "Alex" in text


def test_old_games_are_forgotten_beyond_the_limit(monkeypatch):
    monkeypatch.setattr(srv, "MAX_GAMES", 3)
    server = srv.TicTacToeServer(client_symbol=X)
    for game_id in range(1, 6):
        send(server, 0, game_id=game_id)
    assert list(server.games) == [3, 4, 5]


def test_drop_rate_makes_the_server_ignore_messages():
    server = srv.TicTacToeServer(drop_rate=1.0)
    assert send(server, 0) is None


# --- real client against the real server, over loopback UDP ---------------------------

class RunningServer:
    def __init__(self, logic):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", 0))
        self.address = self.sock.getsockname()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=srv.serve_forever, args=(self.sock, logic, self._stop), daemon=True)

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        self._thread.join()
        self.sock.close()


def play_with_scripted_input(monkeypatch, address):
    """Run client.play_game, answering each prompt with 1, 2, ... 9, 1, ... (taken squares are re-asked)."""
    answers = itertools.cycle("123456789")
    monkeypatch.setattr(builtins, "input", lambda prompt="": next(answers))
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        return client.play_game(sock, address, "Tester")


@pytest.mark.parametrize("client_symbol", [X, O])
@pytest.mark.parametrize("seed", range(5))
def test_client_plays_full_games_against_the_server(monkeypatch, client_symbol, seed):
    monkeypatch.setattr(client, "INITIAL_TIMEOUT", 0.2)
    logic = srv.TicTacToeServer(client_symbol=client_symbol, rng=random.Random(seed))
    with RunningServer(logic) as running:
        assert play_with_scripted_input(monkeypatch, running.address) == client.GAME_FINISHED


def test_client_retries_when_the_server_drops_a_message(monkeypatch):
    class DropFirst(random.Random):
        calls = 0

        def random(self):
            DropFirst.calls += 1
            return 0.0 if DropFirst.calls == 1 else 1.0   # drop only the very first datagram

    monkeypatch.setattr(client, "INITIAL_TIMEOUT", 0.05)
    logic = srv.TicTacToeServer(client_symbol=X, drop_rate=0.5, rng=DropFirst(1))
    with RunningServer(logic) as running:
        assert play_with_scripted_input(monkeypatch, running.address) == client.GAME_FINISHED
