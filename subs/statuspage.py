"""Node status page (plan-node-status-page §3.4, §10). Pure functions of status.json.

The subscription service serves the page at /s/<token>/status; the console's admin status page embeds the same
body, so both show the same thing. Three views: the last hour (one cell per check), the last 24 hours (one cell
per hour, the default) and the last 90 days (one cell per day). No scripts: views and days are links that reload
the page with ?view=minute|hour|day&day=YYYY-MM-DD. A document without the hour and minute parts (an older
console) shows the days only.
"""
import datetime
import html
import json
import re
import urllib.parse

SCHEMA = 1
STATES = {"ok": "正常", "partial": "部分异常", "outage": "故障", "maintenance": "维护中", "nodata": "无数据"}
KINDS = {"outage": "故障", "partial": "部分异常", "maintenance": "维护"}
# one check: a failed check is not an outage until fail_count checks in a row failed
CHECK_STATES = {"ok": "成功", "partial": "部分失败", "outage": "失败", "maintenance": "维护中", "nodata": "无数据"}
VIEWS = ("minute", "hour", "day")
DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
RECENT_DAYS = 7
NARROW_DAYS = 30

CSS = """
.st .sum{font-size:17px;font-weight:600;margin:0 0 4px}
.st .muted{color:var(--muted)}
.st .node{border-top:1px solid var(--line);padding:12px 0}
.st .head{display:flex;justify-content:space-between;gap:8px;flex-wrap:wrap}
.st .views a,.st .views b{margin-right:14px}
.st .cells{display:grid;grid-template-columns:repeat(var(--n),1fr);gap:1px;margin:8px 0 4px}
.st .cells a{display:block;height:28px;border-radius:1px}
.st .axis{display:flex;justify-content:space-between;font-size:12px;color:var(--muted)}
.st .s-ok{background:#3f9b5a}.st .s-partial{background:#d9a21b}.st .s-outage{background:#c8453b}
.st .s-maintenance{background:#4a7fc1}.st .s-nodata{background:var(--line)}
.st .sel{outline:2px solid var(--fg);outline-offset:1px}
.st .badge{font-size:13px;padding:1px 8px;border-radius:10px;color:#fff;white-space:nowrap}
.st .badge.s-nodata{color:var(--fg)}
.st .legend span{display:inline-block;width:10px;height:10px;border-radius:2px;margin:0 4px 0 10px}
.st .ev{margin:6px 0;padding-left:10px;border-left:3px solid var(--line)}
.st .ev.k-outage{border-color:#c8453b}.st .ev.k-partial{border-color:#d9a21b}.st .ev.k-maintenance{border-color:#4a7fc1}
.st h3{font-size:14px;margin:14px 0 4px}
.st .axis .near{display:none}
@media (max-width:600px){.st .cells.day{--n:30 !important}.st .cells.day a:nth-child(-n+60){display:none}.st .axis .far{display:none}.st .axis .near{display:inline}}
"""


class StatusError(ValueError):
    pass


def _require(cond, msg):
    if not cond:
        raise StatusError(msg)


