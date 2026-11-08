# -*- coding: utf-8 -*-
"""
scripts/daily_remind.py —— 每天 20:00 由 GitHub Actions 跑，推一条微信。

环境变量（四选一，按优先级）：
    WXPUSHER_SPT                    推荐：扫码拿 SPT，不用注册、不用实名
    WXPUSHER_TOKEN + WXPUSHER_UID   备选：标准推送（要先建应用）
    WECOM_WEBHOOK                   备选：企业微信群机器人
    PUSHPLUS_TOKEN                  备选：PushPlus（要实名）
    GIST_ID          可选，云存档 ID（拿不到就用内置默认计划）
    GIST_TOKEN       可选，有 gist 读权限的 token

用法：
    python scripts/daily_remind.py --dry    只打印不推送
    python scripts/daily_remind.py          真推
"""
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
import whale  # noqa: E402

BJ = timezone(timedelta(hours=8))
API = "https://api.github.com"
GIST_FILE = "whale-state.json"


def _get_json(url, token=None, timeout=20):
    req = urllib.request.Request(url)
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("User-Agent", "whale-remind")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def load_state():
    """优先读云端存档，读不到就用内置默认计划。"""
    gid = os.environ.get("GIST_ID", "").strip()
    tk = os.environ.get("GIST_TOKEN", "").strip()
    if gid and tk:
        try:
            j = _get_json("%s/gists/%s" % (API, gid), tk)
            f = (j.get("files") or {}).get(GIST_FILE)
            if f and f.get("content"):
                st = json.loads(f["content"])
                if st.get("tracks"):
                    return st, "cloud"
        except Exception as e:
            print("  ! 读云端存档失败：%s" % e)
    return json.loads(json.dumps(whale.DEFAULT_STATE)), "default"


def build_message(state, src):
    now = datetime.now(BJ)
    task = whale.gen_today(state)
    gap = whale.streak_gap(state)
    m = whale.nearest_milestone(state)
    p2 = whale.track_progress(state, "lab2")
    p1 = whale.track_progress(state, "lab1")

    head = "%s 还有 %d 天" % (m["id"], (datetime.strptime(m["due"], "%Y-%m-%d").date() - now.date()).days) if m else "里程碑全清"

    # 一天三次，口吻跟着时段走
    h = now.hour
    if h < 12:
        slot, slot_line = "morning", "早上。今天只盯这一件，做完就算赢，不用想第二步。"
    elif h < 18:
        slot, slot_line = "afternoon", "下午了。上午那件动了吗？动了就往下推，没动就从【保底】开始，10 分钟也算数。"
    else:
        slot, slot_line = "evening", "晚上收尾。今天有没有一个能拿给师兄看的东西（截图 / 一页笔记 / 一条曲线）？"

    # 主动开口：先看今天记没记，再看空窗多久
    if whale.logged_today(state):
        open_line = "今天已经记上了，明天接着来。"
    elif gap >= 2:
        open_line = "已经 %d 天没记了。别想着把落下的补回来，今天只做【保底】那件事，10 分钟也算。" % gap
    elif gap == 1:
        open_line = "昨天空了一天。别补进度，今天只做【保底】那一件。"
    else:
        open_line = "还没记过汇报。今天做完那一件，回来说一句就行，不用写长。"

    tail = [
        "**进度**：实验室2 %d/%d 小节 · 实验室1 %d/%d 小节" % (
            p2["parts_done"], p2["total_parts"], p1["parts_done"], p1["total_parts"]),
        "",
        "📱 **两台设备都刷新一下**，顶部圆点变绿才算同步。",
        "",
        "💬 想细问、想说今天干了啥：[点这里打开](%s)" % APP_URL,
        "",
        "_%s · %s_" % (("云端进度" if src == "cloud" else "默认计划"), now.strftime("%m-%d %H:%M")),
    ]

    md = "\n".join([
        "### 🐳 %s · %s" % (now.strftime("%Y-%m-%d"), whale.WEEKDAY_CN[(now.weekday())]),
        "",
        "**%s**" % head,
        "",
        "【今天】%s" % task["main"],
        "",
        "【保底】%s" % task["low"],
        "",
        "【别碰】%s" % task["avoid"],
        "",
        "---",
        "",
        slot_line,
        "",
        open_line,
        "",
    ] + tail)
    return md


