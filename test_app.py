import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path

import app


class FoodSafetyApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory()
        app.DB_PATH = Path(cls.temp_dir.name) / "food_safety_test.db"
        app.SESSIONS.clear()
        app.seed()
        cls.server = app.ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.port = cls.server.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.thread.join()
        cls.temp_dir.cleanup()

    def request(self, method, path, body=None, cookie=None):
        headers = {}
        if cookie:
            headers["Cookie"] = cookie
        payload = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            payload = json.dumps(body, ensure_ascii=False).encode()
        conn = http.client.HTTPConnection("127.0.0.1", self.port)
        conn.request(method, path, payload, headers)
        response = conn.getresponse()
        data = json.loads(response.read().decode())
        result = response.status, data, response.getheader("Set-Cookie")
        conn.close()
        return result

    def login(self, username):
        status, data, header = self.request("POST", "/api/login", {"username": username, "password": app.PASSWORD})
        self.assertEqual((status, data), (200, {"ok": True}))
        self.assertIn("HttpOnly", header)
        return header.split(";", 1)[0]

    def test_rejects_forged_cookie_and_bad_identifiers(self):
        self.assertEqual(self.request("GET", "/api/state", cookie="food_demo=county")[0], 401)
        county = self.login("county")
        self.assertEqual(self.request("GET", "/api/task/not-a-number", cookie=county)[0], 404)
        self.assertEqual(self.request("POST", "/api/task/9999", {"action": "assign", "assignee": "town_a"}, county)[0], 404)
        self.assertEqual(self.request("POST", "/api/campaign", {"name": "无效事项", "deadline": "2026-10-01", "risk_ids": [999]}, county)[0], 400)

    def test_county_town_grid_closure_flow(self):
        county = self.login("county")
        status, _, _ = self.request("POST", "/api/campaign", {"name": "石桥镇豆制品核查", "deadline": "2026-10-01", "risk_ids": [6]}, county)
        self.assertEqual(status, 200)
        with app.db() as conn:
            task_id = conn.execute("SELECT id FROM tasks WHERE risk_id=6 ORDER BY id DESC").fetchone()[0]
        self.assertEqual(self.request("POST", f"/api/task/{task_id}", {"action": "assign", "assignee": "town_a"}, county)[0], 400)
        self.assertEqual(self.request("POST", f"/api/task/{task_id}", {"action": "assign", "assignee": "town_c"}, county)[0], 200)
        town = self.login("town_c")
        self.assertEqual(self.request("POST", f"/api/task/{task_id}", {"action": "delegate", "assignee": "grid_c", "note": "核对原料票据和复测记录"}, town)[0], 200)
        grid = self.login("grid_c")
        self.assertEqual(self.request("POST", f"/api/task/{task_id}", {"action": "submit", "finding": "复测合格，票据齐全", "rectification": "完成台账归档"}, grid)[0], 200)
        self.assertEqual(self.request("POST", f"/api/task/{task_id}", {"action": "town_review", "note": "现场记录完整，同意报县级复核"}, town)[0], 200)
        self.assertEqual(self.request("POST", f"/api/task/{task_id}", {"action": "close", "note": "材料齐全，予以销号"}, county)[0], 200)
        with app.db() as conn:
            task = conn.execute("SELECT status,finding,rectification,town_review FROM tasks WHERE id=?", (task_id,)).fetchone()
            risk = conn.execute("SELECT status FROM risks WHERE id=6").fetchone()
        self.assertEqual(task["status"], "已销号")
        self.assertEqual(risk["status"], "已处置")
        self.assertTrue(all(task[key] for key in ("finding", "rectification", "town_review")))


if __name__ == "__main__":
    unittest.main()
