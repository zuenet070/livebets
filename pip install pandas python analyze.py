import pandas as pd

ALERTS_FILE = "alerts_log_premium.csv"
RESULTS_FILE = "results_log_premium.csv"

def load_data():
    # Veilig inlezen
    alerts = pd.read_csv(ALERTS_FILE)
    results = pd.read_csv(RESULTS_FILE)

    # Timestamps
    if "timestamp" in alerts.columns:
        alerts["timestamp"] = pd.to_datetime(alerts["timestamp"], errors="coerce")
    if "timestamp" in results.columns:
        results["timestamp"] = pd.to_datetime(results["timestamp"], errors="coerce")

    # Merge (1 alert per fixture_id)
    df = alerts.merge(
        results[["fixture_id", "result", "minute_resolved", "score_resolved"]],
        on="fixture_id",
        how="left"
    )

    # Zorg dat kolommen bestaan (anders 0)
    for col in ["sot_half", "opp_sot_half", "shots_half", "opp_shots_half", "minute", "confidence", "gap", "dominant_score", "tier"]:
        if col not in df.columns:
            df[col] = 0

    # Features maken
    df["dom_sot_half"] = pd.to_numeric(df["sot_half"], errors="coerce").fillna(0)
    df["opp_sot_half"] = pd.to_numeric(df["opp_sot_half"], errors="coerce").fillna(0)
    df["dom_shots_half"] = pd.to_numeric(df["shots_half"], errors="coerce").fillna(0)
    df["opp_shots_half"] = pd.to_numeric(df["opp_shots_half"], errors="coerce").fillna(0)

    df["total_shots_half"] = df["dom_shots_half"] + df["opp_shots_half"]
    df["total_sot_half"] = df["dom_sot_half"] + df["opp_sot_half"]

    # Minute bucket
    df["minute"] = pd.to_numeric(df["minute"], errors="coerce").fillna(0).astype(int)
    df["minute_bucket"] = pd.cut(
        df["minute"],
        bins=[0, 15, 20, 25, 30, 33, 35, 45, 50, 60, 70, 80, 90, 200],
        right=True
    )

    # Date
    if "timestamp" in df.columns:
        df["date"] = df["timestamp"].dt.date

    return df

def summary_overall(df):
    print("\n==============================")
    print("OVERALL SUMMARY")
    print("==============================")

    total = len(df)
    resolved = df["result"].notna().sum()

    hits = (df["result"] == "HIT").sum()
    misses = (df["result"] == "MISS").sum()

    hitrate = (hits / (hits + misses) * 100) if (hits + misses) > 0 else 0

    print(f"Total alerts: {total}")
    print(f"Resolved results: {resolved}")
    print(f"HIT: {hits} | MISS: {misses} | HITRATE: {hitrate:.1f}%")

def summary_by_tier(df):
    print("\n==============================")
    print("SUMMARY BY TIER")
    print("==============================")

    g = df.groupby("tier").agg(
        alerts=("fixture_id", "count"),
        hits=("result", lambda x: (x == "HIT").sum()),
        misses=("result", lambda x: (x == "MISS").sum()),
        avg_conf=("confidence", "mean"),
        avg_gap=("gap", "mean"),
        avg_domscore=("dominant_score", "mean"),
        avg_dom_sot=("dom_sot_half", "mean"),
        avg_total_shots=("total_shots_half", "mean"),
    ).reset_index()

    g["hitrate_%"] = (g["hits"] / (g["hits"] + g["misses"]) * 100).round(1)
    print(g.to_string(index=False))

def summary_by_minute_bucket(df):
    print("\n==============================")
    print("SUMMARY BY MINUTE WINDOW")
    print("==============================")

    g = df.groupby("minute_bucket").agg(
        alerts=("fixture_id", "count"),
        hits=("result", lambda x: (x == "HIT").sum()),
        misses=("result", lambda x: (x == "MISS").sum()),
    ).reset_index()

    g["hitrate_%"] = (g["hits"] / (g["hits"] + g["misses"]) * 100).round(1)
    print(g.to_string(index=False))

