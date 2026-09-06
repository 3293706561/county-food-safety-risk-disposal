"""县域食品安全风险处置台：脱敏模拟数据下的本地流程演示。"""
import json
import os
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).parent
DB_PATH = Path(os.getenv("FOOD_SAFETY_DB", ROOT / "data" / "food_safety.db"))
PORT = int(os.getenv("FOOD_SAFETY_PORT", "8092"))
PASSWORD = "DemoFood!2026"
SESSIONS = {}
USERS = {
    "county": {"name": "县级监管员", "role": "县级", "area": "全县"},
    "town_a": {"name": "青禾镇负责人", "role": "乡镇", "area": "青禾镇"},
    "town_b": {"name": "临江镇负责人", "role": "乡镇", "area": "临江镇"},
    "town_c": {"name": "石桥镇负责人", "role": "乡镇", "area": "石桥镇"},
    "grid_a": {"name": "青禾镇网格员", "role": "网格", "area": "青禾镇"},
    "grid_b": {"name": "临江镇网格员", "role": "网格", "area": "临江镇"},
    "grid_c": {"name": "石桥镇网格员", "role": "网格", "area": "石桥镇"},
}


@contextmanager
def db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=30000")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def parse_day(value, fallback=None):
    if not value and fallback:
        return fallback
    try:
        return date.fromisoformat(value).isoformat()
    except (TypeError, ValueError):
        return None


def user_for(area, role):
    return next((uid for uid, user in USERS.items() if user["area"] == area and user["role"] == role), None)


def migrate(conn):
    columns = {row[1] for row in conn.execute("PRAGMA table_info(tasks)")}
    for name in ("town_owner", "grid_owner", "finding", "rectification", "town_review"):
        if name not in columns:
            conn.execute(f"ALTER TABLE tasks ADD COLUMN {name} TEXT DEFAULT ''")
    conn.execute("UPDATE tasks SET status='待网格核查' WHERE status='待核查'")
    conn.execute("UPDATE tasks SET status='待县级复核' WHERE status='待复核'")
    for task in conn.execute("SELECT id,town,assignee,status FROM tasks WHERE town_owner='' OR town_owner IS NULL"):
        owner = user_for(task["town"], "乡镇") or ""
        conn.execute("UPDATE tasks SET town_owner=? WHERE id=?", (owner, task["id"]))
        if task["status"] == "待网格核查":
            grid = user_for(task["town"], "网格") or ""
            conn.execute("UPDATE tasks SET grid_owner=?,assignee=? WHERE id=?", (grid, grid, task["id"]))


