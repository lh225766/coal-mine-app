#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
智启煤海 · 煤矿生产排程与物料调度智能助手（Web 版）
零依赖，纯 Python 标准库实现。通过 MCP 协议(stdio)调用 coal_mine_mock_mcp.py 的 5 个工具。

启动：python webapp.py
访问：http://localhost:8080
"""

import json
import os
import re
import socket
import subprocess
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HOST = "0.0.0.0"
PORT = 8080
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MCP_SCRIPT = os.path.join(BASE_DIR, "coal_mine_mock_mcp.py")
HTML_FILE = os.path.join(BASE_DIR, "webapp.html")

# ==================== MCP 客户端（stdio JSON-RPC）====================

class MCPClient:
    """通过子进程 stdio 与 MCP 服务器通信的轻量客户端。"""

    def __init__(self, script_path):
        self.script_path = script_path
        self.proc = None
        self.lock = threading.Lock()
        self._id = 0
        self._start()

    def _start(self):
        self.proc = subprocess.Popen(
            [sys.executable, self.script_path],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            bufsize=1,
        )
        self._call("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "zhiqi-coal-web", "version": "1.0.0"},
        })
        self._notify("notifications/initialized", {})

    def _send(self, obj):
        self.proc.stdin.write(json.dumps(obj, ensure_ascii=False) + "\n")
        self.proc.stdin.flush()

    def _read(self, expect_id):
        while True:
            line = self.proc.stdout.readline()
            if not line:
                raise RuntimeError("MCP 服务器已关闭")
            line = line.strip()
            if not line:
                continue
            try:
                resp = json.loads(line)
            except json.JSONDecodeError:
                continue
            if resp.get("id") == expect_id:
                return resp
            # 忽略其他 id 的消息（如通知）

    def _call(self, method, params):
        with self.lock:
            self._id += 1
            mid = self._id
            self._send({"jsonrpc": "2.0", "id": mid, "method": method, "params": params})
            resp = self._read(mid)
            if "error" in resp:
                return {"_error": resp["error"]}
            return resp.get("result", {})

    def _notify(self, method, params):
        with self.lock:
            self._send({"jsonrpc": "2.0", "method": method, "params": params})

    def list_tools(self):
        result = self._call("tools/list", {})
        return result.get("tools", []) if isinstance(result, dict) else []

    def call_tool(self, name, args):
        result = self._call("tools/call", {"name": name, "arguments": args})
        if not isinstance(result, dict):
            return {"_error": "异常返回"}
        if result.get("isError"):
            text = result.get("content", [{}])[0].get("text", "工具执行出错")
            return {"_error": text}
        text = result.get("content", [{}])[0].get("text", "{}")
        try:
            return json.loads(text)
        except (json.JSONDecodeError, TypeError):
            return {"_raw": text}


# ==================== 意图识别引擎 ====================

MATERIAL_CODE = {"锚杆": "M001", "锚索": "M002", "钢带": "M003", "网片": "M004"}


def _extract_material(msg):
    for name, code in MATERIAL_CODE.items():
        if name in msg:
            return name, code
    return None, None


def intent_stock(msg):
    args = {"query_type": "stock"}
    _, code = _extract_material(msg)
    if code:
        args["material_code"] = code
    if "在途" in msg or "到货" in msg:
        args["query_type"] = "inbound"
    elif "消耗" in msg or "单耗" in msg or "用料" in msg:
        args["query_type"] = "consume"
    return [{"tool": "coal_mine_wms_db_query", "desc": "查询WMS物资库存", "args": args}]


def intent_equipment(msg):
    args = {"query_type": "status"}
    if "检修" in msg or "维护" in msg or "保养" in msg:
        args["query_type"] = "maintain"
    elif "能力" in msg or "运力" in msg or "输送量" in msg or "产能" in msg:
        args["query_type"] = "capacity"
    m = re.search(r"EQ[-\s]?0?([1-9])", msg.upper())
    if m:
        args["equip_id"] = "EQ-0" + m.group(1)
    return [{"tool": "coal_mine_equipment_monitor", "desc": "查询设备运行状态", "args": args}]


def intent_route(msg):
    args = {"start_point": "副井口料场", "target_point": "C301工作面", "material_weight": 2}
    if "仓储中心" in msg or "地面仓储" in msg:
        args["start_point"] = "地面仓储中心"
    if "井下物资库" in msg or "井下库" in msg:
        args["start_point"] = "井下物资库"
    if "掘进" in msg or "E102" in msg.upper():
        args["target_point"] = "掘进工作面1号"
    if "综采" in msg or "回采" in msg:
        args["target_point"] = "综采工作面"
    m = re.search(r"(\d+(?:\.\d+)?)\s*吨", msg)
    if m:
        args["material_weight"] = float(m.group(1))
    return [{"tool": "coal_mine_transport_gis_route", "desc": "计算井下配送路线", "args": args}]


def intent_schedule(msg):
    args = {"workface_list": ["C301", "E102"], "optimize_target": "max_output"}
    if "均衡" in msg or "平衡" in msg:
        args["optimize_target"] = "balance_supply"
    faces = []
    if "C301" in msg.upper() or "综采" in msg or "回采" in msg:
        faces.append("C301")
    if "E102" in msg.upper() or "掘进" in msg:
        faces.append("E102")
    if faces:
        args["workface_list"] = faces
    return [{"tool": "coal_mine_schedule_optimizer", "desc": "执行生产排程寻优", "args": args}]


def intent_export(msg):
    args = {"export_type": "excel_schedule", "title": "煤矿生产物料调度方案"}
    if "word" in msg.lower() or "日志" in msg or "调度单" in msg or "通知单" in msg:
        args["export_type"] = "word_log"
        args["title"] = "煤矿物料调度通知单"
    return [{"tool": "coal_mine_schedule_export", "desc": "导出调度文档", "args": args}]


def intent_full(msg):
    return [
        {"tool": "coal_mine_wms_db_query", "desc": "① 盘点物资库存与缺料风险",
         "args": {"query_type": "stock"}},
        {"tool": "coal_mine_equipment_monitor", "desc": "② 检查设备运行与故障状态",
         "args": {"query_type": "status"}},
        {"tool": "coal_mine_transport_gis_route", "desc": "③ 规划井下物料配送路线",
         "args": {"start_point": "副井口料场", "target_point": "C301工作面", "material_weight": 2}},
        {"tool": "coal_mine_schedule_optimizer", "desc": "④ 执行生产排程寻优",
         "args": {"workface_list": ["C301", "E102"], "optimize_target": "max_output"}},
        {"tool": "coal_mine_schedule_export", "desc": "⑤ 导出调度方案文档",
         "args": {"export_type": "excel_schedule", "schedule_id": "SCH-20261109-01",
                  "title": "煤矿生产物料调度方案"}},
    ]


INTENT_RULES = [
    ("full_dispatch",
     ["闭环", "全流程", "一键", "完整调度", "综合调度", "制定调度方案",
      "开始调度", "调度方案", "整体调度"],
     intent_full),
    ("stock",
     ["库存", "物料", "缺料", "在途", "补货", "安全库存", "锚杆", "锚索",
      "钢带", "网片", "炸药", "消耗", "单耗"],
     intent_stock),
    ("equipment",
     ["设备", "故障", "检修", "输送机", "绞车", "刮板", "皮带", "运行状态",
      "抢修", "维护"],
     intent_equipment),
    ("route",
     ["路线", "配送", "耗时", "拥堵", "巷道", "送达", "运输", "怎么送", "送料"],
     intent_route),
    ("schedule",
     ["排程", "优化", "甘特", "产量", "进尺", "排产"],
     intent_schedule),
    ("export",
     ["导出", "文档", "调度单", "下载", "通知单", "excel", "word", "报表"],
     intent_export),
]


def detect_intent(msg):
    """返回 (意图名, 工具调用计划列表)。"""
    best_name, best_score, best_handler = "chat", 0, None
    for name, keywords, handler in INTENT_RULES:
        score = sum(1 for k in keywords if k in msg)
        if score > best_score:
            best_name, best_score, best_handler = name, score, handler
    if best_handler and best_score > 0:
        return best_name, best_handler(msg)
    return "chat", []


# ==================== 结果摘要生成 ====================

def summarize(tool, result):
    if not isinstance(result, dict) or result.get("_error"):
        return "调用失败：" + str(result.get("_error", "未知错误"))
    if tool == "coal_mine_wms_db_query":
        return result.get("conclusion") or result.get("msg", "查询完成")
    if tool == "coal_mine_equipment_monitor":
        return result.get("fault_summary") or result.get("msg", "查询完成")
    if tool == "coal_mine_transport_gis_route":
        return result.get("conclusion", "路线计算完成")
    if tool == "coal_mine_schedule_optimizer":
        return result.get("conclusion", "排程优化完成")
    if tool == "coal_mine_schedule_export":
        return "{msg}：{file_name}（{file_size}）".format(**result) if "file_name" in result else "导出完成"
    return "执行完成"


HELP_TEXT = (
    "您好，我是**智启煤海**智能助手，专注煤矿生产排程与物料调度。"
    "我可以帮您：\n"
    "1. **一键闭环调度** — 说“一键闭环调度”，自动完成库存盘点→设备检查→路线规划→排程优化→文档导出\n"
    "2. **查库存** — 如“查一下锚杆库存”、“在途物料有哪些”\n"
    "3. **查设备** — 如“设备运行状态”、“EQ-02故障情况”\n"
    "4. **算路线** — 如“从副井口料场到掘进工作面怎么送”\n"
    "5. **排程优化** — 如“对C301和E102排程优化”\n"
    "6. **导出文档** — 如“导出Excel调度表”、“导出Word调度单”\n\n"
    "请告诉我您的需求。"
)


def build_reply(intent, calls):
    if intent == "chat":
        return HELP_TEXT
    if intent == "full_dispatch":
        lines = ["已为您完成**全流程闭环调度**，各环节结果如下：", ""]
        for c in calls:
            lines.append("- **{}**：{}".format(c["desc"], c["summary"]))
        lines.append("")
        lines.append("调度方案文档已生成。如需 Word 版调度通知单，请说“导出Word调度单”。")
        return "\n".join(lines)
    parts = []
    for c in calls:
        parts.append("**{}**：{}".format(c["desc"], c["summary"]))
    return "\n".join(parts)


# ==================== 智能体执行 ====================

def run_agent(message):
    intent, plan = detect_intent(message)
    calls = []
    for step in plan:
        result = mcp_client.call_tool(step["tool"], step["args"])
        calls.append({
            "tool": step["tool"],
            "desc": step["desc"],
            "args": step["args"],
            "result": result,
            "summary": summarize(step["tool"], result),
        })
    reply = build_reply(intent, calls)
    return {"intent": intent, "reply": reply, "tool_calls": calls}


mcp_client = None  # 在 main 中初始化


# ==================== HTTP 服务 ====================

class Handler(BaseHTTPRequestHandler):

    def _send_json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, text):
        body = text.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            try:
                with open(HTML_FILE, "r", encoding="utf-8") as f:
                    self._send_html(f.read())
            except FileNotFoundError:
                self._send_html("<h3>未找到 webapp.html，请确认它与 webapp.py 在同一目录。</h3>")
        elif self.path == "/api/tools":
            self._send_json({"tools": mcp_client.list_tools()})
        elif self.path == "/api/health":
            self._send_json({"status": "ok"})
        else:
            self._send_json({"error": "not found"}, 404)

    def do_POST(self):
        if self.path != "/api/chat":
            self._send_json({"error": "not found"}, 404)
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length).decode("utf-8")
            data = json.loads(raw)
            message = str(data.get("message", "")).strip()
        except Exception:
            self._send_json({"error": "请求格式错误"}, 400)
            return
        if not message:
            self._send_json({"reply": HELP_TEXT, "intent": "chat", "tool_calls": []})
            return
        try:
            self._send_json(run_agent(message))
        except Exception as e:
            self._send_json({"error": "智能体执行异常：" + str(e)}, 500)

    def log_message(self, *args):
        pass  # 静默日志


def _get_lan_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return ""


def _safe_open_browser(url):
    """尝试打开浏览器，容器/无GUI环境下静默跳过。"""
    try:
        webbrowser.open(url)
    except Exception:
        pass


def main():
    global mcp_client
    print("=" * 56)
    print("  智启煤海 · 煤矿生产排程与物料调度智能助手（Web 版）")
    print("=" * 56)
    print("  正在连接 MCP 服务器 ...")
    try:
        mcp_client = MCPClient(MCP_SCRIPT)
    except Exception as e:
        print("  [错误] 无法启动 MCP 服务器：", e)
        print("  请确认 coal_mine_mock_mcp.py 与 webapp.py 在同一目录。")
        sys.exit(1)
    tools = mcp_client.list_tools()
    print("  已连接，可用工具 {} 个：".format(len(tools)))
    for t in tools:
        print("   -", t["name"])
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    local_url = "http://127.0.0.1:{}".format(PORT)
    lan_ip = _get_lan_ip()
    print()
    print("  服务已启动（任何人可访问）")
    print("  本机访问   → {}".format(local_url))
    if lan_ip:
        print("  局域网访问 → http://{}:{}".format(lan_ip, PORT))
    print("  浏览器即将自动打开，或手动复制上方链接访问。")
    print("  按 Ctrl+C 停止服务。")
    print("=" * 56)
    threading.Timer(1.0, lambda: _safe_open_browser(local_url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n服务已停止。")


if __name__ == "__main__":
    main()