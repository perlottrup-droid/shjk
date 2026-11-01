import hashlib
import hmac
import json
import os
import re
import sqlite3
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get("DATABASE_PATH", ROOT / "sidequest.sqlite3"))
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
MAX_BODY = 16_000


def connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB_PATH, timeout=15)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA foreign_keys=ON")
    return db


def initialize():
    with connect() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS users (
          id TEXT PRIMARY KEY, name TEXT NOT NULL, username TEXT NOT NULL DEFAULT '',
          photo_url TEXT NOT NULL DEFAULT '', created_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS posts (
          id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL REFERENCES users(id),
          text TEXT NOT NULL, created_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS likes (
          post_id INTEGER NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
          user_id TEXT NOT NULL REFERENCES users(id), PRIMARY KEY(post_id,user_id)
        );
        CREATE TABLE IF NOT EXISTS comments (
          id INTEGER PRIMARY KEY AUTOINCREMENT, post_id INTEGER NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
          user_id TEXT NOT NULL REFERENCES users(id), text TEXT NOT NULL, created_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS follows (
          follower_id TEXT NOT NULL REFERENCES users(id), followed_id TEXT NOT NULL REFERENCES users(id),
          created_at INTEGER NOT NULL, PRIMARY KEY(follower_id,followed_id), CHECK(follower_id <> followed_id)
        );
        CREATE INDEX IF NOT EXISTS posts_created ON posts(created_at DESC);
        CREATE INDEX IF NOT EXISTS comments_post ON comments(post_id,created_at);
        """)


def validate_init_data(raw):
    if not BOT_TOKEN or not raw:
        raise ValueError("Telegram sign-in is not configured")
    values = {}
    for item in raw.split("&"):
        key, sep, value = item.partition("=")
        if sep:
            from urllib.parse import unquote
            values[unquote(key)] = unquote(value.replace("+", " "))
    supplied = values.pop("hash", "")
    if not supplied:
        raise ValueError("Missing Telegram signature")
    check = "\n".join(f"{key}={values[key]}" for key in sorted(values))
    secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    expected = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, supplied):
        raise ValueError("Invalid Telegram signature")
    try:
        auth_date = int(values.get("auth_date", "0"))
        user = json.loads(values.get("user", "{}"))
    except (ValueError, TypeError, json.JSONDecodeError):
        raise ValueError("Invalid Telegram sign-in data")
    if auth_date > int(time.time()) + 60 or int(time.time()) - auth_date > 86_400:
        raise ValueError("Telegram sign-in expired. Reopen the app.")
    if not isinstance(user, dict) or not user.get("id"):
        raise ValueError("Telegram user is missing")
    return user


def user_json(row):
    return {"id": row["id"], "name": row["name"], "username": row["username"], "photoUrl": row["photo_url"]}


def time_ago(timestamp):
    seconds = max(0, int(time.time()) - timestamp)
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        return f"{seconds // 60} min ago"
    if seconds < 86400:
        return f"{seconds // 3600} hr ago"
    return f"{seconds // 86400} d ago"


class App(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def log_message(self, fmt, *args):
        print(f"[{self.log_date_time_string()}] {fmt % args}")

    def send_json(self, status, value):
        payload = json.dumps(value, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def body(self):
        length = int(self.headers.get("Content-Length", "0"))
        if length > MAX_BODY:
            raise ValueError("Request is too large")
        return json.loads(self.rfile.read(length) or b"{}")

    def auth(self):
        header = self.headers.get("Authorization", "")
        if not header.startswith("tma "):
            raise ValueError("Open Sidequest inside Telegram to continue")
        tg_user = validate_init_data(header[4:])
        uid = str(tg_user["id"])
        name = " ".join(x for x in (tg_user.get("first_name"), tg_user.get("last_name")) if x).strip() or "Telegram user"
        username = tg_user.get("username", "")
        photo = tg_user.get("photo_url", "")
        with connect() as db:
            db.execute("INSERT INTO users(id,name,username,photo_url,created_at) VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name,username=excluded.username,photo_url=excluded.photo_url", (uid, name[:100], username[:64], photo[:1000], int(time.time())))
        return uid

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/health":
            return self.send_json(200, {"ok": True})
        if not path.startswith("/api/"):
            return super().do_GET()
        try:
            uid = self.auth()
            with connect() as db:
                if path == "/api/me":
                    row = db.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
                    return self.send_json(200, {"user": user_json(row)})
                if path == "/api/people":
                    rows = db.execute("SELECT u.*, EXISTS(SELECT 1 FROM follows f WHERE f.follower_id=? AND f.followed_id=u.id) AS following FROM users u WHERE u.id<>? ORDER BY u.created_at DESC LIMIT 40", (uid, uid)).fetchall()
                    return self.send_json(200, {"people": [{**user_json(r), "following": bool(r["following"])} for r in rows]})
                if path == "/api/feed":
                    query = """SELECT p.*,u.name,u.username,u.photo_url,
                      (SELECT COUNT(*) FROM likes l WHERE l.post_id=p.id) likes,
                      EXISTS(SELECT 1 FROM likes l WHERE l.post_id=p.id AND l.user_id=?) liked,
                      (SELECT COUNT(*) FROM comments c WHERE c.post_id=p.id) replies
                      FROM posts p JOIN users u ON u.id=p.user_id
                      ORDER BY p.created_at DESC LIMIT 80"""
                    rows = db.execute(query, (uid,)).fetchall()
                    return self.send_json(200, {"posts": [self.post_record(db, r, uid) for r in rows]})
                match = re.fullmatch(r"/api/posts/(\d+)/comments", path)
                if match:
                    rows = db.execute("SELECT c.text,c.created_at,u.name FROM comments c JOIN users u ON u.id=c.user_id WHERE c.post_id=? ORDER BY c.created_at LIMIT 100", (match.group(1),)).fetchall()
                    return self.send_json(200, {"comments": [{"name": r["name"].split()[0], "text": r["text"], "time": time_ago(r["created_at"])} for r in rows]})
            return self.send_json(404, {"error": "Not found"})
        except ValueError as e:
            return self.send_json(401, {"error": str(e)})
        except Exception as e:
            print("API error:", e)
            return self.send_json(500, {"error": "Something went wrong"})

    @staticmethod
    def post_record(db, row, viewer):
        comments = db.execute("SELECT c.text,u.name FROM comments c JOIN users u ON u.id=c.user_id WHERE c.post_id=? ORDER BY c.created_at LIMIT 3", (row["id"],)).fetchall()
        return {"id": str(row["id"]), "person": row["user_id"], "name": row["name"], "handle": "@" + row["username"] if row["username"] else "", "photoUrl": row["photo_url"], "time": time_ago(row["created_at"]), "text": row["text"], "likes": row["likes"], "liked": bool(row["liked"]), "comments": [{"name": c["name"].split()[0], "text": c["text"]} for c in comments], "replies": row["replies"]}

    def do_POST(self):
        try:
            uid = self.auth()
            data = self.body()
            path = urlparse(self.path).path
            now = int(time.time())
            with connect() as db:
                if path == "/api/posts":
                    text = str(data.get("text", "")).strip()
                    if not text or len(text) > 500:
                        return self.send_json(400, {"error": "Posts must be between 1 and 500 characters"})
                    cur = db.execute("INSERT INTO posts(user_id,text,created_at) VALUES(?,?,?)", (uid, text, now))
                    return self.send_json(201, {"id": cur.lastrowid})
                match = re.fullmatch(r"/api/posts/(\d+)/like", path)
                if match:
                    post_id = int(match.group(1))
                    exists = db.execute("SELECT 1 FROM posts WHERE id=?", (post_id,)).fetchone()
                    if not exists:
                        return self.send_json(404, {"error": "Post not found"})
                    liked = db.execute("SELECT 1 FROM likes WHERE post_id=? AND user_id=?", (post_id, uid)).fetchone()
                    if liked:
                        db.execute("DELETE FROM likes WHERE post_id=? AND user_id=?", (post_id, uid))
                    else:
                        db.execute("INSERT INTO likes(post_id,user_id) VALUES(?,?)", (post_id, uid))
                    return self.send_json(200, {"liked": not bool(liked)})
                match = re.fullmatch(r"/api/posts/(\d+)/comments", path)
                if match:
                    text = str(data.get("text", "")).strip()
                    if not text or len(text) > 180:
                        return self.send_json(400, {"error": "Replies must be between 1 and 180 characters"})
                    post_id = int(match.group(1))
                    if not db.execute("SELECT 1 FROM posts WHERE id=?", (post_id,)).fetchone():
                        return self.send_json(404, {"error": "Post not found"})
                    db.execute("INSERT INTO comments(post_id,user_id,text,created_at) VALUES(?,?,?,?)", (post_id, uid, text, now))
                    return self.send_json(201, {"ok": True})
                match = re.fullmatch(r"/api/people/(\d+)/follow", path)
                if match:
                    target = match.group(1)
                    if target == uid or not db.execute("SELECT 1 FROM users WHERE id=?", (target,)).fetchone():
                        return self.send_json(404, {"error": "Person not found"})
                    followed = db.execute("SELECT 1 FROM follows WHERE follower_id=? AND followed_id=?", (uid, target)).fetchone()
                    if followed:
                        db.execute("DELETE FROM follows WHERE follower_id=? AND followed_id=?", (uid, target))
                    else:
                        db.execute("INSERT INTO follows(follower_id,followed_id,created_at) VALUES(?,?,?)", (uid, target, now))
                    return self.send_json(200, {"following": not bool(followed)})
            return self.send_json(404, {"error": "Not found"})
        except (ValueError, json.JSONDecodeError) as e:
            return self.send_json(400 if isinstance(e, json.JSONDecodeError) else 401, {"error": str(e)})
        except Exception as e:
            print("API error:", e)
            return self.send_json(500, {"error": "Something went wrong"})


if __name__ == "__main__":
    initialize()
    port = int(os.environ.get("PORT", "8000"))
    print(f"Sidequest listening on port {port}")
    ThreadingHTTPServer(("0.0.0.0", port), App).serve_forever()