APP_URL = os.environ.get("APP_URL", "https://siyuezhu66.github.io/whale/app.html").strip()


def send_wecom(url, title, content):
    body = json.dumps({
        "msgtype": "markdown",
        "markdown": {"content": "### " + title + "\n" + content},
    }, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("User-Agent", "whale-remind")
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def send_spt(spt, title, content):
    body = json.dumps({
        "content": content,
        "summary": title,
        "contentType": 3,
        "spt": spt,
    }, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        "https://wxpusher.zjiecode.com/api/send/message/simple-push",
        data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("User-Agent", "whale-remind")
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def send_wxpusher(app_token, uid, title, content):
    body = json.dumps({
        "appToken": app_token,
        "content": content,
        "summary": title,
        "contentType": 3,
        "uids": [uid],
    }, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        "https://wxpusher.zjiecode.com/api/send/message", data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("User-Agent", "whale-remind")
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def send_pushplus(token, title, content):
    body = json.dumps({
        "token": token,
        "title": title,
        "content": content,
        "template": "markdown",
        "channel": "wechat",
    }, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request("https://www.pushplus.plus/send", data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("User-Agent", "whale-remind")
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def main():
    dry = "--dry" in sys.argv
    state, src = load_state()
    md = build_message(state, src)
    print(md)
    print("-" * 50)

    if dry:
        print("  (dry run, 没有推送)")
        return 0

    # 凌晨别吵：北京时间 23:00–08:00 一律不推（GitHub 定时偶尔会拖到半夜）
    _hnow = datetime.now(BJ).hour
    if not (8 <= _hnow < 23):
        print("  ! 现在 %02d 点（北京时间），不在推送时段 08:00–23:00，跳过这次" % _hnow)
        return 0

    spt = os.environ.get("WXPUSHER_SPT", "").strip()
    wecom = os.environ.get("WECOM_WEBHOOK", "").strip()
    pp = os.environ.get("PUSHPLUS_TOKEN", "").strip()
    wx_tok = os.environ.get("WXPUSHER_TOKEN", "").strip()
    wx_uid = os.environ.get("WXPUSHER_UID", "").strip()
    if not spt and not wecom and not pp and not (wx_tok and wx_uid):
        print("  ! 没有推送通道：WXPUSHER_SPT / WXPUSHER_TOKEN+UID / WECOM_WEBHOOK / PUSHPLUS_TOKEN 四选一")
        return 1
    _h = datetime.now(BJ).hour
    title = "🐳 早上 · 今天三行" if _h < 12 else ("🐳 下午 · 检查一下" if _h < 18 else "🐳 晚上 · 收尾看看")
    try:
        if spt:
            r = send_spt(spt, title, md)
            print("  WxPusher(SPT) 返回：%s" % json.dumps(r, ensure_ascii=False)[:200])
            if r.get("code") != 1000 and not r.get("success"):
                print("  ! 推送失败，看 msg（SPT 抄错，或没扫码关注）")
                return 1
        elif wx_tok and wx_uid:
            r = send_wxpusher(wx_tok, wx_uid, title, md)
            print("  WxPusher 返回：%s" % json.dumps(r, ensure_ascii=False)[:200])
            if r.get("code") != 1000:
                print("  ! 推送失败，看 msg（AppToken / UID 抄错，或没关注公众号）")
                return 1
        elif wecom:
            r = send_wecom(wecom, title, md)
            print("  企业微信返回：%s" % json.dumps(r, ensure_ascii=False)[:200])
            if r.get("errcode") != 0:
                print("  ! 推送失败，看 errmsg（webhook 失效 / key 抄错）")
                return 1
        else:
            r = send_pushplus(pp, title, md)
            print("  pushplus 返回：%s" % json.dumps(r, ensure_ascii=False)[:200])
            if r.get("code") != 200:
                print("  ! 推送失败（code 不是 200），多半是没实名或被限流")
                return 1
        print("  ✓ 已推到手机")
        return 0
    except urllib.error.HTTPError as e:
        print("  ! HTTP %s：%s" % (e.code, e.read().decode("utf-8", "replace")[:200]))
        return 1
    except Exception as e:
        print("  ! 推送出错：%s: %s" % (type(e).__name__, e))
        return 1


if __name__ == "__main__":
    sys.exit(main())
