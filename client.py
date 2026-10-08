"""UDP Tic-Tac-Toe client.

Usage: python3 client.py <hostname/ip> <port>

Conversation (the byte layout is documented in messaging.py):
  1. Client -> Server: random game ID, all other fields zero, player name as text.
  2. Server -> Client: the board, plus a flag saying whether we play X or O.
  3. Client -> Server: our move, with message ID = last server ID + 1.
  4. Server -> Client: its move, or a win / tie / error. Repeat 3-4 until the game ends.
"""
import random
import socket
import sys
import time

from messaging import (
    EMPTY, X, O,
    FLAG_X_TO_MOVE, FLAG_O_TO_MOVE, FLAG_X_WIN, FLAG_O_WIN, FLAG_TIE, FLAG_ERROR,
    encode_message, decode_message, encode_game_state, decode_game_state,
)

MAX_ATTEMPTS = 4        # total sends per message: waits of 1s, 2s, 4s, 8s
INITIAL_TIMEOUT = 1.0   # seconds; doubles after each failed attempt
RECV_BUFFER = 4096      # an 8-byte header plus at most 1000 bytes of text

SYMBOLS = {EMPTY: " ", X: "X", O: "O"}

# The ways play_game() can end; main() maps them to exit codes.
GAME_FINISHED, GAME_QUIT, GAME_ERROR = "finished", "quit", "error"


def print_board(board):
    """Print the board, showing the number 1-9 on each empty square."""
    cells = [SYMBOLS[s] if s != EMPTY else str(i + 1) for i, s in enumerate(board)]
    print()
    for row in range(3):
        print(" " + " | ".join(cells[row * 3:row * 3 + 3]))
        if row < 2:
            print("---+---+---")
    print()


def prompt_move(board, symbol):
    """Ask until the player enters a legal move.

    Returns a square index 0-8, or None if the player quits. Illegal moves are
    rejected here, so they are never sent to the server.
    """
    while True:
        choice = input(f"Your move ({SYMBOLS[symbol]}) - enter a square 1-9 (or 'q' to quit): ").strip()
        if choice.lower() in ("q", "quit"):
            return None
        if not choice.isdigit() or not 1 <= int(choice) <= 9:
            print("Invalid input: please enter a number from 1 to 9.")
            continue
        square = int(choice) - 1  # squares are shown as 1-9 but indexed 0-8
        if board[square] != EMPTY:
            print(f"Square {choice} is already taken. Choose an empty square.")
            continue
        return square


def report_result(flags, my_symbol):
    """Tell the player how the game ended."""
    if flags & FLAG_TIE:
        print("The game is a tie!")
    elif flags & FLAG_X_WIN:
        print("X wins! " + ("You win!" if my_symbol == X else "The server wins."))
    elif flags & FLAG_O_WIN:
        print("O wins! " + ("You win!" if my_symbol == O else "The server wins."))


def drain_socket(sock):
    """Discard datagrams already waiting on the socket.

    If a reply is late, the server answers both our original message and our
    resend. The extra reply would otherwise be read as the answer to the next move.
    """
    sock.setblocking(False)  # recvfrom raises BlockingIOError once the buffer is empty
    try:
        while True:
            sock.recvfrom(RECV_BUFFER)
    except BlockingIOError:
        pass


def send_and_receive(sock, address, packet, game_id, expected_id=None):
    """Send `packet` and return the server's reply as (game_id, message_id, flags, state, text).

    Resends after each timeout and raises TimeoutError once attempts run out.
    Replies for another game, or whose message ID is not `expected_id`, are stale
    leftovers from earlier retries and are ignored.
    """
    # Once per exchange, not per retry: a reply arriving between retries is valid.
    drain_socket(sock)

    timeout = INITIAL_TIMEOUT
    for attempt in range(1, MAX_ATTEMPTS + 1):
        sock.sendto(packet, address)
        deadline = time.monotonic() + timeout

        # Keep listening until the deadline, skipping packets we don't want.
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            sock.settimeout(remaining)
            try:
                data, _ = sock.recvfrom(RECV_BUFFER)
            except socket.timeout:
                break
            try:
                fields = decode_message(data)
            except ValueError:
                continue  # too short to be one of our messages

            # fields = (game_id, message_id, flags, state, text)
            # Errors are returned first: an "Invalid Game ID" error may not echo our ID.
            if fields[2] & FLAG_ERROR:
                return fields
            if fields[0] != game_id:
                continue
            if expected_id is not None and fields[1] != expected_id:
                continue
            return fields

        if attempt < MAX_ATTEMPTS:
            print(f"No response after {timeout:g}s - resending (attempt {attempt + 1} of {MAX_ATTEMPTS})...")
            timeout *= 2

    raise TimeoutError(f"No response from server after {MAX_ATTEMPTS} attempts.")


