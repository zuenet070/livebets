import time
import os
import requests
import csv
from datetime import date, datetime

# ============================================================
# LIVEBETS BOT (SHARPER) — Big Chances removed ✅
# + 6 MIN COOLDOWN ONLY AFTER A GOAL ✅
# + Pace/recency filter (last 10m + last 5m trend)
# + Time windows: 15–35 & 50–85
# + 1X2 odd for dominant team (if available) AND must be >= 1.50
# + Daily report + league/tier analysis
#
# NEW (your request):
# ✅ 1) 33'-TRAP FIX: stricter 1st-half caps (NORMAL 30, PREMIUM 32, EXTREME 33)
# ✅ 2) Extra 0-0 guard after 30' in 1st half (needs strong last5 pace)
# ============================================================

# =========================
# ENV VARS
# =========================
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
API_KEY = os.getenv("API_FOOTBALL_KEY")

# Optional Railway Volume:
# set DATA_DIR=/data and mount volume to /data
DATA_DIR = os.getenv("DATA_DIR", ".")

if not BOT_TOKEN or not CHAT_ID or not API_KEY:
    print("❌ ERROR: Missing env vars. Check BOT_TOKEN, CHAT_ID, API_FOOTBALL_KEY")
    raise SystemExit(1)

BASE_URL = "https://v3.football.api-sports.io"
HEADERS = {"x-apisports-key": API_KEY}

# =========================
# TIME WINDOWS (your wish)
# =========================
FIRST_HALF_MIN = 15
FIRST_HALF_MAX = 35
SECOND_HALF_MIN = 50
SECOND_HALF_MAX = 85

# =========================
# LOOP TUNING
# =========================
SLEEP_SECONDS = 75
MAX_STATS_CHECKS_PER_LOOP = 10     # keeps API usage stable
MAX_ALERTS_PER_LOOP = 2            # allow some volume

API_TIMEOUT = 25

# =========================
# COOLDOWN (IMPORTANT)
# =========================
NORMAL_COOLDOWN_SECONDS = 90       # normal
POST_GOAL_COOLDOWN_SECONDS = 360   # 6 minutes ONLY after goal

# Score rules
MAX_BEHIND_GOALS = 2               # dominant side can be max 2 behind

# =========================
# ODDS FILTER (1X2)
# =========================
ODDS_MIN_1X2 = 1.50  # if odds available, must be >= 1.50

# =========================
# THRESHOLDS (volume vs quality)
# =========================
# NORMAL
NORMAL_MIN_SCORE = 12
NORMAL_MIN_GAP = 16.0
NORMAL_MAX_OPP_SOT = 2
NORMAL_MAX_OPP_SHOTS = 10

# PREMIUM (stricter)
PREMIUM_MIN_SCORE = 16
PREMIUM_MIN_GAP = 26.0
PREMIUM_MIN_SOT_DIFF = 2
PREMIUM_MAX_OPP_SOT = 1
PREMIUM_MAX_OPP_SHOTS = 7
PREMIUM_MIN_CONF = 72

# EXTREME (rare)
EXTREME_SCORE = 22
EXTREME_MIN_GAP = 34.0
EXTREME_MAX_OPP_SOT = 1
EXTREME_MAX_OPP_SHOTS = 6

# =========================
# WEIGHTS (SOT most important)
# =========================
W_SOT = 6
W_SHOTS = 1
W_CORNERS = 1
W_POSSESSION = 0.06
RED_CARD_BONUS = 6

# =========================
# PACE / RECENCY FILTER (KEY)
# =========================
PACE10_WINDOW_MINUTES = 10
PACE5_WINDOW_MINUTES = 5

# baseline pace
PACE10_MIN_SOT = 2
PACE10_MIN_SHOTS = 4

# premium/extreme pace
PREMIUM_PACE10_MIN_SOT = 2
PREMIUM_PACE10_MIN_SHOTS = 5
EXTREME_PACE10_MIN_SOT = 3
EXTREME_PACE10_MIN_SHOTS = 6

# trend: last5 should not be weaker than prev5
TREND_MIN_SOT_DELTA = 0
TREND_MIN_SHOTS_DELTA = 0

# late game kill switch
LATE_MINUTE = 80
LATE_REQUIRE_PACE10_MIN_SOT = 2
LATE_REQUIRE_PACE10_MIN_SHOTS = 4

