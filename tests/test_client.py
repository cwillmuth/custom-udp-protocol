"""Tests for client.py, run against a scripted fake server on the loopback interface."""
import builtins
import socket
import threading

import pytest

import client
from messaging import (
    EMPTY, X, O,
    FLAG_X_TO_MOVE, FLAG_X_WIN, FLAG_ERROR,
    encode_message, decode_message, encode_game_state, decode_game_state,
)

EMPTY_STATE = encode_game_state([EMPTY] * 9)


def reply(game_id, message_id, flags=0, board=None, text=""):
    state = encode_game_state(board) if board else EMPTY_STATE
    return encode_message(game_id, message_id, flags, state, text)


class FakeServer:
    """Calls handler(n, packet) for the n-th packet received and sends back what it returns."""

    def __init__(self, handler):
        self.handler = handler
        self.received = []
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.settimeout(0.05)
        self.address = self.sock.getsockname()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        while not self._stop.is_set():
            try:
                data, addr = self.sock.recvfrom(4096)
            except socket.timeout:
                continue
            except OSError:
                return
            self.received.append(data)
            for packet in self.handler(len(self.received), data) or []:
                self.sock.sendto(packet, addr)

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        self._thread.join()
        self.sock.close()


@pytest.fixture
def sock():
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.bind(("127.0.0.1", 0))
        yield s


@pytest.fixture(autouse=True)
def fast_timeouts(monkeypatch):
    monkeypatch.setattr(client, "INITIAL_TIMEOUT", 0.05)


def test_resends_after_timeout_then_succeeds(sock):
    packet = encode_message(7, 0, 0, 0, "me")
    with FakeServer(lambda n, data: [reply(7, 1)] if n == 2 else None) as server:
        fields = client.send_and_receive(sock, server.address, packet, 7)
    assert fields[:2] == (7, 1)
    assert server.received == [packet, packet]


def test_raises_timeout_after_max_attempts(sock, monkeypatch):
    monkeypatch.setattr(client, "MAX_ATTEMPTS", 3)
    packet = encode_message(7, 0, 0, 0, "me")
    with FakeServer(lambda n, data: None) as server:
        with pytest.raises(TimeoutError):
            client.send_and_receive(sock, server.address, packet, 7)
    assert len(server.received) == 3


def test_ignores_replies_with_unexpected_message_id(sock):
    packet = encode_message(7, 2, 0, 0, "")
    # A stale reply (ID 1) arrives before the real one (ID 3).
    with FakeServer(lambda n, data: [reply(7, 1), reply(7, 3)]) as server:
        fields = client.send_and_receive(sock, server.address, packet, 7, expected_id=3)
    assert fields[1] == 3


def test_ignores_replies_for_other_games(sock):
    packet = encode_message(7, 0, 0, 0, "")
    with FakeServer(lambda n, data: [reply(99, 1), reply(7, 1)]) as server:
        fields = client.send_and_receive(sock, server.address, packet, 7)
    assert fields[0] == 7


def test_error_reply_is_returned_even_if_game_id_differs(sock):
    packet = encode_message(7, 0, 0, 0, "")
    with FakeServer(lambda n, data: [reply(0, 0, FLAG_ERROR, text="Invalid Game ID")]) as server:
        fields = client.send_and_receive(sock, server.address, packet, 7)
    assert fields[2] & FLAG_ERROR
    assert fields[4] == "Invalid Game ID"


def test_discards_datagrams_that_were_already_waiting(sock):
    packet = encode_message(7, 0, 0, 0, "")
    sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sender.sendto(reply(7, 1, text="stale"), sock.getsockname())
    # Give loopback a moment to deliver the stale datagram before we start.
    threading.Event().wait(0.05)
    with FakeServer(lambda n, data: [reply(7, 1, text="fresh")]) as server:
        fields = client.send_and_receive(sock, server.address, packet, 7)
    sender.close()
    assert fields[4] == "fresh"


def test_full_game_win(sock, monkeypatch, capsys):
    """Server assigns X, client plays square 5, server answers with an X win."""
    won = [EMPTY] * 9
    won[4] = X

    def handler(n, data):
        game_id, message_id, *_ = decode_message(data)
        if n == 1:
            return [reply(game_id, 1, FLAG_X_TO_MOVE)]
        return [reply(game_id, message_id + 1, FLAG_X_WIN, won, "Nice game")]

    monkeypatch.setattr(builtins, "input", lambda prompt="": "5")
    with FakeServer(handler) as server:
        result = client.play_game(sock, server.address, "Tester")

    assert result == client.GAME_FINISHED
    assert "You win!" in capsys.readouterr().out
    first, second = (decode_message(p) for p in server.received)
    assert first[1:3] == (0, 0) and first[4] == "Tester"      # initial message
    assert second[1] == 2                                      # last server ID + 1
    assert decode_game_state(second[3])[4] == X                # our move is on the board


def test_play_game_reports_server_error(sock, capsys):
    def handler(n, data):
        return [reply(0, 0, FLAG_ERROR, text="Bad request")]

    with FakeServer(handler) as server:
        result = client.play_game(sock, server.address, "Tester")
    assert result == client.GAME_ERROR
    assert "Bad request" in capsys.readouterr().out


def test_player_can_quit(sock, monkeypatch):
    monkeypatch.setattr(builtins, "input", lambda prompt="": "q")
    with FakeServer(lambda n, data: [reply(decode_message(data)[0], 1, FLAG_X_TO_MOVE)]) as server:
        assert client.play_game(sock, server.address, "Tester") == client.GAME_QUIT


def test_prompt_move_rejects_bad_and_taken_squares(monkeypatch):
    board = [EMPTY] * 9
    board[0] = O
    answers = iter(["abc", "0", "10", "1", "3"])
    monkeypatch.setattr(builtins, "input", lambda prompt="": next(answers))
    assert client.prompt_move(board, X) == 2   # "3" -> index 2