def play_game(sock, address, name):
    """Play one game. Returns GAME_FINISHED, GAME_QUIT, or GAME_ERROR."""
    game_id = random.randint(0, 0xFFFFFF)  # 24 bits, reused in every message of this game

    # Initial message: random game ID, everything else zero, our name as text.
    packet = encode_message(game_id, 0, 0, 0, name)
    # The reply is (game_id, message_id, flags, state, text); we already know the game ID.
    _, last_id, flags, state, text = send_and_receive(sock, address, packet, game_id)

    # The first reply's flags say which symbol we play.
    if flags & FLAG_X_TO_MOVE:
        my_symbol = X
    elif flags & FLAG_O_TO_MOVE:
        my_symbol = O
    else:
        my_symbol = None  # unexpected; reported below unless it was an error

    while True:
        # Check for an error before decoding: an error's state field may be meaningless.
        if flags & FLAG_ERROR:
            print(f"\nERROR from server: {text or '(no message provided)'}")
            return GAME_ERROR

        try:
            board = decode_game_state(state)
        except ValueError as exc:
            print(f"Server sent an invalid game state: {exc}")
            return GAME_ERROR

        print_board(board)
        if text:
            print(f"Server: {text}")

        if flags & (FLAG_X_WIN | FLAG_O_WIN | FLAG_TIE):
            report_result(flags, my_symbol)
            return GAME_FINISHED

        if my_symbol is None:
            print("The server did not assign a player symbol; cannot continue.")
            return GAME_ERROR

        move = prompt_move(board, my_symbol)
        if move is None:
            print("Quitting game.")
            return GAME_QUIT
        board[move] = my_symbol

        # Message ID = last server ID + 1; "& 0xFF" rolls 255 over to 0.
        # The server answers with our ID + 1, which is how stale replies are spotted.
        message_id = (last_id + 1) & 0xFF
        packet = encode_message(game_id, message_id, 0, encode_game_state(board), "")
        _, last_id, flags, state, text = send_and_receive(
            sock, address, packet, game_id, expected_id=(message_id + 1) & 0xFF)


def main():
    """Run games until the player stops. Exit code: 0 normal, 1 error, 2 bad arguments."""
    if len(sys.argv) != 3:
        print("Usage: python3 client.py <hostname/ip> <port>")
        return 2
    host = sys.argv[1]
    try:
        port = int(sys.argv[2])
        if not 0 < port < 65536:
            raise ValueError  # out of range; handled by the except below
    except ValueError:
        print(f"Invalid port number: {sys.argv[2]}")
        return 2

    try:
        # Accepts a hostname or IP; [0][4] is the first result's (ip, port) address.
        address = socket.getaddrinfo(host, port, socket.AF_INET, socket.SOCK_DGRAM)[0][4]
    except socket.gaierror as exc:
        print(f"Could not resolve host '{host}': {exc}")
        return 1

    exit_code = 0
    # SOCK_DGRAM = UDP. "with" closes the socket however we exit.
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        try:
            name = input("Enter your name: ").strip() or "Player"
            while True:
                result = play_game(sock, address, name)
                if result == GAME_ERROR:
                    exit_code = 1
                    break
                if result == GAME_QUIT:
                    break
                if input("Play again? (y/n): ").strip().lower() not in ("y", "yes"):
                    break
        except TimeoutError as exc:
            print(f"Error: {exc}")
            exit_code = 1
        except (KeyboardInterrupt, EOFError):
            print("\nExiting.")
        except OSError as exc:
            print(f"Network error: {exc}")
            exit_code = 1
    return exit_code


if __name__ == "__main__":
    sys.exit(main())