#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
督学小鲸娘 · 核心引擎
=====================
一个真正会「记账」的督学智能体后端：不是聊天人设，而是状态机 + 规则引擎。

它负责四件事：
  1. 每天生成【今天 / 保底 / 别碰】三行任务（从当前关卡自动切块）
  2. 评判你的汇报是「真推进」还是「原地打转」
  3. 按规则动态砍范围（落后就缩、超前只加一点、提新方向就拦）
  4. 周二/周三生成给导师的一页汇报

用法：
  python whale.py today              今天的任务
  python whale.py checkin "今天..."   汇报 + 判断
  python whale.py status             总览
  python whale.py report lab1|lab2   一页汇报草稿
  python whale.py done               标记当前小节完成
  python whale.py prompt             给大模型的上下文包（可直接粘给鲸鱼娘）
  python whale.py serve              打开本地仪表盘
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
STATE_PATH = DATA_DIR / "state.json"
JOURNAL_PATH = DATA_DIR / "journal.jsonl"

# ---------------------------------------------------------------- 默认状态 ----

DEFAULT_STATE = {
    "version": 1,
    "profile": {
        "user": "主人",
        "term_start": "2026-11-05",
        "exam_week_start": "2026-12-28",
        "term_end": "2027-01-16",
    },
    # 里程碑：离得最近的还没完成的那个，就是全盘的指挥棒
    "milestones": [
        {
            "id": "M1",
            "title": "实验室2 拿到 开环 / P / P+I 三条曲线对比图",
            "due": "2026-11-22",
            "note": "进组敲门砖",
            "done": False,
        },
        {
            "id": "M2",
            "title": "实验室1 交出约 10 页设计报告",
            "due": "2026-12-13",
            "note": "保底线交差",
            "done": False,
        },
        {
            "id": "M3",
            "title": "两边各做一次能讲 5 分钟的演示",
            "due": "2026-12-27",
            "note": "之后进期末周",
            "done": False,
        },
    ],
    # 主线关卡。每个 step 拆成若干 part，一天只推一个 part。
    "tracks": {
        "lab2": {
            "name": "实验室2 · 关节电机驱动（主线）",
            "deadline": "2026-11-22",
            "steps": [
                {
                    "id": "L2-1",
                    "title": "读懂通信协议：把一条指令按字节拆开",
                    "parts": [
                        {
                            "desc": "翻到电机手册的通信章节，抄下一条完整指令（每个字节都写十六进制）",
                            "out": "一页纸：完整指令 + 波特率",
                            "min": 45,
                        },
                        {
                            "desc": "给抄下的每个字节写注释：哪个是 ID、哪个是模式、哪个是目标位置、哪个是校验",
                            "out": "每条指令能指着字节说出用途",
                            "min": 45,
                        },
                    ],
                },
                {
                    "id": "L2-2",
                    "title": "用串口发指令让电机转起来",
                    "parts": [
                        {
                            "desc": "把 USB-TTL 接上电机，用串口助手把上面那条指令发出去，让它转一下",
                            "out": "能复现的指令 + 接线照片",
                            "min": 45,
                        },
                        {
                            "desc": "把「最短一条能让它转」的指令存成 txt（含波特率和接线脚位）",
                            "out": "一个能直接用的指令文件",
                            "min": 45,
                        },
                    ],
                },
                {
                    "id": "L2-3",
                    "title": "读懂电机回传的反馈",
                    "parts": [
                        {
                            "desc": "发一条「读反馈」指令，把回的字节原样抄下来",
                            "out": "一行回传数据",
                            "min": 45,
                        },
                        {
                            "desc": "标出位置 / 速度 / 电流各占哪几个字节、什么单位、怎么换算",
                            "out": "反馈字段表",
                            "min": 45,
                        },
                    ],
                },
                {
                    "id": "L2-4",
                    "title": "定时读位置、自己算速度、画曲线",
                    "parts": [
                        {
                            "desc": "每 20ms 读一次位置，连续读 5 秒，存成 CSV",
                            "out": "一个 250 行的数据文件",
                            "min": 60,
                        },
                        {
                            "desc": "用差分算速度，画出一条速度曲线（能用 Excel 就行）",
                            "out": "一张速度图",
                            "min": 60,
                        },
                    ],
                },
                {
                    "id": "L2-5",
                    "title": "开环 → P → P+I 三条曲线画在一张图上",
                    "parts": [
                        {
                            "desc": "开环给一个阶跃：记下超调量和稳定时间",
                            "out": "开环曲线 + 两个数",
                            "min": 60,
                        },
                        {
                            "desc": "只加 P，调一个 Kp 值，把曲线叠到同一张图上",
                            "out": "第二条曲线",
                            "min": 60,
                        },
                        {
                            "desc": "加上 I，三条曲线同图，写一句结论（谁快、谁超调大）",
                            "out": "对比图 — M1 交付物",
                            "min": 60,
                        },
                    ],
                },
                {
                    "id": "L2-6",
                    "title": "速度环 → 电流环 → 力矩 → 左右轮同步",
                    "parts": [
                        {
                            "desc": "速度环：让电机稳定跑在设定转速，记录波动",
                            "out": "速度环曲线",
                            "min": 90,
                        },
                        {
                            "desc": "电流环 + 力矩控制：给定力矩，验证输出",
                            "out": "力矩响应记录",
                            "min": 90,
                        },
                        {
                            "desc": "左右轮同步：两轮同速跑一条直线",
                            "out": "一段能看的视频",
                            "min": 60,
                        },
                    ],
                },
            ],
        },
        "lab1": {
            "name": "实验室1 · 踝关节助力（保底线）",
            "deadline": "2026-12-13",
            "steps": [
                {
                    "id": "L1-1",
                    "title": "优化设计方案（导师说太简略）",
                    "parts": [
                        {
                            "desc": "翻出导师的批注，列出「太简略」具体缺哪几块，至少写 5 条",
                            "out": "一张缺口清单",
                            "min": 45,
                        },
                        {
                            "desc": "补一页方案 v2：对比表 + 选型理由",
                            "out": "方案 v2 一页纸",
                            "min": 45,
                        },
                    ],
                },
                {
                    "id": "L1-2",
                    "title": "补传动方案和选型依据",
                    "parts": [
                        {
                            "desc": "算出所需力矩和传动比，写成两行式子",
                            "out": "计算页",
                            "min": 45,
                        },
                        {
                            "desc": "列 3 个候选电机/减速器，做一张选型对比表",
                            "out": "选型表",
                            "min": 45,
                        },
                    ],
                },
                {
                    "id": "L1-3",
                    "title": "出工程图 2–3 张",
                    "parts": [
                        {
                            "desc": "画主视图 + 一个剖视图，标好关键尺寸",
                            "out": "2 张图",
                            "min": 60,
                        },
                        {
                            "desc": "补一张零件图，装配体做爆炸视图",
                            "out": "零件图 + 爆炸图",
                            "min": 60,
                        },
                    ],
                },
                {
                    "id": "L1-4",
                    "title": "写约 10 页设计报告",
                    "parts": [
                        {
                            "desc": "搭框架：目录 + 图位，先把页数凑出来",
                            "out": "报告骨架",
                            "min": 60,
                        },
                        {
                            "desc": "写「设计计算」一章（选型、传动比、力矩校核）",
                            "out": "3–4 页正文",
                            "min": 60,
                        },
                        {
                            "desc": "写「搭建与调试」+「结论」两章",
                            "out": "凑满约 10 页",
                            "min": 60,
                        },
                    ],
                },
                {
                    "id": "L1-5",
                    "title": "做一次能讲 5 分钟的演示",
                    "parts": [
                        {
                            "desc": "录一段 5 分钟演示视频，并写一页讲稿要点",
                            "out": "视频 + 讲稿",
                            "min": 60,
                        },
                    ],
                },
            ],
        },
    },
    "log": [],
    "tasks": {},
}

