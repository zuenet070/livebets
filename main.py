import time
import os
import requests
import csv
from datetime import date, datetime, timedelta

# =========================================================
# ENV VARS
# =========================================================
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
API_KEY = os.getenv("API_FOOTBALL_KEY")

if not BOT_TOKEN or not CHAT_ID or not API_KEY:
    print("❌ ERROR: Missing env vars. Check BOT_TOKEN, CHAT_ID, API_FOOTBALL_KEY")
    raise SystemExit(1)

BASE_URL = "https://v3.football.api-sports.io"
HEADERS = {"x-apisports-key": API_KEY}

# =========================================================
# TIME WINDOWS (LESS STRICT)
# =========================================================
FIRST_HALF_MIN = 15
FIRST_HALF_MAX = 42   # was 35 → ruimer zodat je 33-45 niet mist
SECOND_HALF_MIN = 50
SECOND_HALF_MAX = 85

# Risk window (30-39): minder streng dan eerst
EARLY_RISK_START = 30
EARLY_RISK_END = 39

# Goal cooldown (jouw wens: 6 min)
GOAL_COOLDOWN_SECONDS = 360
# Extra streng na goal (nu milder / slimmer)
POST_GOAL_STRICT_UNTIL_SECONDS = 480  # was 600 → 8 min

# Score regels
MAX_BEHIND_GOALS = 2  # dominant team mag max 2 goals achter

# =========================================================
# TIERS (basis)
# =========================================================
NORMAL_MIN_SCORE = 13
NORMAL_MIN_GAP = 18.0
NORMAL_MAX_OPP_SOT = 2
NORMAL_MAX_OPP_SHOTS = 8

PREMIUM_MIN_SCORE = 15
PREMIUM_MIN_GAP = 24.0
PREMIUM_MIN_SOT_DIFF = 2
PREMIUM_MAX_OPP_SOT = 1
PREMIUM_MAX_OPP_SHOTS = 6
PREMIUM_MIN_CONF = 70

EXTREME_SCORE = 21
EXTREME_MIN_GAP = 32.0
EXTREME_MAX_OPP_SOT = 1
EXTREME_MAX_OPP_SHOTS = 6

# =========================================================
# Weights (Big Chances verwijderd)
# =========================================================
W_SOT = 6
W_SHOTS = 1
W_CORNERS = 1
W_POSSESSION = 0.07
RED_CARD_BONUS = 6

# =========================================================
# Pace regels (LESS STRICT)
# =========================================================
# 1e helft (na 20’)
PACE1_MIN_SHOTS_10 = 6  # was 7
PACE1_MIN_SHOTS_5 = 2   # was 3
PACE1_MIN_SOT_10 = 1    # was 2

# 2e helft
PACE2_MIN_SHOTS_10 = 7  # was 8
PACE2_MIN_SHOTS_5 = 2   # was 3
PACE2_MIN_SOT_10 = 1    # was 2

# Late game (75+)
LATE_MINUTE = 75
LATE_MIN_SOT_DIFF = 2
LATE_MIN_SHOTS_10 = 7   # was 8
LATE_MAX_OPP_SOT = 2    # was 1 (iets soepeler)
LATE_MIN_ODD = 1.55     # was 1.6 (iets soepeler)

# Odds filter (1X2)
ODD_MIN = 1.5
REQUIRE_ODDS = False

# =========================================================
# Blacklist rommel
# =========================================================
EXCLUDE_KEYWORDS = [
    "U21", "U20", "U19", "U18", "U17", "U16",
    "Youth", "Junior",
    "Reserves", "Reserve", "B Team", "B-team", "II",
    "Women", "Womens", "Fem", "Dames",
    "Futsal",
    "Esports", "E-sports", "Virtual",
]

# =========================================================
# STATE
# =========================================================
TODAY = date.today()

ALERTED_MATCHES = set()
HALF_TIME_SNAPSHOT = {}
SCORE_STATE = {}  # fid -> {"score": (gh,ga), "changed_at": epoch}

