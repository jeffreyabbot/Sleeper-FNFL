import streamlit as st
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from collections import defaultdict

st.set_page_config(page_title="Sleeper 15-Keeper War Room", layout="wide", page_icon="🏈")

BASE_URL = "https://api.sleeper.app/v1"

# ----------------- ROBUST HTTP SESSION SETUP -----------------
def create_resilient_session():
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    })
    retries = Retry(
        total=5,
        backoff_factor=0.5,
        status_forcelist=[429, 500, 502, 503, 504],
        raise_on_status=False
    )
    adapter = HTTPAdapter(max_retries=retries)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session

HTTP_SESSION = create_resilient_session()

def get_json(url):
    try:
        res = HTTP_SESSION.get(url, timeout=10)
        if res.status_code == 200:
            return res.json()
    except requests.exceptions.RequestException:
        return None
    return None
def get_player_avatar_url(pid):
    """Returns Sleeper CDN headshot for players or team logo for defenses."""
    if not pid:
        return ""
    pid_str = str(pid).upper()
    nfl_teams = ["ARI", "ATL", "BAL", "BUF", "CAR", "CHI", "CIN", "CLE", "DAL", "DEN", 
                 "DET", "GB", "HOU", "IND", "JAX", "KC", "LV", "LAC", "LAR", "MIA", 
                 "MIN", "NE", "NO", "NYG", "NYJ", "PHI", "PIT", "SF", "SEA", "TB", "TEN", "WAS"]
    if pid_str in nfl_teams:
        return f"https://sleepercdn.com/images/team_logos/nfl/{pid_str.lower()}.png"
    return f"https://sleepercdn.com/content/nfl/players/thumb/{pid}.jpg"
def get_team_avatar_url(avatar_id):
    """Returns Sleeper CDN URL for team avatar, or a fallback icon."""
    if not avatar_id:
        # Default clean placeholder if user hasn't set an avatar
        return "https://sleepercdn.com/images/v2/icons/player_default.webp"
    if str(avatar_id).startswith("http"):
        return str(avatar_id)
    return f"https://sleepercdn.com/avatars/thumbs/{avatar_id}"

# ----------------- CACHED DATA FETCHING -----------------
@st.cache_data(ttl=3600)
def get_weekly_projections(season, week):
    """Fetches Sleeper PPR projections for the given season and week."""
    url = f"https://api.sleeper.app/projections/nfl/{season}/{week}?season_type=regular"
    res = get_json(url)
    proj_map = {}
    if isinstance(res, list):
        for item in res:
            pid = item.get("player_id")
            stats = item.get("stats") or {}
            pts = stats.get("pts_ppr") or stats.get("pts_half_ppr") or stats.get("pts_std") or 0.0
            if pid:
                proj_map[pid] = round(float(pts), 1)
    return proj_map

@st.cache_data(ttl=86400)
def load_nfl_players():
    return get_json(f"{BASE_URL}/players/nfl") or {}

@st.cache_data(ttl=3600)
def get_nfl_state():
    return get_json(f"{BASE_URL}/state/nfl") or {}

@st.cache_data(ttl=3600)
def get_league_chain(initial_league_id):
    chain = []
    curr_id = str(initial_league_id).strip()
    while curr_id:
        league = get_json(f"{BASE_URL}/league/{curr_id}")
        if not league:
            break
        chain.append(league)
        curr_id = league.get("previous_league_id")
    return chain

# ----------------- LIVE ESPN VEGAS & WEATHER ENGINE -----------------
@st.cache_data(ttl=1800)
def get_nfl_matchup_env(season_year="2026", week_num=2):
    """
    Pulls live Vegas lines, Over/Unders, stadium weather, and converts 
    kickoff times to local Spanish time (Europe/Madrid / CEST).
    """
    week_num = max(1, int(week_num))
    url = f"https://site.web.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard?seasontype=2&week={week_num}"
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/json"
    }

    res = get_json(url)
    if not res or not res.get("events"):
        url_fallback = "https://site.web.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard"
        res = get_json(url_fallback)

    if not res or not res.get("events"):
        return {}

    env_map = {}
    ABBR_ALIASES = {
        "WSH": "WAS", "WAS": "WSH",
        "JAX": "JAX", "JAC": "JAX",
        "LA": "LAR", "LAR": "LA"
    }

    for ev in res.get("events", []):
        comp = ev.get("competitions", [{}])[0]
        venue = comp.get("venue", {})
        is_indoor = venue.get("indoor", False)

        # Convert UTC game time to Spain (Europe/Madrid / CEST)
        game_utc_str = ev.get("date")
        kickoff_cest = "Sun 19:00"
        is_late_night = False
        
        if game_utc_str:
            try:
                utc_dt = pd.to_datetime(game_utc_str)
                # Convert to Spain time
                madrid_dt = utc_dt.tz_convert("Europe/Madrid")
                day_name = madrid_dt.strftime("%a")
                time_str = madrid_dt.strftime("%H:%M")
                kickoff_cest = f"{day_name} {time_str}"
                # Games starting at or after 23:00 CEST or before 06:00 CEST are night games
                is_late_night = (madrid_dt.hour >= 23 or madrid_dt.hour < 6)
            except Exception:
                pass

        weather = comp.get("weather", {})
        wind_speed = 0
        if isinstance(weather.get("wind"), dict):
            wind_speed = weather.get("wind", {}).get("speed", 0)
        temp = weather.get("temperature", 70)
        condition = weather.get("displayValue", "Fair")

        odds_list = comp.get("odds", [])
        ou = None
        spread = 0.0
        fav_team = ""

        if odds_list:
            ou_raw = odds_list[0].get("overUnder")
            if ou_raw is not None:
                ou = float(ou_raw)
            details = str(odds_list[0].get("details", ""))
            if "-" in details:
                parts = details.split("-")
                fav_team = parts[0].strip().upper()
                try:
                    spread = abs(float(parts[1].strip()))
                except ValueError:
                    spread = 0.0

        competitors = comp.get("competitors", [])
        if len(competitors) >= 2:
            home_c = next((c for c in competitors if c.get("homeAway") == "home"), competitors[0])
            away_c = next((c for c in competitors if c.get("homeAway") == "away"), competitors[1])
            
            home_t = home_c.get("team", {}).get("abbreviation", "").upper()
            away_t = away_c.get("team", {}).get("abbreviation", "").upper()

            itt_base = ((ou - spread) / 2.0) if (ou is not None) else 22.5

            for c, is_home in [(home_c, True), (away_c, False)]:
                tm = c.get("team", {}).get("abbreviation", "").upper()
                opp = away_t if is_home else home_t
                is_fav = (tm == fav_team or ABBR_ALIASES.get(tm) == fav_team)
                team_itt = (itt_base + (spread if is_fav else 0.0)) if (ou is not None) else 22.5

                game_data = {
                    "Opponent": f"vs {opp}" if is_home else f"@{opp}",
                    "Kickoff": kickoff_cest,
                    "IsLateNight": is_late_night,
                    "OverUnder": ou,
                    "Spread": f"-{spread:.1f}" if is_fav else f"+{spread:.1f}",
                    "SpreadVal": spread,
                    "IsFavorite": is_fav,
                    "ImpliedTotal": round(team_itt, 1) if ou is not None else None,
                    "Indoor": is_indoor,
                    "Wind": int(wind_speed),
                    "Temp": int(temp),
                    "Condition": "Dome" if is_indoor else condition
                }

                env_map[tm] = game_data
                if tm in ABBR_ALIASES:
                    env_map[ABBR_ALIASES[tm]] = game_data

    return env_map

# ----------------- OPTIMAL LINEUP SOLVER (BY TRUE POSITION) -----------------
def calculate_optimal_lineup(players_scores, starter_positions, player_db):
    starting_slots = [p for p in starter_positions if p not in ("BN", "IR", "TAXI")]
    pool = []
    for pid, pts in players_scores.items():
        pts = pts or 0.0
        p_info = player_db.get(pid, {})
        pos = p_info.get("position") or "FLEX"
        fantasy_pos = p_info.get("fantasy_positions") or ([pos] if pos else [])
        pool.append({"id": pid, "pts": pts, "positions": fantasy_pos, "primary_pos": pos})
        
    pool.sort(key=lambda x: x["pts"], reverse=True)
    used_ids = set()
    optimal_pts_by_pos = defaultdict(float)
    
    strict_slots = [s for s in starting_slots if "FLEX" not in s]
    flex_slots = [s for s in starting_slots if "FLEX" in s]
    
    # 1. Strict slots
    for slot in strict_slots:
        for player in pool:
            if player["id"] not in used_ids and slot in player["positions"]:
                used_ids.add(player["id"])
                optimal_pts_by_pos[player["primary_pos"]] += player["pts"]
                break

    # 2. Flex slots (attributed to player's true position!)
    for slot in flex_slots:
        for player in pool:
            if player["id"] in used_ids:
                continue
            eligible = False
            # Strictly RB and WR only (No TEs in FLEX per league rules)
            if "FLEX" in slot and any(p in player["positions"] for p in ["RB", "WR"]):
                eligible = True
                
            if eligible:
                used_ids.add(player["id"])
                optimal_pts_by_pos[player["primary_pos"]] += player["pts"]
                break
                
    total_optimal = sum(optimal_pts_by_pos.values())
    return total_optimal, optimal_pts_by_pos

