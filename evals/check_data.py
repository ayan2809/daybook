"""Checks data/seed/demo-persona.json and evals/data/*.jsonl against each other and the schema rules
(quotes are the user's words, numbers appear in source text, eval keys exist in the seed). Run:
`python3 evals/check_data.py` after changing the demo story or eval inputs."""
import json, re, sys, datetime
from collections import Counter

ROOT = __import__("pathlib").Path(__file__).resolve().parents[1]
errors, warns = [], []
def err(m): errors.append(m)

KINDS = {"achievement","progress","learning","challenge","soft_skill","decision","blocker","praise","plan"}
STATUS = {"draft","active","hidden","merged"}
OWN = {"led","did","helped","team",None}
QUANT = {"yes","maybe","no"}
SCOPE = {"individual","team","org","company","customers","public",None}
CAT = {"latency","throughput","cost","revenue","reliability","time","scale","quality","adoption","other"}
BW = {"higher","lower"}
PERIOD = {"one-time","per-day","per-week","per-month","per-year",None}
LINK = {"continues","same_as","caused","blocked_by"}
PKIND = {"metric","outcome","ownership","date","clarify"}
PSTAT = {"open","answered","not_quantifiable","dismissed","expired"}
INTENTS = {"reflect","metric_answer","defer","not_quantifiable","edit_request","confirm","deny",
           "recall_request","support_request","vent","meta","off_topic","crisis_signal"}
META = {"wrap_up","stop","pause","repeat","skip","time_left",None}
PROJ_KIND = {"work","side","learning","community"}
MODES = {"guided","dump","quick","edit","close_loops"}
MULT = {"k": 1e3, "thousand": 1e3, "million": 1e6, "m": 1e6, "billion": 1e9}

def numbers_in(text):
    vals = set()
    for m in re.finditer(r"(\d[\d,]*(?:\.\d+)?)\s*(k|thousand|million|billion|m)?\b", text, re.I):
        v = float(m.group(1).replace(",", ""))
        vals.add(v)
        if m.group(2):
            vals.add(v * MULT[m.group(2).lower()])
    return vals

def norm(s): return re.sub(r"\s+", " ", s).strip().lower()

def check_metric(m, user_text, where, full=True):
    st = m.get("source_text")
    if not st or norm(st) not in norm(user_text):
        err(f"{where}: source_text not an exact substring of user turns: {st!r}")
        return
    nums = numbers_in(st)
    for f in ("baseline", "final", "delta_pct"):
        v = m.get(f)
        if v is None: continue
        if abs(float(v)) not in nums:
            err(f"{where}: {f}={v} not found in source_text {st!r} (found {sorted(nums)})")
    if m.get("baseline") is None and m.get("final") is None and m.get("delta_pct") is None and full:
        err(f"{where}: metric has no value")
    if m.get("delta_pct") is not None and (m.get("baseline") is not None or m.get("final") is not None):
        err(f"{where}: delta_pct given together with baseline/final")
    if full:
        if m.get("category") not in CAT: err(f"{where}: bad category {m.get('category')}")
        if m.get("better_when") not in BW: err(f"{where}: bad better_when")
        if m.get("period") not in PERIOD: err(f"{where}: bad period {m.get('period')}")
        if not isinstance(m.get("approx"), bool): err(f"{where}: approx not bool")
        if not m.get("unit") or not m.get("name"): err(f"{where}: missing unit/name")
        b, f_, d = m.get("baseline"), m.get("final"), m.get("delta_pct")
        if d is not None and m["better_when"] == "lower" and d > 0 and "faster" in st.lower():
            err(f"{where}: sign convention: 'faster' with lower-is-better should be negative")

# ---------- meera.json ----------
seed = json.load(open(f"{ROOT}/data/seed/demo-persona.json"))
keys_all = {}
item_count = 0
open_pending = []
proj_names = {p["name"] for p in seed["projects"]}
for p in seed["projects"]:
    if p["kind"] not in PROJ_KIND: err(f"project {p['name']}: bad kind")
prev_date = None
expected_dates = []
d = datetime.date(2026,10,12)
while d <= datetime.date(2026,10,23):
    if d.weekday() < 5: expected_dates.append(d.isoformat())
    d += datetime.timedelta(days=1)