# Rolling history for pace
HISTORY = {}  # fid -> list of snapshots dicts

# Pending alerts for HIT/MISS tracking
PENDING = {}  # fid -> dict

# CSV logging
ALERTS_LOG = "alerts_log_premium.csv"
RESULTS_LOG = "results_log_premium.csv"
WEEKLY_SUMMARY_LOG = "weekly_summary.csv"

# =========================================================
# BASIC HELPERS
# =========================================================
def send_message(text: str):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": text}
    try:
        requests.post(url, data=payload, timeout=10)
    except:
        pass

def api_get(path, params=None):
    r = requests.get(f"{BASE_URL}{path}", headers=HEADERS, params=params, timeout=25)
    r.raise_for_status()
    return r.json()

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

def safe_float(v):
    if v is None:
        return None
    try:
        return float(v)
    except:
        return None

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

def cleanup_finished(fid):
    HALF_TIME_SNAPSHOT.pop(fid, None)
    SCORE_STATE.pop(fid, None)
    PENDING.pop(fid, None)
    HISTORY.pop(fid, None)

# =========================================================
# HISTORY / PACE
# =========================================================
def update_history(fid, minute, hsot, asot, hshots, ashots, hcorn, acorn):
    if fid not in HISTORY:
        HISTORY[fid] = []
    hist = HISTORY[fid]
    if hist and hist[-1]["minute"] == minute:
        hist[-1] = {"minute": minute, "hsot": hsot, "asot": asot, "hshots": hshots, "ashots": ashots, "hcorn": hcorn, "acorn": acorn}
    else:
        hist.append({"minute": minute, "hsot": hsot, "asot": asot, "hshots": hshots, "ashots": ashots, "hcorn": hcorn, "acorn": acorn})
    if len(hist) > 80:
        HISTORY[fid] = hist[-80:]

def get_snapshot_at_or_before(hist, target_minute):
    best = None
    for row in reversed(hist):
        if row["minute"] <= target_minute:
            best = row
            break
    return best

def pace_last_window(fid, cur_minute, window_minutes, pick_side):
    hist = HISTORY.get(fid, [])
    if not hist or cur_minute is None:
        return (0, 0)

    start_minute = max(0, cur_minute - window_minutes)
    cur = get_snapshot_at_or_before(hist, cur_minute)
    old = get_snapshot_at_or_before(hist, start_minute)
    if not cur or not old:
        return (0, 0)

    if pick_side == "HOME":
        shots = clamp_nonnegative(cur["hshots"] - old["hshots"])
        sot = clamp_nonnegative(cur["hsot"] - old["hsot"])
    else:
        shots = clamp_nonnegative(cur["ashots"] - old["ashots"])
        sot = clamp_nonnegative(cur["asot"] - old["asot"])

    return (shots, sot)

# =========================================================
# ODDS (1X2)
# =========================================================
def get_live_odds(fixture_id):
    endpoints = [
        ("/odds/live", {"fixture": fixture_id}),
        ("/odds", {"fixture": fixture_id, "live": "all"}),
        ("/odds", {"fixture": fixture_id}),
    ]
    for path, params in endpoints:
        try:
            data = api_get(path, params=params)
            resp = data.get("response", [])
            if resp:
                return resp
        except:
            continue
    return None

def find_1x2_odd(odds_response, pick_side, home_name, away_name):
    if not odds_response:
        return None

    market_keywords = ["match winner", "1x2", "full time result", "winner"]
    want = "1" if pick_side == "HOME" else "2"

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
                    v_value = (v.get("value") or "").strip().lower()
                    if v_value == want or (want == "1" and v_value in ["home", home_name.lower()]) or (want == "2" and v_value in ["away", away_name.lower()]):
                        return safe_float(v.get("odd"))
    return None