# ----------------- CACHED SEASON PROCESSING -----------------
@st.cache_data(ttl=1800, show_spinner=False)
def process_season(league_id, season_year, starter_positions, playoff_start, last_league_week, prev_league_id):
    player_db = load_nfl_players()
    nfl_state = get_nfl_state()

    is_current_season = (str(season_year) == str(nfl_state.get("season")))
    current_nfl_week = nfl_state.get("week", 1) if is_current_season else 18
    max_eval_week = min(last_league_week, current_nfl_week if is_current_season else last_league_week)

    raw_users = get_json(f"{BASE_URL}/league/{league_id}/users") or []
    users = {}
    user_avatars = {}

    for u in raw_users:
        u_id = u["user_id"]
        users[u_id] = u.get("metadata", {}).get("team_name") or u["display_name"]
        
        # Sleeper stores custom team avatar in metadata['avatar'] or user['avatar']
        av_id = u.get("metadata", {}).get("avatar") or u.get("avatar")
        user_avatars[u_id] = get_team_avatar_url(av_id)
    rosters = get_json(f"{BASE_URL}/league/{league_id}/rosters") or []
    roster_to_name = {r["roster_id"]: users.get(r.get("owner_id"), f"Team {r['roster_id']}") for r in rosters}
    roster_to_avatar = {r["roster_id"]: user_avatars.get(r.get("owner_id"), get_team_avatar_url(None)) for r in rosters}
    roster_players_map = {r["roster_id"]: r.get("players", []) for r in rosters}
    
    roster_waiver_map = {}
    roster_moves_map = {}
    for r in rosters:
        r_settings = r.get("settings", {})
        roster_waiver_map[r["roster_id"]] = r_settings.get("waiver_position", 99)
        roster_moves_map[r["roster_id"]] = r_settings.get("total_moves", 0)

    drafts = get_json(f"{BASE_URL}/league/{league_id}/drafts") or []
    player_origin = defaultdict(dict)
    if drafts:
        picks = get_json(f"{BASE_URL}/draft/{drafts[0]['draft_id']}/picks") or []
        for pick in picks:
            r_id = pick.get("roster_id")
            p_id = pick.get("player_id")
            player_origin[r_id][p_id] = "Keeper" if pick.get("is_keeper") else "Draft"

    # Track transactions, verified move counts, audit logs, and dropped players
    players_lost_during_season = defaultdict(set)
    actual_moves_count = defaultdict(int)
    tx_audit_log = defaultdict(list)
    seen_tx_ids = set()

    for week in range(1, max_eval_week + 1):
        txs = get_json(f"{BASE_URL}/league/{league_id}/transactions/{week}") or []
        for tx in txs:
            tx_id = tx.get("transaction_id")
            
            # 1. Deduplicate by transaction ID
            if not tx_id or tx_id in seen_tx_ids:
                continue
            seen_tx_ids.add(tx_id)

            if tx.get("status") != "complete":
                continue

            tx_type = tx.get("type")
            if tx_type not in ("waiver", "free_agent", "trade"):
                continue

            # Check if this was an off-season move (before the draft finished)
            created_ts = tx.get("created", 0)
            draft_time = drafts[0].get("last_picked") or drafts[0].get("start_time") or 0 if drafts else 0
            is_in_season = created_ts >= draft_time

            adds = tx.get("adds") or {}
            drops = tx.get("drops") or {}

            if tx_type == "trade":
                participating_rosters = set(tx.get("roster_ids") or [])
            else:
                participating_rosters = set(list(adds.values()) + list(drops.values()))

            # Format human-readable date
            created_ts = tx.get("created")
            date_str = pd.to_datetime(created_ts, unit="ms").strftime("%b %d, %Y") if created_ts else f"Week {week}"

            for r_id in participating_rosters:
                if is_in_season:
                    actual_moves_count[r_id] += 1
                
                r_adds = [player_db.get(pid, {}).get("full_name") or f"Player {pid}" for pid, rid in adds.items() if rid == r_id]
                r_drops = [player_db.get(pid, {}).get("full_name") or f"Player {pid}" for pid, rid in drops.items() if rid == r_id]

                tx_audit_log[r_id].append({
                    "Date": date_str,
                    "Period": "🏈 In-Season" if is_in_season else "🏖️ Off-Season",
                    "Type": tx_type.replace("_", " ").title(),
                    "Added Player": ", ".join(r_adds) if r_adds else "-",
                    "Dropped Player": ", ".join(r_drops) if r_drops else "-"
                })

            for p_id, r_id in adds.items():
                player_origin[r_id][p_id] = "Trade" if tx_type == "trade" else "Waiver/FA"
            for p_id, r_id in drops.items():
                players_lost_during_season[r_id].add(p_id)

    # Positional source tracking
    points_by_source_pos = defaultdict(lambda: defaultdict(lambda: defaultdict(float)))
    actual_pts_total = defaultdict(float)
    max_pts_total = defaultdict(float)
    weekly_scores = defaultdict(list)
    reg_pts, playoff_pts = defaultdict(float), defaultdict(float)
    reg_games, playoff_games = defaultdict(int), defaultdict(int)
    
    actual_pos_pts = defaultdict(lambda: defaultdict(float))
    optimal_pos_pts = defaultdict(lambda: defaultdict(float))
    player_season_pts = defaultdict(float)
    weekly_records = []

    for week in range(1, max_eval_week + 1):
        matchups = get_json(f"{BASE_URL}/league/{league_id}/matchups/{week}") or []
        for m in matchups:
            r_id = m.get("roster_id")
            actual_score = m.get("points", 0.0)
            starters = m.get("starters") or []
            starters_pts = m.get("starters_points") or []
            players_pts = m.get("players_pts") or m.get("players_points") or {}

            for pid, pts in players_pts.items():
                player_season_pts[pid] += (pts or 0.0)

            opt_total, opt_by_slot = calculate_optimal_lineup(players_pts, starter_positions, player_db)
            opt_total = max(opt_total, actual_score)

            actual_pts_total[r_id] += actual_score
            max_pts_total[r_id] += opt_total

            # Positional actual tracking by TRUE player position
            for pid, pts in zip(starters, starters_pts):
                pts = pts or 0.0
                p_info = player_db.get(pid, {})
                true_pos = p_info.get("position") or "FLEX"
                actual_pos_pts[r_id][true_pos] += pts
                origin = player_origin[r_id].get(pid, "Waiver/FA")
                points_by_source_pos[r_id]["All Positions"][origin] += pts
                points_by_source_pos[r_id][true_pos][origin] += pts

            # Positional optimal tracking by TRUE player position
            for pos_key, pts in opt_by_slot.items():
                optimal_pos_pts[r_id][pos_key] += pts

            weekly_records.append({
                "Week": week,
                "Roster ID": r_id,
                "Team": roster_to_name.get(r_id),
                "Actual Score": actual_score,
                "Max Score": opt_total,
                "Left on Bench": opt_total - actual_score,
                "Efficiency": (actual_score / opt_total * 100) if opt_total > 0 else 100.0,
                "Is_Playoff": week >= playoff_start
            })

            if week < playoff_start:
                weekly_scores[week].append((r_id, actual_score))
                reg_pts[r_id] += actual_score
                reg_games[r_id] += 1
            else:
                playoff_pts[r_id] += actual_score
                playoff_games[r_id] += 1

    # All-Play
    ap_wins, ap_losses = defaultdict(int), defaultdict(int)
    for week, scores in weekly_scores.items():
        for r_id, s in scores:
            for opp_id, opp_s in scores:
                if r_id == opp_id:
                    continue
                if s > opp_s:
                    ap_wins[r_id] += 1
                elif s < opp_s:
                    ap_losses[r_id] += 1

    bracket = get_json(f"{BASE_URL}/league/{league_id}/winners_bracket") or []
    champ_id = None
    if bracket:
        max_round = max(m.get("r", 0) for m in bracket)
        for m in bracket:
            if m.get("r") == max_round and m.get("p") == 1:
                champ_id = m.get("w")
                break

    # Build Summary DataFrame
    summary = []
    for r_id, team in roster_to_name.items():
        src = points_by_source_pos[r_id]["All Positions"]
        total_starter_pts = sum(src.values()) or 1.0
        act = actual_pts_total[r_id]
        mx = max_pts_total[r_id]
        eff = (act / mx * 100) if mx > 0 else 0.0
        missed_pts = mx - act
        
        ap_w = ap_wins[r_id]
        ap_l = ap_losses[r_id]
        ap_pct = (ap_w / (ap_w + ap_l) * 100) if (ap_w + ap_l) > 0 else 0.0

        # Caps IQ cleanly between 0% and 100%
        def get_pos_iq(pos_key):
            act_p = actual_pos_pts[r_id].get(pos_key, 0.0)
            opt_p = optimal_pos_pts[r_id].get(pos_key, 0.0)
            if opt_p <= 0:
                return 100.0 if act_p >= opt_p else 0.0
            return round(min(100.0, max(0.0, (act_p / opt_p * 100))), 1)

        # Assigns letter grade based on efficiency
        if eff >= 93.0:
            grade = "A+"
        elif eff >= 88.0:
            grade = "A"
        elif eff >= 83.0:
            grade = "B"
        elif eff >= 78.0:
            grade = "C"
        else:
            grade = "D"

        status = "🔥 Contender" if ap_pct >= 55.0 else ("🏗️ Rebuilder" if ap_pct <= 42.0 else "⚖️ In The Hunt")
        games_count = max(reg_games[r_id], 1)
        # Extract real win-loss record from roster settings
        r_obj = next((r for r in rosters if r["roster_id"] == r_id), {})
        r_settings = r_obj.get("settings", {})
        wins = r_settings.get("wins", 0)
        losses = r_settings.get("losses", 0)
        ties = r_settings.get("ties", 0)
        record_str = f"{wins}-{losses}" + (f"-{ties}" if ties > 0 else "")

        summary.append({
            "Logo": roster_to_avatar.get(r_id, get_team_avatar_url(None)),  # <-- ADD THIS
            "Roster ID": r_id,
            "Team": ("🏆 " if r_id == champ_id else "") + team,
            "Raw Team": team,
            "Record": record_str,         # <-- ADD THIS
            "Wins": wins,                 # <-- ADD THIS
            "Grade": grade,               # <-- Added Grade
            "Status": status,
            "Is Champion": r_id == champ_id,
            "Waiver Priority": f"#{roster_waiver_map.get(r_id, 99)}",
            "Waiver Priority Raw": roster_waiver_map.get(r_id, 99),
            "Total Moves": actual_moves_count.get(r_id, 0),
            "Overall IQ (%)": round(eff, 1),
            "QB IQ (%)": get_pos_iq("QB"),
            "RB IQ (%)": get_pos_iq("RB"),
            "WR IQ (%)": get_pos_iq("WR"),
            "TE IQ (%)": get_pos_iq("TE"),
            "FLEX IQ (%)": get_pos_iq("FLEX"),
            "QB PPG": round(actual_pos_pts[r_id].get("QB", 0.0) / games_count, 1),
            "RB PPG": round(actual_pos_pts[r_id].get("RB", 0.0) / games_count, 1),
            "WR PPG": round(actual_pos_pts[r_id].get("WR", 0.0) / games_count, 1),
            "TE PPG": round(actual_pos_pts[r_id].get("TE", 0.0) / games_count, 1),
            "FLEX PPG": round(actual_pos_pts[r_id].get("FLEX", 0.0) / games_count, 1),
            "K PPG": round(actual_pos_pts[r_id].get("K", 0.0) / games_count, 1),
            "DEF PPG": round(actual_pos_pts[r_id].get("DEF", 0.0) / games_count, 1),
            "Keeper %": round((src.get("Keeper", 0) / total_starter_pts) * 100, 1),
            "Draft %": round((src.get("Draft", 0) / total_starter_pts) * 100, 1),
            "Waiver %": round((src.get("Waiver/FA", 0) / total_starter_pts) * 100, 1),
            "Trade %": round((src.get("Trade", 0) / total_starter_pts) * 100, 1),
            "Pts Left on Bench": round(missed_pts, 1),
            "Avg Bench Pts/Wk": round(missed_pts / max(max_eval_week, 1), 1),
            "All-Play Win %": round(ap_pct, 1),
            "All-Play Record": f"{ap_w}-{ap_l}",
            "Reg PPG": round(reg_pts[r_id] / games_count, 1),
            "Playoff PPG": round(playoff_pts[r_id] / max(playoff_games[r_id], 1), 1)
        })

    # Assemble Positional Attribution Table
    pos_source_rows = []
    for r_id, team in roster_to_name.items():
        for p_cat in ["All Positions", "QB", "RB", "WR", "TE", "FLEX"]:
            s_map = points_by_source_pos[r_id][p_cat]
            t_pts = sum(s_map.values())
            pos_source_rows.append({
                "Team": team,
                "Position": p_cat,
                "Keeper %": round((s_map.get("Keeper", 0) / t_pts) * 100, 1) if t_pts > 0 else 0.0,
                "Draft %": round((s_map.get("Draft", 0) / t_pts) * 100, 1) if t_pts > 0 else 0.0,
                "Waiver %": round((s_map.get("Waiver/FA", 0) / t_pts) * 100, 1) if t_pts > 0 else 0.0,
                "Trade %": round((s_map.get("Trade", 0) / t_pts) * 100, 1) if t_pts > 0 else 0.0,
            })
    df_pos_sources = pd.DataFrame(pos_source_rows)

    # ----------------- TEAM FIDELITY (LOYALTY) CALCULATION -----------------
    prev_owner_players = defaultdict(set)
    if prev_league_id:
        prev_rosters = get_json(f"{BASE_URL}/league/{prev_league_id}/rosters") or []
        for pr in prev_rosters:
            p_owner = pr.get("owner_id")
            if p_owner:
                prev_owner_players[p_owner] = set(pr.get("players") or [])

    fidelity_rows = []
    for r in rosters:
        r_id = r["roster_id"]
        owner_id = r.get("owner_id")
        team_name = roster_to_name.get(r_id, f"Team {r_id}")
        curr_players = set(r.get("players") or [])
        lost_set = players_lost_during_season[r_id]

        if prev_league_id and owner_id in prev_owner_players:
            # Survived from last season through entire current season
            faithful_pids = [
                pid for pid in curr_players 
                if pid in prev_owner_players[owner_id] and pid not in lost_set
            ]
            base_count = len(prev_owner_players[owner_id].intersection(curr_players))
        else:
            # Inaugural season fallback: drafted players who survived all season
            drafted_set = set(player_origin[r_id].keys())
            faithful_pids = [pid for pid in curr_players if pid in drafted_set and pid not in lost_set]
            base_count = len(drafted_set.intersection(curr_players))

        faithful_names = [player_db.get(pid, {}).get("full_name") or f"Player {pid}" for pid in faithful_pids]
        # Sort by points scored
        faithful_names.sort(key=lambda n: next((player_season_pts.get(pid, 0.0) for pid in faithful_pids if (player_db.get(pid, {}).get("full_name") or f"Player {pid}") == n), 0.0), reverse=True)

        fidelity_pct = round((len(faithful_pids) / max(base_count, 1)) * 100, 1) if base_count > 0 else 0.0

        fidelity_rows.append({
            "Logo": roster_to_avatar.get(r_id, get_team_avatar_url(None)),  # <-- ADD THIS
            "Team": team_name,
            "Wire-to-Wire Survivors": len(faithful_pids),
            "Retained at Draft": base_count,
            "Fidelity Rate (%)": fidelity_pct,
            "Cornerstones": ", ".join(faithful_names[:5]) + (f" (+{len(faithful_names)-5} more)" if len(faithful_names) > 5 else "")
        })

    df_fidelity = pd.DataFrame(fidelity_rows).sort_values("Wire-to-Wire Survivors", ascending=False)

    return (
        pd.DataFrame(summary), 
        pd.DataFrame(weekly_records), 
        champ_id, 
        roster_to_name, 
        roster_players_map, 
        dict(player_season_pts),
        df_pos_sources,
        df_fidelity,
        dict(tx_audit_log),
        max_eval_week
    )
    