# 每周可用时段（小时）。周四永远是空着的。
WEEK_SLOTS = {0: 1.5, 1: 1.5, 2: 1.5, 3: 0.0, 4: 5.5, 5: 8.5, 6: 3.0}
WEEKDAY_CN = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]

# 明确不在计划里的东西：提了就拦
BANNED = [
    "视觉", "建图", "SLAM", "slam", "ROS2", "ros2", "FOC", "foc",
    "EtherCAT", "ethercat", "IsaacLab", "isaac", "PCB", "pcb",
    "深度学习", "神经网络", "大模型", "强化学习", "语义", "导航",
]

DOUBT_WORDS = ["好菜", "不配", "我很差", "没用", "废物", "跟不上", "放弃", "摆烂", "自我怀疑", "自卑", "算了"]
BLOCK_WORDS = ["作业", "网课", "考试", "被叫", "临时", "生病", "头疼", "头痛", "好累", "太累", "困", "躺", "没时间", "状态差", "崩了", "加班", "开会", "实验课"]
# 强产出：一定留下了能给别人看的东西
PRODUCE_STRONG = ["写", "画", "做", "发", "测", "跑通", "录", "交", "出图", "焊", "连", "调", "完成", "搞定",
                  "验证", "对比", "截图", "保存", "提交", "抄", "存"]
