import time
import os
import requests
import csv
from datetime import datetime, date, timedelta, timezone
from zoneinfo import ZoneInfo

# =========================
# ENV VARS
# =========================
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
API_KEY = os.getenv("API_FOOTBALL_KEY")

if not BOT_TOKEN or not CHAT_ID or not API_KEY:
    print("❌ ERROR: Missing env vars. Check BOT_TOKEN, CHAT_ID, API_FOOTBALL_KEY")
    raise SystemExit(1)

BASE_URL = "https://v3.football.api-sports.io"
HEADERS = {"x-apisports-key": API_KEY}

# =========================
# BOT SETTINGS
# =========================
TZ_NAME = os.getenv("BOT_TZ", "Europe/Amsterdam")
TZ = ZoneInfo(TZ_NAME)

LOOP_SLEEP_SECONDS = 90

# Alleen alerts sturen in:
# 1e helft: 15-35
# 2e helft: 50-85
FIRST_HALF_MIN = 15
FIRST_HALF_MAX = 35
SECOND_HALF_MIN = 50
SECOND_HALF_MAX = 85

GOAL_COOLDOWN_SECONDS = 90
MAX_BEHIND_GOALS = 2  # dominant team mag max 2 goals achter

# =========================
# TIERS (kwaliteit)
# =========================
# NORMAL = meer volume
NORMAL_MIN_SCORE = 13
NORMAL_MIN_GAP = 18.0
NORMAL_MAX_OPP_SOT = 2
NORMAL_MAX_OPP_SHOTS = 8

# PREMIUM = strenger (betaalwaardig)
PREMIUM_MIN_SCORE = 15
PREMIUM_MIN_GAP = 28.0       # ✅ strenger
PREMIUM_MIN_SOT_DIFF = 2
PREMIUM_MAX_OPP_SOT = 1
PREMIUM_MAX_OPP_SHOTS = 6
PREMIUM_MIN_CONF = 78        # ✅ strenger

# EXTREME = heel zeldzaam (top setups)
EXTREME_SCORE = 24           # ✅ hoger
EXTREME_MIN_GAP = 38.0       # ✅ hoger
EXTREME_MAX_OPP_SOT = 1
EXTREME_MAX_OPP_SHOTS = 6

# =========================
# PACE FILTER (JOUW WENS)
# =========================
# idee: als tempo te laag is -> skip (veel 0-0 / late goals / dood spel)
# Normal = iets losser
NORMAL_MIN_TOTAL_SHOTS_PACE = 7     # dom+opp shots (in half)
NORMAL_MIN_TOTAL_SOT_PACE = 1       # dom+opp SOT (in half)
NORMAL_MIN_DOM_SOT_PACE = 1         # dom SOT

# Premium/Extreme = strenger
PREMIUM_MIN_TOTAL_SHOTS_PACE = 10   # ✅ strenger voor kwaliteit
PREMIUM_MIN_TOTAL_SOT_PACE = 2
PREMIUM_MIN_DOM_SOT_PACE = 2

# =========================
# WEIGHTS (dominantie)
# =========================
W_SOT = 6
W_SHOTS = 1
W_CORNERS = 1
W_POSSESSION = 0.07
W_BIGCHANCES = 3
RED_CARD_BONUS = 6

MIN_DOMINANT_SOT = 2
MIN_DOMINANT_SHOTS = 6

REQUIRE_ODDS = False  # odds nice-to-have

# =========================
# API LIMIT / REQUEST SAVER
# =========================
# minder calls = minder kans op limiet
MAX_STATS_CALLS_PER_LOOP = 8   # max fixtures/statistics per cycle
STATS_CACHE_TTL = 45           # seconden
ODDS_CACHE_TTL = 60            # seconden

# pending checks (fixtures?id=...) zijn duur → throttlen
PENDING_REFRESH_SECONDS = 300  # 1x per 5 min pending "niet-live" check

# =========================
# Blacklist rommel
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
# LOG PATH (Railway Volume support)
# =========================
LOG_DIR = os.getenv("LOG_DIR", ".")
os.makedirs(LOG_DIR, exist_ok=True)

ALERTS_LOG = os.path.join(LOG_DIR, "alerts_log.csv")
RESULTS_LOG = os.path.join(LOG_DIR, "results_log.csv")

