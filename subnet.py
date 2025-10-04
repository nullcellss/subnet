#!/usr/bin/env python3

import asyncio
import argparse
import datetime
import logging
import signal
import os
import json
import hashlib
import binascii
import secrets
from collections import deque
from PIL import Image

HOST = "0.0.0.0"
PORT = 2323
MAX_CLIENTS = 200
HISTORY_SIZE = 200
RATE_LIMIT_PER_SEC = 5
FORUM_FILE = "forum.txt"
USER_DB = "users.json"
BANNED_NICKS = {"admin", "root", "system", "moderator", "server"}

WELCOME = (
    "\033[95m██╗    ██╗\033[96m███████╗\033[94m ██████╗ \033[92m███╗   ██╗\033[95m███████╗\033[0m\n"
    "\033[95m██║    ██║\033[96m██╔════╝\033[94m██╔═══██╗\033[92m████╗  ██║\033[95m██╔════╝\033[0m\n"
    "\033[95m██║ █╗ ██║\033[96m█████╗  \033[94m██║██╗██║\033[92m██╔██╗ ██║\033[95m█████╗  \033[0m\n"
    "\033[95m██║███╗██║\033[96m██╔══╝  \033[94m██║██║██║\033[92m██║╚██╗██║\033[95m██╔══╝  \033[0m\n"
    "\033[95m╚███╔███╔╝\033[96m███████╗\033[94m╚█║████╔╝\033[92m██║ ╚████║\033[95m███████╗\033[0m\n"
    "\033[95m ╚══╝╚══╝ \033[96m╚══════╝\033[94m ╚╝╚═══╝ \033[92m╚═╝  ╚═══╝\033[95m╚══════╝\033[0m\n"
    "\033[94m═════════════════════════════════════════════════════════════════\033[0m\n"
    "\033[95m» \033[96mWELCOME TO \033[92mSUBNET BBS\033[95m — \033[94mREAL-TIME TERMINAL NODE\033[0m\n"
    "\033[94m═════════════════════════════════════════════════════════════════\033[0m\n"
    "\033[95mCommands:\033[96m /register <user> <pass> \033[94m/login <user> <pass> \033[92m/logout \033[95m/nick <name>\033[0m\n"
    "\033[95m          \033[96m/who \033[94m/msg <user> <text> \033[92m/forum \033[95m/forum post <text>\033[0m\n"
    "\033[95m          \033[96m/avatar <file> \033[94m/clear \033[92m/quit \033[95m/help\033[0m\n"
)

LOGFILE = "subnet.log"
logger = logging.getLogger("subnet")
logger.setLevel(logging.INFO)
handler = logging.FileHandler(LOGFILE)
handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
logger.addHandler(handler)

clients = {}  # StreamWriter -> Client
history = deque(maxlen=HISTORY_SIZE)
NEXT_CLIENT_ID = 1


# ---------------------
# user DB helpers (file-based, PBKDF2)
# ---------------------
def load_users():
    if not os.path.exists(USER_DB):
        return {}
    try:
        with open(USER_DB, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_users(users):
    tmp = USER_DB + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(users, f, indent=2)
    os.replace(tmp, USER_DB)


def hash_password(password, salt=None):
    if salt is None:
        salt = secrets.token_bytes(16)
    else:
        salt = binascii.unhexlify(salt)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 150000)
    return binascii.hexlify(salt).decode(), binascii.hexlify(dk).decode()


def verify_password(stored_salt_hex, stored_hash_hex, password_attempt):
    salt = binascii.unhexlify(stored_salt_hex)
    dk = hashlib.pbkdf2_hmac("sha256", password_attempt.encode("utf-8"), salt, 150000)
    return binascii.hexlify(dk).decode() == stored_hash_hex