# 弱产出：得配合一个具体的产物名词才算数（"补了一页"算，"补 C 语言"不算）
PRODUCE_WEAK = ["补", "改", "整理", "梳理"]
ARTIFACTS = ["笔记", "图", "指令", "曲线", "截图", "文件", "视频", "报告", "表格", "表", "数据",
             "页", "代码", "文档", "记录", "草稿", "清单", "公式"]
INPUT_WORDS = ["看", "读", "搜", "查", "找", "想", "学", "理解", "翻", "浏览", "过了一遍",
               "复习", "补课", "补觉"]

# 用来判断一条汇报属于哪个实验室
LAB1_HINT = ["方案", "工程图", "装配", "踝关节", "设计报告", "选型", "传动", "零件图", "爆炸图", "助力"]
LAB2_HINT = ["电机", "协议", "串口", "指令", "曲线", "闭环", "电流环", "速度环", "反馈", "阶跃", "编码器", "Kp", "PI"]

# ---------------------------------------------------------------- 小工具 ----


def today() -> dt.date:
    return dt.date.today()


def parse_day(s: str) -> dt.date:
    return dt.date.fromisoformat(s)


def days_until(d: dt.date) -> int:
    return (d - today()).days


def load() -> dict:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not STATE_PATH.exists():
        save(DEFAULT_STATE)
        return json.loads(json.dumps(DEFAULT_STATE))
    with STATE_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)