# =========================
# STATE
# =========================
TODAY_LOCAL = datetime.now(TZ).date()

ALERTED_MATCHES = set()       # 1 alert per match
HALF_TIME_SNAPSHOT = {}       # fid -> snapshot dict
SCORE_STATE = {}              # fid -> {"score": (gh,ga), "changed_at": epoch}
PENDING = {}                  # fid -> pick dict (voor HIT/MISS)

STATS_CACHE = {}              # fid -> {"at": epoch, "data": stats_response}
ODDS_CACHE = {}               # fid -> {"at": epoch, "data": odds_response}

LAST_PENDING_REFRESH = 0

# =========================
# TELEGRAM
# =========================
def send_message(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": text}
    try:
        requests.post(url, data=payload, timeout=10)
    except:
        pass

# =========================
# API HELPERS (met retries)
# =========================
def api_get(path, params=None, max_retries=4):
    """
    Handles:
    - 429 rate limit
    - 503 server errors
    - quota errors
    """
    backoff = 2
    for attempt in range(max_retries):
        try:
            r = requests.get(f"{BASE_URL}{path}", headers=HEADERS, params=params, timeout=25)
            if r.status_code == 429:
                # rate limit → wachten
                time.sleep(5)
                continue
            if r.status_code in (500, 502, 503, 504):
                time.sleep(backoff)
                backoff *= 2
                continue

            r.raise_for_status()
            data = r.json()

            # quota / token errors van API-Football
            errors = data.get("errors") or {}
            if isinstance(errors, dict):
                # als quota op is krijg je soms "requests" of "token" errors
                if errors.get("requests") or errors.get("token"):
                    raise RuntimeError(f"API quota/token error: {errors}")

            return data

        except Exception as e:
            if attempt == max_retries - 1:
                raise
            time.sleep(backoff)
            backoff *= 2

    raise RuntimeError("api_get failed unexpectedly")

def get_live_matches():
    data = api_get("/fixtures", params={"live": "all"})
    return data.get("response", [])

def get_fixture_by_id(fid):
    data = api_get("/fixtures", params={"id": fid})
    resp = data.get("response", [])
    return resp[0] if resp else None

def get_match_statistics(fid):
    # cache
    now = time.time()
    cached = STATS_CACHE.get(fid)
    if cached and (now - cached["at"] < STATS_CACHE_TTL):
        return cached["data"]

    data = api_get("/fixtures/statistics", params={"fixture": fid})
    resp = data.get("response", [])
    STATS_CACHE[fid] = {"at": now, "data": resp}
    return resp

def get_live_odds(fid):
    # odds zijn duur → cache
    now = time.time()
    cached = ODDS_CACHE.get(fid)
    if cached and (now - cached["at"] < ODDS_CACHE_TTL):
        return cached["data"]

    endpoints = [
        ("/odds/live", {"fixture": fid}),
        ("/odds", {"fixture": fid, "live": "all"}),
        ("/odds", {"fixture": fid}),
    ]
    for path, params in endpoints:
        try:
            data = api_get(path, params=params)
            resp = data.get("response", [])
            if resp:
                ODDS_CACHE[fid] = {"at": now, "data": resp}
                return resp
        except:
            continue

    ODDS_CACHE[fid] = {"at": now, "data": None}
    return None

# =========================
# STATS HELPERS
# =========================
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

def get_big_chances(team_stats_list):
    for key in ["Big Chances", "Big chances", "Big Chances Created", "Big chances created"]:
        v = stat(team_stats_list, key)
        if v != 0:
            return v
    return 0

def clamp_nonnegative(x):
    return x if x > 0 else 0

def is_excluded_match(league_name, home_name, away_name):
    text = f"{league_name} {home_name} {away_name}".lower()
    for kw in EXCLUDE_KEYWORDS:
        if kw.lower() in text:
            return True
    return False

# =========================
# ODDS PARSER
# =========================
def find_next_goal_odds(odds_response, predicted_side, home_name, away_name):
    if not odds_response:
        return None

    market_keywords = [
        "next goal",
        "team to score next",
        "next team to score",
        "next goal scorer (team)",
    ]

    if predicted_side == "HOME":
        want_values = ["home", home_name.lower(), "1"]
    else:
        want_values = ["away", away_name.lower(), "2"]

    for item in odds_response:
        bookmakers = item.get("bookmakers", [])
        for book in bookmakers:
            bets = book.get("bets", [])
            for bet in bets:
                bet_name = (bet.get("name") or "").lower()
                if not any(k in bet_name for k in market_keywords):
                    continue

                values = bet.get("values", [])
                for v in values:
                    v_name = (v.get("value") or "").lower()
                    if any(w in v_name for w in want_values):
                        try:
                            return float(v.get("odd"))
                        except:
                            return None
    return None

# =========================
# CONFIDENCE
# =========================
def confidence_score(dominant_score, gap, sot_diff, red_adv, big_diff):
    score = 0
    score += max(0, (dominant_score - NORMAL_MIN_SCORE) * 6)
    score += min(30, gap * 1.0)
    score += min(25, sot_diff * 10)
    score += min(10, red_adv * 10)
    score += min(10, big_diff * 6)
    return int(max(0, min(100, score)))

# =========================
# CSV LOGGING
# =========================
def ensure_csv_header(file_path, header_cols):
    try:
        with open(file_path, "r", newline="", encoding="utf-8") as f:
            return
    except FileNotFoundError:
        with open(file_path, "w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(header_cols)

def log_alert_row(row):
    ensure_csv_header(ALERTS_LOG, [
        "timestamp", "tier", "fixture_id", "league", "home", "away",
        "minute", "score", "pick", "dominant_score", "gap", "confidence", "odds",
        "hsot_half", "asot_half", "hshots_half", "ashots_half",
        "hcorn_half", "acorn_half", "hbig_half", "abig_half",
        "hred_total", "ared_total"
    ])
    with open(ALERTS_LOG, "a", newline="", encoding="utf-8") as f:
        csv.writer(f).writerow(row)

def log_result_row(row):
    ensure_csv_header(RESULTS_LOG, [
        "timestamp", "fixture_id", "tier", "league", "home", "away",
        "pick", "result", "minute_resolved", "score_at_alert", "score_resolved"
    ])
    with open(RESULTS_LOG, "a", newline="", encoding="utf-8") as f:
        csv.writer(f).writerow(row)

# =========================
# DAILY REPORT (analyseren van wins)
# =========================
def send_daily_report(report_date):
    day_str = report_date.isoformat()

    # Alerts
    day_alerts = []
    try:
        with open(ALERTS_LOG, "r", encoding="utf-8") as f:
            day_alerts = [r for r in csv.DictReader(f) if r["timestamp"].startswith(day_str)]
    except FileNotFoundError:
        day_alerts = []

    # Results
    day_results = []
    try:
        with open(RESULTS_LOG, "r", encoding="utf-8") as f:
            day_results = [r for r in csv.DictReader(f) if r["timestamp"].startswith(day_str)]
    except FileNotFoundError:
        day_results = []

    total_alerts = len(day_alerts)
    hits = sum(1 for r in day_results if r["result"] == "HIT")
    misses = sum(1 for r in day_results if r["result"] == "MISS")
    resolved = len(day_results)
    pending = max(0, total_alerts - resolved)

    hitrate = round((hits / resolved) * 100, 1) if resolved else 0.0

    # Tier stats
    tier_stats = {"NORMAL": {"hit": 0, "miss": 0}, "PREMIUM": {"hit": 0, "miss": 0}, "EXTREME": {"hit": 0, "miss": 0}}
    for r in day_results:
        t = r["tier"]
        if t in tier_stats:
            if r["result"] == "HIT":
                tier_stats[t]["hit"] += 1
            else:
                tier_stats[t]["miss"] += 1

    def tier_rate(t):
        tot = tier_stats[t]["hit"] + tier_stats[t]["miss"]
        return round((tier_stats[t]["hit"] / tot) * 100, 1) if tot else 0.0

    # League hitrates (min 2 results)
    league_map = {}
    for r in day_results:
        league = r.get("league", "Unknown")
        league_map.setdefault(league, {"hit": 0, "miss": 0})
        if r["result"] == "HIT":
            league_map[league]["hit"] += 1
        else:
            league_map[league]["miss"] += 1

    league_rows = []
    for league, v in league_map.items():
        tot = v["hit"] + v["miss"]
        if tot >= 2:
            rate = (v["hit"] / tot) * 100
            league_rows.append((rate, league, v["hit"], v["miss"]))

    league_rows.sort(reverse=True, key=lambda x: x[0])
    top_leagues_text = ""
    if league_rows:
        top3 = league_rows[:3]
        lines = []
        for rate, league, h, m in top3:
            lines.append(f"• {league}: {round(rate,1)}% ({h}-{m})")
        top_leagues_text = "\n".join(lines)
    else:
        top_leagues_text = "• Nog te weinig league-data (min 2 results per league)."

    # Optimalisatie tips (AI vibe / professioneel maar kort)
    tips = []
    prem_rate = tier_rate("PREMIUM")
    norm_rate = tier_rate("NORMAL")

    if prem_rate and prem_rate < 65:
        tips.append("• PREMIUM hitrate laag → maak PREMIUM strenger (PREMIUM_MIN_GAP / PREMIUM_MIN_CONF omhoog).")
        tips.append("• Veel low-tempo misses → pace filter strenger (totaal shots ≥10 + dom SOT ≥2).")
    else:
        tips.append("• Instellingen zijn stabiel ✅ (geen grote aanpassingen nodig vandaag).")

    if norm_rate and norm_rate > 75:
        tips.append("• NORMAL presteert sterk → ideaal voor volume + engagement.")

    send_message(
        f"📊 DAGRAPPORT ({day_str})\n\n"
        f"📌 Alerts: {total_alerts}\n"
        f"✅ HIT: {hits}\n"
        f"❌ MISS: {misses}\n"
        f"⏳ Pending: {pending}\n"
        f"🎯 Hitrate (resolved): {hitrate}%\n\n"
        f"⚠️ NORMAL: {tier_stats['NORMAL']['hit']} | {tier_rate('NORMAL')}%\n"
        f"💎 PREMIUM: {tier_stats['PREMIUM']['hit']} | {tier_rate('PREMIUM')}%\n"
        f"🔥 EXTREME: {tier_stats['EXTREME']['hit']} | {tier_rate('EXTREME')}%\n\n"
        f"🏆 TOP LEAGUES (beste hitrate):\n{top_leagues_text}\n\n"
        f"🤖 Optimalisatie tips:\n" + "\n".join(tips)
    )

# =========================
# HIT/MISS TRACKING
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

    # Goal gevallen -> HIT/MISS
    if goal_home or goal_away:
        scorer = "HOME" if goal_home and not goal_away else "AWAY" if goal_away and not goal_home else ("HOME" if goal_home else "AWAY")
        result = "HIT" if scorer == p["pick_side"] else "MISS"

        send_message(
            f"📌 RESULT ({p['tier']})\n\n"
            f"{p['home']} vs {p['away']}\n"
            f"Pick: {p['pick_team']}\n\n"
            f"{'✅ HIT' if result == 'HIT' else '❌ MISS'} — goal gevallen rond {minute}'\n"
            f"Score: {old_gh}-{old_ga} ➜ {gh}-{ga}"
        )

        log_result_row([
            datetime.now(TZ).isoformat(timespec="seconds"),
            fid,
            p["tier"],
            p.get("league", "Unknown"),
            p["home"],
            p["away"],
            p["pick_team"],
            result,
            minute,
            f"{old_gh}-{old_ga}",
            f"{gh}-{ga}"
        ])

        PENDING.pop(fid, None)
        return

    # Geen goal & wedstrijd klaar -> MISS
    if status_short in ("FT", "AET", "PEN"):
        send_message(
            f"📌 RESULT ({p['tier']})\n\n"
            f"{p['home']} vs {p['away']}\n"
            f"Pick: {p['pick_team']}\n\n"
            f"❌ MISS — geen volgende goal meer gevallen.\n"
            f"Score bleef: {gh}-{ga}"
        )

        log_result_row([
            datetime.now(TZ).isoformat(timespec="seconds"),
            fid,
            p["tier"],
            p.get("league", "Unknown"),
            p["home"],
            p["away"],
            p["pick_team"],
            "MISS",
            minute,
            f"{old_gh}-{old_ga}",
            f"{gh}-{ga}"
        ])

        PENDING.pop(fid, None)
        return

def resolve_pending_not_in_live():
    # Throttle (duur)
    global LAST_PENDING_REFRESH
    now = time.time()
    if now - LAST_PENDING_REFRESH < PENDING_REFRESH_SECONDS:
        return
    LAST_PENDING_REFRESH = now

    for fid in list(PENDING.keys()):
        try:
            match = get_fixture_by_id(fid)
            if match:
                resolve_pending_from_match(match)
        except:
            # als API limiet/503 -> skip
            continue

# =========================
# STARTUP MESSAGE
# =========================
send_message(
    "🟢 Livebets AI Scanner gestart ✅\n\n"
    "• 24/7 live monitoring wereldwijd\n"
    "• Real-time Next Goal detectie (per helft analyse)\n"
    "• NORMAL / PREMIUM / EXTREME\n"
    "• Auto HIT/MISS + dagrapport\n"
    "• Pace-filter actief (anti low-tempo)\n"
)

# =========================
# MAIN LOOP
# =========================
while True:
    try:
        # Daily report wanneer de datum in Amsterdam wisselt
        current_local_date = datetime.now(TZ).date()
        if current_local_date != TODAY_LOCAL:
            # report voor gisteren
            send_daily_report(TODAY_LOCAL)

            # reset
            TODAY_LOCAL = current_local_date
            ALERTED_MATCHES.clear()
            HALF_TIME_SNAPSHOT.clear()
            SCORE_STATE.clear()
            STATS_CACHE.clear()
            ODDS_CACHE.clear()

            send_message("🔄 Nieuwe dag — reset uitgevoerd ✅")

        matches = get_live_matches()
        match_map = {m.get("fixture", {}).get("id"): m for m in matches if m.get("fixture", {}).get("id")}

        # 1) Pending results afhandelen voor live matches
        for fid, m in list(match_map.items()):
            if fid in PENDING:
                resolve_pending_from_match(m)

        # 2) Pending die niet meer live is (throttled)
        if PENDING:
            resolve_pending_not_in_live()

        # 3) Nieuwe alerts zoeken (API saver)
        stats_calls = 0

        for match in matches:
            fixture = match.get("fixture", {})
            fid = fixture.get("id")
            if not fid:
                continue

            if fid in ALERTED_MATCHES:
                continue

            status_short = fixture.get("status", {}).get("short", "")
            if status_short in ("FT", "AET", "PEN", "CANC", "PST", "ABD", "AWD", "WO"):
                continue

            minute = fixture.get("status", {}).get("elapsed")
            if not minute:
                continue

            # Time windows
            in_first_window = FIRST_HALF_MIN <= minute <= FIRST_HALF_MAX
            in_second_window = SECOND_HALF_MIN <= minute <= SECOND_HALF_MAX
            if not (in_first_window or in_second_window):
                continue

            home = match.get("teams", {}).get("home", {}).get("name", "HOME")
            away = match.get("teams", {}).get("away", {}).get("name", "AWAY")

            league = match.get("league", {})
            league_name = league.get("name", "Unknown League")
            league_country = league.get("country", "")

            if is_excluded_match(league_name, home, away):
                continue

            # score
            goals = match.get("goals", {})
            gh = goals.get("home", 0)
            ga = goals.get("away", 0)

            if abs(gh - ga) > MAX_BEHIND_GOALS:
                continue

            # cooldown na goal
            now = time.time()
            cur_score = (gh, ga)

            if fid not in SCORE_STATE:
                SCORE_STATE[fid] = {"score": cur_score, "changed_at": now}
            else:
                if cur_score != SCORE_STATE[fid]["score"]:
                    SCORE_STATE[fid] = {"score": cur_score, "changed_at": now}

            if now - SCORE_STATE[fid]["changed_at"] < GOAL_COOLDOWN_SECONDS:
                continue

            # API saver: max statistics calls per loop
            if stats_calls >= MAX_STATS_CALLS_PER_LOOP:
                break

            stats_response = get_match_statistics(fid)
            stats_calls += 1

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

            hbig_total = get_big_chances(home_stats)
            abig_total = get_big_chances(away_stats)

            hred_total = stat(home_stats, "Red Cards")
            ared_total = stat(away_stats, "Red Cards")

            # halftime snapshot
            if (status_short == "HT" or minute >= 45) and fid not in HALF_TIME_SNAPSHOT:
                HALF_TIME_SNAPSHOT[fid] = {
                    "home": {"sot": hsot_total, "shots": hshots_total, "corn": hcorn_total, "big": hbig_total},
                    "away": {"sot": asot_total, "shots": ashots_total, "corn": acorn_total, "big": abig_total},
                }

            # per half stats
            in_second_half = minute > 45
            use_half_stats = in_second_half and fid in HALF_TIME_SNAPSHOT

            if use_half_stats:
                snap = HALF_TIME_SNAPSHOT[fid]
                hsot = clamp_nonnegative(hsot_total - snap["home"]["sot"])
                asot = clamp_nonnegative(asot_total - snap["away"]["sot"])

                hshots = clamp_nonnegative(hshots_total - snap["home"]["shots"])
                ashots = clamp_nonnegative(ashots_total - snap["away"]["shots"])

                hcorn = clamp_nonnegative(hcorn_total - snap["home"]["corn"])
                acorn = clamp_nonnegative(acorn_total - snap["away"]["corn"])

                hbig = clamp_nonnegative(hbig_total - snap["home"]["big"])
                abig = clamp_nonnegative(abig_total - snap["away"]["big"])
                half_text = "2e helft"
            else:
                hsot, asot = hsot_total, asot_total
                hshots, ashots = hshots_total, ashots_total
                hcorn, acorn = hcorn_total, acorn_total
                hbig, abig = hbig_total, abig_total
                half_text = "1e helft"

            # red card bonus
            red_adv_home = ared_total - hred_total
            red_adv_away = hred_total - ared_total
            red_bonus_home = max(0, red_adv_home) * RED_CARD_BONUS
            red_bonus_away = max(0, red_adv_away) * RED_CARD_BONUS

            # dominance scores
            score_home = (
                (hsot - asot) * W_SOT +
                (hshots - ashots) * W_SHOTS +
                (hcorn - acorn) * W_CORNERS +
                (hbig - abig) * W_BIGCHANCES +
                ((hpos_total - 50) * W_POSSESSION) +
                red_bonus_home
            )
            score_away = (
                (asot - hsot) * W_SOT +
                (ashots - hshots) * W_SHOTS +
                (acorn - hcorn) * W_CORNERS +
                (abig - hbig) * W_BIGCHANCES +
                ((apos_total - 50) * W_POSSESSION) +
                red_bonus_away
            )

            gap = abs(score_home - score_away)

            # dominant side
            if score_home > score_away:
                pick_side = "HOME"
                dom_score = score_home
                dom_sot = hsot
                dom_shots = hshots
                opp_sot = asot
                opp_shots = ashots
                sot_diff = hsot - asot
                red_adv = max(0, red_adv_home)
                big_diff = hbig - abig
            else:
                pick_side = "AWAY"
                dom_score = score_away
                dom_sot = asot
                dom_shots = ashots
                opp_sot = hsot
                opp_shots = hshots
                sot_diff = asot - hsot
                red_adv = max(0, red_adv_away)
                big_diff = abig - hbig

            # NOOIT alert als dominant team VOOR staat
            if pick_side == "HOME" and gh > ga:
                continue
            if pick_side == "AWAY" and ga > gh:
                continue

            # Minimum dreiging dominant team
            if dom_sot < MIN_DOMINANT_SOT and dom_shots < MIN_DOMINANT_SHOTS:
                continue

            # ✅ PACE FILTER (verschilt per tier, maar we checken eerst normal thresholds)
            total_shots_half = hshots + ashots
            total_sot_half = hsot + asot

            if total_shots_half < NORMAL_MIN_TOTAL_SHOTS_PACE:
                continue
            if total_sot_half < NORMAL_MIN_TOTAL_SOT_PACE:
                continue
            if dom_sot < NORMAL_MIN_DOM_SOT_PACE:
                continue

            conf = confidence_score(dom_score, gap, sot_diff, red_adv, big_diff)

            pick_team = home if pick_side == "HOME" else away

            # Odds alleen proberen (nice-to-have)
            picked_odds = None
            try:
                odds_response = get_live_odds(fid)
                picked_odds = find_next_goal_odds(odds_response, pick_side, home, away)
            except:
                picked_odds = None

            if picked_odds is None and REQUIRE_ODDS:
                continue

            odds_line = (
                f"\n💰 Next Goal Odds: {picked_odds} ✅"
                if picked_odds is not None
                else "\n💰 Next Goal Odds: — (SLOT: check BetCity) 🟡"
            )

            # Opponent threat filter (strakker dan jouw oude AND)
            def opp_ok(max_sot, max_shots):
                return (opp_sot <= max_sot) and (opp_shots <= max_shots)

            # Tier bepalen (EXTREME > PREMIUM > NORMAL)
            is_extreme = (
                dom_score >= EXTREME_SCORE and
                gap >= EXTREME_MIN_GAP and
                opp_ok(EXTREME_MAX_OPP_SOT, EXTREME_MAX_OPP_SHOTS) and
                total_shots_half >= PREMIUM_MIN_TOTAL_SHOTS_PACE and
                total_sot_half >= PREMIUM_MIN_TOTAL_SOT_PACE and
                dom_sot >= PREMIUM_MIN_DOM_SOT_PACE
            )

            is_premium = (
                dom_score >= PREMIUM_MIN_SCORE and
                gap >= PREMIUM_MIN_GAP and
                sot_diff >= PREMIUM_MIN_SOT_DIFF and
                opp_ok(PREMIUM_MAX_OPP_SOT, PREMIUM_MAX_OPP_SHOTS) and
                conf >= PREMIUM_MIN_CONF and
                total_shots_half >= PREMIUM_MIN_TOTAL_SHOTS_PACE and
                total_sot_half >= PREMIUM_MIN_TOTAL_SOT_PACE and
                dom_sot >= PREMIUM_MIN_DOM_SOT_PACE
            )

            is_normal = (
                dom_score >= NORMAL_MIN_SCORE and
                gap >= NORMAL_MIN_GAP and
                opp_ok(NORMAL_MAX_OPP_SOT, NORMAL_MAX_OPP_SHOTS)
            )

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

            red_txt = ""
            if hred_total or ared_total:
                red_txt = f"\n🟥 Red Cards: {hred_total} - {ared_total}"

            send_message(
                f"{title} ({half_text})\n\n"
                f"🏆 {league_name} ({league_country})\n"
                f"{home} vs {away}\n"
                f"Minuut: {minute}' | Stand: {gh}-{ga}\n\n"
                f"✅ Confidence: {conf}/100\n"
                f"📏 GAP: {round(gap,1)} | SOT diff: {sot_diff}\n"
                f"🛡️ Opp threat: SOT {opp_sot} | Shots {opp_shots}\n"
                f"⚡ Pace: shots {total_shots_half} | SOT {total_sot_half}\n\n"
                f"📊 Stats ({half_text}):\n"
                f"SOT: {hsot} - {asot}\n"
                f"Shots: {hshots} - {ashots}\n"
                f"Corners: {hcorn} - {acorn}\n"
                f"Big Chances: {hbig} - {abig}\n"
                f"Possession (totaal): {hpos_total}% - {apos_total}%"
                f"{red_txt}"
                f"{odds_line}\n\n"
                f"🔥 Dominantie score: {round(score_home,1)} - {round(score_away,1)}\n"
                f"➡️ Pick: {pick_team}"
            )

            # Log alert
            log_alert_row([
                datetime.now(TZ).isoformat(timespec="seconds"),
                tier,
                fid,
                f"{league_name} ({league_country})",
                home,
                away,
                minute,
                f"{gh}-{ga}",
                pick_team,
                round(dom_score, 2),
                round(gap, 2),
                conf,
                picked_odds if picked_odds is not None else "",
                hsot, asot,
                hshots, ashots,
                hcorn, acorn,
                hbig, abig,
                hred_total, ared_total
            ])

            # Pending voor HIT/MISS
            PENDING[fid] = {
                "tier": tier,
                "league": f"{league_name} ({league_country})",
                "home": home,
                "away": away,
                "pick_side": pick_side,
                "pick_team": pick_team,
                "score_at_alert": (gh, ga),
            }

            ALERTED_MATCHES.add(fid)

            # Anti spam: 1 alert per loop
            break

        time.sleep(LOOP_SLEEP_SECONDS)

    except Exception as e:
        # Als quota op is / 503 -> bot blijft leven
        err = str(e)
        send_message(f"❌ ERROR: {err}")
        time.sleep(70)