def find_false_premiums(df):
    """
    Detecteer PREMIUM valkuilen:
    lage pace maar wel premium label.
    """
    print("\n==============================")
    print("FALSE PREMIUM CHECK (low pace traps)")
    print("==============================")

    prem = df[df["tier"] == "PREMIUM"].copy()
    prem = prem[prem["result"].notna()]

    # Low pace regels (vermijden)
    low_pace = prem[
        (prem["dom_sot_half"] < 3) |
        (prem["total_shots_half"] < 10)
    ]

    if len(low_pace) == 0:
        print("Geen low-pace premiums gevonden ✅")
        return

    low_pace_hits = (low_pace["result"] == "HIT").sum()
    low_pace_miss = (low_pace["result"] == "MISS").sum()
    hitrate = (low_pace_hits / (low_pace_hits + low_pace_miss) * 100) if (low_pace_hits + low_pace_miss) else 0

    print(f"Low pace PREMIUM alerts: {len(low_pace)}")
    print(f"HIT: {low_pace_hits} | MISS: {low_pace_miss} | HITRATE: {hitrate:.1f}%")

    cols = ["timestamp", "home", "away", "minute", "score", "pick", "confidence", "gap",
            "dom_sot_half", "total_shots_half", "result"]
    cols = [c for c in cols if c in low_pace.columns]
    print("\nTop 15 low-pace examples:")
    print(low_pace[cols].head(15).to_string(index=False))

def recommend_thresholds(df):
    """
    Data-driven thresholds (zonder ML):
    welke PREMIUM setups scoren beter.
    """
    print("\n==============================")
    print("THRESHOLD RECOMMENDATIONS (PREMIUM)")
    print("==============================")

    prem = df[df["tier"] == "PREMIUM"].copy()
    prem = prem[prem["result"].notna()]
    if len(prem) < 15:
        print("Nog te weinig PREMIUM samples om hard te tunen (min 15+) ✅")
        return

    candidate_dom_sot = [2, 3, 4]
    candidate_total_shots = [9, 10, 11, 12, 13]
    candidate_gap = [22, 24, 26, 28, 30]

    best = None
    for ds in candidate_dom_sot:
        for ts in candidate_total_shots:
            for gp in candidate_gap:
                subset = prem[
                    (prem["dom_sot_half"] >= ds) &
                    (prem["total_shots_half"] >= ts) &
                    (prem["gap"] >= gp)
                ]
                if len(subset) < 8:
                    continue

                hits = (subset["result"] == "HIT").sum()
                misses = (subset["result"] == "MISS").sum()
                hitrate = hits / (hits + misses) * 100 if (hits + misses) else 0

                score = hitrate + min(20, len(subset))  # volume bonus
                if best is None or score > best["score"]:
                    best = {
                        "dom_sot_min": ds,
                        "total_shots_min": ts,
                        "gap_min": gp,
                        "samples": len(subset),
                        "hitrate": round(hitrate, 1),
                        "score": round(score, 1),
                    }

    if best:
        print("Beste combinatie (hitrate + volume):")
        print(best)
        print("\n👉 Gebruik dit als PREMIUM pace/quality filter.")
    else:
        print("Geen goede subset gevonden (nog te weinig data of te streng).")

def main():
    df = load_data()

    # Alleen resolved rows voor hitrate analyses
    resolved = df[df["result"].notna()].copy()

    if len(resolved) == 0:
        print("Geen resolved resultaten gevonden (result kolom leeg).")
        return

    summary_overall(resolved)
    summary_by_tier(resolved)
    summary_by_minute_bucket(resolved)
    find_false_premiums(resolved)
    recommend_thresholds(resolved)

if __name__ == "__main__":
    main()