def save(state: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = STATE_PATH.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    tmp.replace(STATE_PATH)


def append_journal(entry: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with JOURNAL_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def mentions(text: str, words) -> list[str]:
    return [w for w in words if w in text]


# ------------------------------------------------------------- 进度与状态 ----


def nearest_milestone(state: dict) -> dict | None:
    live = [m for m in state["milestones"] if not m["done"]]
    if not live:
        return None
    return min(live, key=lambda m: parse_day(m["due"]))


def current_step(state: dict, track: str) -> dict | None:
    for s in state["tracks"][track]["steps"]:
        if not s.get("done"):
            return s
    return None


def current_part(state: dict, track: str) -> tuple[dict | None, int, dict | None]:
    """返回 (step, part_index, part)"""
    step = current_step(state, track)
    if step is None:
        return None, -1, None
    done = step.get("done_parts", 0)
    parts = step["parts"]
    if done >= len(parts):
        return step, -1, None
    return step, done, parts[done]


def track_progress(state: dict, track: str) -> dict:
    t = state["tracks"][track]
    steps = t["steps"]
    total_parts = sum(len(s["parts"]) for s in steps)
    done_parts = sum(
        len(s["parts"]) if s.get("done") else min(s.get("done_parts", 0), len(s["parts"]))
        for s in steps
    )
    steps_done = sum(1 for s in steps if s.get("done"))
    return {
        "name": t["name"],
        "total_steps": len(steps),
        "steps_done": steps_done,
        "total_parts": total_parts,
        "parts_done": done_parts,
        "pct": round(done_parts / total_parts * 100) if total_parts else 0,
    }


def streak_gap(state: dict) -> int:
    """距离上次汇报隔了几天（没汇报过算 0，避免一上来就吓人）。"""
    if not state["log"]:
        return 0
    last = parse_day(state["log"][-1]["date"])
    return max(0, (today() - last).days - 1)


def logged_today(state: dict) -> bool:
    return any(e["date"] == today().isoformat() for e in state["log"])


def week_logs(state: dict, weeks_back: int = 1) -> list[dict]:
    """最近 7 天（可调）的汇报。"""
    cut = today() - dt.timedelta(days=7 * weeks_back)
    return [e for e in state["log"] if parse_day(e["date"]) >= cut]


def in_exam_week(state: dict) -> bool:
    return today() >= parse_day(state["profile"]["exam_week_start"])


# ------------------------------------------------------------- 任务生成 ----


def gen_today(state: dict) -> dict:
    """生成今天的【今天 / 保底 / 别碰】。结果落到 state['tasks'][今天]。"""
    d = today()
    key = d.isoformat()
    if key in state["tasks"]:
        return state["tasks"][key]

    m = nearest_milestone(state)
    left = days_until(parse_day(m["due"])) if m else 999
    gap = streak_gap(state)
    slot = WEEK_SLOTS[d.weekday()]

    main = ""
    low = ""
    avoid = []

    if in_exam_week(state):
        main = "期末周：主线全部暂停，只复习考试"
        low = "睡够 7 小时"
        avoid = ["实验室2 闭环", "实验室1 报告"]
    elif gap >= 2:
        # 连续 2 天没汇报：不追问，直接砍到保底
        main = "先别铺大摊子：重启只需 15 分钟"
        low = "把上一节的笔记翻出来读一遍，写一句「我停在哪个字节」"
        avoid = ["新章节", "新工具", "重新规划整个学期"]
    elif d.weekday() == 3:
        main = "周四空着：写作业 / 上网课 / 补 C 语言指针"
        low = "补 20 分钟 C 语言指针（数组和指针怎么绕）"
        avoid = ["实验室任务（今天本来就不排）"]
    else:
        # 主线优先 lab2；M1 过了之后再轮到 lab1 赶 M2
        track = "lab2"
        if state["milestones"][0]["done"]:
            track = "lab1"
        step, idx, part = current_part(state, track)
        if step is None:
            track = "lab1" if track == "lab2" else "lab2"
            step, idx, part = current_part(state, track)
        if step is None:
            main = "两条线的关卡都清完了,等新任务"
            low = "整理一遍现有笔记"
        else:
            tag = "实验室2" if track == "lab2" else "实验室1"
            mm = min(part["min"], 60) if part["min"] > 60 else part["min"]
            main = f"{tag}：{part['desc']}（约 {mm} 分钟）→ {part['out']}"
            low = f"崩了也做这个：{step['title']} 只做 10 分钟，写下「做到哪了」"
            avoid = list(BANNED[:6]) + ["比当前更靠后的章节"]

    if m:
        head = f"{m['id']} 还有 {left} 天"
    else:
        head = "里程碑全清"

    task = {
        "date": key,
        "headline": head,
        "main": main,
        "low": low,
        "avoid": "、".join(avoid),
        "status": "open",
    }
    state["tasks"][key] = task
    return task


def render_task(task: dict) -> str:
    return (
        f"【今天】{task['main']}\n"
        f"【保底】{task['low']}\n"
        f"【别碰】{task['avoid']}"
    )


# ------------------------------------------------------------- 汇报评判 ----


def _similar(a: str, b: str) -> float:
    sa, sb = set(a), set(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def judge(state: dict, text: str) -> dict:
    """把一段汇报翻译成：判断 + 明天改不改 + 降门槛动作。"""
    t = text.strip()
    prev = state["log"][-1]["text"] if state["log"] else ""
    banned = mentions(t, BANNED)
    doubt = mentions(t, DOUBT_WORDS)
    blocked = mentions(t, BLOCK_WORDS)
    strong = mentions(t, PRODUCE_STRONG)
    weak = mentions(t, PRODUCE_WEAK)
    artifacts = mentions(t, ARTIFACTS)
    produced = strong + [w for w in weak if artifacts]
    consumed = mentions(t, INPUT_WORDS)
    repeat = bool(prev) and _similar(prev, t) > 0.55

    step, idx, part = current_part(state, "lab2")
    if step is None:
        step, idx, part = current_part(state, "lab1")

    res = {
        "verdict": "",
        "evidence": [],
        "tomorrow": "keep",
        "action": "",
        "advance": False,
        "reply": "",
    }

    # 1) 跑偏：提了明确不在计划里的东西
    if banned:
        res["verdict"] = "跑偏"
        res["tomorrow"] = "keep"
        res["evidence"] = [f"提到 {', '.join(banned)}"]
        res["reply"] = (
            f"先把闭环跑通，{banned[0]} 这个排到进组以后。\n"
            f"今天要做的还是：{part['desc'] if part else '当前那一节'}。"
        )
        return res

    # 2) 自我怀疑：不安慰，给事实
    if doubt:
        res["verdict"] = "自我怀疑"
        res["evidence"] = [f"出现「{doubt[0]}」"]
        p2 = track_progress(state, "lab2")
        m = nearest_milestone(state)
        left = days_until(parse_day(m["due"])) if m else 0
        res["reply"] = (
            f"你现在会的是电机驱动 + 编码器测速 + PID，离门槛差的就是通信协议和闭环。\n"
            f"口径给你：lab2 关卡完成 {p2['parts_done']}/{p2['total_parts']}，离 M1 还有 {left} 天。按现在的节奏，够。"
        )
        return res

    # 3) 被现实占掉：顺延，不批评
    if blocked and not artifacts:
        res["verdict"] = "受阻顺延"
        res["tomorrow"] = "shift"
        res["evidence"] = [f"被「{', '.join(blocked[:2])}」占了"]
        res["reply"] = (
            f"今天顺延，不算你的错。这周哪天能补？周四或周末各留一个 45 分钟的格子就行。\n"
            f"明天还是：{part['desc'] if part else '当前那一节'}。"
        )
        return res

    # 4) 打转：只有看/读，没有动手产物
    if produced:
        res["verdict"] = "真推进"
        if artifacts and not strong:
            res["evidence"] = [f"有实物：{', '.join(artifacts[:3])}"]
        else:
            res["evidence"] = [f"有产物：{', '.join(produced[:3])}"]
        res["advance"] = True
        if repeat:
            res["evidence"].append("和上次说的内容高度重合")
        res["reply"] = "这是真推进，记一笔。"
        return res

    if consumed:
        res["verdict"] = "原地打转"
        res["tomorrow"] = "shrink"
        res["evidence"] = [f"只有「{', '.join(consumed[:3])}」，没有能拿出来的东西"]
        res["action"] = "把门槛降到 20 分钟，只交一件能给别人看的东西"
        res["reply"] = (
            f"看了不等于会了。今天只做一件事：把一条指令抄下来，每个字节旁边写上它是什么，20 分钟。\n"
            f"写不完也没关系，明天接着这一节。"
        )
        return res

    # 5) 说不清：只问一个最关键的问题
    res["verdict"] = "信息不足"
    res["reply"] = (
        f"一句话就行：今天有没有一个能拿去给师兄看的东西（截图 / 一页笔记 / 一条曲线）？\n"
        f"没有的话明天就补 {part['out'] if part else '当前产出'}。"
    )
    return res


def pick_track(state: dict, text: str) -> str:
    """从汇报文本判断这条活属于哪个实验室。"""
    l1 = mentions(text, LAB1_HINT)
    l2 = mentions(text, LAB2_HINT)
    if l1 and not l2:
        return "lab1"
    if l2 and not l1:
        return "lab2"
    return "lab2" if not state["milestones"][0]["done"] else "lab1"


def apply_advance(state: dict, track: str) -> dict | None:
    """推进当前 track 的当前 part。返回推进后的说明。"""
    step, idx, part = current_part(state, track)
    if step is None:
        return None
    step["done_parts"] = step.get("done_parts", 0) + 1
    finished = step["done_parts"] >= len(step["parts"])
    if finished:
        step["done"] = True
    # 里程碑自动判定
    for m in state["milestones"]:
        if m["done"]:
            continue
        if m["id"] == "M1" and state["tracks"]["lab2"]["steps"][4].get("done"):
            m["done"] = True
        if m["id"] == "M2" and state["tracks"]["lab1"]["steps"][3].get("done"):
            m["done"] = True
        if m["id"] == "M3":
            if state["tracks"]["lab1"]["steps"][4].get("done") and state["tracks"]["lab2"]["steps"][5].get("done"):
                m["done"] = True
    return {"step": step["id"], "finished_step": finished, "part": part["desc"]}


def record(state: dict, text: str, verdict: dict, track: str | None = None) -> None:
    entry = {
        "date": today().isoformat(),
        "weekday": WEEKDAY_CN[today().weekday()],
        "text": text,
        "verdict": verdict["verdict"],
        "evidence": verdict["evidence"],
        "tomorrow": verdict["tomorrow"],
        "track": track,
    }
    state["log"].append(entry)
    append_journal(entry)
    # 今天有汇报了，任务标成 done
    key = today().isoformat()
    if key in state["tasks"]:
        state["tasks"][key]["status"] = "reported"


# ------------------------------------------------------------- 汇报生成 ----


def build_report(state: dict, track: str) -> str:
    t = state["tracks"][track]
    logs = [e for e in week_logs(state, 1) if e.get("track") == track]
    done = []
    for s in t["steps"]:
        if s.get("done"):
            done.append(f"- {s['id']} {s['title']}")
    if not done:
        # 没完成的就写做到哪了
        step, idx, part = current_part(state, track)
        if step:
            done.append(f"- 进行中：{step['id']} {step['title']}，已完成 {idx} 小节")

    asks = {
        "lab1": "需要导师给：确认传动方案的选型边界；一张现场实物图；演示时间确认。",
        "lab2": "需要导师给：电机完整手册（通信章节）；允许上电测试的时间；师兄 10 分钟答疑。",
    }
    returns = []
    for e in logs:
        returns.append(f"- {e['date']} {e['weekday']}：{e['text']}")
    if not returns:
        returns = ["- 本周记录为空"]

    nxt = {
        "lab1": "- 完成方案 v2 并出 2–3 张工程图\n- 启动报告正文",
        "lab2": "- 打通串口指令\n- 读出反馈字段\n- 开始记录位置数据",
    }
    stuck = {
        "lab1": "- 「太简略」具体指哪三块，哪些必须补、哪些可以省？",
        "lab2": "- 校验位是按字节和还是 CRC，手册里哪一页写的？",
    }
    return (
        f"# 本周汇报 · {t['name']}\n\n"
        f"## 一、本周做了什么（只写有产出的）\n" + ("\n".join(done) or "- 无") + "\n\n"
        f"## 二、卡在哪（具体提问）\n{stuck[track]}\n\n"
        f"## 三、下周做什么\n{nxt[track]}\n\n"
        f"## 四、需要导师给什么\n{asks[track]}\n\n"
        f"---\n本周记录：\n" + "\n".join(returns)
    )


# ------------------------------------------------------------- 上下文包 ----


def build_prompt(state: dict) -> str:
    """给大模型（比如本鲸鱼娘）的上下文包。"""
    m = nearest_milestone(state)
    lines = [f"# 督学上下文 · {today().isoformat()} {WEEKDAY_CN[today().weekday()]}"]

    if m:
        left = days_until(parse_day(m["due"]))
        flag = "⚠️ 紧张" if left <= 10 else ("偏紧" if left <= 20 else "还行")
        lines.append(f"最近里程碑：{m['id']} {m['title']} — 还有 {left} 天（{flag}）")
    else:
        lines.append("最近里程碑：全部完成")

    for tk in ("lab2", "lab1"):
        p = track_progress(state, tk)
        lines.append(f"{p['name']}：{p['parts_done']}/{p['total_parts']} 小节（{p['pct']}%）")
        step, idx, part = current_part(state, tk)
        if step:
            lines.append(f"  当前：{step['id']} {step['title']} · 第 {idx+1}/{len(step['parts'])} 小节 → {part['out']}")

    gap = streak_gap(state)
    lines.append(f"连续未汇报：{gap} 天" + ("（按规则要砍到保底）" if gap >= 2 else ""))
    lines.append(f"今天可用：{WEEK_SLOTS[today().weekday()]} 小时")

    recent = state["log"][-3:]
    if recent:
        lines.append("\n最近汇报：")
        for e in recent:
            lines.append(f"- {e['date']} [{e['verdict']}] {e['text'][:80]}")

    lines.append(
        "\n规则：一次只问一个问题；不夸人；提视觉/ROS2/SLAM/FOC/PCB/深度学习要拦住；"
        "回复 200 字以内，固定输出【今天】【保底】【别碰】三行。"
    )
    return "\n".join(lines)


# ------------------------------------------------------------- CLI 命令 ----


def cmd_today(args):
    state = load()
    task = gen_today(state)
    save(state)
    print(f"\n🐳 {today().isoformat()} {WEEKDAY_CN[today().weekday()]} ｜ {task['headline']}")
    print("-" * 52)
    print(render_task(task))
    print("-" * 52)
    print("汇报： python whale.py checkin \"今天做了什么\"\n")


def cmd_checkin(args):
    text = " ".join(args.text) if args.text else sys.stdin.read()
    if not text.strip():
        print("没内容可汇报喵。")
        return
    state = load()
    v = judge(state, text)
    track = pick_track(state, text) if v["advance"] else None
    if v["advance"]:
        if args.no_advance:
            step, idx, part = current_part(state, track)
            if step:
                v["reply"] += f"\n（这间还没记，跑 python whale.py done 记上）明天：{part['desc']}"
        else:
            info = apply_advance(state, track)
            if info:
                v["reply"] += f"\n已记 {info['step']} 一间，" + (
                    "整节完成。" if info["finished_step"] else "下一小节继续。")
            step, idx, part = current_part(state, track)
            if step:
                v["reply"] += f"\n明天：{part['desc']}"
            else:
                v["reply"] += "\n这条线的关卡已经清完了，等新任务。"
    record(state, text, v, track)
    save(state)

    print(f"\n【判断】{v['verdict']}")
    if v["evidence"]:
        print(f"  依据：{'；'.join(v['evidence'])}")
    print(f"\n{v['reply']}")
    if v["tomorrow"] != "keep":
        print(f"\n（明天：{v['tomorrow']}）")
    print()


def cmd_status(args):
    state = load()
    print(f"\n🐳 督学小鲸娘 · {today().isoformat()} {WEEKDAY_CN[today().weekday()]}\n")
    for m in state["milestones"]:
        if m["done"]:
            print(f"  ✅ {m['id']} {m['title']}")
        else:
            left = days_until(parse_day(m["due"]))
            bar = "█" * max(0, min(20, 20 - left // 3)) + "░" * max(0, min(20, left // 3))
            print(f"  ⏳ {m['id']} {left:>3} 天  {bar}  {m['title']}")

    print()
    for tk in ("lab2", "lab1"):
        p = track_progress(state, tk)
        filled = round(p["pct"] / 100 * 24)
        print(f"  {p['name']}")
        print(f"    [{'█'*filled}{'░'*(24-filled)}] {p['pct']}%  {p['parts_done']}/{p['total_parts']} 小节")
        step, idx, part = current_part(state, tk)
        if step:
            print(f"    当前：{step['id']} · 第 {idx+1}/{len(step['parts'])} 小节")

    gap = streak_gap(state)
    print(f"\n  连续未汇报：{gap} 天" + ("  ⚠️ 该砍到保底了" if gap >= 2 else ""))
    print(f"  历史汇报：{len(state['log'])} 条\n")


def cmd_report(args):
    state = load()
    print(build_report(state, args.track))


def cmd_done(args):
    state = load()
    track = args.track or ("lab2" if not state["milestones"][0]["done"] else "lab1")
    info = apply_advance(state, track)
    if not info:
        print("这条线的关卡已经清完了喵。")
        return
    save(state)
    print(f"✅ 记上了 {info['step']}：{info['part']}")
    print("   整节完成" if info["finished_step"] else "   继续下一小节")


def cmd_prompt(args):
    print(build_prompt(load()))


def cmd_serve(args):
    try:
        from whale_web import serve
    except ImportError:
        sys.path.insert(0, str(ROOT))
        from whale_web import serve
    serve(port=args.port, open_browser=not args.no_browser)


def main():
    ap = argparse.ArgumentParser(prog="whale", description="督学小鲸娘")
    sub = ap.add_subparsers(dest="cmd")

    sub.add_parser("today", help="今天的任务").set_defaults(func=cmd_today)

    p = sub.add_parser("checkin", help="汇报一句")
    p.add_argument("text", nargs="*", help="今天做了什么")
    p.add_argument("--no-advance", action="store_true",
                   help="只记录，不推进关卡")
    p.set_defaults(func=cmd_checkin)

    sub.add_parser("status", help="总览").set_defaults(func=cmd_status)

    p = sub.add_parser("report", help="给导师的一页汇报")
    p.add_argument("track", nargs="?", default="lab2", choices=["lab1", "lab2"])
    p.set_defaults(func=cmd_report)

    p = sub.add_parser("done", help="手动标记当前小节完成")
    p.add_argument("track", nargs="?", choices=["lab1", "lab2"])
    p.set_defaults(func=cmd_done)

    sub.add_parser("prompt", help="给大模型的上下文包").set_defaults(func=cmd_prompt)

    p = sub.add_parser("serve", help="打开仪表盘")
    p.add_argument("--port", type=int, default=8420)
    p.add_argument("--no-browser", action="store_true")
    p.set_defaults(func=cmd_serve)

    args = ap.parse_args()
    if not getattr(args, "cmd", None):
        ap.print_help()
        return
    args.func(args)


if __name__ == "__main__":
    main()
