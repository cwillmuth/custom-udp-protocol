# Custom UDP Protocol

A command-line Tic-Tac-Toe client and a small local server that talk to each other
over UDP, using a custom binary message format.

Pure Python, standard library only. Nothing to install to play.

## Highlights

- **Binary protocol by hand:** packs and parses an 8-byte header (24-bit game ID,
  8-bit message ID, 14-bit flags, 18-bit board) with bit shifting and masking.
- **Reliability on top of UDP:** the client resends with exponential backoff
  (1s, 2s, 4s, 8s) and ignores stale or duplicate replies left over from retries.
- **Fails safely:** every move is validated locally before sending, and server
  errors are reported clearly instead of crashing.
- **Self-contained:** `server.py` lets you play and test without any outside server,
  and can drop messages on purpose so you can watch the retry logic work.
- **Tested:** 50+ tests cover the wire format, the client's retry and stale-reply
  handling, the server's rules, and full games between the two over loopback UDP.

## Quick start

Requires Python 3.10+. In one terminal, start the server:

```
python3 server.py
```

In another, start the client:

```
python3 client.py 127.0.0.1 7777
```

The client works with any server that speaks the [protocol](#protocol) below.

### Server options

| Option                 | Meaning                                                              |
|------------------------|----------------------------------------------------------------------|
| `--port PORT`          | UDP port to listen on (default `7777`)                               |
| `--host HOST`          | Address to listen on (default `127.0.0.1`, this machine only)        |
| `--client-symbol X\|O` | Force the symbol you play (default: random each game)                |
| `--drop-rate P`        | Ignore a fraction `P` (0 to 1) of messages, to see the client retry  |

Try `python3 server.py --drop-rate 0.4` and play a game: the client prints a
"resending" notice whenever it has to retry.

## How to play

1. Enter your name, or press Enter to use `Player`. You are asked once.
2. The server assigns you **X** or **O**. X moves first, so if you are O the server
   has already moved.
3. Empty squares are shown by number:

   ```
    1 | 2 | 3
   ---+---+---
    4 | 5 | 6
   ---+---+---
    7 | 8 | 9
   ```

4. On your turn, type the number of an empty square and press Enter. Invalid or
   taken squares are rejected and you are asked again. Enter `q` to quit.
5. The server replies with its move; any message it sends is printed.
6. When the game ends the client reports the winner or a tie, then asks
   `Play again? (y/n)`.

Example:

```
Enter your name: Alex

 1 | 2 | 3
---+---+---
 4 | 5 | 6
---+---+---
 7 | 8 | 9

Server: Hello Alex! You are X.
Your move (X) - enter a square 1-9 (or 'q' to quit): 5

 1 | 2 | 3
---+---+---
 O | X | 6
---+---+---
 7 | 8 | 9

Your move (X) - enter a square 1-9 (or 'q' to quit): 1
...
The game is a tie!
Play again? (y/n): n
```

## Protocol

Each datagram is an 8-byte big-endian header followed by optional UTF-8 text
(up to 250 characters, at most 1000 bytes).

| Field      | Bits | Notes                                                        |
|------------|------|--------------------------------------------------------------|
| Game ID    | 24   | Chosen randomly by the client, reused for the whole game     |
| Message ID | 8    | Client sends last server ID + 1; wraps from 255 to 0         |
| Flags      | 14   | See below                                                    |
| Game state | 18   | Nine 2-bit squares, row by row (`00` empty, `01` X, `10` O)  |

Flags are numbered from the **most significant** bit: X to move, O to move, X wins,
O wins, tie, error. The remaining bits are reserved.

The conversation: the client opens with a zeroed header and its name as text; the
server replies with the board and a "to move" flag naming the symbol the client
plays; then the two sides alternate moves until the server reports a win, a tie,
or an error.

## Design notes

- **Retries:** each message is sent at most 4 times in total, then the client prints
  an error and exits. For one send plus four retries, set `MAX_ATTEMPTS = 5` in
  `client.py`.
- **Stale replies:** if a reply is merely late, the server answers both the original
  message and the resend. The client drains the socket before each exchange and
  only accepts a reply whose message ID is the one it expects.
- **Lost replies:** a server that does not resend its reply to a duplicate message
  will answer the resent move with an "Invalid Message ID" error; the client
  displays it and exits. The bundled server avoids this by resending its cached reply.
- **Exit status:** 0 for a normal exit (including quitting), 1 for a server or
  network error, 2 for bad command-line arguments.

## Security notes

This is a learning project, not hardened software.

- UDP traffic here has no authentication or encryption. The game ID is a random
  label, not a secret.
- `server.py` listens on `127.0.0.1` by default. Only use `--host 0.0.0.0` on a
  network you trust.
- The server caps how many games it remembers, strips control characters from
  player names before echoing them, and ignores malformed datagrams.

## Project layout

```
client.py       Game loop, UI, and send/receive with retries
server.py       Local demo server (protocol logic + a small UDP loop)
messaging.py    Encoding and decoding of the binary messages
tests/          pytest suite
```

`server.py` is an independent implementation written from the message format in
`messaging.py`. It is meant for local play and testing and may differ from other
servers in edge cases.

## Tests

```
pip install pytest
pytest -v
```