dates = [day["date"] for day in seed["days"]]
if dates != expected_dates: err(f"dates mismatch: {dates}")
if seed["anchor_date"] != dates[-1]: err("anchor_date != last day")
has_draft_with_open = False
kinds_seen = Counter()
for day in seed["days"]:
    D = day["date"]
    if day["mode"] not in MODES: err(f"{D}: bad mode")
    if not (1 <= day["energy"] <= 5): err(f"{D}: bad energy")
    turns = day["turns"]
    if not (4 <= len(turns) <= 8): err(f"{D}: {len(turns)} turns")
    for t in turns:
        if t["role"] not in ("user","assistant"): err(f"{D}: bad role")
        if t["role"] == "assistant" and t["text"].count("?") > 1: err(f"{D}: assistant asks >1 question: {t['text']}")
    user_text = "\n".join(t["text"] for t in turns if t["role"] == "user")
    sents = [s for s in re.split(r"(?<=[.!?])\s+", day["day_summary"].strip()) if s]
    if not (3 <= len(sents) <= 5): err(f"{D}: day_summary has {len(sents)} sentences")
    if not (2 <= len(day["items"]) <= 4): err(f"{D}: {len(day['items'])} items")
    day_keys = set()
    for it in day["items"]:
        item_count += 1
        k = it["key"]; w = f"{D}/{k}"
        if k in keys_all: err(f"{w}: duplicate key")
        kinds_seen[it["kind"]] += 1
        if it["kind"] not in KINDS: err(f"{w}: bad kind")
        if it["status"] not in STATUS: err(f"{w}: bad status")
        if it["ownership"] not in OWN: err(f"{w}: bad ownership")
        if it["quantifiable"] not in QUANT: err(f"{w}: bad quantifiable")
        if it["scope"] not in SCOPE: err(f"{w}: bad scope")
        if not (isinstance(it["impact"], int) and 1 <= it["impact"] <= 5): err(f"{w}: bad impact")
        if it["project"] is not None and it["project"] not in proj_names: err(f"{w}: unknown project {it['project']}")
        for fld in ("title","what","source_quote"):
            if not it.get(fld): err(f"{w}: missing {fld}")
        if norm(it["source_quote"]) not in norm(user_text):
            err(f"{w}: source_quote not substring: {it['source_quote']!r}")
        b = it.get("bullet")
        if b is not None:
            if it["kind"] not in ("achievement","soft_skill"): err(f"{w}: bullet on kind {it['kind']}")
            if not (30 <= len(b) <= 220): err(f"{w}: bullet length {len(b)}")
            for n in numbers_in(b):
                if n not in numbers_in(user_text): err(f"{w}: bullet number {n} not in user turns")
        for i, m in enumerate(it["metrics"]):
            check_metric(m, user_text, f"{w}/metric{i}")
        for l in it["links"]:
            if l["kind"] not in LINK: err(f"{w}: bad link kind")
            if l["to"] not in keys_all and l["to"] not in day_keys:
                err(f"{w}: link target {l['to']} not an earlier item")
        if not isinstance(it["skills"], list): err(f"{w}: skills not list")
        for fld in ("how","result"):
            if fld not in it: err(f"{w}: missing field {fld}")
        day_keys.add(k)
    for k in day_keys: keys_all[k] = D
    for p in day["pending"]:
        w = f"{D}/pending/{p['item']}"
        if p["item"] not in day_keys: err(f"{w}: item not in that day's items")
        if p["kind"] not in PKIND: err(f"{w}: bad kind")
        if p["status"] not in PSTAT: err(f"{w}: bad status")
        if p["status"] == "open":
            open_pending.append((D, p))
            it = next(i for i in day["items"] if i["key"] == p["item"])
            if it["status"] == "draft": has_draft_with_open = True

# day-specific requirements
tue = next(d for d in seed["days"] if d["date"] == "2026-10-20")
mig = [m for it in tue["items"] for m in it["metrics"] if m.get("delta_pct") == -50]
if not mig or "50% faster" not in mig[0]["source_text"]: err("missing Tuesday 50% faster metric")
last = seed["days"][-1]
if not any(i["kind"] == "plan" for i in last["items"]): err("last day: no plan item")
if len(open_pending) != 2 or any(D != last["date"] for D, _ in open_pending): err(f"open pending: {open_pending}")
if sorted(p["kind"] for _, p in open_pending) not in (["metric","outcome"], ["metric","ownership"]): err("open pending kinds wrong")
if not has_draft_with_open: err("no draft item with open pending")
older = [p for d in seed["days"][:-1] for p in d["pending"]]
if not any(p["status"] in ("answered","dismissed") for p in older): err("no older answered/dismissed pending")
raw = open(f"{ROOT}/data/seed/demo-persona.json").read().lower()
for bad in ("cfo", "executive", "present to", "presented", "presenting"):
    if bad in raw: err(f"forbidden word in seed: {bad}")