# =========================
# ✅ NEW: 33'-TRAP FIX (your request)
# =========================
FIRST_HALF_NORMAL_MAX = 30      # was 32
FIRST_HALF_PREMIUM_MAX = 32     # was 33
FIRST_HALF_EXTREME_MAX = 33     # was 35

# ✅ NEW: Extra 0-0 guard after 30' (needs strong last5 pace)
ZEROZERO_GUARD_START = 30
ZEROZERO_GUARD_END = 45
ZEROZERO_MIN_PACE5_SOT = 2
ZEROZERO_MIN_PACE5_SHOTS = 5

# =========================
# BLACKLIST (optional)
# =========================
EXCLUDE_KEYWORDS = [
    "U21", "U20", "U19", "U18", "U17", "U16",
    "Youth", "Junior",
    "Reserves", "Reserve", "B Team", "B-team", "II",
    "Women", "Womens", "Fem", "Dames",
    "Futsal",
    "Esports", "E-sports", "Virtual",
]

# =========================
# STATE
# =========================
TODAY = date.today()

ALERTED_MATCHES = set()          # 1 alert per match per day
HALF_TIME_SNAPSHOT = {}          # fid -> HT totals snapshot
SCORE_STATE = {}                 # fid -> {"score":(gh,ga),"changed_at":t,"just_scored":bool}
PENDING = {}                     # fid -> pick info for HIT/MISS
STATS_HISTORY = {}               # fid -> list of snapshots (minute + totals)

ALERTS_LOG = os.path.join(DATA_DIR, "alerts_log.csv")
RESULTS_LOG = os.path.join(DATA_DIR, "results_log.csv")

# =========================
# HELPERS
# =========================
def send_message(text: str):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": text}
    try:
        requests.post(url, data=payload, timeout=10)
    except:
        pass

def api_get(path, params=None, retries=3):
    url = f"{BASE_URL}{path}"
    last_exc = None
    for i in range(retries):
        try:
            r = requests.get(url, headers=HEADERS, params=params, timeout=API_TIMEOUT)
            if r.status_code == 429:
                time.sleep(20 + i * 20)
                continue
            if r.status_code >= 500:
                time.sleep(5 + i * 10)
                continue
            r.raise_for_status()
            return r.json()
        except Exception as e:
            last_exc = e
            time.sleep(2 + i * 4)
    raise last_exc

def get_live_matches():
    data = api_get("/fixtures", params={"live": "all"})
    return data.get("response", [])

def get_fixture_by_id(fid):
    data = api_get("/fixtures", params={"id": fid})
    resp = data.get("response", [])
    return resp[0] if resp else None

def get_match_statistics(fixture_id):
    data = api_get("/fixtures/statistics", params={"fixture": fixture_id})
    return data.get("response", [])

def safe_int(v):
    if v is None:
        return 0
    if isinstance(v, str):
        v = v.replace("%", "").strip()
    try:
        return int(float(v))
    except:
        return 0

def stat(team_stats_list, name):
    for s in team_stats_list:
        if s.get("type") == name:
            return safe_int(s.get("value"))
    return 0

def clamp_nonnegative(x):
    return x if x > 0 else 0

def is_excluded_match(league_name, home_name, away_name):
    text = f"{league_name} {home_name} {away_name}".lower()
    for kw in EXCLUDE_KEYWORDS:
        if kw.lower() in text:
            return True
    return False