# ----------------- UI CONTROLLER -----------------
st.sidebar.title("🏈 Sleeper War Room")
league_id_input = st.sidebar.text_input("League ID", value="1341361816172789760")

with st.spinner("Connecting to Sleeper API..."):
    league_chain = get_league_chain(league_id_input)

if not league_chain:
    st.error("League not found. Check the League ID.")
    st.stop()

seasons_map = {f"{l.get('season')} - {l.get('name')}": l for l in league_chain}
selected_season_name = st.sidebar.selectbox("Select Season", list(seasons_map.keys()))
selected_league = seasons_map[selected_season_name]

with st.spinner("Analyzing season data..."):
    (
        df_summary, 
        df_weekly, 
        champ_id, 
        roster_names, 
        roster_players_map, 
        player_season_pts,
        df_pos_sources,
        df_fidelity,
        tx_audit_log,         # <-- CAPTURE THIS
        evaluated_weeks
    ) = process_season(
        selected_league["league_id"],
        selected_league.get("season"),
        tuple(selected_league.get("roster_positions", [])),
        selected_league.get("settings", {}).get("playoff_week_start", 15),
        selected_league.get("settings", {}).get("last_scored_leg", 17),
        selected_league.get("previous_league_id")
    )
    player_db = load_nfl_players()

st.title(f"🏆 {selected_league.get('name')} ({selected_league.get('season')})")
st.caption(f"Weeks Evaluated: **1 to {evaluated_weeks}** | Mode: **15-Keeper PPR**")

# ----------------- TABS -----------------
tab1, tab2, tab3, tab4, tab5, tab6, tab7 = st.tabs([
    "📊 Standings & Anatomy", 
    "⚡ Positional Strengths & Trades",
    "📡 Waiver Radar & Handcuffs", 
    "🧠 Positional Lineup IQ", 
    "🛡️ 15-Keeper Cut-Line", 
    "🍀 Matchup Luck",
    "🏈 TheMarshallFaulks War Room"  
])

# ----- TAB 1: STANDINGS, ATTRIBUTION & FIDELITY -----
with tab1:
    st.subheader("Season Standings & Summary")
    
    # Explainer for Contender / Rebuilder status
    with st.expander("ℹ️ How Team Status (Contender vs. Rebuilder) is Determined"):
        st.markdown(
            """
            Team statuses are determined by **All-Play Win %** (how each roster performs against every league team weekly):
            * **🔥 Contender (All-Play ≥ 55%):** Roster has legitimate week-to-week dominance regardless of schedule luck. Buy win-now starters.
            * **⚖️ In The Hunt (All-Play 43%–54%):** Playoff-bubble rosters. Highly sensitive to weekly start/sit choices.
            * **🏗️ Rebuilder (All-Play ≤ 42%):** Consistently outscored by the league. Should sell aging veterans for keeper-bubble talent and future picks.
            """
        )

    cols_to_show = [
        "Logo", "Team", "Record", "Status", "Waiver Priority", "Total Moves", "Overall IQ (%)", 
        "Keeper %", "Draft %", "Waiver %", "Trade %", "All-Play Win %", "Reg PPG"
    ]
    standings_config = {
        "Logo": st.column_config.ImageColumn("Logo", width="small"),
        "Team": st.column_config.TextColumn("Team", width="medium"),
        "Record": st.column_config.TextColumn("Record", width="small")
    }
    st.dataframe(
        df_summary[cols_to_show], 
        column_config=standings_config,
        use_container_width=False, 
        hide_index=True
    )

    st.markdown("---")

    # 1. 100% POSITIONAL SHARE (NEW REQUESTED CHART)
    st.subheader("📊 Team Scoring DNA (100% Positional Point Share)")
    st.caption("Normalized breakdown: What percentage of each team's total points comes from each position group?")

    share_rows = []
    for _, row in df_summary.iterrows():
        t_name = row["Raw Team"]
        pos_ppg = {
            "QB": row.get("QB PPG", 0),
            "RB": row.get("RB PPG", 0),
            "WR": row.get("WR PPG", 0),
            "TE": row.get("TE PPG", 0),
            "FLEX": row.get("FLEX PPG", 0),
            "K": row.get("K PPG", 0),
            "DEF": row.get("DEF PPG", 0)
        }
        total_ppg = sum(pos_ppg.values()) or 1.0
        for pos_name, val in pos_ppg.items():
            share_rows.append({
                "Team": t_name,
                "Position": pos_name,
                "Point Share %": round((val / total_ppg) * 100, 1)
            })

    df_pos_share = pd.DataFrame(share_rows)
    fig_share = px.bar(
        df_pos_share,
        x="Team",
        y="Point Share %",
        color="Position",
        title="Weekly Points Distribution by Position (Normalized to 100%)",
        color_discrete_sequence=px.colors.qualitative.Plotly
    )
    fig_share.update_layout(barmode="stack", xaxis_tickangle=-45, yaxis_title="% Contribution to Team Score")
    st.plotly_chart(fig_share, use_container_width=False)

    st.markdown("---")

    # 2. ACQUISITION TYPE ATTRIBUTION
    st.subheader("Points Attribution by Acquisition Type")
    pos_choice = st.selectbox(
        "Filter Point Source by Position:", 
        ["All Positions", "QB", "RB", "WR", "TE", "FLEX"],
        key="pos_attribution_select"
    )
    filtered_source_df = df_pos_sources[df_pos_sources["Position"] == pos_choice]

    fig_sources = px.bar(
        filtered_source_df,
        x="Team",
        y=["Keeper %", "Draft %", "Waiver %", "Trade %"],
        title=f"Starting Points Attribution: {pos_choice}",
        color_discrete_map={"Keeper %": "#1f77b4", "Draft %": "#2ca02c", "Waiver %": "#ff7f0e", "Trade %": "#d62728"}
    )
    fig_sources.update_layout(barmode="stack", xaxis_tickangle=-45, yaxis_title="Percentage of Points")
    st.plotly_chart(fig_sources, use_container_width=False)

    st.markdown("---")

    # 3. TEAM FIDELITY
    st.subheader("🛡️ Team Fidelity & Franchise Loyalty")
    f_col1, f_col2 = st.columns([1, 1])
    with f_col1:
        fig_fid = px.bar(
            df_fidelity,
            x="Wire-to-Wire Survivors",
            y="Team",
            orientation="h",
            text="Wire-to-Wire Survivors",
            title="Wire-to-Wire Franchise Pillars (Count)",
            color="Wire-to-Wire Survivors",
            color_continuous_scale="Teal"
        )
        fig_fid.update_layout(yaxis=dict(autorange="reversed"))
        st.plotly_chart(fig_fid, use_container_width=False)

    with f_col2:
        st.markdown("### 🏆 Franchise Cornerstones Table")
        cornerstone_config = {
            "Logo": st.column_config.ImageColumn("Logo", width="small"),
            "Team": st.column_config.TextColumn("Team", width="medium")
        }
        st.dataframe(
            df_fidelity[["Logo", "Team", "Wire-to-Wire Survivors", "Fidelity Rate (%)", "Cornerstones"]],
            column_config=cornerstone_config,
            use_container_width=False,
            hide_index=True
        )
    with st.expander("🔍 Transaction Auditor (Inspect every move logged by Sleeper)"):
        audit_team = st.selectbox("Select Team to Inspect Moves", list(roster_names.values()), key="audit_team_sel")
        audit_r_id = [k for k, v in roster_names.items() if v == audit_team][0]
        team_txs = tx_audit_log.get(audit_r_id, [])
        if team_txs:
            st.dataframe(
                pd.DataFrame(team_txs), 
                use_container_width=False, 
                hide_index=True
            )
        else:
            st.write("No waiver, free agent, or trade transactions found on record.")
                
