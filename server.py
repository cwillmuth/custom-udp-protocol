"""A small local UDP Tic-Tac-Toe server that speaks the protocol in messaging.py.

Usage: python3 server.py [--host HOST] [--port PORT] [--client-symbol X|O]
                         [--drop-rate P]

It exists so the client can be run and tested without any outside server. By default
it listens on 127.0.0.1 only; pass --host 0.0.0.0 to accept other machines (this is
a demo server with no authentication or encryption, so don't expose it to the internet).

The rules, as seen from the client:
  * The first message from a new game ID (message ID 0) starts a game. The reply
    carries the board and a "to move" flag naming the symbol the client plays.
    X always moves first, so if the client is O the server has already moved.
  * Each client move must carry the message ID of the last server reply + 1.
  * The server answers with its own move, or a win / tie flag, or an error.
  * A repeated message (a client resend) gets the same reply again.
"""
import argparse
import random
import socket
import sys

from messaging import (
    EMPTY, X, O,
    FLAG_X_TO_MOVE, FLAG_O_TO_MOVE, FLAG_X_WIN, FLAG_O_WIN, FLAG_TIE, FLAG_ERROR,
    encode_message, decode_message, encode_game_state, decode_game_state,
)

RECV_BUFFER = 4096
MAX_GAMES = 1000        # oldest games are forgotten beyond this, so memory stays bounded
MAX_NAME_CHARS = 30     # player names are echoed back, so keep them short and printable

LINES = [(0, 1, 2), (3, 4, 5), (6, 7, 8), (0, 3, 6), (1, 4, 7), (2, 5, 8), (0, 4, 8), (2, 4, 6)]
SYMBOL_NAMES = {X: "X", O: "O"}
TO_MOVE = {X: FLAG_X_TO_MOVE, O: FLAG_O_TO_MOVE}
WIN = {X: FLAG_X_WIN, O: FLAG_O_WIN}


def winner(board):
    """Return X or O if that symbol has three in a row, otherwise None."""
    for a, b, c in LINES:
        if board[a] != EMPTY and board[a] == board[b] == board[c]:
            return board[a]
    return None


def choose_move(board, symbol, rng):
    """Pick a square for `symbol`: win if possible, else block, else take the centre or a random square."""
    other = O if symbol == X else X
    empties = [i for i, square in enumerate(board) if square == EMPTY]
    for mark in (symbol, other):
        for i in empties:
            board[i] = mark
            won = winner(board) == mark
            board[i] = EMPTY
            if won:
                return i
    if 4 in empties:
        return 4
    return rng.choice(empties)


def clean_name(text):
    """Strip control characters (they could mess with a terminal) and cap the length."""
    name = "".join(ch for ch in text if ch.isprintable())[:MAX_NAME_CHARS].strip()
    return name or "Player"


class Game:
    def __init__(self, board, client_symbol):
        self.board = board
        self.client_symbol = client_symbol
        self.expected_id = 2        # message ID the client must send next
        self.last_client_id = 0     # message ID of the last message we acted on
        self.last_reply = b""       # resent verbatim if the client repeats itself
        self.over = False