def _int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def validate(doc):
    """Reject anything body() could not render; the page then says there is no status data."""
    _require(isinstance(doc, dict) and doc.get("schema") == SCHEMA, "unsupported status schema")
    _require(_int(doc.get("generated_at")), "generated_at must be an epoch")
    _require(_int(doc.get("interval")) and doc["interval"] > 0, "invalid interval")
    tz = doc.get("tz")
    _require(isinstance(tz, dict) and isinstance(tz.get("offset_hours"), (int, float))
             and not isinstance(tz["offset_hours"], bool) and -14 <= tz["offset_hours"] <= 14, "invalid tz offset")
    _require(isinstance(tz.get("label"), str), "invalid tz label")
    days = doc.get("days")
    _require(isinstance(days, list) and days and all(isinstance(d, str) and DAY_RE.match(d) for d in days), "invalid days")
    for d in days:
        try:
            datetime.date.fromisoformat(d)
        except ValueError:
            raise StatusError("invalid day") from None
    nodes, events = doc.get("nodes", []), doc.get("events", [])
    _require(isinstance(nodes, list) and isinstance(events, list), "nodes and events must be lists")
    for node in nodes:
        _require(isinstance(node, dict) and isinstance(node.get("label"), str) and node["label"], "node without label")
        _require(node.get("state") in STATES, "invalid node state")
        availability = node.get("availability")
        _require(availability is None or (isinstance(availability, (int, float)) and not isinstance(availability, bool)),
                 "invalid availability")
        cells = node.get("days")
        _require(isinstance(cells, list) and len(cells) == len(days), "node days do not match")
        for cell in cells:
            _require(isinstance(cell, list) and len(cell) == 4 and cell[0] in STATES
                     and all(_int(v) for v in cell[1:]), "invalid day cell")
    _require("fail_count" not in doc or (_int(doc["fail_count"]) and doc["fail_count"] > 0), "invalid fail_count")
    if "hours" in doc:
        part = doc["hours"]
        _require(isinstance(part, dict) and _int(part.get("start")) and _int(part.get("count")) and part["count"] > 0,
                 "invalid hours")
        for node in nodes:
            cells = node.get("hours")
            _require(isinstance(cells, list) and len(cells) == part["count"], "node hours do not match")
            for cell in cells:
                _require(isinstance(cell, list) and len(cell) == 6 and cell[0] in STATES
                         and all(_int(v) for v in cell[1:]), "invalid hour cell")
    if "minutes" in doc:
        part = doc["minutes"]
        _require(isinstance(part, dict) and all(_int(part.get(k)) for k in ("start", "step", "count"))
                 and part["step"] > 0 and part["count"] > 0, "invalid minutes")
        for node in nodes:
            cells = node.get("minutes")
            _require(isinstance(cells, list) and len(cells) == part["count"], "node minutes do not match")
            for cell in cells:
                _require(isinstance(cell, list) and len(cell) == 3 and cell[0] in STATES and _int(cell[2])
                         and isinstance(cell[1], list) and all(isinstance(x, str) for x in cell[1]),
                         "invalid minute cell")
    for event in events:
        _require(isinstance(event, dict) and event.get("kind") in KINDS and isinstance(event.get("node"), str),
                 "invalid event")
        _require(_int(event.get("started_at")), "invalid event start")
        _require(event.get("ended_at") is None or _int(event["ended_at"]), "invalid event end")
        _require(isinstance(event.get("transports"), list) and all(isinstance(x, str) for x in event["transports"]),
                 "invalid event transports")
        _require(_int(event.get("all_transports", 1)) and isinstance(event.get("note"), str), "invalid event")
    return doc


def load(path):
    with open(path) as fh:
        return validate(json.load(fh))


def _zone(doc):
    return datetime.timezone(datetime.timedelta(hours=doc["tz"]["offset_hours"]))


def _local(doc, epoch):
    return datetime.datetime.fromtimestamp(epoch, _zone(doc))


def _day_range(doc, day):
    start = datetime.datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=_zone(doc))
    return int(start.timestamp()), int((start + datetime.timedelta(days=1)).timestamp())


def _minutes(total):
    hours, minutes = divmod(max(0, int(total)), 60)
    if hours and minutes:
        return f"{hours} 小时 {minutes} 分钟"
    return f"{hours} 小时" if hours else f"{minutes} 分钟"


def _cell_title(day, cell):
    state, outage, partial, maintenance = cell
    parts = [f"{day[5:]} {STATES[state]}"]
    for name, value in (("故障", outage), ("部分异常", partial), ("维护", maintenance)):
        if value:
            parts.append(f"{name} {_minutes(value)}")
    return "，".join(parts)


def _what(event):
    if event["kind"] == "maintenance":
        return "维护中，暂时不在订阅中"
    names = "、".join(event["transports"])
    if event["kind"] == "outage":
        return "所有链接都无法连接" if event.get("all_transports", 1) > 1 else "无法连接"
    return f"{names} 链接无法连接，其他链接正常"


def _event_html(doc, event, day):
    start = _local(doc, event["started_at"])
    fmt = "%H:%M" if start.strftime("%Y-%m-%d") == day else "%m-%d %H:%M"
    when = start.strftime(fmt)
    if event["ended_at"] is None:
        span = f"{when} 起，仍在持续（已 {_minutes((doc['generated_at'] - event['started_at']) // 60)}）"
    else:
        end = _local(doc, event["ended_at"])
        end_fmt = "%H:%M" if end.strftime("%Y-%m-%d") == start.strftime("%Y-%m-%d") else "%m-%d %H:%M"
        span = f"{when} – {end.strftime(end_fmt)}（{_minutes((event['ended_at'] - event['started_at']) // 60)}）"
    note = f"<br>说明：{html.escape(event['note'])}" if event["note"] else ""
    return (f"<div class='ev k-{event['kind']}'><b>{html.escape(event['node'])}</b> · {KINDS[event['kind']]} · "
            f"{span}<br>{html.escape(_what(event))}{note}</div>")


