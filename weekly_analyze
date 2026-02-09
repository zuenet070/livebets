import csv
from datetime import datetime, timedelta

def _parse_ts(s):
    # verwacht: 2026-02-04T10:12:34 of 2026-02-04 10:12:34
    try:
        return datetime.fromisoformat(s)
    except:
        try:
            return datetime.fromisoformat(s.replace(" ", "T"))
        except:
            return None

def _read_csv(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return list(csv.DictReader(f))
    except FileNotFoundError:
        return []

def _safe_float(x):
    try:
        if x is None or x == "":
            return None
        return float(x)
    except:
        return None

def _bucket_minute(m):
    # simpele buckets voor jouw probleem
    if m <= 25: return "15-25"
    if m <= 30: return "26-30"
    if m <= 39: return "31-39 (risk)"
    if m <= 45: return "40-45"
    if m <= 60: return "46-60"
    if m <= 75: return "61-75"
    return "76-85"

def generate_weekly_summary(alerts_file, results_file, days=7):
    since = datetime.now() - timedelta(days=days)

    alerts = _read_csv(alerts_file)
    results = _read_csv(results_file)

    # results map by fixture_id
    res_map = {}
    for r in results:
        ts = _parse_ts(r.get("timestamp", ""))
        if not ts or ts < since:
            continue
        res_map[str(r.get("fixture_id"))] = r

    rows = []
    for a in alerts:
        ts = _parse_ts(a.get("timestamp", ""))
        if not ts or ts < since:
            continue

        fid = str(a.get("fixture_id"))
        r = res_map.get(fid)
        if not r:
            continue  # alleen resolved in weekrapport

        tier = a.get("tier", "NA")
        minute = int(float(a.get("minute", 0)))
        result = r.get("result")

        rows.append({
            "tier": tier,
            "minute": minute,
            "bucket": _bucket_minute(minute),
            "result": result,
            "in_risk": int(a.get("in_early_risk_window", 0) or 0),
            "post_goal_strict": int(a.get("in_post_goal_strict", 0) or 0),
            "late_game": int(a.get("is_late_game", 0) or 0),
        })

    if not rows:
        return ("📊 WEEKRAPPORT\nGeen (resolved) data in de laatste 7 dagen.", None)

    total = len(rows)
    hits = sum(1 for x in rows if x["result"] == "HIT")
    misses = sum(1 for x in rows if x["result"] == "MISS")
    hitrate = round((hits / total) * 100, 1) if total else 0.0

    def stats(filter_fn):
        subset = [x for x in rows if filter_fn(x)]
        if not subset:
            return (0, 0.0)
        t = len(subset)
        h = sum(1 for x in subset if x["result"] == "HIT")
        return (t, round((h / t) * 100, 1))

    n_cnt, n_hr = stats(lambda x: x["tier"] == "NORMAL")
    p_cnt, p_hr = stats(lambda x: x["tier"] == "PREMIUM")
    e_cnt, e_hr = stats(lambda x: x["tier"] == "EXTREME")

    risk_cnt, risk_hr = stats(lambda x: x["in_risk"] == 1)
    pg_cnt, pg_hr = stats(lambda x: x["post_goal_strict"] == 1)
    late_cnt, late_hr = stats(lambda x: x["late_game"] == 1)

    # bucket hitrates
    buckets = {}
    for x in rows:
        b = x["bucket"]
        buckets.setdefault(b, {"t": 0, "h": 0})
        buckets[b]["t"] += 1
        if x["result"] == "HIT":
            buckets[b]["h"] += 1

    bucket_lines = []
    for b in ["15-25","26-30","31-39 (risk)","40-45","46-60","61-75","76-85"]:
        if b in buckets:
            t = buckets[b]["t"]
            hr = round((buckets[b]["h"]/t)*100, 1) if t else 0
            bucket_lines.append(f"• {b}: {hr}% ({t})")

    # simpele tips gebaseerd op jouw pain points
    tips = []
    if risk_cnt >= 3 and risk_hr < hitrate:
        tips.append("• 31–39 min blijft risk: maak pace10 strenger of conf-min hoger in die window.")
    if pg_cnt >= 3 and pg_hr < hitrate:
        tips.append("• Post-goal momentum fakes: verleng strict window of eis hogere pace5.")
    if late_cnt >= 3 and late_hr < hitrate:
        tips.append("• Late-game misses: tighten LATE rules (opp_sot=0 of pace10_shots hoger).")
    if not tips:
        tips.append("• Instellingen lijken stabiel. Focus op meer data voor betere tuning.")

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
        f"🕒 Hitrate per tijdwindow:\n" + "\n".join(bucket_lines) + "\n\n"
        f"🧨 Risk window 31–39: {risk_cnt} | {risk_hr}%\n"
        f"⏱️ Post-goal strict: {pg_cnt} | {pg_hr}%\n"
        f"🕯️ Late-game: {late_cnt} | {late_hr}%\n\n"
        f"🤖 Optimalisatie tips:\n" + "\n".join(tips)
    )

    summary_row = [
        week_ending, total, hits, misses, hitrate,
        n_cnt, n_hr,
        p_cnt, p_hr,
        e_cnt, e_hr,
        risk_cnt, risk_hr,
        pg_cnt, pg_hr,
        late_cnt, late_hr
    ]

    return (text, summary_row)
