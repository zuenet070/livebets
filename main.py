import time
import os
import requests
import csv
from datetime import date, datetime

# =========================
# ENV VARS
# =========================
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
API_KEY = os.getenv("API_FOOTBALL_KEY")

if not BOT_TOKEN or not CHAT_ID or not API_KEY:
    print("❌ ERROR: Missing env vars. Check BOT_TOKEN, CHAT_ID, API_FOOTBALL_KEY")
    exit()

BASE_URL = "https://v3.football.api-sports.io"
HEADERS = {"x-apisports-key": API_KEY}

# =========================
# LOG DIRECTORY (Railway Volume)
# =========================
# ✅ Als je Railway Volume mount op /app/data -> laat dit zo.
# ❌ Als je GEEN volume hebt, zet naar "." (punt)
LOG_DIR = os.getenv("LOG_DIR", ".")  # bv: "/app/data" of "."

ALERTS_LOG = os.path.join(LOG_DIR, "alerts_log_premium.csv")
RESULTS_LOG = os.path.join(LOG_DIR, "results_log_premium.csv")

# =========================
# TIME WINDOWS (JOUW WENS)
# =========================
# 1e helft: 15-35
# 2e helft: 50-85
FIRST_HALF_MIN = 15
FIRST_HALF_MAX = 35
SECOND_HALF_MIN = 50
SECOND_HALF_MAX = 85

# Goal cooldown (voorkomt alert direct na goal)
GOAL_COOLDOWN_SECONDS = 90

# Score regels
MAX_BEHIND_GOALS = 2  # dominant team mag max 2 goals achter

# =========================
# NORMAL (meer volume)
# =========================
NORMAL_MIN_SCORE = 13
NORMAL_MIN_GAP = 18.0
NORMAL_MAX_OPP_SOT = 2
NORMAL_MAX_OPP_SHOTS = 8

# =========================
# PREMIUM (betaalwaardig)
# =========================
PREMIUM_MIN_SCORE = 15
PREMIUM_MIN_GAP = 24.0
PREMIUM_MIN_SOT_DIFF = 2
PREMIUM_MAX_OPP_SOT = 1
PREMIUM_MAX_OPP_SHOTS = 6
PREMIUM_MIN_CONF = 70

# =========================
# EXTREME (VEEL ZELDZAMER)
# =========================
EXTREME_SCORE = 21
EXTREME_MIN_GAP = 32.0
EXTREME_MAX_OPP_SOT = 1
EXTREME_MAX_OPP_SHOTS = 6

# =========================
# Weights (SOT super belangrijk)
# =========================
W_SOT = 6
W_SHOTS = 1
W_CORNERS = 1
W_POSSESSION = 0.07
W_BIGCHANCES = 3
RED_CARD_BONUS = 6

# Minimum dreiging dominant team
MIN_DOMINANT_SOT = 2
MIN_DOMINANT_SHOTS = 6

# Odds (nice-to-have)
REQUIRE_ODDS = False

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
# STATE
# =========================
TODAY = date.today()

ALERTED_MATCHES = set()          # max 1 alert per match
HALF_TIME_SNAPSHOT = {}          # fid -> snapshot dict
SCORE_STATE = {}                 # fid -> {"score": (gh,ga), "changed_at": epoch}

# Pending alerts voor HIT/MISS tracking
PENDING = {}  # fid -> dict met pick info


# =========================
# BASIC HELPERS
# =========================
def send_message(text):
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


def cleanup_finished(fid):
    HALF_TIME_SNAPSHOT.pop(fid, None)
    SCORE_STATE.pop(fid, None)
    # ⚠️ PENDING wordt pas weggehaald als het resultaat resolved is
    # zodat je geen HIT/MISS mist na 00:00.