# ---------------------
# Client class and utilities
# ---------------------
class Client:
    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        global NEXT_CLIENT_ID
        self.reader = reader
        self.writer = writer
        self.id = NEXT_CLIENT_ID
        NEXT_CLIENT_ID += 1
        self.nick = None            # temporary nick
        self.auth_user = None       # username if logged in
        self.addr = writer.get_extra_info("peername")
        self.connected_at = datetime.datetime.utcnow()
        self.msg_timestamps = deque(maxlen=RATE_LIMIT_PER_SEC * 2)
        self.avatar = None
        self.flood_count = 0

    def safe_nick(self):
        if self.auth_user:
            return self.auth_user
        if self.nick:
            return self.nick
        return f"User{self.id}"


# ---------------------
# avatars helper
# ---------------------
def ascii_avatar_from_image(path, size=(8, 8)):
    try:
        img = Image.open(path).convert("L")
        img.thumbnail(size)
        chars = "@%#*+=-:. "
        result = ""
        for y in range(img.height):
            for x in range(img.width):
                pixel = img.getpixel((x, y))
                result += chars[pixel * len(chars) // 256]
            result += "\n"
        return result
    except Exception:
        return None


# ---------------------
# broadcast / history
# ---------------------
async def broadcast(message: str, exclude_writer: asyncio.StreamWriter = None):
    history.append((datetime.datetime.utcnow(), message))
    dead = []
    for w in list(clients.keys()):
        if w is exclude_writer:
            continue
        try:
            w.write(message.encode("utf-8") + b"\r\n")
            await w.drain()
        except Exception:
            dead.append(w)
    for d in dead:
        await disconnect_writer(d, reason="write-error")


async def send_history(writer: asyncio.StreamWriter):
    if not history:
        return
    try:
        writer.write(b"--- LAST MESSAGES (last 5) ---\r\n")
        last_msgs = list(history)[-5:]
        for ts, msg in last_msgs:
            ts_s = ts.strftime("%H:%M:%S")
            writer.write(f"{ts_s} {msg}\r\n".encode())
        writer.write(b"--- END HISTORY ---\r\n")
        await writer.drain()
    except Exception:
        pass


# ---------------------
# connection helpers
# ---------------------
async def disconnect_writer(writer: asyncio.StreamWriter, reason: str = "unknown"):
    client = clients.get(writer)
    if client:
        who = client.safe_nick()
        try:
            writer.write(b"Goodbye.\r\n")
            await writer.drain()
        except Exception:
            pass
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass
        clients.pop(writer, None)
        await broadcast(f"* {who} disconnected ({reason})")
        logger.info(f"Disconnected {who}: {reason}")


# ---------------------
# command handling
# ---------------------
async def handle_commands(line: str, client: Client):
    line = line.strip()
    if not line:
        return

    # Commands start with /
    if line.startswith("/"):
        parts = line.split(maxsplit=2)
        cmd = parts[0].lower()
        arg = parts[1] if len(parts) > 1 else ""
        arg2 = parts[2] if len(parts) > 2 else ""

        # /register <user> <password>
        if cmd == "/register":
            username = arg.strip()
            password = arg2.strip()
            if not username or not password:
                client.writer.write(b"Usage: /register <user> <password>\r\n")
                await client.writer.drain()
                return
            if username.lower() in {n.lower() for n in BANNED_NICKS}:
                client.writer.write(b"This username is prohibited. Choose another.\r\n")
                await client.writer.drain()
                return
            users = load_users()
            if username in users:
                client.writer.write(b"Username already exists.\r\n")
                await client.writer.drain()
                return
            salt_hex, hash_hex = hash_password(password)
            users[username] = {"salt": salt_hex, "hash": hash_hex}
            save_users(users)
            client.auth_user = username
            client.nick = None
            client.flood_count = 0
            logger.info(f"User registered: {username}")
            await broadcast(f"* {client.safe_nick()} registered and logged in")
            client.writer.write(b"Registered and logged in.\r\n")
            await client.writer.drain()
            return

        # /login <user> <password>
        if cmd == "/login":
            username = arg.strip()
            password = arg2.strip()
            if not username or not password:
                client.writer.write(b"Usage: /login <user> <password>\r\n")
                await client.writer.drain()
                return
            users = load_users()
            if username not in users:
                client.writer.write(b"Unknown user.\r\n")
                await client.writer.drain()
                return
            # prevent double login
            for w, c in clients.items():
                if c.auth_user == username:
                    client.writer.write(b"User already logged in elsewhere.\r\n")
                    await client.writer.drain()
                    return
            entry = users[username]
            if verify_password(entry["salt"], entry["hash"], password):
                client.auth_user = username
                client.nick = None
                client.flood_count = 0
                logger.info(f"User logged in: {username}")
                await broadcast(f"* {client.safe_nick()} logged in")
                client.writer.write(b"Login successful.\r\n")
                await client.writer.drain()
            else:
                client.writer.write(b"Invalid password.\r\n")
                await client.writer.drain()
            return

        # /logout
        if cmd == "/logout":
            if client.auth_user:
                user = client.auth_user
                client.auth_user = None
                client.nick = None
                client.flood_count = 0
                await broadcast(f"* {user} logged out")
                client.writer.write(b"Logged out.\r\n")
                await client.writer.drain()
            else:
                client.writer.write(b"Not logged in.\r\n")
                await client.writer.drain()
            return

        # /nick <name>
        if cmd == "/nick":
            new = arg.strip()[:32]
            if not new:
                client.writer.write(b"Usage: /nick <name>\r\n")
                await client.writer.drain()
                return
            if new.lower() in {n.lower() for n in BANNED_NICKS}:
                client.writer.write(b"This nickname is prohibited. Choose another.\r\n")
                await client.writer.drain()
                return
            if client.auth_user:
                client.writer.write(b"You're logged in; to set a separate nick, logout first.\r\n")
                await client.writer.drain()
                return
            old = client.safe_nick()
            client.nick = new
            logger.info(f"Nick change: {old} -> {client.safe_nick()}")
            await broadcast(f"* {old} now is {client.safe_nick()}")
            return

        # /who
        if cmd == "/who":
            lines = ["Users connected:"]
            for w, c in clients.items():
                lines.append(f" - {c.safe_nick()}")
            client.writer.write(("\r\n".join(lines) + "\r\n").encode())
            await client.writer.drain()
            return

        # /msg <user> <text>
        if cmd == "/msg":
            target = arg
            text = arg2
            if not target or not text:
                client.writer.write(b"Usage: /msg <user> <text>\r\n")
                await client.writer.drain()
                return
            for w, c in clients.items():
                if c.safe_nick() == target:
                    try:
                        w.write(f"[PM from {client.safe_nick()}] {text}\r\n".encode())
                        await w.drain()
                    except Exception:
                        await disconnect_writer(w, "write-error")
                    client.writer.write(f"[PM to {target}] {text}\r\n".encode())
                    await client.writer.drain()
                    return
            client.writer.write(b"User not found.\r\n")
            await client.writer.drain()
            return

        # /avatar <file>
        if cmd == "/avatar":
            avatar_file = arg
            avatar = ascii_avatar_from_image(avatar_file)
            if avatar:
                client.avatar = avatar
                client.writer.write(b"Avatar set successfully.\r\n")
            else:
                client.writer.write(b"Failed to load avatar.\r\n")
            await client.writer.drain()
            return

        # /forum [post <text>]
        if cmd == "/forum":
            if arg == "post":
                text = arg2
                with open(FORUM_FILE, "a", encoding="utf-8") as f:
                    f.write(f"[{datetime.datetime.utcnow().isoformat()}] {client.safe_nick()}: {text}\n")
                client.writer.write(b"Post submitted.\r\n")
                await client.writer.drain()
            else:
                try:
                    with open(FORUM_FILE, encoding="utf-8") as f:
                        lines = f.readlines()[-20:]
                    client.writer.write(b"--- Forum last posts ---\r\n")
                    for l in lines:
                        client.writer.write(l.encode())
                    client.writer.write(b"--- End forum ---\r\n")
                    await client.writer.drain()
                except FileNotFoundError:
                    client.writer.write(b"No posts yet.\r\n")
                    await client.writer.drain()
            return

        # /clear
        if cmd == "/clear":
            client.writer.write(b"\033[2J\033[H")
            await client.writer.drain()
            return

        # /quit or /exit
        if cmd in ("/quit", "/exit"):
            await disconnect_writer(client.writer, reason="user-quit")
            return

        # /help
        if cmd == "/help":
            msg = (
                "/register <user> <password> - create account and login\r\n"
                "/login <user> <password> - login to existing account\r\n"
                "/logout - logout of account\r\n"
                "/nick <name> - temporary nick (not while logged in)\r\n"
                "/who - list users\r\n"
                "/msg <user> <text> - private message\r\n"
                "/forum - view forum /forum post <text> - post\r\n"
                "/avatar <file> - set ASCII avatar (server-side)\r\n"
                "/clear - clear your screen\r\n"
                "/quit - exit\r\n"
            )
            client.writer.write(msg.encode())
            await client.writer.drain()
            return

        # unknown command
        client.writer.write(b"Unknown command. Try /help\r\n")
        await client.writer.drain()
        return

    # ---------------------
    # Public message (not a command) -> flood control + broadcast
    # ---------------------
    now = datetime.datetime.utcnow().timestamp()
    client.msg_timestamps.append(now)

    if len(client.msg_timestamps) >= RATE_LIMIT_PER_SEC:
        window = now - client.msg_timestamps[0]
        if window < 1.0:
            client.flood_count += 1
            client.writer.write(f"⚠️ Flood detected ({client.flood_count}/3). Slow down.\r\n".encode())
            await client.writer.drain()
            if client.flood_count >= 3:
                # disconnect for flood
                await disconnect_writer(client.writer, reason="flood")
            return
        else:
            # user slowed down, reset counter
            client.flood_count = 0

    sender = client.safe_nick()
    msg = f"[{sender}] {line}"
    if client.avatar:
        msg = f"{client.avatar}\n{msg}"
    logger.info(msg)
    await broadcast(msg)


# ---------------------
# connection lifecycle
# ---------------------
async def handle_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
    if len(clients) >= MAX_CLIENTS:
        try:
            writer.write(b"Server full, try again.\r\n")
            await writer.drain()
        except Exception:
            pass
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass
        return

    client = Client(reader, writer)
    clients[writer] = client
    logger.info(f"New connection id User{client.id}")

    try:
        writer.write(WELCOME.encode("utf-8") + b"\r\n")
        writer.write(b"Register with /register <user> <pass> or /login <user> <pass>\r\n")
        writer.write(b"Set a temporary nick with /nick <name> (or login/register to use account name)\r\n")
        await writer.drain()
    except Exception:
        await disconnect_writer(writer, reason="welcome-fail")
        return

    await send_history(writer)
    await broadcast(f"* {client.safe_nick()} joined")

    try:
        while not reader.at_eof():
            line = await reader.readline()
            if not line:
                break
            text = line.decode("utf-8", errors="replace").rstrip("\r\n")
            await handle_commands(text, client)
    except Exception:
        logger.exception("Client error")
    finally:
        await disconnect_writer(writer, reason="connection-closed")


# ---------------------
# main
# ---------------------
async def main(host: str, port: int):
    server = await asyncio.start_server(handle_client, host, port)
    addrs = ", ".join(str(s.getsockname()) for s in server.sockets)
    print(f"SUBNET listening on {addrs}")
    logger.info(f"SUBNET listening on {addrs}")
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="SUBNET real-time terminal BBS with accounts")
    parser.add_argument("--host", default=HOST)
    parser.add_argument("--port", type=int, default=PORT)
    args = parser.parse_args()

    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda s, f: exit(0))

    asyncio.run(main(args.host, args.port))
