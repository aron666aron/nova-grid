"""Start the grid bot loop"""
import sys, os, threading
sys.path.insert(0, '.')
import web_server

web_server._init_bot()
web_server.bot_running = True
t = threading.Thread(target=web_server._bot_loop, daemon=True)
t.start()
print('Bot started!')