# =========================
# ODDS
# =========================
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
# CONFIDENCE (0-100)
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
# LOGGING
# =========================
def ensure_csv_header(file_path, header_cols):
    try:
        with open(file_path, "r", newline="", encoding="utf-8") as f:
            return
    except FileNotFoundError:
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        with open(file_path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(header_cols)


def log_alert_row(row):
    ensure_csv_header(ALERTS_LOG, [
        "timestamp", "tier", "fixture_id", "league", "home", "away",
        "minute", "score", "pick", "dominant_score", "gap", "confidence", "odds",
        "sot_half", "shots_half", "opp_sot_half", "opp_shots_half"
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


# =========================
# DAILY REPORT (PRO)
# =========================
def send_daily_report(report_date):
    day_str = report_date.isoformat()

    # alerts van die dag ophalen
    alerts = []
    try:
        with open(ALERTS_LOG, "r", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        alerts = [r for r in rows if r["timestamp"].startswith(day_str)]
    except FileNotFoundError:
        alerts = []

    # results van die dag ophalen
    results = []
    try:
        with open(RESULTS_LOG, "r", encoding="utf-8") as f:
            rrows = list(csv.DictReader(f))
        results = [r for r in rrows if r["timestamp"].startswith(day_str)]
    except FileNotFoundError:
        results = []

    # result map (fixture_id -> HIT/MISS)
    res_map = {}
    for r in results:
        fid = r.get("fixture_id")
        if fid:
            res_map[str(fid)] = r.get("result", "")

    # totals
    total_alerts = len(alerts)
    resolved = 0
    hits = 0
    misses = 0

    # tier stats
    tiers = {
        "NORMAL": {"alerts": 0, "hit": 0, "miss": 0},
        "PREMIUM": {"alerts": 0, "hit": 0, "miss": 0},
        "EXTREME": {"alerts": 0, "hit": 0, "miss": 0},
    }

    # league stats
    leagues = {}  # league -> {"alerts":0,"hit":0,"miss":0}

    # premium low tempo trap
    low_tempo_premium = 0
    low_tempo_premium_miss = 0

    for a in alerts:
        tier = a.get("tier", "NORMAL")
        fid = str(a.get("fixture_id", ""))

        tiers.setdefault(tier, {"alerts": 0, "hit": 0, "miss": 0})
        tiers[tier]["alerts"] += 1

        league = a.get("league", "Unknown")
        if league not in leagues:
            leagues[league] = {"alerts": 0, "hit": 0, "miss": 0}
        leagues[league]["alerts"] += 1

        result = res_map.get(fid, "")
        if result in ("HIT", "MISS"):
            resolved += 1
            if result == "HIT":
                hits += 1
                tiers[tier]["hit"] += 1
                leagues[league]["hit"] += 1
            else:
                misses += 1
                tiers[tier]["miss"] += 1
                leagues[league]["miss"] += 1

        # low tempo check (alleen PREMIUM)
        if tier == "PREMIUM":
            try:
                dom_sot_half = int(float(a.get("sot_half", 0)))
                dom_shots_half = int(float(a.get("shots_half", 0)))
                opp_shots_half = int(float(a.get("opp_shots_half", 0)))
                total_shots_half = dom_shots_half + opp_shots_half

                # low tempo = vaak 0-0 valkuil
                if dom_sot_half < 3 or total_shots_half < 10:
                    low_tempo_premium += 1
                    if result == "MISS":
                        low_tempo_premium_miss += 1
            except:
                pass

    def rate(h, m):
        t = h + m
        return round((h / t) * 100, 1) if t > 0 else 0.0

    total_hitrate = rate(hits, misses)

    normal_rate = rate(tiers["NORMAL"]["hit"], tiers["NORMAL"]["miss"])
    premium_rate = rate(tiers["PREMIUM"]["hit"], tiers["PREMIUM"]["miss"])
    extreme_rate = rate(tiers["EXTREME"]["hit"], tiers["EXTREME"]["miss"])

    # Top leagues (min 2 samples)
    league_list = []
    for lg, d in leagues.items():
        if (d["hit"] + d["miss"]) >= 2:
            league_list.append((lg, rate(d["hit"], d["miss"]), d["hit"], d["miss"], d["alerts"]))
    league_list.sort(key=lambda x: x[1], reverse=True)
    top_leagues = league_list[:3]

    # Optimalisatie tips (heuristics)
    tips = []
    if tiers["EXTREME"]["alerts"] >= 6:
        tips.append("• Te veel EXTREME → verhoog EXTREME_SCORE of EXTREME_MIN_GAP.")
    if premium_rate < 55 and tiers["PREMIUM"]["alerts"] >= 6:
        tips.append("• PREMIUM hitrate laag → maak PREMIUM strenger (PREMIUM_MIN_GAP of PREMIUM_MIN_CONF omhoog).")
    if low_tempo_premium_miss >= 2:
        tips.append("• Veel low-tempo PREMIUM misses → pace filter strenger (dom SOT ≥3 + totaal shots ≥10).")
    if not tips:
        tips.append("• Instellingen zijn stabiel ✅ (geen grote aanpassingen nodig vandaag).")

    # Top leagues tekst
    if top_leagues:
        league_txt = "\n".join([f"• {lg}: {hr}% ({h}-{m})" for lg, hr, h, m, _ in top_leagues])
    else:
        league_txt = "• Nog te weinig league-data (min 2 results per league)."

    send_message(
        f"📊 DAGRAPPORT ({day_str})\n\n"
        f"📌 Alerts: {total_alerts}\n"
        f"✅ HIT: {hits}\n"
        f"❌ MISS: {misses}\n"
        f"🎯 Hitrate: {total_hitrate}%\n\n"
        f"⚠️ NORMAL: {tiers['NORMAL']['alerts']} | {normal_rate}%\n"
        f"💎 PREMIUM: {tiers['PREMIUM']['alerts']} | {premium_rate}%\n"
        f"🔥 EXTREME: {tiers['EXTREME']['alerts']} | {extreme_rate}%\n\n"
        f"🏆 TOP LEAGUES (beste hitrate):\n{league_txt}\n\n"
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

    # goal gevallen -> HIT/MISS meteen
    if goal_home or goal_away:
        if goal_home and not goal_away:
            scorer = "HOME"
        elif goal_away and not goal_home:
            scorer = "AWAY"
        else:
            scorer = "HOME" if goal_home else "AWAY"

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
            f"{gh}-{ga}"
        ])

        PENDING.pop(fid, None)
        return

    # Geen goal meer & wedstrijd klaar -> MISS
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
            f"{old_gh}-{old_ga}",
            f"{gh}-{ga}"
        ])

        PENDING.pop(fid, None)
        cleanup_finished(fid)
        return


def resolve_pending_not_in_live():
    for fid in list(PENDING.keys()):
        match = get_fixture_by_id(fid)
        if match:
            resolve_pending_from_match(match)


# =========================
# START
# =========================
send_message("🟢 Bot gestart – NORMAL + PREMIUM + EXTREME + HIT/MISS + PRO DAGRAPPORT ✅")


# =========================
# MAIN LOOP
# =========================
while True:
    try:
        # new day -> report yesterday
        if date.today() != TODAY:
            yesterday = TODAY
            send_daily_report(yesterday)

            TODAY = date.today()
            ALERTED_MATCHES.clear()
            HALF_TIME_SNAPSHOT.clear()
            SCORE_STATE.clear()

            # ✅ PENDING NIET clearen -> anders mis je results na 00:00
            send_message("🔄 Nieuwe dag — reset uitgevoerd ✅")

        matches = get_live_matches()
        match_map = {m.get("fixture", {}).get("id"): m for m in matches if m.get("fixture", {}).get("id")}

        # 1) pending results afhandelen
        for fid, m in list(match_map.items()):
            if fid in PENDING:
                resolve_pending_from_match(m)

        # pending die niet meer live is -> check fixture status
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
            if not minute:
                continue

            # ✅ Alleen jouw tijdwindows
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

            # stats ophalen
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

            # comeback max 2 goals
            if pick_side == "HOME" and (ga - gh) > MAX_BEHIND_GOALS:
                continue
            if pick_side == "AWAY" and (gh - ga) > MAX_BEHIND_GOALS:
                continue

            # minimum dreiging dominant team
            if dom_sot < MIN_DOMINANT_SOT and dom_shots < MIN_DOMINANT_SHOTS:
                continue

            conf = confidence_score(dom_score, gap, sot_diff, red_adv, big_diff)

            # odds
            picked_odds = None
            odds_response = get_live_odds(fid)
            picked_odds = find_next_goal_odds(odds_response, pick_side, home, away)

            if picked_odds is None and REQUIRE_ODDS:
                continue

            odds_line = (
                f"\n💰 Next Goal Odds: {picked_odds} ✅"
                if picked_odds is not None
                else "\n💰 Next Goal Odds: — (SLOT: check BetCity) 🟡"
            )

            pick_team = home if pick_side == "HOME" else away

            red_txt = ""
            if hred_total or ared_total:
                red_txt = f"\n🟥 Red Cards: {hred_total} - {ared_total}"

            # TIER bepalen (EXTREME > PREMIUM > NORMAL)
            is_extreme = (
                dom_score >= EXTREME_SCORE and
                gap >= EXTREME_MIN_GAP and
                opp_sot <= EXTREME_MAX_OPP_SOT and
                opp_shots <= EXTREME_MAX_OPP_SHOTS
            )

            is_premium = (
                dom_score >= PREMIUM_MIN_SCORE and
                gap >= PREMIUM_MIN_GAP and
                sot_diff >= PREMIUM_MIN_SOT_DIFF and
                opp_sot <= PREMIUM_MAX_OPP_SOT and
                opp_shots <= PREMIUM_MAX_OPP_SHOTS and
                conf >= PREMIUM_MIN_CONF
            )

            is_normal = (
                dom_score >= NORMAL_MIN_SCORE and
                gap >= NORMAL_MIN_GAP and
                not (opp_sot > NORMAL_MAX_OPP_SOT and opp_shots > NORMAL_MAX_OPP_SHOTS)
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

            # ALERT sturen
            send_message(
                f"{title} ({half_text})\n\n"
                f"🏆 {league_name} ({league_country})\n"
                f"{home} vs {away}\n"
                f"Minuut: {minute}' | Stand: {gh}-{ga}\n\n"
                f"✅ Confidence: {conf}/100\n"
                f"📏 GAP: {round(gap,1)} | SOT diff: {sot_diff}\n"
                f"🛡️ Opp threat: SOT {opp_sot} | Shots {opp_shots}\n\n"
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

            # LOG alert
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
                picked_odds if picked_odds is not None else "",
                dom_sot,
                dom_shots,
                opp_sot,
                opp_shots,
            ])

            # Pending opslaan voor HIT/MISS
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

        time.sleep(90)

    except Exception as e:
        send_message(f"❌ ERROR: {e}")
        time.sleep(60)