# ----- TAB 2: POSITIONAL STRENGTHS & TRADES -----
with tab2:
    st.subheader("Positional Power Rankings (PPG from Starting Slots)")
    st.markdown("Rankings (1 = Best in League) based on starter production across each position group.")

    # 1. Include all active position PPG columns + Reg PPG for tiebreaking
    all_pos_cols = ["QB PPG", "RB PPG", "WR PPG", "TE PPG", "FLEX PPG", "K PPG", "DEF PPG"]
    active_pos_cols = [c for c in all_pos_cols if c in df_summary.columns]

    df_ranks = df_summary[["Logo", "Raw Team", "Reg PPG"] + active_pos_cols].copy()
    rank_cols = []

    # 2. Break ties using overall team Reg PPG so every rank is a distinct 1 to 10 (no repeats!)
    for col in active_pos_cols:
        r_col = col.replace("PPG", "Rank")
        df_ranks = df_ranks.sort_values(by=[col, "Reg PPG"], ascending=[False, False])
        df_ranks[r_col] = list(range(1, len(df_ranks) + 1))
        rank_cols.append(r_col)

    # Sort back alphabetically by team name
    df_ranks = df_ranks.sort_values("Raw Team").reset_index(drop=True)
    display_ranks = df_ranks[["Raw Team"] + rank_cols]

    # 3. Style ONLY the digits (returning empty string "" keeps 4-7 fully visible!)
    def color_number_outliers(val):
        if isinstance(val, (int, float)):
            if val <= 3:
                return "color: #E53935; font-weight: 800;"
            elif val >= 8:
                return "color: #1E88E5; font-weight: 800;"
        return ""

    styler_map = display_ranks.style.map if hasattr(display_ranks.style, "map") else display_ranks.style.applymap
    styled_ranks = styler_map(color_number_outliers, subset=rank_cols)

    # 1. Define compact column widths (fits each column to the size of its header)
    col_config = {
        "Logo": st.column_config.ImageColumn("Logo", width="small"),
        "Raw Team": st.column_config.TextColumn("Team", width="medium")
    }
    for col in rank_cols:
        col_config[col] = st.column_config.NumberColumn(col, width="small")

    # 2. Display with use_container_width=False so it hugs the data tightly
    st.caption("🔴 **Red = Top 3 Strength (1–3)** | 🔵 **Blue = Bottom 3 Deficit (8–10)** | Standard = Mid-Tier (4–7)")
    st.dataframe(
        styled_ranks, 
        column_config=col_config,
        use_container_width=False,  # <-- Hugs the content, stops stretching across the whole screen!
        hide_index=True
    )

    st.markdown("---")
    c_left, c_right = st.columns([1, 1])

    # ----------------- RADAR PROFILE (LEFT COLUMN) -----------------
    with c_left:
        st.subheader("🕸️ Team Positional Radar Profile")
        inspect_team = st.selectbox("Select Team for Radar", list(roster_names.values()), key="radar_sel")
        team_row = df_summary[df_summary["Raw Team"] == inspect_team].iloc[0]

        radar_categories = ["QB", "RB", "WR", "TE", "FLEX", "K", "DEF"]
        active_radar_cats = [c for c in radar_categories if f"{c} PPG" in df_summary.columns]
        
        team_vals = [team_row[f"{c} PPG"] for c in active_radar_cats]
        league_avg = [df_summary[f"{c} PPG"].mean() for c in active_radar_cats]

        fig_radar = go.Figure()
        fig_radar.add_trace(go.Scatterpolar(r=team_vals, theta=active_radar_cats, fill='toself', name=inspect_team, line_color="#1f77b4"))
        fig_radar.add_trace(go.Scatterpolar(r=league_avg, theta=active_radar_cats, fill='toself', name='League Average', line_color="rgba(180,180,180,0.5)"))
        fig_radar.update_layout(polar=dict(radialaxis=dict(visible=True)), showlegend=True, title=f"Scoring Profile: {inspect_team}")
        st.plotly_chart(fig_radar, use_container_width=False)

    # ----------------- TRADE RECOMMENDATIONS (RIGHT COLUMN) -----------------
    with c_right:
        st.subheader("💡 Automated Trade Synergy Engine")
        
        target_team_filter = st.selectbox(
            "Find Best Trade Partners For:", 
            ["All Teams"] + list(roster_names.values()), 
            key="trade_filter_team"
        )

        team_ranks = {}
        for _, row in df_ranks.iterrows():
            team_ranks[row["Raw Team"]] = {pos: row[f"{pos} Rank"] for pos in ["QB", "RB", "WR", "TE"]}

        teams_list = df_ranks["Raw Team"].tolist()
        trade_matches = []
        seen_pairs = set()

        positions = ["QB", "RB", "WR", "TE"]
        for i in range(len(teams_list)):
            for j in range(i + 1, len(teams_list)):
                ta = teams_list[i]
                tb = teams_list[j]

                for p1 in positions:
                    for p2 in positions:
                        if p1 == p2:
                            continue

                        rank_ta_p1 = team_ranks[ta][p1]
                        rank_ta_p2 = team_ranks[ta][p2]
                        rank_tb_p1 = team_ranks[tb][p1]
                        rank_tb_p2 = team_ranks[tb][p2]

                        imbalance_a = rank_ta_p2 - rank_ta_p1
                        imbalance_b = rank_tb_p1 - rank_tb_p2

                        if imbalance_a >= 2 and imbalance_b >= 2:
                            synergy = imbalance_a + imbalance_b
                            pair_key = (tuple(sorted([ta, tb])), tuple(sorted([p1, p2])))

                            if pair_key not in seen_pairs:
                                seen_pairs.add(pair_key)
                                trade_matches.append({
                                    "Team A": ta,
                                    "Team B": tb,
                                    "Surplus Pos A": p1,
                                    "Deficit Pos A": p2,
                                    "Rank A Surplus": rank_ta_p1,
                                    "Rank A Deficit": rank_ta_p2,
                                    "Surplus Pos B": p2,
                                    "Deficit Pos B": p1,
                                    "Rank B Surplus": rank_tb_p2,
                                    "Rank B Deficit": rank_tb_p1,
                                    "Synergy": synergy
                                })

        trade_matches.sort(key=lambda x: x["Synergy"], reverse=True)

        if target_team_filter != "All Teams":
            filtered_matches = [m for m in trade_matches if m["Team A"] == target_team_filter or m["Team B"] == target_team_filter]
        else:
            filtered_matches = trade_matches

        if filtered_matches:
            for match in filtered_matches[:4]:
                ta = match["Team A"]
                tb = match["Team B"]
                st.info(
                    f"🤝 **Synergy Score: {match['Synergy']} pts** | **{ta}** ↔ **{tb}**\n\n"
                    f"* **{ta}**: Strong at **{match['Surplus Pos A']}** (#{match['Rank A Surplus']}) ➔ Needs **{match['Deficit Pos A']}** (#{match['Rank A Deficit']})\n"
                    f"* **{tb}**: Strong at **{match['Surplus Pos B']}** (#{match['Rank B Surplus']}) ➔ Needs **{match['Deficit Pos B']}** (#{match['Rank B Deficit']})\n"
                    f"* **Proposal:** `{ta}` sends starter **{match['Surplus Pos A']}** to `{tb}` for starter **{match['Surplus Pos B']}**."
                )
        else:
            st.write(f"No complementary trades found for {target_team_filter}.")
            
# ----- TAB 3: WAIVER RADAR, ROOKIES & HANDCUFFS -----
with tab3:
    st.subheader("📋 League Waiver Wire Priority Order")
    df_waivers = df_summary.sort_values("Waiver Priority Raw", ascending=True).reset_index(drop=True)

    if len(df_waivers) >= 3:
        w1, w2, w3 = st.columns(3)
        w1.metric("🥇 Priority #1 (Golden Claim)", df_waivers.iloc[0]["Raw Team"], f"{df_waivers.iloc[0]['Total Moves']} moves made")
        w2.metric("🥈 Priority #2", df_waivers.iloc[1]["Raw Team"], f"{df_waivers.iloc[1]['Total Moves']} moves made")
        w3.metric("🥉 Priority #3", df_waivers.iloc[2]["Raw Team"], f"{df_waivers.iloc[2]['Total Moves']} moves made")

    with st.expander("🔍 View Complete League Priority Order"):
        st.dataframe(
            df_waivers[["Waiver Priority", "Raw Team", "Total Moves", "Status", "All-Play Record"]],
            use_container_width=False,
            hide_index=True
        )

    st.markdown("---")

    # Data collection with Avatars & Injuries
    proj_week = max(1, evaluated_weeks)
    projections_map = get_weekly_projections(selected_league.get("season"), proj_week)

    all_rostered_ids = set()
    for p_list in roster_players_map.values():
        all_rostered_ids.update(p_list)

    free_agents = []
    rookies = []
    ir_stashes = []

    injury_badge_map = {
        "IR": "🏥 IR",
        "Out": "🔴 Out",
        "Questionable": "🟡 Q",
        "Doubtful": "🟠 D",
        "PUP": "🏥 PUP",
        "Sus": "⚖️ Susp"
    }

    for pid, p_info in player_db.items():
        if pid not in all_rostered_ids:
            nfl_t = p_info.get("team")
            pos = p_info.get("position")
            
            if nfl_t and pos in ("QB", "RB", "WR", "TE"):
                pts = player_season_pts.get(pid, 0.0)
                proj = projections_map.get(pid, 0.0)
                depth = p_info.get("depth_chart_order")
                years_exp = p_info.get("years_exp", 99)
                raw_inj = p_info.get("injury_status")
                inj_display = injury_badge_map.get(raw_inj, "🟢 Healthy")

                player_entry = {
                    "Photo": get_player_avatar_url(pid),
                    "Player": p_info.get("full_name") or f"Player {pid}",
                    "Pos": pos,
                    "NFL Team": nfl_t,
                    "Injury": inj_display,
                    "Depth Chart": f"#{depth}" if depth else "N/A",
                    "Depth Raw": depth if depth else 99,
                    "Proj Pts": round(proj, 1),
                    "Season Pts": round(pts, 1),
                    "PPG": round(pts / max(evaluated_weeks, 1), 1),
                    "Age": p_info.get("age", "N/A")
                }

                free_agents.append(player_entry)

                if years_exp == 0:
                    role_status = "⭐ Starter" if depth == 1 else ("🔥 Primary Backup" if depth == 2 else "📈 Depth Stash")
                    if pts >= 8.0:
                        role_status = "⚡ Breakout"
                    rookie_entry = player_entry.copy()
                    rookie_entry["Role Status"] = role_status
                    rookies.append(rookie_entry)

                if raw_inj in ("IR", "Out", "PUP"):
                    ir_stashes.append(player_entry)

    df_fa = pd.DataFrame(free_agents)
    df_rookies = pd.DataFrame(rookies)
    df_ir = pd.DataFrame(ir_stashes)

    # Clean, fluid column configuration for Tab 3
    scouting_col_config = {
        "Photo": st.column_config.ImageColumn("", width="small"),
        "Player": st.column_config.TextColumn("Player", width="medium"),
        "Pos": st.column_config.TextColumn("Pos", width="small"),
        "NFL Team": st.column_config.TextColumn("Team", width="small"),
        "Injury": st.column_config.TextColumn("Injury", width="medium"),
        "Role Status": st.column_config.TextColumn("Role", width="medium"),
        "Depth Chart": st.column_config.TextColumn("Depth", width="small"),
        "Proj Pts": st.column_config.NumberColumn("Proj Pts", format="%.1f", width="small"),
        "Season Pts": st.column_config.NumberColumn("Season Pts", format="%.1f", width="small"),
        "PPG": st.column_config.NumberColumn("PPG", format="%.1f", width="small"),
        "Age": st.column_config.NumberColumn("Age", width="small")
    }

    st.subheader("📡 Waiver Wire Intelligence Hub")
    subtab1, subtab2, subtab3, subtab4 = st.tabs([
        "🏆 Top Unrostered Scorers", 
        "🌟 Rising Rookie Radar (Year 1)", 
        "🚨 High-Value Handcuff Watch (RB2s)",
        "🏥 Free IR Stash Radar (Open IR Slot)"
    ])

    with subtab1:
        c_filter, _ = st.columns([1, 2])
        with c_filter:
            pos_filter = st.selectbox("Position Filter", ["All", "RB", "WR", "TE", "QB"], key="fa_pos_select")
        df_fa_filtered = df_fa if pos_filter == "All" else df_fa[df_fa["Pos"] == pos_filter]
        top_scorers = df_fa_filtered.sort_values(by=["Season Pts", "Proj Pts"], ascending=[False, False]).head(15)
        
        st.dataframe(
            top_scorers[["Photo", "Player", "Pos", "NFL Team", "Injury", "Depth Chart", "Proj Pts", "Season Pts", "PPG", "Age"]],
            column_config=scouting_col_config,
            use_container_width=True,   # <-- Expands smoothly across the tab (eliminates horizontal scrollbar!)
            hide_index=True,
            height=(len(top_scorers) + 1) * 38 + 10  # <-- Fits all 15 rows with ZERO cutoffs or vertical scrollbars!
        )

    with subtab2:
        st.caption("Unrostered Year-1 Rookies ascending depth charts.")
        if not df_rookies.empty:
            sorted_rookies = df_rookies.sort_values(by=["Depth Raw", "Season Pts", "Proj Pts"], ascending=[True, False, False]).head(15)
            st.dataframe(
                sorted_rookies[["Photo", "Player", "Pos", "NFL Team", "Injury", "Role Status", "Depth Chart", "Proj Pts", "Season Pts", "Age"]],
                column_config=scouting_col_config,
                use_container_width=True,
                hide_index=True,
                height=(len(sorted_rookies) + 1) * 38 + 10
            )
        else:
            st.write("No active unrostered rookies found.")

    with subtab3:
        st.caption("Unrostered backup RBs who are **one injury away** (`Depth Chart = 2`).")
        handcuffs = df_fa[(df_fa["Pos"] == "RB") & (df_fa["Depth Raw"] == 2)].sort_values(by=["Proj Pts", "Season Pts"], ascending=[False, False]).head(15)
        st.dataframe(
            handcuffs[["Photo", "Player", "NFL Team", "Injury", "Depth Chart", "Proj Pts", "Season Pts", "Age"]],
            column_config=scouting_col_config,
            use_container_width=True,
            hide_index=True,
            height=(len(handcuffs) + 1) * 38 + 10
        )

    with subtab4:
        st.caption("💡 **IR Loophole:** Add and stash these injured players immediately without cutting an active bench player.")
        if not df_ir.empty:
            sorted_ir = df_ir.sort_values(by=["Season Pts", "Proj Pts"], ascending=[False, False]).head(15)
            st.dataframe(
                sorted_ir[["Photo", "Player", "Pos", "NFL Team", "Injury", "Depth Chart", "Season Pts", "Age"]],
                column_config=scouting_col_config,
                use_container_width=True,
                hide_index=True,
                height=(len(sorted_ir) + 1) * 38 + 10
            )
        else:
            st.write("No high-value free agent IR stashes currently available.")
        
