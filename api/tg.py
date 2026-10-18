from http.server import BaseHTTPRequestHandler
import json
import asyncio
import base64
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import SessionPasswordNeededError

TOOLS = [
    {'name': 'get_me', 'description': 'Get info about the logged-in Telegram account.',
     'inputSchema': {'type': 'object', 'properties': {}}},
    {'name': 'list_chats', 'description': 'List recent Telegram chats (id, title, unread count, last message).',
     'inputSchema': {'type': 'object', 'properties': {'limit': {'type': 'integer', 'description': 'default 20, max 50'}}}},
    {'name': 'read_messages', 'description': 'Read recent messages from a chat. chat = numeric id, @username, phone, chat title, or me for Saved Messages.',
     'inputSchema': {'type': 'object', 'properties': {'chat': {'type': 'string'}, 'limit': {'type': 'integer', 'description': 'default 20, max 100'}}, 'required': ['chat']}},
    {'name': 'search_messages', 'description': 'Search messages by text, inside one chat (if chat is given) or globally.',
     'inputSchema': {'type': 'object', 'properties': {'query': {'type': 'string'}, 'chat': {'type': 'string'}, 'limit': {'type': 'integer'}}, 'required': ['query']}},
    {'name': 'send_message', 'description': 'Send a text message to a chat. The user confirms every send.',
     'inputSchema': {'type': 'object', 'properties': {'chat': {'type': 'string'}, 'text': {'type': 'string'}, 'reply_to': {'type': 'integer', 'description': 'message id to reply to'}}, 'required': ['chat', 'text']}},
]


async def resolve(c, chat):
    chat = str(chat).strip()
    if chat.lower() == 'me':
        return 'me'
    key = int(chat) if chat.lstrip('-').isdigit() else chat
    try:
        return await c.get_entity(key)
    except Exception:
        pass
    low = chat.lower()
    async for d in c.iter_dialogs(limit=100):
        if d.id == key or (isinstance(key, str) and low in (d.name or '').lower()):
            return d.entity
    raise ValueError('chat not found: ' + chat)


def fmt(m):
    sender = ''
    try:
        s = m.sender
        sender = getattr(s, 'first_name', None) or getattr(s, 'title', None) or getattr(s, 'username', None) or ''
    except Exception:
        pass
    return {
        'id': m.id,
        'date': m.date.isoformat() if m.date else '',
        'from': sender,
        'out': bool(m.out),
        'text': m.message or '',
        'media': type(m.media).__name__ if m.media else None,
    }


async def call_tool(c, name, a):
    if name == 'get_me':
        me = await c.get_me()
        return {'id': me.id, 'name': ((me.first_name or '') + ' ' + (me.last_name or '')).strip(), 'username': me.username, 'phone': me.phone}
    if name == 'list_chats':
        n = min(int(a.get('limit') or 20), 50)
        out = []
        async for d in c.iter_dialogs(limit=n):
            last = ''
            if d.message and d.message.message:
                last = d.message.message[:120]
            out.append({'id': d.id, 'title': d.name, 'unread': d.unread_count, 'last': last})
        return out
    if name == 'read_messages':
        n = min(int(a.get('limit') or 20), 100)
        ent = await resolve(c, a['chat'])
        msgs = await c.get_messages(ent, limit=n)
        return [fmt(m) for m in reversed(msgs)]
    if name == 'search_messages':
        n = min(int(a.get('limit') or 20), 100)
        ent = await resolve(c, a['chat']) if a.get('chat') else None
        msgs = await c.get_messages(ent, search=a['query'], limit=n)
        return [fmt(m) for m in msgs]
    if name == 'send_message':
        ent = await resolve(c, a['chat'])
        m = await c.send_message(ent, a['text'], reply_to=a.get('reply_to'))
        return {'sent': True, 'id': m.id}
    raise ValueError('unknown tool: ' + str(name))


async def with_client(creds, name, a):
    c = TelegramClient(StringSession(creds['session']), int(creds['api_id']), creds['api_hash'])
    await c.connect()
    try:
        if not await c.is_user_authorized():
            raise RuntimeError('session is not authorized, log in again')
        return await call_tool(c, name, a)
    finally:
        await c.disconnect()


async def login(b):
    step = b['tg_login']
    api_id = int(b['api_id'])
    api_hash = b['api_hash']
    if step == 'send_code':
        c = TelegramClient(StringSession(), api_id, api_hash)
        await c.connect()
        try:
            sent = await c.send_code_request(b['phone'])
            return {'session': c.session.save(), 'phone_code_hash': sent.phone_code_hash}
        finally:
            await c.disconnect()
    if step == 'sign_in':
        c = TelegramClient(StringSession(b['session']), api_id, api_hash)
        await c.connect()
        try:
            if b.get('need_password') and b.get('password'):
                await c.sign_in(phone=b['phone'], password=b['password'])
            else:
                try:
                    await c.sign_in(phone=b['phone'], code=b['code'], phone_code_hash=b['phone_code_hash'])
                except SessionPasswordNeededError:
                    if not b.get('password'):
                        return {'need_password': True, 'session': c.session.save()}
                    await c.sign_in(phone=b['phone'], password=b['password'])
            me = await c.get_me()
            return {'session': c.session.save(), 'me': (me.first_name or '') + ' @' + str(me.username)}
        finally:
            await c.disconnect()
    return {'error': 'bad step'}


class handler(BaseHTTPRequestHandler):
    def _send(self, code, obj=None):
        data = json.dumps(obj).encode() if obj is not None else b''
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(data)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Headers', '*')
        self.send_header('Access-Control-Allow-Methods', 'POST,OPTIONS')
        self.end_headers()

    def do_POST(self):
        try:
            n = int(self.headers.get('Content-Length') or 0)
            body = json.loads(self.rfile.read(n) or b'{}')
        except Exception:
            return self._send(400, {'error': 'bad json'})
        try:
            if 'tg_login' in body:
                return self._send(200, asyncio.run(login(body)))
            return self.mcp(body)
        except Exception as e:
            return self._send(200, {'error': type(e).__name__ + ': ' + str(e)})

    def mcp(self, body):
        rid = body.get('id')
        method = body.get('method')
        p = body.get('params') or {}
        if rid is None:
            return self._send(202)

        def ok(res):
            return self._send(200, {'jsonrpc': '2.0', 'id': rid, 'result': res})

        if method == 'initialize':
            return ok({'protocolVersion': '2025-03-26', 'capabilities': {'tools': {}}, 'serverInfo': {'name': 'telegram', 'version': '1.0'}})
        if method == 'tools/list':
            return ok({'tools': TOOLS})
        if method == 'tools/call':
            try:
                auth = self.headers.get('Authorization') or ''
                creds = json.loads(base64.b64decode(auth.replace('Bearer ', '', 1).strip()))
                res = asyncio.run(with_client(creds, p.get('name'), p.get('arguments') or {}))
                return ok({'content': [{'type': 'text', 'text': json.dumps(res, ensure_ascii=False)}]})
            except Exception as e:
                return ok({'content': [{'type': 'text', 'text': 'Error: ' + type(e).__name__ + ': ' + str(e)}], 'isError': True})
        return self._send(200, {'jsonrpc': '2.0', 'id': rid, 'error': {'code': -32601, 'message': 'unknown method'}})