def seed():
    with db() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS risks(id INTEGER PRIMARY KEY, code TEXT UNIQUE, town TEXT, subject TEXT, category TEXT, level TEXT, source TEXT, score INTEGER, status TEXT, created_at TEXT);
        CREATE TABLE IF NOT EXISTS campaigns(id INTEGER PRIMARY KEY, name TEXT, scope TEXT, deadline TEXT, status TEXT, created_by TEXT, created_at TEXT);
        CREATE TABLE IF NOT EXISTS tasks(id INTEGER PRIMARY KEY, campaign_id INTEGER, risk_id INTEGER, town TEXT, subject TEXT, level TEXT, status TEXT, assignee TEXT, due_date TEXT, note TEXT, version INTEGER DEFAULT 1, town_owner TEXT DEFAULT '', grid_owner TEXT DEFAULT '', finding TEXT DEFAULT '', rectification TEXT DEFAULT '', town_review TEXT DEFAULT '');
        CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY, task_id INTEGER, actor TEXT, action TEXT, note TEXT, created_at TEXT);
        """)
        migrate(c)
        if c.execute("SELECT COUNT(*) FROM risks").fetchone()[0]:
            return
        risks = [
            ("FS-2026-001", "青禾镇", "青禾中心小学食堂", "校园餐饮", "红", "快检：餐具洁净度连续异常", 92),
            ("FS-2026-002", "青禾镇", "禾丰农贸市场熟食区", "集市经营", "橙", "投诉：同类投诉 3 起", 78),
            ("FS-2026-003", "临江镇", "临江冷链仓", "冷链流通", "红", "抽检：冷链温度记录缺失", 89),
            ("FS-2026-004", "临江镇", "滨河夜市 17 号摊", "网络餐饮", "黄", "巡查：证照临近到期", 61),
            ("FS-2026-005", "青禾镇", "新民食品小作坊", "食品小作坊", "橙", "历史：近 90 天两次整改", 75),
            ("FS-2026-006", "石桥镇", "石桥集市豆制品摊", "集市经营", "黄", "快检：单次指标异常待复测", 58),
        ]
        c.executemany("INSERT INTO risks(code,town,subject,category,level,source,score,status,created_at) VALUES(?,?,?,?,?,?,?,?,?)", [r + ("待处置", now()) for r in risks])
        c.execute("INSERT INTO campaigns(name,scope,deadline,status,created_by,created_at) VALUES(?,?,?,?,?,?)", ("开学季校园及周边食品安全检查", "青禾镇、临江镇", (date.today()+timedelta(days=7)).isoformat(), "进行中", "county", now()))
        campaign = c.execute("SELECT last_insert_rowid()").fetchone()[0]
        for risk_id, state, assignee in [(1, "待网格核查", "grid_a"), (3, "待乡镇复核", "town_b"), (5, "待分派", "")]:
            row = c.execute("SELECT town,subject,level FROM risks WHERE id=?", (risk_id,)).fetchone()
            c.execute("INSERT INTO tasks(campaign_id,risk_id,town,subject,level,status,assignee,due_date,note,town_owner,grid_owner) VALUES(?,?,?,?,?,?,?,?,?,?,?)", (campaign, risk_id, row["town"], row["subject"], row["level"], state, assignee, (date.today()+timedelta(days=3)).isoformat(), "", user_for(row["town"], "乡镇"), assignee if state == "待网格核查" else ""))
            task_id = c.execute("SELECT last_insert_rowid()").fetchone()[0]
            if risk_id == 3:
                c.execute("UPDATE tasks SET finding=?,rectification=? WHERE id=?", ("冷链温度记录缺失", "已补录当日温度记录并完成设备巡检", task_id))
            c.execute("INSERT INTO events(task_id,actor,action,note,created_at) VALUES(?,?,?,?,?)", (task_id, USERS["county"]["name"], "生成任务", "来自风险清单", now()))


def current_user(handler):
    cookie = SimpleCookie(handler.headers.get("Cookie"))
    sid = cookie.get("food_demo")
    uid = SESSIONS.get(sid.value) if sid else None
    return (USERS[uid] | {"id": uid}) if uid in USERS else None


def scope_sql(user, column="town"):
    return ("", []) if user["role"] == "县级" else (f" WHERE {column}=?", [user["area"]])


def dashboard(user):
    where, params = scope_sql(user)
    with db() as c:
        risks = [dict(row) for row in c.execute("SELECT * FROM risks" + where + " ORDER BY score DESC", params)]
        tasks = [dict(row) for row in c.execute("SELECT * FROM tasks" + where + " ORDER BY CASE status WHEN '待县级复核' THEN 1 WHEN '待乡镇复核' THEN 2 WHEN '待网格核查' THEN 3 WHEN '待分派' THEN 4 ELSE 5 END, id", params)]
        campaigns = [dict(row) for row in c.execute("SELECT * FROM campaigns ORDER BY id DESC")]
    if user["role"] != "县级":
        campaigns = [item for item in campaigns if user["area"] in item["scope"].split("、")]
    metrics = {"active": sum(x["status"] != "已销号" for x in tasks), "red": sum(x["level"] == "红" and x["status"] != "已处置" for x in risks), "review": sum(x["status"] in ("待乡镇复核", "待县级复核") for x in tasks), "overdue": sum(x["due_date"] < date.today().isoformat() and x["status"] != "已销号" for x in tasks)}
    return {"user": user, "metrics": metrics, "risks": risks, "tasks": tasks, "campaigns": campaigns}


class RequestError(Exception):
    def __init__(self, message, code=400): self.message, self.code = message, code


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_): pass

    def send_json(self, data, code=200, cookie=None):
        payload = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(code); self.send_header("Content-Type", "application/json; charset=utf-8"); self.send_header("Content-Length", len(payload))
        if cookie: self.send_header("Set-Cookie", cookie)
        self.end_headers(); self.wfile.write(payload)

    def body(self):
        try:
            size = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            raise RequestError("请求长度无效")
        if size < 0 or size > 100000:
            raise RequestError("请求内容过大")
        try:
            return json.loads(self.rfile.read(size).decode() or "{}")
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise RequestError("请求内容必须为 JSON")

    def task_id(self, path):
        try:
            value = int(path.rsplit("/", 1)[1])
            if value < 1: raise ValueError
            return value
        except (ValueError, IndexError):
            raise RequestError("任务不存在", 404)

    def task_for_user(self, task_id, user, conn):
        task = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if not task: raise RequestError("任务不存在", 404)
        if user["role"] != "县级" and task["town"] != user["area"]:
            raise RequestError("无权操作该任务", 403)
        return task

    def do_GET(self):
        path = urlparse(self.path).path
        try:
            if path == "/api/state":
                user = current_user(self)
                return self.send_json(dashboard(user) if user else {"error": "请先登录"}, 200 if user else 401)
            if path.startswith("/api/task/"):
                user = current_user(self)
                if not user: return self.send_json({"error": "请先登录"}, 401)
                with db() as c:
                    task = self.task_for_user(self.task_id(path), user, c)
                    events = [dict(x) for x in c.execute("SELECT * FROM events WHERE task_id=? ORDER BY id", (task["id"],))]
                return self.send_json({"task": dict(task), "events": events})
            if path in ("/", "/index.html"):
                content = (ROOT / "index.html").read_bytes(); self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8"); self.end_headers(); return self.wfile.write(content)
            return self.send_json({"error": "接口不存在"}, 404)
        except RequestError as error:
            return self.send_json({"error": error.message}, error.code)

    def do_POST(self):
        try:
            path, data = urlparse(self.path).path, self.body()
            if path == "/api/login":
                uid = data.get("username", "")
                if uid not in USERS or not secrets.compare_digest(str(data.get("password", "")), PASSWORD): return self.send_json({"error": "账号或密码不正确"}, 401)
                sid = secrets.token_urlsafe(24); SESSIONS[sid] = uid
                return self.send_json({"ok": True}, cookie=f"food_demo={sid}; HttpOnly; SameSite=Strict; Max-Age=28800; Path=/")
            if path == "/api/logout":
                cookie = SimpleCookie(self.headers.get("Cookie")); sid = cookie.get("food_demo")
                if sid: SESSIONS.pop(sid.value, None)
                return self.send_json({"ok": True}, cookie="food_demo=; Max-Age=0; Path=/")
            user = current_user(self)
            if not user: return self.send_json({"error": "请先登录"}, 401)
            if path == "/api/campaign": return self.create_campaign(data, user)
            if path.startswith("/api/task/"): return self.transition(self.task_id(path), data, user)
            return self.send_json({"error": "接口不存在"}, 404)
        except RequestError as error:
            return self.send_json({"error": error.message}, error.code)

    def create_campaign(self, data, user):
        if user["role"] != "县级": raise RequestError("仅县级监管员可立项", 403)
        name, ids = str(data.get("name", "")).strip(), data.get("risk_ids", [])
        deadline = parse_day(data.get("deadline"), (date.today()+timedelta(days=7)).isoformat())
        if not name or not isinstance(ids, list) or not ids: raise RequestError("请填写专项名称并选择风险事项")
        if not deadline: raise RequestError("截止日期格式应为 YYYY-MM-DD")
        if len(set(ids)) != len(ids) or any(not isinstance(x, int) for x in ids): raise RequestError("风险事项无效")
        with db() as c:
            risks = [c.execute("SELECT * FROM risks WHERE id=?", (rid,)).fetchone() for rid in ids]
            if any(x is None for x in risks): raise RequestError("风险事项不存在")
            scope = "、".join(sorted({x["town"] for x in risks}))
            c.execute("INSERT INTO campaigns(name,scope,deadline,status,created_by,created_at) VALUES(?,?,?,?,?,?)", (name, scope, deadline, "进行中", user["id"], now()))
            campaign_id = c.execute("SELECT last_insert_rowid()").fetchone()[0]
            for risk in risks:
                c.execute("INSERT INTO tasks(campaign_id,risk_id,town,subject,level,status,due_date,note,town_owner) VALUES(?,?,?,?,?,?,?,?,?)", (campaign_id, risk["id"], risk["town"], risk["subject"], risk["level"], "待分派", deadline, "", user_for(risk["town"], "乡镇")))
                task_id = c.execute("SELECT last_insert_rowid()").fetchone()[0]
                c.execute("INSERT INTO events(task_id,actor,action,note,created_at) VALUES(?,?,?,?,?)", (task_id, user["name"], "生成任务", "纳入专项整治", now()))
        return self.send_json({"ok": True})

    def transition(self, task_id, data, user):
        action = data.get("action")
        labels = {"assign": "县级分派", "delegate": "乡镇派发网格", "submit": "网格提交核查", "town_review": "乡镇复核", "return": "退回补充", "close": "县级销号"}
        with db() as c:
            task = self.task_for_user(task_id, user, c)
            note = str(data.get("note", "")).strip()
            if action == "assign":
                if user["role"] != "县级" or task["status"] != "待分派": raise RequestError("当前账号或状态不能分派", 409)
                owner = data.get("assignee"); person = USERS.get(owner)
                if not person or person["role"] != "乡镇" or person["area"] != task["town"]: raise RequestError("分派对象需为任务属地负责人")
                fields = ("待乡镇派发", owner, task["grid_owner"], task["finding"], task["rectification"], task["town_review"], task["id"])
            elif action == "delegate":
                if user["role"] != "乡镇" or task["status"] != "待乡镇派发" or task["assignee"] != user["id"]: raise RequestError("仅当前属地负责人可派发网格", 403)
                grid = data.get("assignee"); person = USERS.get(grid)
                if not person or person["role"] != "网格" or person["area"] != task["town"]: raise RequestError("请选择任务属地网格员")
                if not note: raise RequestError("请填写本次核查重点")
                fields = ("待网格核查", grid, grid, task["finding"], task["rectification"], task["town_review"], task["id"])
            elif action == "submit":
                if user["role"] != "网格" or task["status"] != "待网格核查" or task["assignee"] != user["id"]: raise RequestError("仅被分派的网格员可提交核查", 403)
                finding, rectification = str(data.get("finding", "")).strip(), str(data.get("rectification", "")).strip()
                if not finding or not rectification: raise RequestError("请填写发现项和整改结果")
                fields = ("待乡镇复核", task["town_owner"], task["grid_owner"], finding, rectification, task["town_review"], task["id"])
                note = f"发现项：{finding}；整改结果：{rectification}"
            elif action == "town_review":
                if user["role"] != "乡镇" or task["status"] != "待乡镇复核" or task["town_owner"] != user["id"]: raise RequestError("仅属地负责人可复核", 403)
                if not note: raise RequestError("请填写乡镇复核结论")
                fields = ("待县级复核", "county", task["grid_owner"], task["finding"], task["rectification"], note, task["id"])
            elif action == "return":
                if user["role"] != "县级" or task["status"] != "待县级复核": raise RequestError("仅县级可退回待复核任务", 403)
                if not note: raise RequestError("请填写退回原因")
                due = (date.fromisoformat(task["due_date"]) + timedelta(days=3)).isoformat()
                c.execute("UPDATE tasks SET status='待网格核查',assignee=?,due_date=?,note=?,version=version+1 WHERE id=?", (task["grid_owner"], due, note, task["id"]))
                c.execute("INSERT INTO events(task_id,actor,action,note,created_at) VALUES(?,?,?,?,?)", (task_id, user["name"], labels[action], note, now()))
                return self.send_json({"ok": True})
            elif action == "close":
                if user["role"] != "县级" or task["status"] != "待县级复核": raise RequestError("仅县级可销号待复核任务", 403)
                if not note: raise RequestError("请填写县级销号结论")
                fields = ("已销号", "", task["grid_owner"], task["finding"], task["rectification"], task["town_review"], task["id"])
            else:
                raise RequestError("不支持的任务操作")
            c.execute("UPDATE tasks SET status=?,assignee=?,grid_owner=?,finding=?,rectification=?,town_review=?,version=version+1 WHERE id=?", fields)
            if action == "close": c.execute("UPDATE risks SET status='已处置' WHERE id=?", (task["risk_id"],))
            c.execute("INSERT INTO events(task_id,actor,action,note,created_at) VALUES(?,?,?,?,?)", (task_id, user["name"], labels[action], note, now()))
        return self.send_json({"ok": True})


if __name__ == "__main__":
    seed()
    print(f"县域食品安全风险处置台：http://127.0.0.1:{PORT}")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
