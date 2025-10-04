import asyncio
import argparse
import datetime
import logging
import signal
from collections import deque
from PIL import Image

HOST = "0.0.0.0"
PORT = 2323
MAX_CLIENTS = 200
HISTORY_SIZE = 200
RATE_LIMIT_PER_SEC = 5
FORUM_FILE = "forum.txt"

# Cyberpunk-colored logo using ANSI codes
WELCOME = (
"\033[95m███████╗\033[96m██╗   ██╗\033[92m███████╗\033[95m███████╗\033[96m███████╗\033[0m\n"
"\033[95m██╔════╝\033[96m██║   ██║\033[92m██╔════╝\033[95m██╔════╝\033[96m██╔════╝\033[0m\n"
"\033[95m█████╗  \033[96m██║   ██║\033[92m█████╗  \033[95m███████╗\033[96m███████╗\033[0m\n"
"\033[95m██╔══╝  \033[96m██║   ██║\033[92m██╔══╝  \033[95m╚════██║\033[96m╚════██║\033[0m\n"
"\033[95m███████╗\033[96m╚██████╔╝\033[92m███████╗\033[95m███████║\033[96m███████║\033[0m\n"
"\033[95m╚══════╝ \033[96m╚═════╝ \033[92m╚══════╝\033[95m╚══════╝\033[96m╚══════╝\033[0m\n"
"Welcome to SUBNET BBS (Real-time Terminal)\n"
"Commands: /nick <name> /who /msg <user> <text> /forum /forum post <text> /avatar <file> /clear /quit /help"
)

LOGFILE = "subnet.log"
logger = logging.getLogger("subnet")
logger.setLevel(logging.INFO)
handler = logging.FileHandler(LOGFILE)
handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
logger.addHandler(handler)

clients = {}
history = deque(maxlen=HISTORY_SIZE)

class Client:
    def __init__(self, reader, writer):
        self.reader = reader
        self.writer = writer
        self.nick = None
        self.addr = writer.get_extra_info("peername")
        self.connected_at = datetime.datetime.utcnow()
        self.msg_timestamps = deque(maxlen=RATE_LIMIT_PER_SEC*2)
        self.avatar = None

    def safe_nick(self):
        return self.nick if self.nick else f"{self.addr[0]}:{self.addr[1]}"

async def broadcast(message, exclude_writer=None):
    history.append((datetime.datetime.utcnow(), message))
    dead = []
    for w in list(clients.keys()):
        if w is exclude_writer:
            continue
        try:
            w.write(message.encode("utf-8") + b"\r\n")
            await w.drain()
        except:
            dead.append(w)
    for d in dead:
        await disconnect_writer(d, reason="write-error")

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
        writer.write(b"--- LAST MESSAGES ---\r\n")
        for ts, msg in history:
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
        try:
            writer.close()
            await writer.wait_closed()
        except:
            pass
        clients.pop(writer, None)
        await broadcast(f"* {nick} disconnected ({reason})")
        logger.info(f"Disconnected {nick}: {reason}")

async def handle_commands(line, client):
    line = line.strip()
    if not line:
        return
    if line.startswith("/"):
        parts = line.split(maxsplit=2)
        cmd = parts[0].lower()
        arg = parts[1] if len(parts) > 1 else ""
        arg2 = parts[2] if len(parts) > 2 else ""
        if cmd == "/nick":
            new = arg.strip()[:32]
            if not new:
                client.writer.write(b"Usage: /nick <name>\r\n")
                await client.writer.drain()
                return
            old = client.safe_nick()
            client.nick = new
            logger.info(f"Nick change: {old} -> {client.nick}")
            await broadcast(f"* {old} now is {client.nick}")
        elif cmd == "/who":
            lines = ["Users connected:"]
            for w, c in clients.items():
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
        elif cmd == "/avatar":
            avatar_file = arg
            avatar = ascii_avatar_from_image(avatar_file)
            if avatar:
                client.avatar = avatar
                client.writer.write(b"Avatar set successfully.\r\n")
            else:
                client.writer.write(b"Failed to load avatar.\r\n")
            await client.writer.drain()
        elif cmd == "/forum":
            if arg == "post":
                text = arg2
                with open(FORUM_FILE, "a") as f:
                    f.write(f"[{datetime.datetime.utcnow().isoformat()}] {client.safe_nick()}: {text}\n")
                client.writer.write(b"Post submitted.\r\n")
                await client.writer.drain()
            else:
                try:
                    with open(FORUM_FILE) as f:
                        lines = f.readlines()[-20:]
                    client.writer.write(b"--- Forum last posts ---\r\n")
                    for l in lines:
                        client.writer.write(l.encode())
                    client.writer.write(b"--- End forum ---\r\n")
                    await client.writer.drain()
                except FileNotFoundError:
                    client.writer.write(b"No posts yet.\r\n")
                    await client.writer.drain()
        elif cmd == "/clear":
            client.writer.write(b"\033[2J\033[H")  # Clear screen ANSI
            await client.writer.drain()
        elif cmd in ("/quit", "/exit"):
            await disconnect_writer(client.writer, reason="user-quit")
        elif cmd == "/help":
            msg = "/nick <name> - change nick\r\n/who - list users\r\n/msg <user> <text> - private message\r\n"
            msg += "/forum - view forum /forum post <text> - post\r\n/avatar <file> - set ASCII avatar\r\n"
            msg += "/clear - clear screen\r\n/quit - exit\r\n"
            client.writer.write(msg.encode())
            await client.writer.drain()
        else:
            client.writer.write(b"Unknown command. Try /help\r\n")
            await client.writer.drain()
    else:
        now = datetime.datetime.utcnow().timestamp()
        client.msg_timestamps.append(now)
        if len(client.msg_timestamps) >= RATE_LIMIT_PER_SEC:
            window = now - client.msg_timestamps[0]
            if window < 1.0:
                client.writer.write(b"Rate limit exceeded, slow down.\r\n")
                await client.writer.drain()
                return
        msg = f"[{client.safe_nick()}] {line}"
        if client.avatar:
            msg = f"{client.avatar}\n{msg}"
        logger.info(msg)
        await broadcast(msg)

async def handle_client(reader, writer):
    if len(clients) >= MAX_CLIENTS:
        writer.write(b"Server full, try again.\r\n")
        await writer.drain()
        writer.close()
        await writer.wait_closed()
        return

    client = Client(reader, writer)
    clients[writer] = client
    peer = client.addr
    logger.info(f"New connection: {peer}")

    try:
        writer.write(WELCOME.encode("utf-8") + b"\r\n")
        writer.write(b"Set your nickname with /nick <name>\r\n")
        await writer.drain()
    except:
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

async def main(host, port):
    server = await asyncio.start_server(handle_client, host, port)
    addrs = ", ".join(str(s.getsockname()) for s in server.sockets)
    print(f"SUBNET listening on {addrs}")
    logger.info(f"SUBNET listening on {addrs}")
    async with server:
        await server.serve_forever()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="SUBNET real-time terminal BBS")
    parser.add_argument("--host", default=HOST)
    parser.add_argument("--port", type=int, default=PORT)
    args = parser.parse_args()

    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda s, f: exit(0))

    asyncio.run(main(args.host, args.port))