# =========================================================
# CONFIDENCE (pace-leidend)
# =========================================================
def confidence_score(gap, sot_diff_total, opp_sot, pace10_shots, pace5_shots, pace10_sot, odd_value):
    score = 0

    # Pace
    if pace10_shots >= 8:
        score += 20
    elif pace10_shots >= 6:
        score += 12

    if pace5_shots >= 4:
        score += 20
    elif pace5_shots >= 3:
        score += 12
    elif pace5_shots >= 2:
        score += 6

    if pace10_sot >= 2:
        score += 20
    elif pace10_sot == 1:
        score += 10

    # Dominantie/druk
    score += min(20, max(0, gap) * 0.6)
    score += min(10, max(0, sot_diff_total) * 3)

    # Opp threat
    if opp_sot == 0:
        score += 15
    elif opp_sot == 1:
        score += 8

    # Odds (bonus)
    if odd_value is not None:
        if odd_value >= 2.0:
            score += 10
        elif odd_value >= 1.7:
            score += 6
        elif odd_value >= 1.5:
            score += 3

    return int(max(0, min(100, score)))

# =========================================================
# LOGGING (maakt bestanden zelf)
# =========================================================
def ensure_csv_header(file_path, header_cols):
    try:
        with open(file_path, "r", newline="", encoding="utf-8") as _:
            return
    except FileNotFoundError:
        with open(file_path, "w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(header_cols)

def log_alert_row(row):
    ensure_csv_header(ALERTS_LOG, [
        "timestamp", "tier", "fixture_id", "league", "home", "away",
        "minute", "score", "pick", "dominant_score", "gap", "confidence", "odd_1x2",
        "pace10_shots", "pace10_sot", "pace5_shots", "pace5_sot",
        "sot_half", "shots_half", "opp_sot_half", "opp_shots_half",
        "is_risk_31_39", "post_goal_strict"
    ])
    with open(ALERTS_LOG, "a", newline="", encoding="utf-8") as f:
        csv.writer(f).writerow(row)

def log_result_row(row):
    ensure_csv_header(RESULTS_LOG, [
        "timestamp", "fixture_id", "tier", "home", "away", "pick", "result",
        "minute_resolved", "score_at_alert", "score_resolved"
    ])
    with open(RESULTS_LOG, "a", newline="", encoding="utf-8") as f:
        csv.writer(f).writerow(row)

# =========================================================
# DAILY REPORT (zonder leagues)
# =========================================================
def send_daily_report(report_date):
    day_str = report_date.isoformat()

    normal_count = premium_count = extreme_count = 0
    total_alerts = 0
    try:
        with open(ALERTS_LOG, "r", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        day_rows = [r for r in rows if r["timestamp"].startswith(day_str)]
        normal_count = sum(1 for r in day_rows if r["tier"] == "NORMAL")
        premium_count = sum(1 for r in day_rows if r["tier"] == "PREMIUM")
        extreme_count = sum(1 for r in day_rows if r["tier"] == "EXTREME")
        total_alerts = len(day_rows)
    except FileNotFoundError:
        pass

    hits = misses = 0
    total_results = 0
    try:
        with open(RESULTS_LOG, "r", encoding="utf-8") as f:
            rrows = list(csv.DictReader(f))
        day_rrows = [r for r in rrows if r["timestamp"].startswith(day_str)]
        hits = sum(1 for r in day_rrows if r["result"] == "HIT")
        misses = sum(1 for r in day_rrows if r["result"] == "MISS")
        total_results = len(day_rrows)
    except FileNotFoundError:
        pass

    hitrate = round((hits / total_results) * 100, 1) if total_results else 0.0

    send_message(
        f"📊 DAGRAPPORT ({day_str})\n\n"
        f"📌 Alerts: {total_alerts}\n"
        f"✅ HIT: {hits}\n"
        f"❌ MISS: {misses}\n"
        f"🎯 Hitrate (resolved): {hitrate}%\n\n"
        f"⚠️ NORMAL: {normal_count}\n"
        f"💎 PREMIUM: {premium_count}\n"
        f"🔥 EXTREME: {extreme_count}\n\n"
        f"🤖 Optimalisatie tips:\n"
        f"• Minder strenge 1e helft + milde risk window + pace iets lager ✅\n"
        f"• 6 min goal cooldown + milde post-goal strict ✅"
    )

# =========================================================
# WEEKLY REPORT (in dezelfde file → geen module error)
# =========================================================
def _read_csv_rows(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return list(csv.DictReader(f))
    except FileNotFoundError:
        return []

def build_weekly_report(days=7):
    since = datetime.now() - timedelta(days=days)
    alerts = _read_csv_rows(ALERTS_LOG)
    results = _read_csv_rows(RESULTS_LOG)

    # filter resolved results laatste X dagen
    results_recent = []
    for r in results:
        try:
            ts = datetime.fromisoformat(r["timestamp"])
            if ts >= since:
                results_recent.append(r)
        except:
            continue

    if not results_recent:
        return ("📊 WEEKRAPPORT\nGeen (resolved) data in de laatste 7 dagen.", None)

    total = len(results_recent)
    hits = sum(1 for r in results_recent if r.get("result") == "HIT")
    misses = sum(1 for r in results_recent if r.get("result") == "MISS")
    hitrate = round((hits / total) * 100, 1) if total else 0.0

    # tier stats (op results)
    def tier_counts(tier):
        rows = [r for r in results_recent if r.get("tier") == tier]
        t = len(rows)
        h = sum(1 for r in rows if r.get("result") == "HIT")
        hr = round((h / t) * 100, 1) if t else 0.0
        return t, hr

    n_cnt, n_hr = tier_counts("NORMAL")
    p_cnt, p_hr = tier_counts("PREMIUM")
    e_cnt, e_hr = tier_counts("EXTREME")

    # risk window + post-goal strict analyse (op alerts, gekoppeld via fixture_id)
    alerts_map = {}
    for a in alerts:
        fid = a.get("fixture_id")
        if fid:
            alerts_map[fid] = a

    risk_cnt = risk_hit = 0
    pg_cnt = pg_hit = 0
    late_cnt = late_hit = 0

    for r in results_recent:
        fid = r.get("fixture_id")
        a = alerts_map.get(fid)
        if not a:
            continue

        try:
            minute_alert = int(float(a.get("minute", "0")))
        except:
            minute_alert = 0

        is_risk = (a.get("is_risk_31_39") == "1")
        is_pg = (a.get("post_goal_strict") == "1")
        is_late = (minute_alert >= LATE_MINUTE)

        if is_risk:
            risk_cnt += 1
            if r.get("result") == "HIT":
                risk_hit += 1

        if is_pg:
            pg_cnt += 1
            if r.get("result") == "HIT":
                pg_hit += 1

        if is_late:
            late_cnt += 1
            if r.get("result") == "HIT":
                late_hit += 1

    def pct(h, t):
        return round((h / t) * 100, 1) if t else 0.0

    risk_hr = pct(risk_hit, risk_cnt)
    pg_hr = pct(pg_hit, pg_cnt)
    late_hr = pct(late_hit, late_cnt)

    # simpele tips
    tips = []
    if risk_cnt >= 5 and risk_hr < hitrate:
        tips.append("• 31–39 min window: nog steeds tricky → eventueel SOT diff eis +1 of conf +5.")
    if pg_cnt >= 5 and pg_hr < hitrate:
        tips.append("• Post-goal window: teveel false momentum → maak post-goal strict weer iets strenger.")
    if late_cnt >= 5 and late_hr < hitrate:
        tips.append("• Late-game misses: verhoog LATE_MIN_SHOTS_10 of LATE_MIN_ODD iets.")

    if not tips:
        tips.append("• Instellingen lijken stabiel ✅ (kleine tweaks pas na meer data).")

    week_ending = datetime.now().date().isoformat()

    text = (
        f"📊 WEEKRAPPORT (laatste {days} dagen)\n"
        f"Week ending: {week_ending}\n\n"
        f"📌 Resolved alerts: {total}\n"
        f"✅ HIT: {hits}\n"
        f"❌ MISS: {misses}\n"
        f"🎯 Hitrate: {hitrate}%\n\n"
        f"⚠️ NORMAL: {n_cnt} | {n_hr}%\n"
        f"💎 PREMIUM: {p_cnt} | {p_hr}%\n"
        f"🔥 EXTREME: {e_cnt} | {e_hr}%\n\n"
        f"🧪 Checks:\n"
        f"🧨 Risk window 31–39: {risk_cnt} | {risk_hr}%\n"
        f"⏱️ Post-goal strict: {pg_cnt} | {pg_hr}%\n"
        f"🕯️ Late-game (75+): {late_cnt} | {late_hr}%\n\n"
        f"🤖 Optimalisatie tips:\n" + "\n".join(tips)
    )

    # weekly summary csv row
    summary_row = [
        week_ending, days, total, hits, misses, hitrate,
        n_cnt, n_hr, p_cnt, p_hr, e_cnt, e_hr,
        risk_cnt, risk_hr, pg_cnt, pg_hr, late_cnt, late_hr
    ]

    return (text, summary_row)

def log_weekly_summary(row):
    ensure_csv_header(WEEKLY_SUMMARY_LOG, [
        "week_ending", "days", "resolved_total", "hits", "misses", "hitrate",
        "normal_cnt", "normal_hr", "premium_cnt", "premium_hr", "extreme_cnt", "extreme_hr",
        "risk_cnt", "risk_hr", "post_goal_cnt", "post_goal_hr", "late_cnt", "late_hr"
    ])
    with open(WEEKLY_SUMMARY_LOG, "a", newline="", encoding="utf-8") as f:
        csv.writer(f).writerow(row)

def maybe_send_weekly_report():
    # 1x per dag checken; sturen op maandag 12:00 (server time)
    now = datetime.now()
    if now.weekday() != 0:  # maandag = 0
        return
    if not (11 <= now.hour <= 12):  # venster 11:00-12:59 om 1x te pakken
        return

    # voorkom dubbel sturen: check of er vandaag al een weekly_summary row is
    today_str = now.date().isoformat()
    rows = _read_csv_rows(WEEKLY_SUMMARY_LOG)
    if any(r.get("week_ending") == today_str for r in rows):
        return

    text, row = build_weekly_report(days=7)
    send_message(text)
    if row:
        log_weekly_summary(row)

# =========================================================
# HIT/MISS TRACKING
# =========================================================
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
            datetime.now().isoformat(timespec="seconds"),
            fid, p["tier"], p["home"], p["away"], p["pick_team"],
            result, minute, f"{old_gh}-{old_ga}", f"{gh}-{ga}"
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
            fid, p["tier"], p["home"], p["away"], p["pick_team"],
            "MISS", minute, f"{old_gh}-{old_ga}", f"{gh}-{ga}"
        ])

        PENDING.pop(fid, None)
        cleanup_finished(fid)
        return

def resolve_pending_not_in_live():
    for fid in list(PENDING.keys()):
        match = get_fixture_by_id(fid)
        if match:
            resolve_pending_from_match(match)

# =========================================================
# START
# =========================================================
send_message("🟢 Bot gestart – logging + WEEKRAPPORT + minder strenge filters ✅")

# =========================================================
# MAIN LOOP
# =========================================================
while True:
    try:
        # weekly report check (maandag)
        maybe_send_weekly_report()

        # new day -> report yesterday
        if date.today() != TODAY:
            yesterday = TODAY
            send_daily_report(yesterday)

            TODAY = date.today()
            ALERTED_MATCHES.clear()
            HALF_TIME_SNAPSHOT.clear()
            SCORE_STATE.clear()
            PENDING.clear()
            HISTORY.clear()

            send_message("🔄 Nieuwe dag — reset uitgevoerd ✅")

        matches = get_live_matches()
        match_map = {m.get("fixture", {}).get("id"): m for m in matches if m.get("fixture", {}).get("id")}

        # 1) pending results
        for fid, m in list(match_map.items()):
            if fid in PENDING:
                resolve_pending_from_match(m)

        if PENDING:
            resolve_pending_not_in_live()

        # 2) nieuwe alerts zoeken
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
            if minute is None:
                continue

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

            # cooldown na score change
            now = time.time()
            cur_score = (gh, ga)

            if fid not in SCORE_STATE:
                SCORE_STATE[fid] = {"score": cur_score, "changed_at": now}
            else:
                if cur_score != SCORE_STATE[fid]["score"]:
                    SCORE_STATE[fid] = {"score": cur_score, "changed_at": now}

            since_change = now - SCORE_STATE[fid]["changed_at"]
            if since_change < GOAL_COOLDOWN_SECONDS:
                continue

            # stats
            stats_response = get_match_statistics(fid)
            if not stats_response or len(stats_response) != 2:
                continue

            home_stats = stats_response[0].get("statistics", [])
            away_stats = stats_response[1].get("statistics", [])

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

            # pace history
            update_history(fid, minute, hsot_total, asot_total, hshots_total, ashots_total, hcorn_total, acorn_total)

            # halftime snapshot
            if (status_short == "HT" or minute >= 45) and fid not in HALF_TIME_SNAPSHOT:
                HALF_TIME_SNAPSHOT[fid] = {
                    "home": {"sot": hsot_total, "shots": hshots_total, "corn": hcorn_total},
                    "away": {"sot": asot_total, "shots": ashots_total, "corn": acorn_total},
                }

            # per-half stats
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

            # dominance score
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

            # dominant side
            if score_home > score_away:
                pick_side = "HOME"
                dom_score = score_home
                dom_sot = hsot
                dom_shots = hshots
                opp_sot = asot
                opp_shots = ashots
                sot_diff = hsot - asot
            else:
                pick_side = "AWAY"
                dom_score = score_away
                dom_sot = asot
                dom_shots = ashots
                opp_sot = hsot
                opp_shots = hshots
                sot_diff = asot - hsot

            # Geen alert als dominant team VOOR staat
            if pick_side == "HOME" and gh > ga:
                continue
            if pick_side == "AWAY" and ga > gh:
                continue

            # comeback max 2 goals
            if pick_side == "HOME" and (ga - gh) > MAX_BEHIND_GOALS:
                continue
            if pick_side == "AWAY" and (gh - ga) > MAX_BEHIND_GOALS:
                continue

            # Risk window 30-39: minder streng (basisfilter)
            is_risk = 1 if (EARLY_RISK_START <= minute <= EARLY_RISK_END) else 0
            if is_risk:
                if abs(sot_diff) < 3:   # was 4
                    continue

            # Pace (dominant team)
            pace10_shots, pace10_sot = pace_last_window(fid, minute, 10, pick_side)
            pace5_shots, pace5_sot = pace_last_window(fid, minute, 5, pick_side)
            prev5_shots, prev5_sot = pace_last_window(fid, minute - 5 if minute >= 5 else minute, 5, pick_side)

            # Pace rules
            if minute >= 20 and not in_second_half:
                if pace10_shots < PACE1_MIN_SHOTS_10:
                    continue
                if pace5_shots < PACE1_MIN_SHOTS_5:
                    continue
                if pace10_sot < PACE1_MIN_SOT_10:
                    continue

            if in_second_half:
                if pace10_shots < PACE2_MIN_SHOTS_10:
                    continue
                if pace5_shots < PACE2_MIN_SHOTS_5:
                    continue
                if pace10_sot < PACE2_MIN_SOT_10:
                    continue

            # Post-goal strict: milder & slimmer (alleen skip als zowel sot_diff als pace5 zwak is)
            post_goal_strict = 1 if (GOAL_COOLDOWN_SECONDS <= since_change < POST_GOAL_STRICT_UNTIL_SECONDS) else 0
            if post_goal_strict:
                if abs(sot_diff) < 2 and pace5_shots < 3:
                    continue

            # Late game filter (iets soepeler)
            if minute >= LATE_MINUTE:
                if abs(sot_diff) < LATE_MIN_SOT_DIFF:
                    continue
                if pace10_shots < LATE_MIN_SHOTS_10:
                    continue
                if opp_sot > LATE_MAX_OPP_SOT:
                    continue

            # Odds pas later ophalen (performance)
            odd_1x2 = None
            odds_response = get_live_odds(fid)
            odd_1x2 = find_1x2_odd(odds_response, pick_side, home, away)

            if odd_1x2 is None and REQUIRE_ODDS:
                continue
            if odd_1x2 is not None and odd_1x2 < ODD_MIN:
                continue
            if minute >= LATE_MINUTE and odd_1x2 is not None and odd_1x2 < LATE_MIN_ODD:
                continue

            # Confidence
            conf = confidence_score(
                gap=gap,
                sot_diff_total=abs(sot_diff),
                opp_sot=opp_sot,
                pace10_shots=pace10_shots,
                pace5_shots=pace5_shots,
                pace10_sot=pace10_sot,
                odd_value=odd_1x2
            )

            # Risk window extra check (nu milder dan eerst)
            if is_risk:
                if not (abs(sot_diff) >= 3 and pace10_shots >= 8 and conf >= 80):
                    continue

            pick_team = home if pick_side == "HOME" else away

            red_txt = ""
            if hred_total or ared_total:
                red_txt = f"\n🟥 Red Cards: {hred_total} - {ared_total}"

            odds_line = (
                f"\n💰 1X2 Odd ({pick_team}): {odd_1x2} ✅"
                if odd_1x2 is not None
                else "\n💰 1X2 Odd: — (check bookie) 🟡"
            )

            # Tier
            is_extreme = (
                dom_score >= EXTREME_SCORE and
                gap >= EXTREME_MIN_GAP and
                opp_sot <= EXTREME_MAX_OPP_SOT and
                opp_shots <= EXTREME_MAX_OPP_SHOTS and
                conf >= 85
            )

            is_premium = (
                dom_score >= PREMIUM_MIN_SCORE and
                gap >= PREMIUM_MIN_GAP and
                abs(sot_diff) >= PREMIUM_MIN_SOT_DIFF and
                opp_sot <= PREMIUM_MAX_OPP_SOT and
                opp_shots <= PREMIUM_MAX_OPP_SHOTS and
                conf >= PREMIUM_MIN_CONF
            )

            is_normal = (
                dom_score >= NORMAL_MIN_SCORE and
                gap >= NORMAL_MIN_GAP and
                not (opp_sot > NORMAL_MAX_OPP_SOT and opp_shots > NORMAL_MAX_OPP_SHOTS) and
                conf >= 55
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

            # SEND ALERT
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

            # LOG ALERT
            log_alert_row([
                datetime.now().isoformat(timespec="seconds"),
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
                odd_1x2 if odd_1x2 is not None else "",
                pace10_shots, pace10_sot,
                pace5_shots, pace5_sot,
                dom_sot, dom_shots,
                opp_sot, opp_shots,
                str(is_risk),
                str(post_goal_strict),
            ])

            # PENDING for HIT/MISS
            PENDING[fid] = {
                "tier": tier,
                "home": home,
                "away": away,
                "pick_side": pick_side,
                "pick_team": pick_team,
                "score_at_alert": (gh, ga),
            }

            ALERTED_MATCHES.add(fid)

            # anti spam: 1 alert per loop
            break

        time.sleep(91)

    except Exception as e:
        send_message(f"❌ ERROR: {e}")
        time.sleep(60)