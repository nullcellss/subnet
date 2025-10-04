SUBNET – Real-Time Encrypted Terminal BBS

SUBNET is a modern reinterpretation of the classic 1980s BBS, offering a real-time, encrypted terminal-based connection between users. It allows multiple clients to connect via TCP, chat instantly, post to forums, use ASCII avatars, send private messages, and optionally secure connections with TLS encryption.

Features

Real-time chat: messages are broadcast to all connected users immediately

Private messaging with /msg <user> <text>

Avatars: upload a .png or .jpg and generate a small ASCII avatar

Forum mode: /forum post <text> to post, /forum to view last posts

Screen clearing: /clear clears your terminal

User management: /nick <name> to set nickname, /who to list users

Logging: connections and chat messages logged to subnet.log

TLS encryption (optional): secure your server with SSL certificates

Installation

Clone the repository:

git clone https://github.com/yourusername/subnet.git
cd subnet


Install dependencies:

pip3 install pillow


(Optional) Create SSL certificates for TLS:

openssl req -new -x509 -days 365 -nodes -out server.pem -keyout server.key

Running the server
python3 subnet.py --host 0.0.0.0 --port 2323


Connect using any terminal with TCP support:

nc localhost 2323


For TLS:

openssl s_client -connect localhost:2323

Commands
Command	Description
/nick <name>	Set your nickname
/who	List connected users (shows ASCII avatars)
/avatar <file.png/.jpg>	Upload and set a small ASCII avatar
/msg <user> <text>	Send a private message
/forum post <text>	Post a message to the forum
/forum	Show last 20 forum posts
/clear	Clear your terminal screen
/quit	Disconnect from the server
/help	Show command list
How It Works

Networking: Built with asyncio, handling multiple TCP clients asynchronously

Broadcast: Messages are appended to history and sent to all connected clients

Rate limiting: Users are limited to a few messages per second to prevent flooding

Avatars: Small ASCII avatars generated from uploaded images using Pillow

Forum: Posts stored in forum.txt, allowing terminal-based reading and posting

Logging: All connections, disconnections, nick changes, and messages logged in subnet.log

TLS: Optional SSL context secures the TCP connection

Customization

Change logo: Modify the LOGO variable in subnet.py

Colors: Adjust ANSI escape codes for different themes

Rate limit: Change RATE_LIMIT_PER_SEC

History size: Change HISTORY_SIZE

Forum file: Modify FORUM_FILE location/name

Avatars: Adjust ascii_avatar_from_image function for different ASCII sizes/styles

TLS: Replace server.pem and server.key with your own certificates

Notes

Best experienced in terminals supporting ANSI colors and images (Kitty/iTerm2)

Users on unsupported terminals will see only ASCII avatars

SUBNET is designed for educational and personal use; be mindful of security if exposing it online