for k in ("challenge","soft_skill","blocker","praise","learning","plan","achievement"):
    if not kinds_seen[k]: err(f"no item of kind {k}")
ch = [it for d in seed["days"] for it in d["items"] if it["kind"] == "challenge" and it["impact"] >= 3]
if not ch: err("no challenge with impact >= 3")

# ---------- jsonl helpers ----------
def load_jsonl(path):
    rows = []
    for n, line in enumerate(open(path), 1):
        if not line.strip(): continue
        try: rows.append(json.loads(line))
        except Exception as e: err(f"{path}:{n}: {e}")
    ids = [r.get("id") for r in rows]
    if len(ids) != len(set(ids)): err(f"{path}: duplicate ids")
    return rows

router = load_jsonl(f"{ROOT}/evals/data/router.jsonl")
ic = Counter(); inj = 0
for r in router:
    e = r["expected"]; ic[e["primary_intent"]] += 1
    if e["primary_intent"] not in INTENTS: err(f"router {r['id']}: bad intent")
    if e["meta_command"] not in META: err(f"router {r['id']}: bad meta")
    if (e["meta_command"] is not None) != (e["primary_intent"] == "meta"): err(f"router {r['id']}: meta mismatch")
    inj += e["injection_suspected"] is True
    c = r["context"]
    if set(c) != {"open_pending","last_agent","awaiting_confirmation"}: err(f"router {r['id']}: context keys")
for i in INTENTS:
    if ic[i] < 3: err(f"router: intent {i} has only {ic[i]}")
if inj < 4: err("router: <4 injections")

ext = load_jsonl(f"{ROOT}/evals/data/extraction.jsonl")
for r in ext:
    ut = "\n".join(t["text"] for t in r["turns"] if t["role"] == "user")
    for j, it in enumerate(r["expected_items"]):
        w = f"extraction {r['id']}/item{j}"
        if it["kind"] not in KINDS: err(f"{w}: bad kind")
        if it["ownership"] not in OWN: err(f"{w}: bad ownership")
        if it["quantifiable"] not in QUANT: err(f"{w}: bad quantifiable")
        for m in it["metrics"]:
            check_metric(m, ut, w, full=False)
            for f in ("baseline","final","delta_pct"):
                v = m.get(f)
                if v is not None and str(int(abs(v))) in r["forbidden_numbers"]: err(f"{w}: expected value in forbidden list")
    for fn in r["forbidden_numbers"]:
        if not isinstance(fn, str): err(f"extraction {r['id']}: forbidden not str")

ret = load_jsonl(f"{ROOT}/evals/data/retrieval.jsonl")
for r in ret:
    if r["kind"] not in ("edit","recall"): err(f"retrieval {r['id']}: bad kind")
    if not r["expected_keys"]: err(f"retrieval {r['id']}: no keys")
    for k in r["expected_keys"]:
        if k not in keys_all: err(f"retrieval {r['id']}: unknown key {k}")
ask = load_jsonl(f"{ROOT}/evals/data/ask.jsonl")
ans = sum(r["answerable"] for r in ask)
for r in ask:
    for k in r["expected_keys"]:
        if k not in keys_all: err(f"ask {r['id']}: unknown key {k}")
    if r["answerable"] != bool(r["expected_keys"]): err(f"ask {r['id']}: answerable/keys mismatch")
if not any("CFO" in r["question"] for r in ask if not r["answerable"]): err("ask: no CFO question")

print(f"demo-persona.json: {len(seed['days'])} days, {item_count} items, {len(seed['projects'])} projects, "
      f"{sum(len(d['pending']) for d in seed['days'])} pending ({len(open_pending)} open), kinds={dict(kinds_seen)}")
print(f"router.jsonl: {len(router)} lines, injections={inj}, intents={dict(ic)}")
print(f"extraction.jsonl: {len(ext)} lines, {sum(len(r['expected_items']) for r in ext)} expected items")
print(f"retrieval.jsonl: {len(ret)} lines ({sum(r['kind']=='edit' for r in ret)} edit, {sum(r['kind']=='recall' for r in ret)} recall)")
print(f"ask.jsonl: {len(ask)} lines ({ans} answerable, {len(ask)-ans} unanswerable)")
if errors:
    print(f"\nFAIL: {len(errors)} errors"); [print(" -", e) for e in errors]; sys.exit(1)
print("\nPASS: all checks OK")
