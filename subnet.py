import asyncio
import argparse
import datetime
import logging
import signal
import json
from collections import deque
from PIL import Image

HOST = "0.0.0.0"
PORT = 2323
MAX_CLIENTS = 200
HISTORY_SIZE = 200
RATE_LIMIT_PER_SEC = 3  # máximo de mensajes por segundo
FORUM_FILE = "forum.txt"
USER_FILE = "users.json"

FORBIDDEN_NICKS = {"admin", "root", "system", "moderator", "sysop"}

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
"\033[95mCommands:\033[96m /nick <name> \033[94m/who \033[92m/msg <user> <text> \033[95m/forum \033[94m/forum post <text>\033[0m\n"
"\033[95m          \033[96m/avatar <file> \033[94m/clear \033[92m/quit \033[95m/help\033[0m\n"
)

LOGFILE = "subnet.log"
logger = logging.getLogger("subnet")
logger.setLevel(logging.INFO)
handler = logging.FileHandler(LOGFILE)
handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
logger.addHandler(handler)

clients = {}
history = deque(maxlen=HISTORY_SIZE)

# ---------- Sistema de cuentas ----------
def load_users():
    try:
        with open(USER_FILE, "r") as f:
            return json.load(f)
    except:
        return {}

def save_users(data):
    with open(USER_FILE, "w") as f:
        json.dump(data, f, indent=2)

users = load_users()

class Client:
    def __init__(self, reader, writer):
        self.reader = reader
        self.writer = writer
        self.nick = None
        self.logged_in = False
        self.addr = writer.get_extra_info("peername")
        self.connected_at = datetime.datetime.utcnow()
        self.msg_timestamps = deque(maxlen=RATE_LIMIT_PER_SEC * 2)
        self.avatar = None

    def safe_nick(self):
        return self.nick if self.nick else "???"

async def broadcast(message, exclude_writer=None):
    history.append((datetime.datetime.utcnow(), message))
    for w in list(clients.keys()):
        if w is exclude_writer:
            continue
        try:
            w.write(message.encode("utf-8") + b"\r\n")
            await w.drain()
        except:
            await disconnect_writer(w, reason="write-error")

def ascii_avatar_from_image(path, size=(8,8)):
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
    except:
        return None

async def send_history(writer):
    if not history:
        return
    try:
        writer.write(b"--- LAST 5 MESSAGES ---\r\n")
        for ts, msg in list(history)[-5:]:
            ts_s = ts.strftime("%H:%M:%S")
            writer.write(f"{ts_s} {msg}\r\n".encode())
        writer.write(b"--- END HISTORY ---\r\n")
        await writer.drain()
    except:
        pass

async def disconnect_writer(writer, reason="unknown"):
    client = clients.get(writer)
    if client:
        nick = client.safe_nick()
        try:
            writer.write(b"Goodbye.\r\n")
            await writer.drain()
        except:
            pass
        writer.close()
        await writer.wait_closed()
        clients.pop(writer, None)
        await broadcast(f"* {nick} disconnected ({reason})")
        logger.info(f"Disconnected {nick}: {reason}")

async def register_user(client):
    client.writer.write(b"=== REGISTER ===\r\nEnter username: ")
    await client.writer.drain()
    username = (await client.reader.readline()).decode().strip()
    if username.lower() in FORBIDDEN_NICKS:
        client.writer.write(b"That name is not allowed.\r\n")
        await client.writer.drain()
        return False
    if username in users:
        client.writer.write(b"User already exists. Try /login instead.\r\n")
        await client.writer.drain()
        return False
    client.writer.write(b"Enter password: ")
    await client.writer.drain()
    password = (await client.reader.readline()).decode().strip()
    users[username] = {"password": password}
    save_users(users)
    client.nick = username
    client.logged_in = True
    await broadcast(f"* {username} joined (new user)")
    return True