def _events_on(doc, day):
    lo, hi = _day_range(doc, day)
    return [e for e in doc.get("events", [])
            if e["started_at"] < hi and (e["ended_at"] is None or e["ended_at"] > lo)]


def _hour_title(doc, start, cell):
    state, succeeded, checked, outage, partial, maintenance = cell
    lo, hi = _local(doc, start), _local(doc, start + 3600)
    parts = [f"{lo:%m-%d %H:%M}–{hi:%H:%M} {STATES[state]}"]
    for name, value in (("故障", outage), ("部分异常", partial), ("维护", maintenance)):
        if value:
            parts.append(f"{name} {_minutes(value)}")
    if checked:
        parts.append(f"检测 {checked} 次全部成功" if succeeded == checked else f"检测 {checked} 次，成功 {succeeded} 次")
    return "，".join(parts)


def _minute_title(doc, start, cell):
    state, failed, _ = cell
    text = f"{_local(doc, start):%m-%d %H:%M} {CHECK_STATES[state]}"
    return text + (f"（{'、'.join(failed)} 失败）" if failed and state == "partial" else "")


def _views(doc):
    return [v for v in ("minute", "hour") if v + "s" in doc] + ["day"]


def _default_href(view, day):
    return "?" + urllib.parse.urlencode({"view": view, **({"day": day} if day else {})})


def _grid(doc, node, view, day, href):
    """(cells html, summary, axis html) of one node in one view."""
    if view == "minute":
        part = doc["minutes"]
        cells = []
        for k, cell in enumerate(node["minutes"]):
            at = part["start"] + k * part["step"]
            title = html.escape(_minute_title(doc, at, cell))
            link = html.escape(href(view, _local(doc, at).strftime("%Y-%m-%d")) + "#events")
            cells.append(f"<a class='s-{cell[0]}' href='{link}' title='{title}' aria-label='{title}'></a>")
        checked = [c for c in node["minutes"] if c[2]]
        span = part["count"] * part["step"] // 60
        summary = (f"近 {span} 分钟检测 {len(checked)} 次，成功 {sum(1 for c in checked if c[0] == 'ok')} 次"
                   if checked else f"近 {span} 分钟没有检测数据")
        axis = f"<span>{span} 分钟前</span><span>现在</span>"
        return "".join(cells), summary, axis
    if view == "hour":
        part = doc["hours"]
        cells = []
        for k, cell in enumerate(node["hours"]):
            at = part["start"] + k * 3600
            title = html.escape(_hour_title(doc, at, cell))
            link = html.escape(href(view, _local(doc, at).strftime("%Y-%m-%d")) + "#events")
            cells.append(f"<a class='s-{cell[0]}' href='{link}' title='{title}' aria-label='{title}'></a>")
        known = sum(c[2] for c in node["hours"]) * doc["interval"] / 60
        outage = min(sum(c[3] for c in node["hours"]), known)
        summary = f"近 {part['count']} 小时可用率 " + (f"{100 * (known - outage) / known:.2f}%" if known else "—")
        axis = f"<span>{part['count']} 小时前</span><span>现在</span>"
        return "".join(cells), summary, axis
    days = doc["days"]
    cells = "".join(
        f"<a class='s-{cell[0]}{' sel' if d == day else ''}' href='{html.escape(href(view, d) + '#events')}' "
        f"title='{html.escape(_cell_title(d, cell))}' aria-label='{html.escape(_cell_title(d, cell))}'></a>"
        for d, cell in zip(days, node["days"]))
    availability = "—" if node["availability"] is None else f"{node['availability']:.2f}%"
    axis = (f"<span class='far'>{len(days)} 天前</span><span class='near'>{min(len(days), NARROW_DAYS)} 天前</span>"
            f"<span>今天</span>")
    return cells, f"近 {len(days)} 天可用率 {availability}", axis