# ----- TAB 4: MANAGER LINEUP IQ & DECISION DIAGNOSTIC -----
with tab4:
    st.subheader("🧠 Manager Lineup IQ & Start/Sit Diagnostic")

    best_mgr = df_summary.sort_values("Overall IQ (%)", ascending=False).iloc[0]
    worst_mgr = df_summary.sort_values("Overall IQ (%)", ascending=True).iloc[0]
    most_missed = df_summary.sort_values("Pts Left on Bench", ascending=False).iloc[0]

    m1, m2, m3 = st.columns(3)
    m1.metric("🏆 Best Head Coach", f"{best_mgr['Raw Team']} ({best_mgr['Grade']})", f"{best_mgr['Overall IQ (%)']:.1f}%")
    m2.metric("⚠️ Lowest Lineup IQ", f"{worst_mgr['Raw Team']} ({worst_mgr['Grade']})", f"{worst_mgr['Overall IQ (%)']:.1f}%")
    m3.metric("💔 Most Pts Left on Pine", f"{most_missed['Raw Team']}", f"{most_missed['Pts Left on Bench']:.1f} pts")

    st.markdown("---")

    st.subheader("📋 Master Start/Sit Coaching Scorecard")
    st.caption("🔴 **Red = Top 3 / Elite (≥92%)** | 🔵 **Blue = Deficit (<80%)** | Standard = Average")

    scorecard_cols = ["Logo", "Team", "Grade", "Overall IQ (%)", "Avg Bench Pts/Wk", "QB IQ (%)", "RB IQ (%)", "WR IQ (%)", "TE IQ (%)", "K IQ (%)", "DEF IQ (%)"]
    active_scorecard = [c for c in scorecard_cols if c in df_summary.columns]
    df_scorecard = df_summary.sort_values("Overall IQ (%)", ascending=False)[active_scorecard].copy()

    # Float columns to format
    num_cols = [c for c in active_scorecard if "IQ" in c or "Avg" in c]

    # Red/Blue styling
    def style_iq_outliers(val):
        if isinstance(val, (int, float)):
            if val >= 92.0:
                return "color: #E53935; font-weight: 800;"  # Red for high
            elif val < 80.0:
                return "color: #1E88E5; font-weight: 800;"  # Blue for low
        return ""

    # Styler with formatting to 1 decimal place
    styler_sc = df_scorecard.style.map if hasattr(df_scorecard.style, "map") else df_scorecard.style.applymap
    styled_scorecard = styler_sc(style_iq_outliers, subset=num_cols)
    styled_scorecard = styled_scorecard.format({col: "{:.1f}" for col in num_cols})

    # Configure compact columns and explicit 1-decimal display
    col_config = {
        "Logo": st.column_config.ImageColumn("", width=45),
        "Team": st.column_config.TextColumn("Team", width=140),
        "Grade": st.column_config.TextColumn("Grade", width=65),
        "Overall IQ (%)": st.column_config.NumberColumn("Overall IQ", format="%.1f", width=95),
        "Avg Bench Pts/Wk": st.column_config.NumberColumn("Avg Bench Pts", format="%.1f", width=110)
    }
    for c in ["QB IQ (%)", "RB IQ (%)", "WR IQ (%)", "TE IQ (%)", "K IQ (%)", "DEF IQ (%)"]:
        if c in df_scorecard.columns:
            col_config[c] = st.column_config.NumberColumn(c.replace(" IQ (%)", ""), format="%.1f", width=65)

    st.dataframe(
        styled_scorecard, 
        column_config=col_config,
        use_container_width=False,
        hide_index=True
    )

    st.markdown("---")

    # ----------------- TABLE 2: BENCH BLUNDERS -----------------
    st.subheader("💔 Worst Start/Sit Blunders of the Season")
    st.caption("Weeks where a manager left a mountain of points on the pine.")

    worst_weeks = df_weekly.sort_values("Left on Bench", ascending=False).head(10).reset_index(drop=True)
    
    # 1. Map team logos from df_summary so it matches the rest of the app
    team_logo_map = dict(zip(df_summary["Raw Team"], df_summary["Logo"]))
    worst_weeks["Logo"] = worst_weeks["Team"].map(team_logo_map)
    
    worst_weeks["Week Label"] = worst_weeks["Week"].apply(lambda w: f"Week {w}")
    worst_weeks["Actual Pts"] = worst_weeks["Actual Score"].apply(lambda s: round(float(s), 1))
    worst_weeks["Max Potential"] = worst_weeks["Max Score"].apply(lambda m: round(float(m), 1))
    worst_weeks["Points Missed"] = worst_weeks["Left on Bench"].apply(lambda p: round(float(p), 1))
    worst_weeks["Efficiency %"] = worst_weeks["Efficiency"].apply(lambda e: round(float(e), 1))

    # Highlight severe blunders: >=25 pts in Bold Red, <=12 in Blue
    def style_blunder_outliers(val):
        if isinstance(val, (int, float)):
            if val >= 25.0:
                return "color: #E53935; font-weight: 800;"  # Severe blunder
            elif val <= 12.0:
                return "color: #1E88E5; font-weight: 800;"
        return ""

    blunder_display = worst_weeks[["Logo", "Week Label", "Team", "Actual Pts", "Max Potential", "Points Missed", "Efficiency %"]]
    
    styler_blunder = blunder_display.style.map if hasattr(blunder_display.style, "map") else blunder_display.style.applymap
    styled_blunder = styler_blunder(style_blunder_outliers, subset=["Points Missed"])
    styled_blunder = styled_blunder.format({
        "Actual Pts": "{:.1f}",
        "Max Potential": "{:.1f}",
        "Points Missed": "-{:.1f}",
        "Efficiency %": "{:.1f}%"
    })

    # 2. Generous pixel widths: stops "Max Potential" and "Points Missed" from being squished!
    blunder_config = {
        "Logo": st.column_config.ImageColumn("", width=45),
        "Week Label": st.column_config.TextColumn("Week", width=75),
        "Team": st.column_config.TextColumn("Team", width=140),
        "Actual Pts": st.column_config.NumberColumn("Actual Pts", format="%.1f", width=90),
        "Max Potential": st.column_config.NumberColumn("Max Potential", format="%.1f", width=115),  # Room for full title
        "Points Missed": st.column_config.TextColumn("Pts Missed", width=100),                      # Room for full title
        "Efficiency %": st.column_config.TextColumn("Efficiency", width=95)
    }

    st.dataframe(
        styled_blunder,
        column_config=blunder_config,
        use_container_width=False,
        hide_index=True
        # Removed height: all 10 rows fit with clean padding and no sliced bottom row!
    )
    
# ----- TAB 5: 15-KEEPER BUBBLE & STRATEGY LAB -----
with tab5:
    st.subheader("🛡️ 15-Keeper Cut-Line & Roster Capital Analyzer")
    st.markdown(
        """
        In a 15-keeper league, **Roster Depth Past Player #15 is Wasted Capital** unless traded before the deadline.
        * 🟢 **Core Keepers (1–12):** Locked-in foundational assets.
        * 🟡 **The Keeper Bubble (13–17):** Fringe decisions. High risk of cutting someone who breaks out next year.
        * 🔴 **Cut Candidates (18+):** Will be dropped for free. **Package them in a 2-for-1 trade today.**
        """
    )

    selected_team_name = st.selectbox("Select Team to Inspect Keepers", list(roster_names.values()), key="keeper_sel")
    selected_r_id = [k for k, v in roster_names.items() if v == selected_team_name][0]
    
    team_player_ids = roster_players_map.get(selected_r_id, [])
    player_rows = []
    for pid in team_player_ids:
        p_info = player_db.get(pid, {})
        full_name = p_info.get("full_name") or f"Player {pid}"
        pos = p_info.get("position", "N/A")
        team = p_info.get("team") or "FA"
        season_pts = player_season_pts.get(pid, 0.0)
        player_rows.append({
            "Photo": get_player_avatar_url(pid),   # <-- PLAYER HEADSHOT
            "Player": full_name,
            "Pos": pos,
            "NFL Team": team,
            "Season Points": round(season_pts, 1),
            "PPG": round(season_pts / max(evaluated_weeks, 1), 1)
        })

    df_roster = pd.DataFrame(player_rows).sort_values("Season Points", ascending=False).reset_index(drop=True)
    df_roster["Rank"] = df_roster.index + 1

    def assign_keeper_status(rank):
        if rank <= 12:
            return "🟢 Core Keeper (1-12)"
        elif rank <= 17:
            return "🟡 Keeper Bubble (13-17)"
        else:
            return "🔴 Cut Candidate (18+)"
            
    df_roster["Keeper Status"] = df_roster["Rank"].apply(assign_keeper_status)

    # 1. VISUAL KEEPER CLIFF CHART
    fig_cliff = px.bar(
        df_roster,
        x="Rank",
        y="Season Points",
        hover_data=["Player", "Pos", "Keeper Status"],
        color="Keeper Status",
        title=f"Roster Talent Drop-Off & The Cut-Line: {selected_team_name}",
        color_discrete_map={
            "🟢 Core Keeper (1-12)": "#2ca02c",
            "🟡 Keeper Bubble (13-17)": "#ff7f0e",
            "🔴 Cut Candidate (18+)": "#d62728"
        }
    )
    fig_cliff.add_vline(x=15.5, line_dash="dash", line_color="red", annotation_text="15-Keeper Cut Line", annotation_position="top right")
    st.plotly_chart(fig_cliff, use_container_width=True)

    # 2. ROSTER TABLE WITH PLAYER HEADSHOTS
    c_tbl, c_diag = st.columns([3, 2])
    with c_tbl:
        roster_col_config = {
            "Rank": st.column_config.NumberColumn("#", width=50),
            "Photo": st.column_config.ImageColumn("", width=45),
            "Keeper Status": st.column_config.TextColumn("Keeper Status", width=160),
            "Player": st.column_config.TextColumn("Player", width=150),
            "Pos": st.column_config.TextColumn("Pos", width=60),
            "NFL Team": st.column_config.TextColumn("Team", width=65),
            "Season Points": st.column_config.NumberColumn("Season Pts", format="%.1f", width=95),
            "PPG": st.column_config.NumberColumn("PPG", format="%.1f", width=75)
        }

        st.dataframe(
            df_roster[["Rank", "Photo", "Keeper Status", "Player", "Pos", "NFL Team", "Season Points", "PPG"]],
            column_config=roster_col_config,
            use_container_width=False,
            hide_index=True
        )

    # 3. KEEPER CAPITAL STRATEGY DIAGNOSTIC
    with c_diag:
        st.markdown("### 💡 Keeper Strategy Diagnostic")
        bubble_players = df_roster[(df_roster["Rank"] >= 13) & (df_roster["Rank"] <= 17)]
        cut_players = df_roster[df_roster["Rank"] >= 18]

        pts_player_15 = df_roster.iloc[14]["Season Points"] if len(df_roster) >= 15 else 0
        pts_player_16 = df_roster.iloc[15]["Season Points"] if len(df_roster) >= 16 else 0
        cut_gap = round(pts_player_15 - pts_player_16, 1)

        if cut_gap < 15.0 and len(df_roster) >= 16:
            st.warning(
                f"⚠️ **Agonizing Cut Decision:** Player #15 ({df_roster.iloc[14]['Player']}) and Player #16 ({df_roster.iloc[15]['Player']}) "
                f"are separated by only **{cut_gap} points**.\n\n"
                f"**Recommendation:** Do NOT drop Player #16 for free! Package Player #14 and #15 in a **2-for-1 trade** to upgrade to a top-10 starter before cuts."
            )
        else:
            st.success(
                f"✅ **Clear Cut-Line:** You have a decisive {cut_gap}-point drop-off between your 15th keeper and your cut candidates."
            )

        st.info(
            f"📊 **Asset Inventory:**\n"
            f"* **Core Studs (1–12):** {min(12, len(df_roster))} players\n"
            f"* **Bubble Dilemmas (13–17):** {len(bubble_players)} players\n"
            f"* **Guaranteed Cuts (18+):** {len(cut_players)} players to trade away"
        )

    st.markdown("---")

    # 4. LEAGUE-WIDE: KEEPER CORE STRENGTH VS. ACTUAL WINS
    st.subheader("🏆 League-Wide: Top-15 Keeper Strength vs. Actual Wins")
    st.caption("Did teams with powerhouse keeper foundations actually win games, or did bad coaching and schedule luck derail them?")

    keeper_strength_rows = []
    for r_id, t_name in roster_names.items():
        p_ids = roster_players_map.get(r_id, [])
        pts_list = sorted([player_season_pts.get(pid, 0.0) for pid in p_ids], reverse=True)
        top15_pts = sum(pts_list[:15])
        
        t_sum = df_summary[df_summary["Raw Team"] == t_name].iloc[0]
        keeper_strength_rows.append({
            "Team": t_name,
            "Top-15 Keeper Points": round(top15_pts, 1),
            "Wins": t_sum.get("Wins", 0),
            "Record": t_sum.get("Record", "N/A"),
            "Status": t_sum.get("Status", "N/A")
        })

    df_core_vs_wins = pd.DataFrame(keeper_strength_rows)
    fig_core = px.scatter(
        df_core_vs_wins,
        x="Top-15 Keeper Points",
        y="Wins",
        text="Team",
        color="Status",
        size="Top-15 Keeper Points",
        title="Top-15 Keeper Production vs. Regular Season Wins",
        color_discrete_map={"🔥 Contender": "#2ca02c", "🏗️ Rebuilder": "#d62728", "⚖️ In The Hunt": "#ff7f0e"}
    )
    fig_core.update_traces(textposition="top center")
    st.plotly_chart(fig_core, use_container_width=True)