def ensure_csv_header(file_path, header_cols):
    try:
        with open(file_path, "r", newline="", encoding="utf-8") as f:
            return
    except FileNotFoundError:
        os.makedirs(os.path.dirname(file_path) or ".", exist_ok=True)
        with open(file_path, "w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(header_cols)

def log_alert_row(row):
    ensure_csv_header(ALERTS_LOG, [
        "timestamp", "tier", "fixture_id", "league", "home", "away",
        "minute", "score", "pick", "dominant_score", "gap", "confidence",
        "odds_1x2", "pace10_shots", "pace10_sot", "pace5_shots", "pace5_sot",
        "prev5_shots", "prev5_sot"
    ])
    with open(ALERTS_LOG, "a", newline="", encoding="utf-8") as f:
        csv.writer(f).writerow(row)

def log_result_row(row):
    ensure_csv_header(RESULTS_LOG, [
        "timestamp", "fixture_id", "tier", "home", "away", "pick", "result",
        "minute_resolved", "score_at_alert", "score_resolved", "league"
    ])
    with open(RESULTS_LOG, "a", newline="", encoding="utf-8") as f:
        csv.writer(f).writerow(row)

def cleanup_finished(fid):
    HALF_TIME_SNAPSHOT.pop(fid, None)
    SCORE_STATE.pop(fid, None)
    PENDING.pop(fid, None)
    STATS_HISTORY.pop(fid, None)

# =========================
# Rolling pace history
# =========================
def update_stats_history(fid, minute, hsot_total, asot_total, hshots_total, ashots_total):
    now = time.time()
    if fid not in STATS_HISTORY:
        STATS_HISTORY[fid] = []
    STATS_HISTORY[fid].append({
        "t": now,
        "m": int(minute),
        "hsot": int(hsot_total),
        "asot": int(asot_total),
        "hshots": int(hshots_total),
        "ashots": int(ashots_total),
    })

    # trim: keep last ~25 minutes or last 20 game-min span
    hist = STATS_HISTORY[fid]
    STATS_HISTORY[fid] = [p for p in hist if (now - p["t"] <= 25 * 60)]
    hist = STATS_HISTORY[fid]
    if hist:
        cur_m = hist[-1]["m"]
        STATS_HISTORY[fid] = [p for p in hist if (cur_m - p["m"] <= 20)]

def get_pace_deltas(fid, minute, hsot_total, asot_total, hshots_total, ashots_total, window_minutes):
    if fid not in STATS_HISTORY or len(STATS_HISTORY[fid]) < 2:
        return (0, 0, 0, 0)

    hist = STATS_HISTORY[fid]
    cur_m = int(minute)
    target_m = cur_m - window_minutes

    candidate = None
    for p in reversed(hist):
        if p["m"] <= target_m:
            candidate = p
            break
    if candidate is None:
        candidate = hist[0]

    h_sot = clamp_nonnegative(hsot_total - candidate["hsot"])
    a_sot = clamp_nonnegative(asot_total - candidate["asot"])
    h_shots = clamp_nonnegative(hshots_total - candidate["hshots"])
    a_shots = clamp_nonnegative(ashots_total - candidate["ashots"])
    return (h_sot, a_sot, h_shots, a_shots)

# =========================
# ODDS: 1X2 / Match Winner
# =========================
def get_odds_1x2_for_side(fixture_id, pick_side, home_name, away_name):
    try:
        data = api_get("/odds", params={"fixture": fixture_id})
    except:
        return (None, None, None)

    resp = data.get("response", [])
    if not resp:
        return (None, None, None)

    for item in resp:
        for book in item.get("bookmakers", []) or []:
            bname = book.get("name")
            for bet in book.get("bets", []) or []:
                mname = (bet.get("name") or "").lower()
                if ("match winner" not in mname) and ("1x2" not in mname) and ("match result" not in mname):
                    continue

                for v in bet.get("values", []) or []:
                    vlabel = (v.get("value") or "").strip().lower()
                    if pick_side == "HOME" and vlabel in ("home", "1", home_name.lower()):
                        try:
                            return (float(v.get("odd")), bname, bet.get("name"))
                        except:
                            return (None, bname, bet.get("name"))
                    if pick_side == "AWAY" and vlabel in ("away", "2", away_name.lower()):
                        try:
                            return (float(v.get("odd")), bname, bet.get("name"))
                        except:
                            return (None, bname, bet.get("name"))
    return (None, None, None)

# =========================
# CONFIDENCE (0-100)
# =========================
def confidence_score(dom_score, gap, sot_diff, red_adv, pace10_sot, pace10_shots):
    score = 0
    score += max(0, (dom_score - NORMAL_MIN_SCORE) * 6)
    score += min(30, gap * 1.0)
    score += min(25, sot_diff * 10)
    score += min(10, red_adv * 10)
    score += min(15, pace10_sot * 5 + pace10_shots * 1.5)
    return int(max(0, min(100, score)))

# =========================
# HIT/MISS tracking
# =========================
def resolve_pending_from_match(match):
    fixture = match.get("fixture", {})
    fid = fixture.get("id")
    if not fid or fid not in PENDING:
        return

    status_short = fixture.get("status", {}).get("short", "")
    minute = fixture.get("status", {}).get("elapsed") or 0

    gh = match.get("goals", {}).get("home", 0)
    ga = match.get("goals", {}).get("away", 0)

    p = PENDING[fid]
    old_gh, old_ga = p["score_at_alert"]

    goal_home = gh > old_gh
    goal_away = ga > old_ga

    if goal_home or goal_away:
        scorer = "HOME" if goal_home and not goal_away else "AWAY"
        result = "HIT" if scorer == p["pick_side"] else "MISS"

        send_message(
            f"📌 RESULT ({p['tier']})\n\n"
            f"{p['home']} vs {p['away']}\n"
            f"Pick: {p['pick_team']}\n\n"
            f"{'✅ HIT' if result == 'HIT' else '❌ MISS'} — goal gevallen rond {minute}'\n"
            f"Score: {old_gh}-{old_ga} ➜ {gh}-{ga}"
        )

        log_result_row([
            datetime.now().isoformat(timespec="seconds"),
            fid,
            p["tier"],
            p["home"],
            p["away"],
            p["pick_team"],
            result,
            minute,
            f"{old_gh}-{old_ga}",
            f"{gh}-{ga}",
            p.get("league", "")
        ])

        PENDING.pop(fid, None)
        return

    if status_short in ("FT", "AET", "PEN"):
        send_message(
            f"📌 RESULT ({p['tier']})\n\n"
            f"{p['home']} vs {p['away']}\n"
            f"Pick: {p['pick_team']}\n\n"
            f"❌ MISS — geen volgende goal meer gevallen.\n"
            f"Score bleef: {gh}-{ga}"
        )

        log_result_row([
            datetime.now().isoformat(timespec="seconds"),
            fid,
            p["tier"],
            p["home"],
            p["away"],
            p["pick_team"],
            "MISS",
            minute,
            f"{p['score_at_alert'][0]}-{p['score_at_alert'][1]}",
            f"{gh}-{ga}",
            p.get("league", "")
        ])

        PENDING.pop(fid, None)
        cleanup_finished(fid)

def resolve_pending_not_in_live():
    for fid in list(PENDING.keys()):
        match = get_fixture_by_id(fid)
        if match:
            resolve_pending_from_match(match)

# =========================
# DAILY REPORT (yesterday)
# =========================
def send_daily_report(report_date):
    day_str = report_date.isoformat()

    tiers_count = {"NORMAL": 0, "PREMIUM": 0, "EXTREME": 0}
    total_alerts = 0

    try:
        with open(ALERTS_LOG, "r", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        day_rows = [r for r in rows if (r.get("timestamp") or "").startswith(day_str)]
        total_alerts = len(day_rows)
        for r in day_rows:
            t = (r.get("tier") or "").strip()
            if t in tiers_count:
                tiers_count[t] += 1
    except FileNotFoundError:
        pass

    hits = misses = 0
    league_results = {}
    tier_results = {"NORMAL": {"HIT": 0, "MISS": 0}, "PREMIUM": {"HIT": 0, "MISS": 0}, "EXTREME": {"HIT": 0, "MISS": 0}}

    try:
        with open(RESULTS_LOG, "r", encoding="utf-8") as f:
            rrows = list(csv.DictReader(f))
        day_rrows = [r for r in rrows if (r.get("timestamp") or "").startswith(day_str)]
        for r in day_rrows:
            res = r.get("result")
            tr = r.get("tier")
            lg = r.get("league") or "Unknown League"

            if res == "HIT":
                hits += 1
            elif res == "MISS":
                misses += 1

            if tr in tier_results and res in ("HIT", "MISS"):
                tier_results[tr][res] += 1

            if lg not in league_results:
                league_results[lg] = {"HIT": 0, "MISS": 0}
            if res in ("HIT", "MISS"):
                league_results[lg][res] += 1
    except FileNotFoundError:
        pass

    resolved = hits + misses
    hitrate = round((hits / resolved) * 100, 1) if resolved else 0.0

    tier_lines = []
    for t in ["NORMAL", "PREMIUM", "EXTREME"]:
        th = tier_results[t]["HIT"]
        tm = tier_results[t]["MISS"]
        trn = th + tm
        hr = round((th / trn) * 100, 1) if trn else 0.0
        icon = "⚠️" if t == "NORMAL" else ("💎" if t == "PREMIUM" else "🔥")
        tier_lines.append(f"{icon} {t}: {tiers_count[t]} | {hr}%")

    league_rank = []
    for lg, d in league_results.items():
        trn = d["HIT"] + d["MISS"]
        if trn >= 2:
            hr = d["HIT"] / trn
            league_rank.append((hr, trn, lg, d["HIT"], d["MISS"]))
    league_rank.sort(reverse=True)

    if league_rank:
        top_leagues_text = "\n".join(
            [f"• {lg}: {round(hr*100,1)}% ({h}-{m})" for hr, trn, lg, h, m in league_rank[:5]]
        )
    else:
        top_leagues_text = "• Nog te weinig league-data (min 2 results per league)."

    tips = []
    prem_res = tier_results["PREMIUM"]["HIT"] + tier_results["PREMIUM"]["MISS"]
    if prem_res >= 3:
        prem_hr = tier_results["PREMIUM"]["HIT"] / prem_res
        if prem_hr < 0.55:
            tips.append("• PREMIUM hitrate laag → maak PREMIUM strenger (PREMIUM_MIN_GAP of PREMIUM_MIN_CONF omhoog).")

    if resolved >= 6 and hitrate < 55:
        tips.append("• Hitrate laag → verhoog pace-eisen (PACE10_MIN_SHOTS/SOT omhoog) en tighten late-game rules.")

    if not tips:
        tips.append("• Instellingen zijn stabiel ✅ (geen grote aanpassingen nodig vandaag).")

    send_message(
        f"📊 DAGRAPPORT ({day_str})\n\n"
        f"📌 Alerts: {total_alerts}\n"
        f"✅ HIT: {hits}\n"
        f"❌ MISS: {misses}\n"
        f"⏳ Pending: {len(PENDING)}\n"
        f"🎯 Hitrate (resolved): {hitrate}%\n\n"
        + "\n".join(tier_lines) +
        f"\n\n🏆 TOP LEAGUES (beste hitrate):\n{top_leagues_text}\n\n"
        f"🤖 Optimalisatie tips:\n" + "\n".join(tips)
    )

# =========================
# START
# =========================
send_message(
    "🟢 LiveBets bot gestart (SHARPER)\n"
    "✅ Big Chances removed\n"
    "✅ Pace/recency + trend\n"
    "✅ 6 min cooldown only after a goal\n"
    "✅ 33'-trap fix (1H caps)\n"
    "✅ 0-0 guard after 30'\n"
    "✅ 1X2 odds (>= 1.50 if available)\n"
    "✅ Daily report + league analysis"
)

# =========================
# MAIN LOOP
# =========================
while True:
    try:
        # New day -> report yesterday + reset day-state
        if date.today() != TODAY:
            yesterday = TODAY
            send_daily_report(yesterday)

            TODAY = date.today()
            ALERTED_MATCHES.clear()
            HALF_TIME_SNAPSHOT.clear()
            SCORE_STATE.clear()
            PENDING.clear()
            STATS_HISTORY.clear()

            send_message("🔄 Nieuwe dag — reset uitgevoerd ✅")

        matches = get_live_matches()
        match_map = {m.get("fixture", {}).get("id"): m for m in matches if m.get("fixture", {}).get("id")}

        # Resolve pending
        for fid, m in list(match_map.items()):
            if fid in PENDING:
                resolve_pending_from_match(m)
        if PENDING:
            resolve_pending_not_in_live()

        # Build candidates (no extra API calls)
        candidates = []
        for match in matches:
            fixture = match.get("fixture", {})
            fid = fixture.get("id")
            if not fid:
                continue
            if fid in ALERTED_MATCHES:
                continue

            status_short = fixture.get("status", {}).get("short", "")
            if status_short in ("FT", "AET", "PEN", "CANC", "PST", "ABD", "AWD", "WO"):
                cleanup_finished(fid)
                continue

            minute = fixture.get("status", {}).get("elapsed")
            if not minute:
                continue

            # time windows
            in_first_window = FIRST_HALF_MIN <= minute <= FIRST_HALF_MAX
            in_second_window = SECOND_HALF_MIN <= minute <= SECOND_HALF_MAX
            if not (in_first_window or in_second_window):
                continue

            home = match.get("teams", {}).get("home", {}).get("name", "HOME")
            away = match.get("teams", {}).get("away", {}).get("name", "AWAY")
            league = match.get("league", {})
            league_name = league.get("name", "Unknown League")

            if is_excluded_match(league_name, home, away):
                continue

            goals = match.get("goals", {})
            gh = goals.get("home", 0)
            ga = goals.get("away", 0)

            if abs(gh - ga) > MAX_BEHIND_GOALS:
                continue

            # cooldown after goal: normal vs post-goal (6 min only after goal)
            now = time.time()
            cur_score = (gh, ga)

            if fid not in SCORE_STATE:
                SCORE_STATE[fid] = {"score": cur_score, "changed_at": now, "just_scored": False}
            else:
                if cur_score != SCORE_STATE[fid]["score"]:
                    SCORE_STATE[fid] = {"score": cur_score, "changed_at": now, "just_scored": True}

            cooldown = POST_GOAL_COOLDOWN_SECONDS if SCORE_STATE[fid].get("just_scored") else NORMAL_COOLDOWN_SECONDS
            if now - SCORE_STATE[fid]["changed_at"] < cooldown:
                continue
            SCORE_STATE[fid]["just_scored"] = False

            candidates.append(match)

        alerts_sent = 0
        stats_checks = 0

        # Evaluate candidates with stats (limited)
        for match in candidates:
            if alerts_sent >= MAX_ALERTS_PER_LOOP:
                break
            if stats_checks >= MAX_STATS_CHECKS_PER_LOOP:
                break

            fixture = match.get("fixture", {})
            fid = fixture.get("id")
            status_short = fixture.get("status", {}).get("short", "")
            minute = fixture.get("status", {}).get("elapsed") or 0

            home = match.get("teams", {}).get("home", {}).get("name", "HOME")
            away = match.get("teams", {}).get("away", {}).get("name", "AWAY")

            league = match.get("league", {})
            league_name = league.get("name", "Unknown League")
            league_country = league.get("country", "")
            league_full = f"{league_name} ({league_country})"

            goals = match.get("goals", {})
            gh = goals.get("home", 0)
            ga = goals.get("away", 0)

            # fetch statistics
            stats_checks += 1
            stats_response = get_match_statistics(fid)
            if not stats_response or len(stats_response) != 2:
                continue

            home_stats = stats_response[0].get("statistics", [])
            away_stats = stats_response[1].get("statistics", [])

            # totals
            hsot_total = stat(home_stats, "Shots on Goal")
            asot_total = stat(away_stats, "Shots on Goal")
            hshots_total = stat(home_stats, "Total Shots")
            ashots_total = stat(away_stats, "Total Shots")
            hcorn_total = stat(home_stats, "Corner Kicks")
            acorn_total = stat(away_stats, "Corner Kicks")
            hpos_total = stat(home_stats, "Ball Possession")
            apos_total = stat(away_stats, "Ball Possession")
            hred_total = stat(home_stats, "Red Cards")
            ared_total = stat(away_stats, "Red Cards")

            # update rolling history
            update_stats_history(fid, minute, hsot_total, asot_total, hshots_total, ashots_total)

            # pace10 + pace5 + prev5
            h_sot10, a_sot10, h_shots10, a_shots10 = get_pace_deltas(
                fid, minute, hsot_total, asot_total, hshots_total, ashots_total, PACE10_WINDOW_MINUTES
            )
            h_sot5, a_sot5, h_shots5, a_shots5 = get_pace_deltas(
                fid, minute, hsot_total, asot_total, hshots_total, ashots_total, PACE5_WINDOW_MINUTES
            )
            h_prev5_sot = clamp_nonnegative(h_sot10 - h_sot5)
            a_prev5_sot = clamp_nonnegative(a_sot10 - a_sot5)
            h_prev5_shots = clamp_nonnegative(h_shots10 - h_shots5)
            a_prev5_shots = clamp_nonnegative(a_shots10 - a_shots5)

            # halftime snapshot for 2H stats
            if (status_short == "HT" or minute >= 45) and fid not in HALF_TIME_SNAPSHOT:
                HALF_TIME_SNAPSHOT[fid] = {
                    "hsot": hsot_total, "asot": asot_total,
                    "hshots": hshots_total, "ashots": ashots_total,
                    "hcorn": hcorn_total, "acorn": acorn_total,
                }

            in_second_half = minute > 45
            use_half_stats = in_second_half and fid in HALF_TIME_SNAPSHOT

            if use_half_stats:
                snap = HALF_TIME_SNAPSHOT[fid]
                hsot = clamp_nonnegative(hsot_total - snap["hsot"])
                asot = clamp_nonnegative(asot_total - snap["asot"])
                hshots = clamp_nonnegative(hshots_total - snap["hshots"])
                ashots = clamp_nonnegative(ashots_total - snap["ashots"])
                hcorn = clamp_nonnegative(hcorn_total - snap["hcorn"])
                acorn = clamp_nonnegative(acorn_total - snap["acorn"])
                half_text = "2e helft"
            else:
                hsot, asot = hsot_total, asot_total
                hshots, ashots = hshots_total, ashots_total
                hcorn, acorn = hcorn_total, acorn_total
                half_text = "1e helft"

            # red card bonus
            red_adv_home = ared_total - hred_total
            red_adv_away = hred_total - ared_total
            red_bonus_home = max(0, red_adv_home) * RED_CARD_BONUS
            red_bonus_away = max(0, red_adv_away) * RED_CARD_BONUS

            # dominance (no big chances)
            score_home = (
                (hsot - asot) * W_SOT +
                (hshots - ashots) * W_SHOTS +
                (hcorn - acorn) * W_CORNERS +
                ((hpos_total - 50) * W_POSSESSION) +
                red_bonus_home
            )
            score_away = (
                (asot - hsot) * W_SOT +
                (ashots - hshots) * W_SHOTS +
                (acorn - hcorn) * W_CORNERS +
                ((apos_total - 50) * W_POSSESSION) +
                red_bonus_away
            )

            gap = abs(score_home - score_away)

            # dominant side + side pace fields
            if score_home > score_away:
                pick_side = "HOME"
                dom_score = score_home
                opp_sot = asot
                opp_shots = ashots
                sot_diff = hsot - asot
                red_adv = max(0, red_adv_home)
                pace10_sot = h_sot10
                pace10_shots = h_shots10
                pace5_sot = h_sot5
                pace5_shots = h_shots5
                prev5_sot = h_prev5_sot
                prev5_shots = h_prev5_shots
            else:
                pick_side = "AWAY"
                dom_score = score_away
                opp_sot = hsot
                opp_shots = hshots
                sot_diff = asot - hsot
                red_adv = max(0, red_adv_away)
                pace10_sot = a_sot10
                pace10_shots = a_shots10
                pace5_sot = a_sot5
                pace5_shots = a_shots5
                prev5_sot = a_prev5_sot
                prev5_shots = a_prev5_shots

            pick_team = home if pick_side == "HOME" else away

            # never alert if dominant team already leading
            if pick_side == "HOME" and gh > ga:
                continue
            if pick_side == "AWAY" and ga > gh:
                continue

            # =========================
            # SHARPNESS FIXES
            # =========================

            # baseline pace (avoid dead games)
            if pace10_sot < PACE10_MIN_SOT and pace10_shots < PACE10_MIN_SHOTS:
                continue

            # trend filter: last5 should not be weaker than prev5
            if (pace5_sot < prev5_sot + TREND_MIN_SOT_DELTA) and (pace5_shots < prev5_shots + TREND_MIN_SHOTS_DELTA):
                continue

            # late game kill switch
            if minute >= LATE_MINUTE:
                if pace10_sot < LATE_REQUIRE_PACE10_MIN_SOT and pace10_shots < LATE_REQUIRE_PACE10_MIN_SHOTS:
                    continue

            # ✅ NEW: Extra anti-33'-trap — 0-0 after 30' requires STRONG last5 pace
            if minute >= ZEROZERO_GUARD_START and minute <= ZEROZERO_GUARD_END and gh == 0 and ga == 0:
                if pace5_sot < ZEROZERO_MIN_PACE5_SOT and pace5_shots < ZEROZERO_MIN_PACE5_SHOTS:
                    continue

            # confidence
            conf = confidence_score(dom_score, gap, sot_diff, red_adv, pace10_sot, pace10_shots)

            # 1X2 odds
            odd_1x2, book, market = get_odds_1x2_for_side(fid, pick_side, home, away)
            if odd_1x2 is not None and odd_1x2 < ODDS_MIN_1X2:
                continue

            odds_line = (
                f"\n💰 1X2 Odd ({pick_team}): {odd_1x2} ✅"
                if odd_1x2 is not None
                else "\n💰 1X2 Odd: — (odds not available) 🟡"
            )

            red_txt = ""
            if hred_total or ared_total:
                red_txt = f"\n🟥 Red Cards: {hred_total} - {ared_total}"

            # =========================
            # TIER LOGIC
            # =========================
            is_extreme = (
                dom_score >= EXTREME_SCORE and
                gap >= EXTREME_MIN_GAP and
                opp_sot <= EXTREME_MAX_OPP_SOT and
                opp_shots <= EXTREME_MAX_OPP_SHOTS and
                pace10_sot >= EXTREME_PACE10_MIN_SOT and
                pace10_shots >= EXTREME_PACE10_MIN_SHOTS
            )

            is_premium = (
                dom_score >= PREMIUM_MIN_SCORE and
                gap >= PREMIUM_MIN_GAP and
                sot_diff >= PREMIUM_MIN_SOT_DIFF and
                opp_sot <= PREMIUM_MAX_OPP_SOT and
                opp_shots <= PREMIUM_MAX_OPP_SHOTS and
                conf >= PREMIUM_MIN_CONF and
                pace10_sot >= PREMIUM_PACE10_MIN_SOT and
                pace10_shots >= PREMIUM_PACE10_MIN_SHOTS
            )

            is_normal = (
                dom_score >= NORMAL_MIN_SCORE and
                gap >= NORMAL_MIN_GAP and
                not (opp_sot > NORMAL_MAX_OPP_SOT and opp_shots > NORMAL_MAX_OPP_SHOTS)
            )

            # ✅ NEW: 1H minute caps per tier (33'-trap fix)
            if minute <= 45:
                if is_normal and minute > FIRST_HALF_NORMAL_MAX:
                    continue
                if is_premium and minute > FIRST_HALF_PREMIUM_MAX:
                    continue
                if is_extreme and minute > FIRST_HALF_EXTREME_MAX:
                    continue

            if is_extreme:
                tier = "EXTREME"
                title = "🔥🔥 EXTREME NEXT GOAL ALERT"
            elif is_premium:
                tier = "PREMIUM"
                title = "💎💎 PREMIUM NEXT GOAL ALERT"
            elif is_normal:
                tier = "NORMAL"
                title = "⚠️ NEXT GOAL ALERT"
            else:
                continue

            # Send alert
            send_message(
                f"{title} ({half_text})\n\n"
                f"🏆 {league_name} ({league_country})\n"
                f"{home} vs {away}\n"
                f"Minuut: {minute}' | Stand: {gh}-{ga}\n\n"
                f"✅ Confidence: {conf}/100\n"
                f"📏 GAP: {round(gap,1)} | SOT diff: {sot_diff}\n"
                f"🛡️ Opp threat: SOT {opp_sot} | Shots {opp_shots}\n"
                f"⚡ Pace last10m: shots {pace10_shots} | SOT {pace10_sot}\n"
                f"⚡ Pace last5m: shots {pace5_shots} | SOT {pace5_sot}\n"
                f"📉 Prev5m: shots {prev5_shots} | SOT {prev5_sot}\n\n"
                f"📊 Stats ({half_text}):\n"
                f"SOT: {hsot} - {asot}\n"
                f"Shots: {hshots} - {ashots}\n"
                f"Corners: {hcorn} - {acorn}\n"
                f"Possession (totaal): {hpos_total}% - {apos_total}%"
                f"{red_txt}"
                f"{odds_line}\n\n"
                f"🔥 Dominantie score: {round(score_home,1)} - {round(score_away,1)}\n"
                f"➡️ Pick: {pick_team}"
            )

            # Log alert
            log_alert_row([
                datetime.now().isoformat(timespec="seconds"),
                tier,
                fid,
                league_full,
                home,
                away,
                minute,
                f"{gh}-{ga}",
                pick_team,
                round(dom_score, 2),
                round(gap, 2),
                conf,
                odd_1x2 if odd_1x2 is not None else "",
                pace10_shots,
                pace10_sot,
                pace5_shots,
                pace5_sot,
                prev5_shots,
                prev5_sot
            ])

            # Save pending for HIT/MISS
            PENDING[fid] = {
                "tier": tier,
                "home": home,
                "away": away,
                "pick_side": pick_side,
                "pick_team": pick_team,
                "score_at_alert": (gh, ga),
                "league": league_full
            }

            ALERTED_MATCHES.add(fid)
            alerts_sent += 1

        time.sleep(SLEEP_SECONDS)

    except Exception as e:
        msg = str(e)
        if "503" in msg or "429" in msg:
            time.sleep(30)
        else:
            send_message(f"❌ ERROR: {e}")
            time.sleep(60)