def body(doc, day=None, href=None, now=None, view=None):
    """The status fragment inside <div class="st">.

    view is minute, hour or day (default hour when the document has hours); day selects one day's events;
    href(view, day) builds links. With `now`, a document older than three intervals is shown as expired and every
    current state as unknown.
    """
    href = href or _default_href
    views = _views(doc)
    view = view if view in views else ("hour" if "hour" in views else "day")
    days = doc["days"]
    stale = now is not None and now - doc["generated_at"] > 3 * doc["interval"]
    nodes = [dict(n, state="nodata") if stale and n["state"] != "maintenance" else n for n in doc.get("nodes", [])]
    out = ["<div class='st'>"]
    problems = [n for n in nodes if n["state"] in ("outage", "partial")]
    if stale:
        out.append("<p class='sum'>状态数据已过期，当前状态未知</p>")
    elif not nodes:
        out.append("<p class='sum'>还没有纳入检测的节点</p>")
    elif problems:
        out.append("<p class='sum'>" + "；".join(f"{html.escape(n['label'])}：{STATES[n['state']]}" for n in problems) + "</p>")
    else:
        out.append("<p class='sum'>所有节点工作正常</p>" if all(n["state"] == "ok" for n in nodes)
                   else "<p class='sum'>没有正在发生的故障</p>")
    updated = _local(doc, doc["generated_at"]).strftime("%Y-%m-%d %H:%M")
    out.append(f"<p class='muted'>更新于 {updated}（{html.escape(doc['tz']['label'])}），每 {doc.get('interval', 60)} 秒检测一次。</p>")
    if len(views) > 1:
        span = doc["minutes"]["count"] * doc["minutes"]["step"] // 60 if "minutes" in doc else 60
        names = {"minute": f"最近 {span // 60} 小时" if span % 60 == 0 else f"最近 {span} 分钟",
                 "hour": f"最近 {doc.get('hours', {}).get('count', 24)} 小时",
                 "day": f"最近 {len(days)} 天"}
        out.append("<p class='views'>" + "".join(
            f"<b>{names[v]}</b>" if v == view else f"<a href='{html.escape(href(v, day if day in days else ''))}'>{names[v]}</a>"
            for v in views) + "</p>")
    for node in nodes:
        cells, summary, axis = _grid(doc, node, view, day, href)
        count = len(node["minutes"] if view == "minute" else node["hours"] if view == "hour" else days)
        out.append(f"<div class='node'><div class='head'><b>{html.escape(node['label'])}</b>"
                   f"<span><span class='badge s-{node['state']}'>{STATES[node['state']]}</span> "
                   f"<span class='muted'>{summary}</span></span></div>"
                   f"<div class='cells {view}' style='--n:{count}'>{cells}</div><div class='axis'>{axis}</div></div>")
    names = CHECK_STATES if view == "minute" else STATES
    out.append("<p class='legend muted'>" + "".join(f"<span class='s-{k}'></span>{v}" for k, v in names.items()) + "</p>")
    if view == "minute":
        out.append(f"<p class='muted'>每格是一次检测。单次失败不算故障，连续 {doc.get('fail_count', 3)} 次失败才记为故障，"
                   "并出现在下方事件记录中。</p>")
    elif view == "hour":
        out.append("<p class='muted'>每格是一小时；这一小时里有过故障即按故障显示。鼠标移到色块上可看检测次数与成功次数。</p>")
    out.append("<h2 id='events'>事件记录</h2>")
    if day in days:
        shown_days = [day]
        out.append(f"<p class='muted'>{day} 的事件。<a href='{html.escape(href(view, '') + '#events')}'>查看最近 {RECENT_DAYS} 天</a></p>")
    else:
        shown_days = list(reversed(days[-RECENT_DAYS:]))
        out.append(f"<p class='muted'>最近 {RECENT_DAYS} 天；点上方色块查看那一天。</p>")
    for d in shown_days:
        events = _events_on(doc, d)
        out.append(f"<h3>{d}</h3>")
        out.append("".join(_event_html(doc, e, d) for e in events) if events else "<p class='muted'>没有事件</p>")
    out.append("<p class='muted'>检测从服务器侧发起，经每个节点访问一个固定地址；不能反映你所在网络或地区的情况。"
               "某个链接只在你那里连不上时，请联系管理员。</p></div>")
    return "\n".join(out)


def page(doc, day=None, page_css="", back_href="", now=None, view=None):
    """A full page for the subscription service. doc None means no status data has been published yet."""
    content = body(doc, day, now=now, view=view) if doc else "<p>暂无状态数据。</p>"
    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><meta name="referrer" content="no-referrer">
<meta name="robots" content="noindex"><meta http-equiv="refresh" content="60"><title>节点状态</title>
<style>{page_css}{CSS}</style></head><body><main>
<h1>节点状态</h1>{f'<p><a href="{html.escape(back_href)}">返回订阅页</a></p>' if back_href else ""}
<section>{content}</section>
</main></body></html>
"""