# =====================================================================
    # 5. THE 15-KEEPER DYNASTY LIFE-CYCLE & AGE RADAR (FEATURE 4)
    # =====================================================================
    st.markdown("---")
    st.subheader("⏳ Dynasty Asset Depreciation & Age Radar")
    st.caption("Identify 'Sell-High Windows' on aging veterans before their keeper value drops off a cliff.")

    # Calculate Dynasty Age Tiers
    age_audit_rows = []
    for pid in team_player_ids:
        p_info = player_db.get(pid, {})
        full_name = p_info.get("full_name") or f"Player {pid}"
        pos = p_info.get("position", "N/A")
        age = p_info.get("age", 25)
        pts = player_season_pts.get(pid, 0.0)

        # Dynasty Age Cliff Logic
        if pos == "RB":
            if age >= 29:
                window = "🚨 Sell-High Window (Cliff Approaching)"
            elif age <= 24:
                window = "💎 Ascending Youth (Core Asset)"
            else:
                window = "🟢 Prime Window"
        elif pos in ("WR", "TE"):
            if age >= 31:
                window = "🚨 Sell-High Window (Cliff Approaching)"
            elif age <= 24:
                window = "💎 Ascending Youth (Core Asset)"
            else:
                window = "🟢 Prime Window"
        elif pos == "QB":
            if age >= 35:
                window = "🚨 Sell-High Window (Cliff Approaching)"
            elif age <= 25:
                window = "💎 Ascending Youth (Core Asset)"
            else:
                window = "🟢 Prime Window"
        else:
            window = "Standard"

        age_audit_rows.append({
            "Player": full_name,
            "Pos": pos,
            "Age": age,
            "Season Pts": round(pts, 1),
            "Dynasty Window": window
        })

    df_age_radar = pd.DataFrame(age_audit_rows).sort_values(by=["Age", "Season Pts"], ascending=[False, False])

    avg_keeper_age = df_age_radar.head(15)["Age"].mean() if len(df_age_radar) >= 15 else 26.0

    a1, a2 = st.columns([1, 2])
    with a1:
        st.metric("Avg Keeper Age (Top 15)", f"{avg_keeper_age:.1f} yrs")
        if avg_keeper_age >= 28.0:
            st.warning("⚠️ **Win-Now Window:** Older roster. Maximize your championship push this year before age-related keeper cuts.")
        else:
            st.success("🌱 **Young Dynasty Core:** Roster has strong multi-year championship runway.")

    with a2:
        st.dataframe(
            df_age_radar[["Player", "Pos", "Age", "Season Pts", "Dynasty Window"]],
            use_container_width=True,
            hide_index=True
        )
        
# ----- TAB 6: LUCK VS STRENGTH -----
with tab6:
    st.subheader("Schedule Luck: True Strength vs Matchup Variance")
    fig_luck = px.scatter(
        df_summary,
        x="Reg PPG",
        y="All-Play Win %",
        text="Raw Team",
        size="Overall IQ (%)",
        color="Status",
        color_discrete_map={"🔥 Contender": "#2ca02c", "🏗️ Rebuilder": "#d62728", "⚖️ In The Hunt": "#ff7f0e"},
        title="Regular Season Scoring vs All-Play Strength"
    )
    fig_luck.update_traces(textposition='top center')
    st.plotly_chart(fig_luck, use_container_width=False)
    
