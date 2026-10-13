from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect, UploadFile, File, Header
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from typing import Optional
import sqlite3, json, secrets, string, os, random, time
from pathlib import Path

ROOT = Path(__file__).parent
DB_PATH = ROOT / "dnd.db"
UPLOAD_DIR = ROOT / "static" / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="D&D Web Platform MVP", version="1.0.0")
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")

def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn

def init_db():
    with db() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            gm_key TEXT NOT NULL,
            map_image TEXT DEFAULT '',
            created_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS participants (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
            name TEXT NOT NULL,
            role TEXT NOT NULL CHECK(role IN ('GM','PLAYER')),
            access_key TEXT NOT NULL,
            joined_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS characters (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
            owner_id INTEGER REFERENCES participants(id) ON DELETE SET NULL,
            name TEXT NOT NULL,
            kind TEXT NOT NULL DEFAULT 'PLAYER',
            level INTEGER NOT NULL DEFAULT 1,
            str_stat INTEGER NOT NULL DEFAULT 10,
            dex INTEGER NOT NULL DEFAULT 10,
            con INTEGER NOT NULL DEFAULT 10,
            int_stat INTEGER NOT NULL DEFAULT 10,
            wis INTEGER NOT NULL DEFAULT 10,
            cha INTEGER NOT NULL DEFAULT 10,
            hp INTEGER NOT NULL DEFAULT 10,
            max_hp INTEGER NOT NULL DEFAULT 10,
            ac INTEGER NOT NULL DEFAULT 10,
            conditions TEXT NOT NULL DEFAULT '',
            x INTEGER NOT NULL DEFAULT 2,
            y INTEGER NOT NULL DEFAULT 2,
            created_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
            actor TEXT NOT NULL,
            event_type TEXT NOT NULL,
            message TEXT NOT NULL,
            created_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS combat (
            session_id INTEGER PRIMARY KEY REFERENCES sessions(id) ON DELETE CASCADE,
            initiative_json TEXT NOT NULL DEFAULT '[]',
            active_index INTEGER NOT NULL DEFAULT 0,
            round_no INTEGER NOT NULL DEFAULT 1
        );
        """)
init_db()

class CreateSession(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    gm_name: str = Field(min_length=1, max_length=40)

class JoinSession(BaseModel):
    code: str
    name: str = Field(min_length=1, max_length=40)

class CharacterCreate(BaseModel):
    name: str = Field(min_length=1, max_length=50)
    kind: str = "PLAYER"
    level: int = 1
    str_stat: int = 10
    dex: int = 10
    con: int = 10
    int_stat: int = 10
    wis: int = 10
    cha: int = 10
    hp: int = 10
    max_hp: int = 10
    ac: int = 10

class CharacterUpdate(BaseModel):
    name: Optional[str] = None
    level: Optional[int] = None
    str_stat: Optional[int] = None
    dex: Optional[int] = None
    con: Optional[int] = None
    int_stat: Optional[int] = None
    wis: Optional[int] = None
    cha: Optional[int] = None
    hp: Optional[int] = None
    max_hp: Optional[int] = None
    ac: Optional[int] = None
    conditions: Optional[str] = None
    x: Optional[int] = None
    y: Optional[int] = None

class RollRequest(BaseModel):
    actor: str
    formula: str = "1d20"

class MoveRequest(BaseModel):
    participant_id: int
    x: int
    y: int

class CombatStart(BaseModel):
    entries: list[dict]

class HpChange(BaseModel):
    amount: int
    actor: str = "GM"

class AttackRequest(BaseModel):
    attacker_id: int
    target_id: int
    bonus: int = 0
    damage: str = "1d6"

connections: dict[int, set[WebSocket]] = {}

def now():
    return time.time()

def make_code():
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "-".join("".join(secrets.choice(alphabet) for _ in range(3)) for _ in range(2))

def session_by_code(code):
    with db() as c:
        row = c.execute("SELECT * FROM sessions WHERE code=?", (code.upper().strip(),)).fetchone()
        return dict(row) if row else None

def participant_by_key(session_id, key):
    with db() as c:
        row = c.execute("SELECT * FROM participants WHERE session_id=? AND access_key=?", (session_id, key)).fetchone()
        return dict(row) if row else None

def log_event(session_id, actor, event_type, message):
    with db() as c:
        c.execute("INSERT INTO events(session_id,actor,event_type,message,created_at) VALUES(?,?,?,?,?)",
                  (session_id, actor, event_type, message, now()))

async def broadcast(session_id, event_type="state"):
    sockets = list(connections.get(session_id, set()))
    payload = json.dumps({"type": event_type, "state": get_state_by_id(session_id)}, ensure_ascii=False)
    dead = []
    for ws in sockets:
        try:
            await ws.send_text(payload)
        except Exception:
            dead.append(ws)
    for ws in dead:
        connections.get(session_id, set()).discard(ws)

def get_state_by_id(session_id):
    with db() as c:
        s = c.execute("SELECT id,code,name,map_image,created_at FROM sessions WHERE id=?", (session_id,)).fetchone()
        if not s:
            return None
        participants = [dict(r) for r in c.execute(
            "SELECT id,name,role,joined_at FROM participants WHERE session_id=? ORDER BY joined_at", (session_id,)).fetchall()]
        chars = [dict(r) for r in c.execute(
            "SELECT id,owner_id,name,kind,level,str_stat,dex,con,int_stat,wis,cha,hp,max_hp,ac,conditions,x,y FROM characters WHERE session_id=? ORDER BY id", (session_id,)).fetchall()]
        events = [dict(r) for r in c.execute(
            "SELECT id,actor,event_type,message,created_at FROM events WHERE session_id=? ORDER BY id DESC LIMIT 60", (session_id,)).fetchall()]
        combat_row = c.execute("SELECT initiative_json,active_index,round_no FROM combat WHERE session_id=?", (session_id,)).fetchone()
        combat = dict(combat_row) if combat_row else {"initiative_json":"[]","active_index":0,"round_no":1}
        combat["initiative"] = json.loads(combat.pop("initiative_json"))
        return {"session":dict(s), "participants":participants, "characters":chars, "events":events, "combat":combat}

def verify(session_id, access_key, gm=False):
    p = participant_by_key(session_id, access_key or "")
    if not p:
        raise HTTPException(401, "Невірний ключ доступу. Приєднайтеся до сесії знову.")
    if gm and p["role"] != "GM":
        raise HTTPException(403, "Ця дія доступна лише GM.")
    return p

@app.get("/", response_class=HTMLResponse)
def home():
    return (ROOT / "static" / "index.html").read_text(encoding="utf-8")

@app.get("/api/health")
def health():
    return {"ok": True, "app": "D&D Web Platform MVP"}

@app.post("/api/sessions")
async def create_session(data: CreateSession):
    code = make_code()
    gm_key = secrets.token_urlsafe(24)
    with db() as c:
        while c.execute("SELECT 1 FROM sessions WHERE code=?", (code,)).fetchone():
            code = make_code()
        cur = c.execute("INSERT INTO sessions(code,name,gm_key,created_at) VALUES(?,?,?,?)",
                        (code, data.name.strip(), gm_key, now()))
        sid = cur.lastrowid
        participant_cur = c.execute("INSERT INTO participants(session_id,name,role,access_key,joined_at) VALUES(?,?,?,?,?)",
                  (sid, data.gm_name.strip(), "GM", gm_key, now()))
        gm_participant_id = participant_cur.lastrowid
        c.execute("INSERT INTO combat(session_id,initiative_json,active_index,round_no) VALUES(?,?,0,1)", (sid, "[]"))
    log_event(sid, data.gm_name.strip(), "session_created", f'Створено сесію «{data.name.strip()}»')
    await broadcast(sid, "session_created")
    return {"session_id": sid, "participant_id": gm_participant_id, "code": code, "access_key": gm_key, "role": "GM", "name": data.gm_name.strip()}

@app.post("/api/join")
async def join_session(data: JoinSession):
    s = session_by_code(data.code)
    if not s:
        raise HTTPException(404, "Сесію з таким кодом не знайдено.")
    key = secrets.token_urlsafe(24)
    with db() as c:
        cur = c.execute("INSERT INTO participants(session_id,name,role,access_key,joined_at) VALUES(?,?,?,?,?)",
                        (s["id"], data.name.strip(), "PLAYER", key, now()))
        pid = cur.lastrowid
    log_event(s["id"], data.name.strip(), "joined", f'{data.name.strip()} приєднався до сесії')
    await broadcast(s["id"], "participant_joined")
    return {"session_id": s["id"], "participant_id": pid, "code": s["code"], "access_key": key, "role": "PLAYER", "name": data.name.strip()}

@app.get("/api/sessions/{session_id}/state")
def get_state(session_id: int, x_access_key: str = Header(default="")):
    verify(session_id, x_access_key)
    state = get_state_by_id(session_id)
    if not state:
        raise HTTPException(404, "Сесію не знайдено.")
    return state

@app.post("/api/sessions/{session_id}/characters")
async def create_character(session_id: int, data: CharacterCreate, x_access_key: str = Header(default="")):
    p = verify(session_id, x_access_key)
    kind = "NPC" if data.kind.upper() == "NPC" else "PLAYER"
    if kind == "NPC" and p["role"] != "GM":
        raise HTTPException(403, "NPC може створювати лише GM.")
    if kind == "PLAYER":
        with db() as c:
            existing = c.execute("SELECT id FROM characters WHERE session_id=? AND owner_id=? AND kind='PLAYER'",
                                  (session_id, p["id"])).fetchone()
        if existing:
            raise HTTPException(400, "У вас уже є персонаж у цій сесії.")
    with db() as c:
        cur = c.execute("""INSERT INTO characters(session_id,owner_id,name,kind,level,str_stat,dex,con,int_stat,wis,cha,hp,max_hp,ac,x,y,created_at)
          VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
          (session_id, p["id"] if kind == "PLAYER" else None, data.name.strip(), kind, max(1,data.level),
           data.str_stat,data.dex,data.con,data.int_stat,data.wis,data.cha,max(0,data.hp),max(1,data.max_hp),data.ac,
           random.randint(0,5),random.randint(0,5),now()))
        cid = cur.lastrowid
    log_event(session_id, p["name"], "character_created", f'Створено {"NPC" if kind=="NPC" else "персонажа"} «{data.name.strip()}»')
    await broadcast(session_id, "character_created")
    return {"id":cid,"message":"Персонажа створено"}

@app.patch("/api/sessions/{session_id}/characters/{character_id}")
async def update_character(session_id: int, character_id: int, data: CharacterUpdate, x_access_key: str = Header(default="")):
    p = verify(session_id, x_access_key)
    changes = data.model_dump(exclude_unset=True)
    if not changes:
        return {"ok":True}
    with db() as c:
        char = c.execute("SELECT * FROM characters WHERE id=? AND session_id=?", (character_id,session_id)).fetchone()
        if not char:
            raise HTTPException(404, "Персонажа не знайдено.")
        if p["role"] != "GM" and char["owner_id"] != p["id"]:
            raise HTTPException(403, "Гравець може змінювати лише власного персонажа.")
        if p["role"] != "GM" and any(k in changes for k in ("x","y")):
            # Players may move their own token, but not edit NPC tokens.
            if char["kind"] != "PLAYER" or char["owner_id"] != p["id"]:
                raise HTTPException(403, "Не можна переміщувати цей токен.")
        allowed = {"name","level","str_stat","dex","con","int_stat","wis","cha","hp","max_hp","ac","conditions","x","y"}
        changes = {k:v for k,v in changes.items() if k in allowed}
        if "hp" in changes:
            changes["hp"] = max(0, min(int(changes["hp"]), int(changes.get("max_hp", char["max_hp"]))))
        if "max_hp" in changes:
            changes["max_hp"] = max(1, int(changes["max_hp"]))
            changes["hp"] = min(int(changes.get("hp",char["hp"])), changes["max_hp"])
        if "x" in changes: changes["x"] = max(0,min(11,int(changes["x"])))
        if "y" in changes: changes["y"] = max(0,min(7,int(changes["y"])))
        assignments = ",".join(f"{k}=?" for k in changes)
        c.execute(f"UPDATE characters SET {assignments} WHERE id=?", (*changes.values(), character_id))
        label = changes.get("name", char["name"])
        if "hp" in changes:
            log_event(session_id,p["name"],"hp_changed",f'{label}: HP {changes["hp"]}/{changes.get("max_hp",char["max_hp"])}')
        elif "x" in changes or "y" in changes:
            log_event(session_id,p["name"],"token_moved",f'{label} переміщено на ({changes.get("x",char["x"])}, {changes.get("y",char["y"])})')
        else:
            log_event(session_id,p["name"],"character_updated",f'Оновлено персонажа «{label}»')
    await broadcast(session_id, "character_updated")
    return {"ok":True}

def parse_dice(formula):
    import re
    f = formula.replace(" ","").lower()
    m = re.fullmatch(r"(\d{1,2})d(4|6|8|10|12|20|100)([+-]\d{1,4})?", f)
    if not m:
        raise HTTPException(400, "Формат кидка: 1d20+5. Доступні d4, d6, d8, d10, d12, d20, d100; максимум 20 кубиків.")
    count, sides, mod = int(m.group(1)), int(m.group(2)), int(m.group(3) or 0)
    if count < 1 or count > 20:
        raise HTTPException(400, "Кількість кубиків має бути від 1 до 20.")
    rolls = [random.randint(1,sides) for _ in range(count)]
    total = sum(rolls) + mod
    return {"formula":formula,"rolls":rolls,"modifier":mod,"total":total,"sides":sides}

@app.post("/api/sessions/{session_id}/roll")
async def roll_dice(session_id: int, data: RollRequest, x_access_key: str = Header(default="")):
    p = verify(session_id, x_access_key)
    result = parse_dice(data.formula)
    detail = " + ".join(map(str,result["rolls"]))
    if result["modifier"]:
        detail += f' {result["modifier"]:+d}'
    log_event(session_id, data.actor.strip() or p["name"], "dice_rolled",
              f'{data.actor.strip() or p["name"]}: {result["formula"]} = {detail} = {result["total"]}')
    await broadcast(session_id, "dice_rolled")
    return result

@app.post("/api/sessions/{session_id}/combat/start")
async def combat_start(session_id: int, data: CombatStart, x_access_key: str = Header(default="")):
    p = verify(session_id, x_access_key, gm=True)
    entries = []
    with db() as c:
        chars = {r["id"]:dict(r) for r in c.execute("SELECT id,name FROM characters WHERE session_id=?",(session_id,)).fetchall()}
        for item in data.entries:
            cid = int(item.get("character_id",0))
            if cid not in chars: continue
            roll = random.randint(1,20) + int(item.get("bonus",0))
            entries.append({"character_id":cid,"name":chars[cid]["name"],"initiative":roll})
        entries.sort(key=lambda e:e["initiative"], reverse=True)
        c.execute("UPDATE combat SET initiative_json=?,active_index=0,round_no=1 WHERE session_id=?",
                  (json.dumps(entries,ensure_ascii=False),session_id))
    log_event(session_id,p["name"],"combat_started","Бій розпочато. Ініціатива: "+", ".join(f'{e["name"]} ({e["initiative"]})' for e in entries))
    await broadcast(session_id,"combat_started")
    return {"initiative":entries}

@app.post("/api/sessions/{session_id}/combat/next")
async def combat_next(session_id: int, x_access_key: str = Header(default="")):
    p = verify(session_id, x_access_key, gm=True)
    with db() as c:
        row = c.execute("SELECT initiative_json,active_index,round_no FROM combat WHERE session_id=?",(session_id,)).fetchone()
        entries = json.loads(row["initiative_json"] or "[]")
        if entries:
            idx = int(row["active_index"]) + 1
            rnd = int(row["round_no"])
            if idx >= len(entries):
                idx = 0; rnd += 1
            c.execute("UPDATE combat SET active_index=?,round_no=? WHERE session_id=?",(idx,rnd,session_id))
            active = entries[idx]["name"]
        else:
            active = "—"
            rnd = row["round_no"]
    log_event(session_id,p["name"],"turn_changed",f'Наступний хід: {active}, раунд {rnd}')
    await broadcast(session_id,"turn_changed")
    return {"active":active,"round":rnd}

@app.post("/api/sessions/{session_id}/combat/attack")
async def combat_attack(session_id: int, data: AttackRequest, x_access_key: str = Header(default="")):
    p = verify(session_id, x_access_key, gm=True)
    with db() as c:
        a = c.execute("SELECT * FROM characters WHERE id=? AND session_id=?",(data.attacker_id,session_id)).fetchone()
        t = c.execute("SELECT * FROM characters WHERE id=? AND session_id=?",(data.target_id,session_id)).fetchone()
        if not a or not t: raise HTTPException(404,"Персонажа не знайдено.")
        attack = random.randint(1,20)
        total = attack + data.bonus
        if attack == 20:
            hit = True
        elif attack == 1:
            hit = False
        else:
            hit = total >= t["ac"]
        damage = parse_dice(data.damage) if hit else {"total":0,"rolls":[],"formula":data.damage}
        new_hp = max(0,t["hp"]-damage["total"])
        c.execute("UPDATE characters SET hp=? WHERE id=?",(new_hp,t["id"]))
    outcome = "влучання" if hit else "промах"
    log_event(session_id,p["name"],"attack",f'{a["name"]} атакує {t["name"]}: d20 {attack}+{data.bonus}={total} проти AC {t["ac"]} — {outcome}; шкода {damage["total"]}, HP {new_hp}/{t["max_hp"]}')
    await broadcast(session_id,"attack")
    return {"attack_roll":attack,"attack_total":total,"target_ac":t["ac"],"hit":hit,"damage":damage["total"],"target_hp":new_hp}

@app.post("/api/sessions/{session_id}/characters/{character_id}/hp")
async def change_hp(session_id: int, character_id: int, data: HpChange, x_access_key: str = Header(default="")):
    p = verify(session_id,x_access_key,gm=True)
    with db() as c:
        char = c.execute("SELECT * FROM characters WHERE id=? AND session_id=?",(character_id,session_id)).fetchone()
        if not char: raise HTTPException(404,"Персонажа не знайдено.")
        hp = max(0,min(char["max_hp"],char["hp"]+data.amount))
        c.execute("UPDATE characters SET hp=? WHERE id=?",(hp,character_id))
    log_event(session_id,p["name"],"hp_changed",f'{char["name"]}: {data.amount:+d} HP → {hp}/{char["max_hp"]}')
    await broadcast(session_id,"hp_changed")
    return {"hp":hp}

@app.post("/api/sessions/{session_id}/map")
async def upload_map(session_id: int, file: UploadFile = File(...), x_access_key: str = Header(default="")):
    p = verify(session_id,x_access_key,gm=True)
    if file.content_type not in ("image/png","image/jpeg","image/webp"):
        raise HTTPException(400,"Дозволені лише PNG, JPG або WEBP.")
    content = await file.read()
    if len(content) > 8 * 1024 * 1024:
        raise HTTPException(400,"Максимальний розмір карти — 8 МБ.")
    ext = {"image/png":".png","image/jpeg":".jpg","image/webp":".webp"}[file.content_type]
    filename = f"map_{session_id}_{secrets.token_hex(5)}{ext}"
    (UPLOAD_DIR / filename).write_bytes(content)
    with db() as c:
        c.execute("UPDATE sessions SET map_image=? WHERE id=?",(f"/static/uploads/{filename}",session_id))
    log_event(session_id,p["name"],"map_updated","GM завантажив фон карти")
    await broadcast(session_id,"map_updated")
    return {"map_image":f"/static/uploads/{filename}"}

@app.websocket("/ws/{session_id}")
async def websocket_endpoint(websocket: WebSocket, session_id: int, key: str):
    p = participant_by_key(session_id,key)
    if not p:
        await websocket.close(code=1008)
        return
    await websocket.accept()
    connections.setdefault(session_id,set()).add(websocket)
    try:
        await websocket.send_text(json.dumps({"type":"initial","state":get_state_by_id(session_id)},ensure_ascii=False))
        while True:
            # Client sends a small heartbeat; state changes are broadcast by API handlers.
            await websocket.receive_text()
    except WebSocketDisconnect:
        connections.get(session_id,set()).discard(websocket)
    except Exception:
        connections.get(session_id,set()).discard(websocket)
