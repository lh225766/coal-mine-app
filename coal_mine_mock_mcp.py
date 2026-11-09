#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
煤矿生产排程与物料调度 Mock MCP Server (stdio)
纯标准库实现，无需安装任何依赖。

启动方式：python coal_mine_mock_mcp.py
"""

import json
import sys

SERVER_INFO = {"name": "coal-mine-mock", "version": "1.0.0"}

# ---------------- 工具定义 ----------------

TOOLS = [
    {
        "name": "coal_mine_wms_db_query",
        "description": "查询煤矿物资库存、在途订单、历史单耗。对接仓储WMS与采掘台账的仿真数据，用于物料需求测算与缺料判断。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "workface_id": {"type": "string", "description": "工作面编号，可选，如 C301"},
                "material_code": {"type": "string", "description": "物料编码，可选，如 M001"},
                "query_type": {
                    "type": "string",
                    "description": "查询类型：stock 库存 / inbound 在途 / consume 历史消耗",
                    "enum": ["stock", "inbound", "consume"],
                },
            },
            "required": ["query_type"],
        },
    },
    {
        "name": "coal_mine_equipment_monitor",
        "description": "查询综采设备、皮带、绞车等设备的实时运行状态、故障标记、计划检修时间与额定输送能力，用于排程校验与异常重调度。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "equip_id": {"type": "string", "description": "设备编号，可选，如 EQ-01"},
                "workface_id": {"type": "string", "description": "工作面编号，可选"},
                "query_type": {
                    "type": "string",
                    "description": "查询类型：status 运行状态 / maintain 检修计划 / capacity 输送能力",
                    "enum": ["status", "maintain", "capacity"],
                },
            },
            "required": ["query_type"],
        },
    },
    {
        "name": "coal_mine_transport_gis_route",
        "description": "基于井下巷道拓扑计算物料配送时长，校验通道拥堵，返回多条配送路线并推荐最优方案。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "start_point": {"type": "string", "description": "起始点位，如 副井口料场"},
                "target_point": {"type": "string", "description": "目标工作面，如 C301工作面"},
                "material_weight": {"type": "number", "description": "物料重量，单位吨"},
                "delivery_time_window": {"type": "string", "description": "要求送达时段，如 08:00-10:00"},
            },
            "required": ["start_point", "target_point"],
        },
    },
    {
        "name": "coal_mine_schedule_optimizer",
        "description": "接收采掘工作面计划与物料、设备约束，执行排程寻优，输出甘特排程要点、预计产量与风险提示。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "workface_list": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "参与排程的工作面列表",
                },
                "optimize_target": {
                    "type": "string",
                    "description": "优化目标：max_output 产量最大 / balance_supply 供需均衡",
                    "enum": ["max_output", "balance_supply"],
                },
                "constraint": {
                    "type": "object",
                    "description": "约束开关，如 material_limit 物料约束、equip_limit 设备约束",
                },
            },
            "required": ["workface_list"],
        },
    },
    {
        "name": "coal_mine_schedule_export",
        "description": "将排程与调度结果导出为Excel调度表或Word调度日志，返回仿真下载链接。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "schedule_id": {"type": "string", "description": "排程编号"},
                "export_type": {
                    "type": "string",
                    "description": "导出类型：excel_schedule / word_log",
                    "enum": ["excel_schedule", "word_log"],
                },
                "title": {"type": "string", "description": "文档标题"},
            },
            "required": ["export_type"],
        },
    },
]

# ---------------- 仿真数据 ----------------

MATERIALS = [
    {"material_code": "M001", "material_name": "锚杆", "stock_num": 1250, "safe_stock": 300,
     "on_the_way": 800, "arrival_time": "2026-11-10", "unit_consume": 0.85},
    {"material_code": "M002", "material_name": "锚索", "stock_num": 180, "safe_stock": 250,
     "on_the_way": 400, "arrival_time": "2026-11-12", "unit_consume": 0.42},
    {"material_code": "M003", "material_name": "钢带", "stock_num": 620, "safe_stock": 400,
     "on_the_way": 0, "arrival_time": "", "unit_consume": 1.10},
    {"material_code": "M004", "material_name": "网片", "stock_num": 980, "safe_stock": 500,
     "on_the_way": 300, "arrival_time": "2026-11-09", "unit_consume": 2.30},
]

EQUIPMENTS = [
    {"equip_id": "EQ-01", "equip_name": "综采面刮板输送机", "workface_id": "C301",
     "run_status": "正常运行", "next_maintain": "2026-11-15", "capacity": 1200, "fault_alert": False},
    {"equip_id": "EQ-02", "equip_name": "顺槽胶带输送机", "workface_id": "C301",
     "run_status": "故障停机", "next_maintain": "2026-11-08", "capacity": 0, "fault_alert": True},
    {"equip_id": "EQ-03", "equip_name": "掘进面刮板机", "workface_id": "E102",
     "run_status": "正常运行", "next_maintain": "2026-11-20", "capacity": 600, "fault_alert": False},
]

CONSUME_HISTORY = [
    {"workface_id": "C301", "material_name": "锚杆", "avg_daily_consume": 120, "period": "2026-10"},
    {"workface_id": "E102", "material_name": "锚索", "avg_daily_consume": 36, "period": "2026-10"},
]


def tool_query_material_stock(args):
    qtype = args.get("query_type", "stock")
    code = args.get("material_code")
    data = MATERIALS
    if code:
        data = [m for m in data if m["material_code"] == code]
    if qtype == "consume":
        return {"code": 200, "msg": "查询成功", "query_type": "consume", "data": CONSUME_HISTORY}
    if qtype == "inbound":
        data = [m for m in data if m["on_the_way"] > 0]
        slim = [{"material_name": m["material_name"], "on_the_way": m["on_the_way"],
                 "arrival_time": m["arrival_time"]} for m in data]
        return {"code": 200, "msg": "查询成功", "query_type": "inbound", "data": slim}
    shortages = [m["material_name"] for m in data if m["stock_num"] < m["safe_stock"]]
    conclusion = ("库存低于安全线的物料：" + "、".join(shortages) + "，建议立即补货") if shortages else "全部物料库存充足"
    return {"code": 200, "msg": "查询成功", "query_type": "stock", "data": data, "conclusion": conclusion}


def tool_query_equipment_status(args):
    qtype = args.get("query_type", "status")
    equip_id = args.get("equip_id")
    data = EQUIPMENTS
    if equip_id:
        data = [e for e in data if e["equip_id"] == equip_id]
    if qtype == "maintain":
        return {"code": 200, "msg": "查询成功", "query_type": "maintain",
                "data": [{"equip_name": e["equip_name"], "next_maintain": e["next_maintain"]} for e in data]}
    if qtype == "capacity":
        return {"code": 200, "msg": "查询成功", "query_type": "capacity",
                "data": [{"equip_name": e["equip_name"], "capacity": e["capacity"]} for e in data]}
    faults = [e["equip_name"] for e in data if e["fault_alert"]]
    return {"code": 200, "msg": "查询成功", "query_type": "status", "data": data,
            "fault_summary": ("存在故障设备：" + "、".join(faults)) if faults else "设备运行正常"}


def tool_plan_transport_route(args):
    start = args.get("start_point", "副井口料场")
    target = args.get("target_point", "C301工作面")
    weight = args.get("material_weight", 2)
    return {
        "code": 200,
        "msg": "路径计算完成",
        "start_point": start,
        "target_point": target,
        "material_weight": weight,
        "route_list": [
            {"route_no": "路线1", "path": start + " → 主运大巷 → 单轨吊 → " + target,
             "cost_minute": 42, "congestion": False, "recommend": True,
             "remark": "避开出矸高峰，推荐采用"},
            {"route_no": "路线2", "path": start + " → 轨道大巷 → 绞车提升 → " + target,
             "cost_minute": 65, "congestion": True, "recommend": False,
             "remark": "与人员升井时段冲突，作为备用"},
        ],
        "conclusion": "推荐路线1，预计42分钟送达，可满足配送时间窗",
    }


def tool_optimize_schedule(args):
    faces = args.get("workface_list", ["C301", "E102"])
    target = args.get("optimize_target", "max_output")
    return {
        "code": 200,
        "msg": "优化计算完成",
        "schedule_result": "SCH-20261107-01",
        "optimize_target": target,
        "gantt_info": [
            {"workface_id": faces[0], "task": "回采推进", "period": "11-08 至 11-12", "plan": "日推进4刀"},
            {"workface_id": faces[-1], "task": "掘进支护", "period": "11-09 至 11-14", "plan": "日进尺6m"},
        ],
        "expect_output": {"daily_coal": "8200吨", "daily_tunneling": "6m"},
        "risk": [
            "EQ-02 胶带输送机故障停机，11-08 前需完成抢修，否则影响 C301 出煤",
            "锚索库存180根低于安全库存250根，11-12 前须补货到位",
        ],
        "conclusion": "在保障检修与物料到位前提下，该排程可达成日产量目标",
    }


def tool_export_schedule_document(args):
    etype = args.get("export_type", "excel_schedule")
    schedule_id = args.get("schedule_id", "SCH-20261107-01")
    title = args.get("title", "煤矿生产物料调度方案")
    if etype == "word_log":
        return {"code": 200, "msg": "文件生成成功", "file_type": "Word",
                "file_name": title + ".docx",
                "download_url": "mock://files/" + schedule_id + "_log.docx", "file_size": "96KB"}
    return {"code": 200, "msg": "文件生成成功", "file_type": "Excel",
            "file_name": title + ".xlsx",
            "download_url": "mock://files/" + schedule_id + "_schedule.xlsx", "file_size": "138KB",
            "sheets": ["采掘排程", "物料需求清单", "运输配送计划"]}


HANDLERS = {
    "coal_mine_wms_db_query": tool_query_material_stock,
    "coal_mine_equipment_monitor": tool_query_equipment_status,
    "coal_mine_transport_gis_route": tool_plan_transport_route,
    "coal_mine_schedule_optimizer": tool_optimize_schedule,
    "coal_mine_schedule_export": tool_export_schedule_document,
}

# ---------------- JSON-RPC 主循环 ----------------


def send(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def ok(msg_id, result):
    send({"jsonrpc": "2.0", "id": msg_id, "result": result})


def err(msg_id, code, message):
    send({"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}})


def handle(req):
    method = req.get("method", "")
    msg_id = req.get("id")
    params = req.get("params", {}) or {}

    # 通知类消息无 id，不需要回复
    if msg_id is None:
        return

    if method == "initialize":
        ok(msg_id, {
            "protocolVersion": params.get("protocolVersion", "2024-11-05"),
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": SERVER_INFO,
        })
        return

    if method == "tools/list":
        ok(msg_id, {"tools": TOOLS})
        return

    if method == "tools/call":
        name = params.get("name")
        args = params.get("arguments", {}) or {}
        handler = HANDLERS.get(name)
        if not handler:
            ok(msg_id, {"content": [{"type": "text", "text": "未知工具：" + str(name)}], "isError": True})
            return
        try:
            result = handler(args)
            ok(msg_id, {"content": [{"type": "text",
                                     "text": json.dumps(result, ensure_ascii=False, indent=2)}],
                        "isError": False})
        except Exception as e:  # noqa: BLE001
            ok(msg_id, {"content": [{"type": "text", "text": "工具执行异常：" + str(e)}], "isError": True})
        return

    if method == "ping":
        ok(msg_id, {})
        return

    err(msg_id, -32601, "Method not found: " + method)


def main():
    # Windows 下把标准流固定为 UTF-8，避免中文 JSON 乱码
    try:
        sys.stdin.reconfigure(encoding="utf-8")
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError as e:
            send({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error: " + str(e)}})
            continue
        try:
            handle(req)
        except Exception as e:  # noqa: BLE001
            send({"jsonrpc": "2.0", "id": req.get("id"), "error": {"code": -32603, "message": str(e)}})


if __name__ == "__main__":
    main()