async def login_user(client):
    client.writer.write(b"=== LOGIN ===\r\nUsername: ")
    await client.writer.drain()
    username = (await client.reader.readline()).decode().strip()
    if username not in users:
        client.writer.write(b"User not found.\r\n")
        await client.writer.drain()
        return False
    client.writer.write(b"Password: ")
    await client.writer.drain()
    password = (await client.reader.readline()).decode().strip()
    if users[username]["password"] != password:
        client.writer.write(b"Invalid password.\r\n")
        await client.writer.drain()
        return False
    client.nick = username
    client.logged_in = True
    await broadcast(f"* {username} logged in")
    return True

async def handle_commands(line, client):
    line = line.strip()
    if not line:
        return

    # Restringir comandos si no ha iniciado sesión
    if not client.logged_in and not line.startswith(("/register", "/login", "/quit", "/exit", "/help")):
        client.writer.write(b"You must /login or /register first.\r\n")
        await client.writer.drain()
        return

    if line.startswith("/"):
        parts = line.split(maxsplit=2)
        cmd = parts[0].lower()
        arg = parts[1] if len(parts) > 1 else ""
        arg2 = parts[2] if len(parts) > 2 else ""
        if cmd == "/register":
            await register_user(client)
        elif cmd == "/login":
            await login_user(client)
        elif cmd == "/who":
            lines = ["Users connected:"]
            for _, c in clients.items():
                lines.append(f" - {c.safe_nick()}")
            client.writer.write(("\r\n".join(lines) + "\r\n").encode())
            await client.writer.drain()
        elif cmd == "/msg":
            target = arg
            text = arg2
            for w, c in clients.items():
                if c.safe_nick() == target:
                    w.write(f"[PM from {client.safe_nick()}] {text}\r\n".encode())
                    await w.drain()
                    client.writer.write(f"[PM to {target}] {text}\r\n".encode())
                    await client.writer.drain()
                    return
            client.writer.write(b"User not found.\r\n")
            await client.writer.drain()
        elif cmd == "/clear":
            client.writer.write(b"\033[2J\033[H")
            await client.writer.drain()
        elif cmd in ("/quit", "/exit"):
            await disconnect_writer(client.writer, reason="user-quit")
        elif cmd == "/help":
            msg = ("/register - create new user\r\n/login - sign in\r\n"
                   "/who - list users\r\n/msg <user> <text> - private message\r\n"
                   "/clear - clear screen\r\n/quit - exit\r\n")
            client.writer.write(msg.encode())
            await client.writer.drain()
        else:
            client.writer.write(b"Unknown command.\r\n")
            await client.writer.drain()
    else:
        # Antiflood
        now = datetime.datetime.utcnow().timestamp()
        client.msg_timestamps.append(now)
        if len(client.msg_timestamps) >= RATE_LIMIT_PER_SEC:
            window = now - client.msg_timestamps[0]
            if window < 1.0:
                client.writer.write(b"[!] Flood detected. Slow down.\r\n")
                await client.writer.drain()
                return
        msg = f"[{client.safe_nick()}] {line}"
        logger.info(msg)
        await broadcast(msg)

async def handle_client(reader, writer):
    if len(clients) >= MAX_CLIENTS:
        writer.write(b"Server full, try again later.\r\n")
        await writer.drain()
        writer.close()
        await writer.wait_closed()
        return

    client = Client(reader, writer)
    clients[writer] = client
    logger.info("New connection")

    writer.write(WELCOME.encode("utf-8") + b"\r\n")
    writer.write(b"Use /register or /login to start.\r\n")
    await writer.drain()
    await send_history(writer)

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

async def main(host, port):
    server = await asyncio.start_server(handle_client, host, port)
    addrs = ", ".join(str(s.getsockname()) for s in server.sockets)
    print(f"SUBNET listening on {addrs}")
    logger.info(f"Listening on {addrs}")
    async with server:
        await server.serve_forever()

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default=HOST)
    parser.add_argument("--port", type=int, default=PORT)
    args = parser.parse_args()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda s, f: exit(0))
    asyncio.run(main(args.host, args.port))
