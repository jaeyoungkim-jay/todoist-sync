"""Todoist -> Confluence 페이지 동기화 (데일리 보드용)
GitHub Actions에서 실행. 필요한 Secrets:
  TODOIST_TOKEN, ATLASSIAN_EMAIL, ATLASSIAN_TOKEN, CONFLUENCE_PAGE_ID
"""
import base64, datetime, hashlib, html, json, os, sys, urllib.request, urllib.error

TODOIST = os.environ["TODOIST_TOKEN"].strip()
EMAIL = os.environ["ATLASSIAN_EMAIL"].strip()
ATOKEN = os.environ["ATLASSIAN_TOKEN"].strip()
PAGE = os.environ["CONFLUENCE_PAGE_ID"].strip()
SITE = os.environ.get("CONFLUENCE_SITE", "https://fursys.atlassian.net").rstrip("/")

def req(url, headers, data=None, method=None):
    r = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(r, timeout=30) as f:
        body = f.read().decode("utf-8")
        return json.loads(body) if body else {}

def todoist(path):
    """v1 API(커서 페이지) 우선, 실패하면 구 REST v2로."""
    h = {"Authorization": f"Bearer {TODOIST}"}
    out, cur = [], None
    try:
        while True:
            q = f"?limit=200" + (f"&cursor={cur}" if cur else "")
            r = req(f"https://api.todoist.com/api/v1/{path}{q}", h)
            out += r.get("results", []) if isinstance(r, dict) else r
            cur = r.get("next_cursor") if isinstance(r, dict) else None
            if not cur: return out
    except urllib.error.HTTPError as e:
        if e.code in (401, 403): raise
        return req(f"https://api.todoist.com/rest/v2/{path}", h)

import time
auth = "Basic " + base64.b64encode(f"{EMAIL}:{ATOKEN}".encode()).decode()

LAST = {"sig": ""}
def sync_once():
    projects = {str(p["id"]): p.get("name", "") for p in todoist("projects")}
    tasks = []
    for t in todoist("tasks"):
        if t.get("checked") or t.get("is_completed") or t.get("is_deleted"): continue
        due = t.get("due") or {}
        tasks.append({
            "id": str(t["id"]),
            "text": t.get("content", ""),
            "project": projects.get(str(t.get("project_id")), ""),
            "parent": str(t["parent_id"]) if t.get("parent_id") else "",
            "date": (due.get("date") or "")[:10],
            "time": (due.get("datetime") or "")[11:16] if due.get("datetime") and "T" in due.get("datetime") else "",
            "recurring": bool(due.get("is_recurring")),
            "priority": t.get("priority", 1),
            "order": t.get("child_order", t.get("order", 0)),
        })
    tasks.sort(key=lambda x: (x["project"], x["order"]))
    data = {"v": 1, "tasks": tasks}
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    sig = hashlib.sha1(payload.encode()).hexdigest()[:12]

    if sig == LAST["sig"]:
        return False
    H = {"Authorization": auth, "Accept": "application/json", "Content-Type": "application/json"}
    page = req(f"{SITE}/wiki/api/v2/pages/{PAGE}?body-format=storage", H)
    old = ((page.get("body") or {}).get("storage") or {}).get("value", "")
    if f"sig:{sig}" in old:
        LAST["sig"] = sig
        return False

    now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9))).strftime("%Y-%m-%d %H:%M")
    body = (f"<p>데일리 보드용 자동 동기화 페이지입니다. 직접 고치지 마세요. 마지막 동기화 {now} (KST) · 할 일 {len(tasks)}건 · sig:{sig}</p>"
            f'<ac:structured-macro ac:name="code"><ac:parameter ac:name="language">json</ac:parameter>'
            f"<ac:plain-text-body><![CDATA[TODOIST_JSON {payload} TODOIST_END]]></ac:plain-text-body></ac:structured-macro>")
    upd = {"id": PAGE, "status": "current", "title": page["title"],
           "body": {"representation": "storage", "value": body},
           "version": {"number": page["version"]["number"] + 1, "message": "todoist sync"}}
    req(f"{SITE}/wiki/api/v2/pages/{PAGE}", H, json.dumps(upd).encode(), "PUT")
    LAST["sig"] = sig
    print(f"업데이트 완료: {len(tasks)}건", flush=True)
    return True


# LOOP_MINUTES가 있으면 그 시간 동안 1분마다 확인(바뀐 게 있을 때만 페이지 갱신), 밤 11시(KST)엔 종료
LOOP = int(os.environ.get("LOOP_MINUTES", "0") or 0)
start = time.time()
while True:
    try:
        sync_once()
    except Exception as e:
        print("오류:", type(e).__name__, getattr(e, "code", ""), flush=True)
        if not LOOP: raise
    kst = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9)))
    if not LOOP or time.time() - start > LOOP * 60 or kst.hour >= 23: break
    time.sleep(60)