class TicTacToeServer:
    """Protocol logic only; no sockets, so it can be tested by calling handle() directly."""

    def __init__(self, client_symbol=None, drop_rate=0.0, rng=None):
        self.client_symbol = client_symbol   # X, O, or None for random
        self.drop_rate = drop_rate           # chance of ignoring a datagram, to exercise client retries
        self.rng = rng or random.Random()
        self.games = {}

    def handle(self, data):
        """Process one datagram. Returns the reply bytes, or None to stay silent."""
        if self.drop_rate and self.rng.random() < self.drop_rate:
            return None
        try:
            game_id, message_id, _flags, state, text = decode_message(data)
        except ValueError:
            return None  # too short to be one of our messages

        game = self.games.get(game_id)
        if game is None:
            if message_id != 0:
                return self._error(game_id, message_id, "Invalid Game ID")
            return self._start_game(game_id, text)
        if message_id == game.last_client_id:
            return game.last_reply  # the client is resending; answer the same way
        if message_id != game.expected_id:
            return self._error(game_id, message_id, "Invalid Message ID")
        if game.over:
            return self._error(game_id, message_id, "Game is over")
        return self._play_move(game_id, game, message_id, state)

    def _error(self, game_id, message_id, text):
        return encode_message(game_id, (message_id + 1) & 0xFF, FLAG_ERROR, 0, text)

    def _reply(self, game_id, reply_id, flags, board, text=""):
        return encode_message(game_id, reply_id, flags, encode_game_state(board), text)

    def _start_game(self, game_id, text):
        if len(self.games) >= MAX_GAMES:
            del self.games[next(iter(self.games))]  # forget the oldest game

        client_symbol = self.client_symbol or self.rng.choice((X, O))
        board = [EMPTY] * 9
        if client_symbol == O:
            board[choose_move(board, X, self.rng)] = X  # X goes first, so the server opens

        game = Game(board, client_symbol)
        greeting = f"Hello {clean_name(text)}! You are {SYMBOL_NAMES[client_symbol]}."
        game.last_reply = self._reply(game_id, 1, TO_MOVE[client_symbol], board, greeting)
        self.games[game_id] = game
        return game.last_reply

    def _play_move(self, game_id, game, message_id, state):
        try:
            new_board = decode_game_state(state)
        except ValueError:
            return self._error(game_id, message_id, "Invalid game state")

        changed = [i for i in range(9) if new_board[i] != game.board[i]]
        if (len(changed) != 1 or game.board[changed[0]] != EMPTY
                or new_board[changed[0]] != game.client_symbol):
            return self._error(game_id, message_id, "Invalid move")

        board = new_board
        server_symbol = O if game.client_symbol == X else X
        text = ""
        if winner(board) == game.client_symbol:
            flags, text, game.over = WIN[game.client_symbol], "You win!", True
        elif EMPTY not in board:
            flags, game.over = FLAG_TIE, True
        else:
            board[choose_move(board, server_symbol, self.rng)] = server_symbol
            if winner(board) == server_symbol:
                flags, text, game.over = WIN[server_symbol], "I win!", True
            elif EMPTY not in board:
                flags, game.over = FLAG_TIE, True
            else:
                flags = TO_MOVE[game.client_symbol]

        game.board = board
        reply_id = (message_id + 1) & 0xFF
        game.last_client_id = message_id
        game.expected_id = (reply_id + 1) & 0xFF
        game.last_reply = self._reply(game_id, reply_id, flags, board, text)
        return game.last_reply


def serve_forever(sock, server, stop_event=None):
    """Answer datagrams on `sock` until `stop_event` is set (or forever, if it is None)."""
    sock.settimeout(0.2)  # wake up regularly so a stop request is noticed
    while stop_event is None or not stop_event.is_set():
        try:
            data, address = sock.recvfrom(RECV_BUFFER)
        except socket.timeout:
            continue
        except OSError:
            return  # socket closed
        reply = server.handle(data)
        if reply is not None:
            sock.sendto(reply, address)


def _port(value):
    port = int(value)
    if not 0 < port < 65536:
        raise argparse.ArgumentTypeError(f"invalid port: {value}")
    return port


def main(argv=None):
    parser = argparse.ArgumentParser(description="Local UDP Tic-Tac-Toe demo server.")
    parser.add_argument("--host", default="127.0.0.1",
                        help="address to listen on (default: 127.0.0.1, this machine only)")
    parser.add_argument("--port", type=_port, default=7777, help="UDP port (default: 7777)")
    parser.add_argument("--client-symbol", choices=["X", "O"],
                        help="symbol the client plays (default: random each game)")
    parser.add_argument("--drop-rate", type=float, default=0.0, metavar="P",
                        help="ignore this fraction (0-1) of incoming messages, to try the client's retries")
    args = parser.parse_args(argv)
    if not 0 <= args.drop_rate <= 1:
        parser.error("--drop-rate must be between 0 and 1")

    symbol = {"X": X, "O": O}.get(args.client_symbol)
    server = TicTacToeServer(client_symbol=symbol, drop_rate=args.drop_rate)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        try:
            sock.bind((args.host, args.port))
        except OSError as exc:
            print(f"Could not listen on {args.host}:{args.port}: {exc}")
            return 1
        print(f"Listening on {args.host}:{args.port} (Ctrl+C to stop)")
        try:
            serve_forever(sock, server)
        except KeyboardInterrupt:
            print("\nStopped.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