# =====================================================================
# ----- TAB 7: THE MARSHALL FAULKS WAR ROOM & MATCHDAY ADVISOR -----
# =====================================================================
with tab7:
    st.subheader("🏈 TheMarshallFaulks Personal Matchup War Room")
    st.caption("Live Vegas odds, stadium weather impact, floor/ceiling volatility, and start/sit decision badges.")

    # 1. Identify Target Team
    all_team_names = list(roster_names.values())
    default_idx = 0
    for i, name in enumerate(all_team_names):
        if "marshall" in name.lower():
            default_idx = i
            break

    c_team_sel, c_wk_sel, c_view_mode = st.columns([2, 1, 2])
    with c_team_sel:
        selected_my_team = st.selectbox("Active Roster Target", all_team_names, index=default_idx, key="war_room_team_sel")
    
    nfl_st = get_nfl_state()
    active_nfl_wk = int(nfl_st.get("week") or 2)
    
    with c_wk_sel:
        selected_matchup_week = st.selectbox("NFL Week", list(range(1, 19)), index=active_nfl_wk - 1, key="war_room_week_sel")

    my_r_id = [k for k, v in roster_names.items() if v == selected_my_team][0]

    # 2. Fetch live Vegas & Weather for the selected week
    curr_season = selected_league.get("season", "2026")
    vegas_env = get_nfl_matchup_env(curr_season, selected_matchup_week)
    projections_map = get_weekly_projections(curr_season, selected_matchup_week)
    has_active_games = len(vegas_env) > 0

    # Extract Team Roster
    my_player_ids = roster_players_map.get(my_r_id, [])
    
    latest_matchups = get_json(f"{BASE_URL}/league/{selected_league['league_id']}/matchups/{selected_matchup_week}") or []
    my_latest_m = next((m for m in latest_matchups if m.get("roster_id") == my_r_id), {})
    current_starters_set = set(my_latest_m.get("starters") or [])

    # Historical completed points for volatility (Completed weeks only!)
    player_completed_pts = defaultdict(list)
    completed_weeks_count = max(0, selected_matchup_week - 1)

    if completed_weeks_count >= 1:
        for w in range(1, completed_weeks_count + 1):
            w_matchups = get_json(f"{BASE_URL}/league/{selected_league['league_id']}/matchups/{w}") or []
            for m in w_matchups:
                if m.get("roster_id") == my_r_id:
                    p_pts = m.get("players_points") or {}
                    for pid, pts in p_pts.items():
                        if pts is not None and pts > 0.0:
                            player_completed_pts[pid].append(pts)

    # Sleeper Exact Lineup Ordering
    starter_slots = [p for p in selected_league.get("roster_positions", []) if p not in ("BN", "IR", "TAXI")]
    my_starters_list = my_latest_m.get("starters") or []

    starter_slot_assignments = {}
    ordered_starter_pids = []
    slot_counts = defaultdict(int)

    for slot_name, pid in zip(starter_slots, my_starters_list):
        if pid and pid != "0":
            slot_counts[slot_name] += 1
            label = f"{slot_name}{slot_counts[slot_name]}" if starter_slots.count(slot_name) > 1 else slot_name
            starter_slot_assignments[pid] = label
            ordered_starter_pids.append(pid)

    bench_pids = [pid for pid in my_player_ids if pid not in starter_slot_assignments]
    pos_order = {"QB": 1, "RB": 2, "WR": 3, "TE": 4, "K": 5, "DEF": 6, "N/A": 7}
    bench_pids.sort(key=lambda pid: (
        pos_order.get(player_db.get(pid, {}).get("position", "N/A"), 7),
        -projections_map.get(pid, {}).get("pts", 0.0) if isinstance(projections_map.get(pid), dict) else 0.0
    ))

    ordered_all_pids = [(pid, starter_slot_assignments[pid], "⭐ Starter") for pid in ordered_starter_pids] + \
                       [(pid, f"BN ({player_db.get(pid, {}).get('position', 'BN')})", "Pine") for pid in bench_pids]

    injury_badge_map = {
        "IR": "🏥 IR",
        "Out": "🔴 Out",
        "Questionable": "🟡 Q",
        "Doubtful": "🟠 D",
        "PUP": "🏥 PUP",
        "Sus": "⚖️ Susp"
    }

    roster_board_rows = []
    for pid, slot_label, role_label in ordered_all_pids:
        p_info = player_db.get(pid, {})
        full_name = p_info.get("full_name") or f"Player {pid}"
        pos = p_info.get("position", "N/A")
        nfl_t = (p_info.get("team") or "").upper()
        
        raw_inj = p_info.get("injury_status")
        inj_display = injury_badge_map.get(raw_inj, "🟢 Healthy")

        p_proj_info = projections_map.get(pid, {})
        proj = p_proj_info.get("pts", 0.0) if isinstance(p_proj_info, dict) else (float(p_proj_info) if p_proj_info else 0.0)
        hist = player_completed_pts.get(pid, [])

        if proj == 0.0 and hist:
            import numpy as np
            proj = round(float(np.mean(hist)), 1)

        # Realistic Floor/Ceiling
        if len(hist) >= 4:
            import numpy as np
            mu = float(np.mean(hist))
            sigma = float(np.std(hist))
            cv = sigma / mu if mu > 0 else 0.4
            floor_val = max(1.0, proj * (1.0 - min(0.6, cv)))
            ceil_val = proj * (1.0 + min(1.0, cv * 1.5))
        else:
            if pos == "QB":
                cv = 0.25
                floor_val = proj * 0.75
                ceil_val = proj * 1.35
            elif pos == "RB":
                cv = 0.35
                floor_val = proj * 0.65
                ceil_val = proj * 1.45
            elif pos in ("WR", "TE"):
                cv = 0.45
                floor_val = proj * 0.55
                ceil_val = proj * 1.60
            else:
                cv = 0.50
                floor_val = proj * 0.45
                ceil_val = proj * 1.65

        if proj <= 0.0 or raw_inj in ("Out", "IR", "PUP"):
            floor_val = 0.0
            ceil_val = 0.0

        # Match Vegas Odds
        if not nfl_t or nfl_t in ("FA", "NONE"):
            env = {"Opponent": "Free Agent", "Kickoff": "-", "IsLateNight": False, "OverUnder": None, "Spread": "-", "SpreadVal": 0.0, "IsFavorite": False, "ImpliedTotal": None, "Indoor": False, "Wind": 0, "Condition": "N/A"}
        elif nfl_t in vegas_env:
            env = vegas_env[nfl_t]
        elif has_active_games:
            env = {"Opponent": "💤 BYE", "Kickoff": "-", "IsLateNight": False, "OverUnder": None, "Spread": "-", "SpreadVal": 0.0, "IsFavorite": False, "ImpliedTotal": None, "Indoor": False, "Wind": 0, "Condition": "Bye Week"}
        else:
            env = {"Opponent": "TBD", "Kickoff": "-", "IsLateNight": False, "OverUnder": None, "Spread": "-", "SpreadVal": 0.0, "IsFavorite": False, "ImpliedTotal": None, "Indoor": False, "Wind": 0, "Condition": "Clear"}

        itt_val = env.get("ImpliedTotal")
        itt_disp = f"{itt_val:.1f}" if (itt_val is not None and isinstance(itt_val, (int, float))) else "-"

        ou_val = env.get("OverUnder")
        ou_disp = f"{ou_val:.1f}" if (ou_val is not None and isinstance(ou_val, (int, float))) else "-"

        if env.get("Opponent") in ("💤 BYE", "Free Agent"):
            weather_disp = "-"
        elif env.get("Indoor"):
            weather_disp = "🏟️ Dome"
        elif env.get("Wind", 0) >= 14:
            weather_disp = f"💨 {env['Wind']}mph"
        else:
            weather_disp = "☀️ Fair"

        badges = []
        if isinstance(ou_val, (int, float)):
            if ou_val >= 47.5:
                badges.append("🔥 Shootout")
            elif ou_val <= 41.0:
                badges.append("🧱 Slog Alert")

        if env.get("Indoor"):
            badges.append("🏟️ Dome")
        elif env.get("Wind", 0) >= 16:
            if pos in ("QB", "WR", "K"):
                badges.append("💨 Wind Threat")
            elif pos == "RB":
                badges.append("🏃 Run Funnel")

        if env.get("IsFavorite") and env.get("SpreadVal", 0) >= 5.0 and pos == "RB":
            badges.append("🏃 Positive Script")
        elif not env.get("IsFavorite") and env.get("SpreadVal", 0) >= 5.0 and pos in ("WR", "TE"):
            badges.append("🎯 Pass Volume Script")

        if cv >= 0.55 or ceil_val >= 20.0:
            badges.append("🧨 Boom/Bust")
        elif cv <= 0.35 and floor_val >= 8.5:
            badges.append("🛡️ Safe Floor")

        game_time_str = f"{env.get('Opponent', '-')} ({env.get('Kickoff', '')})" if env.get('Kickoff') != "-" else env.get('Opponent', '-')

        roster_board_rows.append({
            "Slot": slot_label,
            "Photo": get_player_avatar_url(pid),
            "Player": full_name,
            "Pos": pos,
            "Injury": inj_display,
            "Raw Injury": raw_inj,
            "Role": role_label,
            "Game (CEST)": game_time_str,
            "Kickoff": env.get("Kickoff", ""),
            "IsLateNight": env.get("IsLateNight", False),
            "O/U": ou_disp,
            "Team ITT": itt_disp,
            "Weather": weather_disp,
            "Proj": round(proj, 1),
            "Floor": round(floor_val, 1),
            "Ceiling": round(ceil_val, 1),
            "Badges": " | ".join(badges[:3]) if badges else "Standard"
        })

    df_war_room = pd.DataFrame(roster_board_rows)

    with c_view_mode:
        role_filter = st.radio("Display Filter", ["All Players (Sleeper Order)", "Starters Only", "Bench Only", "FLEX Options (RB/WR/TE)"], horizontal=True)

    if role_filter == "Starters Only":
        df_display_war = df_war_room[df_war_room["Role"] == "⭐ Starter"]
    elif role_filter == "Bench Only":
        df_display_war = df_war_room[df_war_room["Role"] == "Pine"]
    elif role_filter == "FLEX Options (RB/WR/TE)":
        df_display_war = df_war_room[df_war_room["Pos"].isin(["RB", "WR", "TE"])]
    else:
        df_display_war = df_war_room

    # 3. Main Roster Table with Outlier Detector
    st.markdown("### 📋 Full Roster Matchup Environment Board")
    st.caption(
        "🔴 **Red = High / Favorable** (Shootout O/U ≥ 47.5 | Floor ≥ 10.0 | Ceiling ≥ 22.0) &nbsp;|&nbsp; "
        "🔵 **Blue = Low / Unfavorable** (Slog O/U ≤ 41.0 | Floor ≤ 5.0 | Ceiling ≤ 13.0)"
    )

    def color_ou_outliers(val):
        try:
            num = float(val)
            if num >= 47.5:
                return "color: #E53935; font-weight: 800;"
            elif num <= 41.0:
                return "color: #1E88E5; font-weight: 800;"
        except (ValueError, TypeError):
            pass
        return ""

    def color_floor_outliers(val):
        try:
            num = float(val)
            if num >= 10.0:
                return "color: #E53935; font-weight: 800;"
            elif 0.0 < num <= 5.0:
                return "color: #1E88E5; font-weight: 800;"
        except (ValueError, TypeError):
            pass
        return ""

    def color_ceiling_outliers(val):
        try:
            num = float(val)
            if num >= 22.0:
                return "color: #E53935; font-weight: 800;"
            elif 0.0 < num <= 13.0:
                return "color: #1E88E5; font-weight: 800;"
        except (ValueError, TypeError):
            pass
        return ""

    table_cols = ["Slot", "Photo", "Player", "Pos", "Injury", "Game (CEST)", "O/U", "Team ITT", "Weather", "Proj", "Floor", "Ceiling", "Badges"]
    df_to_render = df_display_war[table_cols].copy()

    styler_war = df_to_render.style.map if hasattr(df_to_render.style, "map") else df_to_render.style.applymap
    styled_war = styler_war(color_ou_outliers, subset=["O/U"])
    styled_war = styler_war(color_floor_outliers, subset=["Floor"])
    styled_war = styler_war(color_ceiling_outliers, subset=["Ceiling"])
    styled_war = styled_war.format({"Proj": "{:.1f}", "Floor": "{:.1f}", "Ceiling": "{:.1f}"})

    war_col_config = {
        "Slot": st.column_config.TextColumn("Slot", width=70),
        "Photo": st.column_config.ImageColumn("", width=45),
        "Player": st.column_config.TextColumn("Player", width=140),
        "Pos": st.column_config.TextColumn("Pos", width=50),
        "Injury": st.column_config.TextColumn("Injury", width=85),
        "Game (CEST)": st.column_config.TextColumn("Game (CEST)", width=135),
        "O/U": st.column_config.TextColumn("O/U", width=65),
        "Team ITT": st.column_config.TextColumn("ITT (Pts)", width=75),
        "Weather": st.column_config.TextColumn("Weather", width=85),
        "Proj": st.column_config.NumberColumn("Proj", format="%.1f", width=65),
        "Floor": st.column_config.NumberColumn("Floor", format="%.1f", width=65),
        "Ceiling": st.column_config.NumberColumn("Ceiling", format="%.1f", width=70),
        "Badges": st.column_config.TextColumn("Matchup Badges & Archetype", width=230)
    }

    st.dataframe(styled_war, column_config=war_col_config, use_container_width=True, hide_index=True)

    # 4. Opponent Scouting Dossier
    st.markdown("---")
    st.subheader("⚔️ Matchup Scouting Dossier: Know Your Enemy")
    st.caption("Auto-detects your Sleeper head-to-head opponent, compares starting lineups slot-by-slot, and builds a tactical battle plan.")

    my_matchup_id = my_latest_m.get("matchup_id")
    opp_m = next((m for m in latest_matchups if m.get("matchup_id") == my_matchup_id and m.get("roster_id") != my_r_id), None)

    if opp_m:
        opp_r_id = opp_m.get("roster_id")
        opp_team_name = roster_names.get(opp_r_id, f"Team {opp_r_id}")
        opp_starters_list = opp_m.get("starters") or []

        # Standardize slot names
        slot_counts = defaultdict(int)
        ordered_slots = []
        for s in starter_slots:
            slot_counts[s] += 1
            label = f"{s}{slot_counts[s]}" if starter_slots.count(s) > 1 else s
            ordered_slots.append(label)

        my_starter_ids = my_latest_m.get("starters") or []
        h2h_rows = []
        my_chart_names, my_chart_pts = [], []
        opp_chart_names, opp_chart_pts = [], []
        opp_proj_total = 0.0

        for i, slot_name in enumerate(ordered_slots):
            # Your team
            my_pid = my_starter_ids[i] if i < len(my_starter_ids) else None
            my_info = player_db.get(my_pid, {}) if my_pid else {}
            my_name = my_info.get("full_name") or f"Player {my_pid}" if my_pid and my_pid != "0" else "Empty"
            my_inj = injury_badge_map.get(my_info.get("injury_status"), "🟢 Healthy")
            
            p_my_proj = projections_map.get(my_pid, {})
            my_p = p_my_proj.get("pts", 0.0) if isinstance(p_my_proj, dict) else (float(p_my_proj) if p_my_proj else 0.0)
            hist_my = player_completed_pts.get(my_pid, [])
            if my_p == 0.0 and hist_my:
                import numpy as np
                my_p = round(float(np.mean(hist_my)), 1)
            if my_name == "Empty":
                my_p = 0.0

            # Opponent team
            opp_pid = opp_starters_list[i] if i < len(opp_starters_list) else None
            opp_info = player_db.get(opp_pid, {}) if opp_pid else {}
            opp_name = opp_info.get("full_name") or f"Player {opp_pid}" if opp_pid and opp_pid != "0" else "Empty"
            opp_inj = injury_badge_map.get(opp_info.get("injury_status"), "🟢 Healthy")
            
            p_opp_proj = projections_map.get(opp_pid, {})
            opp_p = p_opp_proj.get("pts", 0.0) if isinstance(p_opp_proj, dict) else (float(p_opp_proj) if p_opp_proj else 0.0)
            if opp_name == "Empty":
                opp_p = 0.0

            opp_proj_total += opp_p
            diff = round(my_p - opp_p, 1)
            adv_str = f"+{diff:.1f} (You)" if diff > 0 else (f"{diff:.1f} ({opp_team_name})" if diff < 0 else "Even")

            h2h_rows.append({
                "Slot": slot_name,
                "Your Player": f"{my_name} ({my_inj})",
                "Your Proj": my_p,
                "Opponent Player": f"{opp_name} ({opp_inj})",
                "Opp Proj": opp_p,
                "Net Advantage": adv_str,
                "Raw Diff": diff
            })

            my_chart_names.append(my_name)
            my_chart_pts.append(my_p)
            opp_chart_names.append(opp_name)
            opp_chart_pts.append(opp_p)

        my_starters_df = df_war_room[df_war_room["Role"] == "⭐ Starter"]
        my_proj_total = round(float(my_starters_df["Proj"].sum()), 1)
        spread_diff = round(my_proj_total - opp_proj_total, 1)

        c_my, c_vs, c_opp = st.columns([2, 1, 2])
        c_my.metric(f"Your Lineup ({selected_my_team})", f"{my_proj_total:.1f} pts", delta=f"{spread_diff:+.1f} pts favored" if spread_diff >= 0 else f"{spread_diff:.1f} pts underdog")
        c_vs.markdown("<h2 style='text-align: center; margin-top: 15px;'>VS</h2>", unsafe_allow_html=True)
        c_opp.metric(f"Opponent ({opp_team_name})", f"{opp_proj_total:.1f} pts")

        if spread_diff >= 8.0:
            st.success(f"🛡️ **Tactical Blueprint: PROTECT THE LEAD.** You are favored by **{spread_diff:.1f} points**. Avoid high-variance boom/bust traps. Lock in high-floor volume starters to secure the win.")
        elif spread_diff <= -8.0:
            st.warning(f"🧨 **Tactical Blueprint: CEILING CHASE.** You are an underdog by **{abs(spread_diff):.1f} points**. A safe floor won't win this week. Start explosive 🧨 **Boom/Bust** players with high ceiling potential.")
        else:
            st.info(f"⚖️ **Tactical Blueprint: TIGHT CONTEST.** Projected as a coin-flip matchup (within {abs(spread_diff):.1f} pts). Target players in high Vegas Over/Under shootouts.")

        # Bi-Directional Mirror Bar Chart
        fig_mirror = go.Figure()
        fig_mirror.add_trace(go.Bar(
            y=ordered_slots,
            x=[-val for val in my_chart_pts],
            orientation='h',
            name=selected_my_team,
            text=[f"<b>{val:.1f}</b> ({name})" for name, val in zip(my_chart_names, my_chart_pts)],
            textposition='auto',
            marker=dict(color='#1f77b4'),
            hovertemplate="<b>%{y}</b>: %{customdata} (%{text})<extra></extra>",
            customdata=my_chart_names
        ))
        fig_mirror.add_trace(go.Bar(
            y=ordered_slots,
            x=opp_chart_pts,
            orientation='h',
            name=opp_team_name,
            text=[f"({name}) <b>{val:.1f}</b>" for name, val in zip(opp_chart_names, opp_chart_pts)],
            textposition='auto',
            marker=dict(color='#ff7f0e'),
            hovertemplate="<b>%{y}</b>: %{customdata} (%{text})<extra></extra>",
            customdata=opp_chart_names
        ))

        max_val = max(max(my_chart_pts, default=20), max(opp_chart_pts, default=20))
        axis_limit = int(max_val + 5)
        tick_vals = [-25, -20, -15, -10, -5, 0, 5, 10, 15, 20, 25]
        tick_labels = [str(abs(t)) for t in tick_vals]

        fig_mirror.update_layout(
            barmode='overlay',
            yaxis=dict(autorange="reversed", title=""),
            xaxis=dict(range=[-axis_limit, axis_limit], tickvals=tick_vals, ticktext=tick_labels, title="Projected Points (Left: You | Right: Opponent)"),
            legend=dict(orientation="h", yanchor="bottom", y=1.03, xanchor="center", x=0.5),
            height=460,
            margin=dict(l=60, r=60, t=40, b=40)
        )
        fig_mirror.add_vline(x=0, line_width=2, line_color="rgba(150, 150, 150, 0.5)")
        st.plotly_chart(fig_mirror, use_container_width=True)

        with st.expander("📋 View Exact Slot Advantage Data Table"):
            df_h2h = pd.DataFrame(h2h_rows)

            def style_h2h_diff(val):
                if isinstance(val, (int, float)):
                    if val > 0:
                        return "color: #2ca02c; font-weight: 800;"  # Green for your advantage
                    elif val < 0:
                        return "color: #d62728; font-weight: 800;"  # Red for opponent advantage
                return ""

            styler_h2h = df_h2h.style.map if hasattr(df_h2h.style, "map") else df_h2h.style.applymap
            styled_h2h = styler_h2h(style_h2h_diff, subset=["Raw Diff"])
            
            # Formats Raw Diff to 1 decimal place with explicit +/- signs
            styled_h2h = styled_h2h.format({
                "Your Proj": "{:.1f}", 
                "Opp Proj": "{:.1f}",
                "Raw Diff": "{:+.1f}"  # <-- Locks to 1 decimal (+4.1, -2.2)
            })

            h2h_col_config = {
                "Slot": st.column_config.TextColumn("Slot", width=65),
                "Your Player": st.column_config.TextColumn("Your Starter", width=180),
                "Your Proj": st.column_config.NumberColumn("Proj", format="%.1f", width=65),
                "Opponent Player": st.column_config.TextColumn(f"{opp_team_name} Starter", width=180),
                "Opp Proj": st.column_config.NumberColumn("Proj", format="%.1f", width=65),
                "Net Advantage": st.column_config.TextColumn("Advantage", width=130),
                "Raw Diff": st.column_config.NumberColumn("Margin (Pts)", format="%.1f", width=85)
            }

            st.dataframe(
                styled_h2h, 
                column_config=h2h_col_config,
                use_container_width=True, 
                hide_index=True
            )
    else:
        st.write("Matchup schedule in progress.")

    # 5. European Midnight Risk Protocol
    st.markdown("---")
    st.subheader("🔮 Lineup Optimizer & European Midnight Safety Protocol")

    my_starters_df = df_war_room[df_war_room["Role"] == "⭐ Starter"]
    night_injury_risks = my_starters_df[
        (my_starters_df["IsLateNight"]) & 
        (my_starters_df["Raw Injury"].isin(["Questionable", "Doubtful"]))
    ]

    if not night_injury_risks.empty:
        for _, risk_row in night_injury_risks.iterrows():
            st.error(
                f"🚨 **EUROPEAN MIDNIGHT RISK (Spain CEST):**\n\n"
                f"**{risk_row['Player']}** ({risk_row['Slot']}) kicks off at **{risk_row['Kickoff']}** (after midnight) "
                f"and is listed as **{str(risk_row['Raw Injury']).upper()}**!\n\n"
                f"* **The Problem:** Inactives drop at 00:45 CEST while you are asleep. If he is ruled OUT, you will wake up with a 0!\n"
                f"* **Action Plan:** Unless you have an emergency backup playing in that same late game, **sub him out before the 19:00 CEST early kickoff!**"
            )
    else:
        st.success("✅ **European Schedule Clear:** None of your starters carry late-night (02:15 CEST) injury uncertainty. All your Questionable players play in the early/afternoon windows (19:00 or 22:05 CEST).")

    # Toggle for Questionable starters
    c_opt_title, c_toggle = st.columns([3, 2])
    with c_toggle:
        risk_mode = st.toggle("Assume Questionable (Q) players will play", value=True)

    # Optimization Pool
    roster_proj_pool = []
    for pid in my_player_ids:
        p_info = player_db.get(pid, {})
        full_name = p_info.get("full_name") or f"Player {pid}"
        pos = p_info.get("position", "N/A")
        fantasy_pos = p_info.get("fantasy_positions") or ([pos] if pos else [])
        raw_inj = p_info.get("injury_status")
        
        p_proj_info = projections_map.get(pid, {})
        p_pts = p_proj_info.get("pts", 0.0) if isinstance(p_proj_info, dict) else (float(p_proj_info) if p_proj_info else 0.0)

        if raw_inj in ("Out", "IR", "PUP", "Sus"):
            effective_pts = 0.0
        elif raw_inj in ("Questionable", "Doubtful") and not risk_mode:
            effective_pts = 0.0
        else:
            effective_pts = p_pts

        roster_proj_pool.append({
            "id": pid,
            "name": full_name,
            "pos": pos,
            "raw_inj": raw_inj,
            "inj_display": injury_badge_map.get(raw_inj, "🟢 Healthy"),
            "positions": fantasy_pos,
            "pts": effective_pts
        })

    roster_proj_pool.sort(key=lambda x: x["pts"], reverse=True)

    opt_starting_slots = [p for p in selected_league.get("roster_positions", []) if p not in ("BN", "IR", "TAXI")]
    strict_slots = [s for s in opt_starting_slots if "FLEX" not in s]
    flex_slots = [s for s in opt_starting_slots if "FLEX" in s]

    optimal_starters_dict = {}
    used_opt_ids = set()

    for slot in strict_slots:
        for p in roster_proj_pool:
            if p["id"] not in used_opt_ids and slot in p["positions"]:
                used_opt_ids.add(p["id"])
                optimal_starters_dict[p["id"]] = {"player": p, "slot": slot}
                break

    # STRICT RULE: FLEX is RB/WR only (NO TE)
    for slot in flex_slots:
        for p in roster_proj_pool:
            if p["id"] in used_opt_ids:
                continue
            eligible = False
            if "FLEX" in slot and any(pos_tag in p["positions"] for pos_tag in ["RB", "WR"]):
                eligible = True

            if eligible:
                used_opt_ids.add(p["id"])
                optimal_starters_dict[p["id"]] = {"player": p, "slot": slot}
                break

    optimal_total_pts = sum(item["player"]["pts"] for item in optimal_starters_dict.values())
    current_proj_lookup = {p["id"]: p["pts"] for p in roster_proj_pool}
    current_total_pts = sum(current_proj_lookup.get(pid, 0.0) for pid in current_starters_set)
    pts_gain = max(0.0, optimal_total_pts - current_total_pts)

    c_cur, c_opt, c_gain = st.columns(3)
    c_cur.metric("Current Lineup Projected", f"{current_total_pts:.1f} pts")
    c_opt.metric("Optimal Lineup Projected", f"{optimal_total_pts:.1f} pts")
    if pts_gain > 0.5:
        c_gain.metric("Potential Lineup Boost", f"+{pts_gain:.1f} pts", delta=f"+{pts_gain:.1f} pts", delta_color="normal")
    else:
        status_note = "Safe Mode Active" if not risk_mode else "100% Optimal"
        c_gain.metric("Lineup Efficiency", status_note, delta="Perfect Lineup", delta_color="normal")

    bench_should_start = [item["player"] for pid, item in optimal_starters_dict.items() if pid not in current_starters_set]
    starters_should_bench = [p for p in roster_proj_pool if p["id"] in current_starters_set and p["id"] not in used_opt_ids]

    if bench_should_start and starters_should_bench:
        st.markdown("#### 🚨 Recommended Lineup Adjustments")
        entering = list(bench_should_start)
        exiting = list(starters_should_bench)

        entering.sort(key=lambda x: x["pts"], reverse=True)
        exiting.sort(key=lambda x: x["pts"])

        resolved_swaps = []
        remaining_entering = []

        # Pass 1: Strict position match
        for b_p in entering:
            matched_exit = None
            for s_p in exiting:
                if s_p["pos"] == b_p["pos"]:
                    matched_exit = s_p
                    break
            if matched_exit:
                exiting.remove(matched_exit)
                resolved_swaps.append((b_p, matched_exit, f"{b_p['pos']} Slot"))
            else:
                remaining_entering.append(b_p)

        # Pass 2: FLEX (RB/WR only!)
        has_flex_slot = any("FLEX" in s for s in opt_starting_slots)
        for b_p in remaining_entering:
            matched_exit = None
            if has_flex_slot and b_p["pos"] in ("RB", "WR"):
                for s_p in exiting:
                    if s_p["pos"] in ("RB", "WR"):
                        matched_exit = s_p
                        break
            if matched_exit:
                exiting.remove(matched_exit)
                resolved_swaps.append((b_p, matched_exit, "FLEX Slot (RB/WR)"))

        for b_player, matched_starter, slot_desc in resolved_swaps:
            diff = b_player["pts"] - matched_starter["pts"]
            if diff <= 0.0:
                continue

            if b_player.get("raw_inj") in ("Questionable", "Doubtful"):
                st.warning(
                    f"🔄 **SUB IN (HIGH UPSIDE): {b_player['name']} ({b_player['pos']}) [ {b_player['inj_display']} ]** "
                    f"for **{matched_starter['name']} ({matched_starter['pos']})** in `{slot_desc}`\n\n"
                    f"* **Projected Gain:** `+{diff:.1f} pts` ({b_player['pts']:.1f} vs. {matched_starter['pts']:.1f})\n"
                    f"* **🩺 Game-Time Protocol:** Check inactives before kickoff. If announced ACTIVE, start him for the ceiling advantage. If ruled out, keep **{matched_starter['name']}** locked in."
                )
            else:
                st.success(
                    f"🔄 **SUB IN: {b_player['name']} ({b_player['pos']})** for **{matched_starter['name']} ({matched_starter['pos']})** in `{slot_desc}`\n\n"
                    f"* **Projected Gain:** `+{diff:.1f} pts` ({b_player['pts']:.1f} vs. {matched_starter['pts']:.1f})"
                )
    else:
        st.success("✅ **Lineup Perfection:** Your active starting lineup is currently mathematically optimal based on weekly projections. No adjustments recommended!")