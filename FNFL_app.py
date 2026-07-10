import streamlit as st
import pandas as pd
import plotly.express as px
import requests
import io
import os
import re
import json

# Set page configuration
st.set_page_config(page_title="Fantasy NFL Dashboard", layout="wide")

# --- CONFIGURATION FILE HELPERS ---
CONFIG_FILE = "sleeper_leagues.json"

# --- GLOBAL SCORING SETTINGS DICTIONARY ---
SCORING_SETTINGS = {}

def load_league_config():
    """Loads saved league mappings from local JSON file."""
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r") as f:
                return json.load(f)
        except:
            return {}
    return {}

def save_league_config(config):
    """Saves league mappings to local JSON file."""
    try:
        with open(CONFIG_FILE, "w") as f:
            json.dump(config, f, indent=4)
    except Exception as e:
        st.sidebar.error(f"Error saving config file: {e}")

def force_rerun():
    """Backwards compatible stream rerun helper."""
    try:
        st.rerun()
    except AttributeError:
        st.experimental_rerun()

# --- SLEEPER API SCORING SETTINGS FETCH ---
@st.cache_data(ttl=86400)
def get_league_scoring_settings(league_id):
    """Fetches custom league scoring settings from the Sleeper API."""
    if not league_id:
        return {}
    try:
        url = f"https://api.sleeper.app/v1/league/{league_id}"
        resp = requests.get(url, timeout=5)
        if resp.status_code == 200:
            return resp.json().get('scoring_settings', {})
    except:
        pass
    return {}

# --- SCORING FUNCTION ---
def calculate_fantasy_points(row):
    """Calculates fantasy points dynamically using custom Sleeper scoring rules with fallbacks."""
    settings = SCORING_SETTINGS
    points = 0.0
    row = row.fillna(0)
    
    # 1. Passing Yards
    pass_yd_factor = settings.get('pass_yd', 0.04)  # Default: 1 pt per 25 yds (0.04)
    pass_yds = row.get('PassingYDS', row.get('PassingYds', row.get('Passing YDS', row.get('Passing Yds', 0))))
    points += pass_yds * pass_yd_factor
    
    # 2. Passing TDs
    pass_td_factor = settings.get('pass_td', 4.0)  # Default: 4 pt
    pass_tds = row.get('PassingTD', row.get('Passing TD', 0))
    points += pass_tds * pass_td_factor
    
    # 3. Passing INTs
    pass_int_factor = settings.get('pass_int', -2.0)  # Default: -2 pt
    pass_ints = row.get('PassingInt', row.get('PassingInts', row.get('Passing INT', row.get('Passing Int', 0))))
    points += pass_ints * pass_int_factor
    
    # 4. Pass Completions (PPC)
    pass_cmp_factor = settings.get('pass_cmp', 0.0)  # Default: 0.0
    pass_cmps = row.get('PassingCMP', row.get('PassingCmp', row.get('Passing CMP', row.get('Passing Cmp', row.get('Cmp', row.get('CMP', 0))))))
    points += pass_cmps * pass_cmp_factor
    
    # 5. Pass Incompletions
    pass_inc_factor = settings.get('pass_inc', 0.0)  # Default: 0.0
    pass_att = row.get('PassingATT', row.get('PassingAtt', row.get('Passing ATT', row.get('Passing Att', row.get('Att', row.get('ATT', 0))))))
    pass_inc = max(0, pass_att - pass_cmps)
    points += pass_inc * pass_inc_factor
    
    # 6. Sacks
    sack_factor = settings.get('sack', 0.0)  # Default: 0.0
    sacks = row.get('Sack', row.get('Sacks', row.get('SK', 0)))
    points += sacks * sack_factor
    
    # 7. Rushing Yards
    rush_yd_factor = settings.get('rush_yd', 0.1)  # Default: 1 pt per 10 yds (0.1)
    rush_yds = row.get('RushingYDS', row.get('RushingYds', row.get('Rushing YDS', row.get('Rushing Yds', 0))))
    points += rush_yds * rush_yd_factor
    
    # 8. Rushing TDs
    rush_td_factor = settings.get('rush_td', 6.0)  # Default: 6 pt
    rush_tds = row.get('RushingTD', row.get('Rushing TD', 0))
    points += rush_tds * rush_td_factor
    
    # 9. Receptions (PPR)
    rec_factor = settings.get('rec', 1.0)  # Default: 1.0 (PPR)
    recs = row.get('ReceivingRec', row.get('ReceivingRecs', row.get('Receiving Rec', row.get('Receiving Recs', 0))))
    points += recs * rec_factor
    
    # 10. Receiving Yards
    rec_yd_factor = settings.get('rec_yd', 0.1)  # Default: 1 pt per 10 yds (0.1)
    rec_yds = row.get('ReceivingYDS', row.get('ReceivingYds', row.get('Receiving YDS', row.get('Receiving Yds', 0))))
    points += rec_yds * rec_yd_factor
    
    # 11. Receiving TDs
    rec_td_factor = settings.get('rec_td', 6.0)  # Default: 6 pt
    rec_tds = row.get('ReceivingTD', row.get('Receiving TD', 0))
    points += rec_tds * rec_td_factor
    
    # 12. Fumbles (Total)
    fum_factor = settings.get('fum', 0.0)  # Default: 0.0
    fumbles = row.get('Fumble', row.get('Fumbles', row.get('FUM', 0)))
    points += fumbles * fum_factor
    
    # 13. Fumbles Lost
    fum_lost_factor = settings.get('fum_lost', -2.0)  # Default: -2.0
    fumbles_lost = row.get('Fum', row.get('FumblesLost', row.get('Fumbles Lost', row.get('FumLost', row.get('Fum Lost', 0)))))
    points += fumbles_lost * fum_lost_factor
    
    # 14. 2-Point Conversions
    two_pt_factor = settings.get('two_pt', 2.0)  # Default: 2.0
    two_pt = row.get('2PT', row.get('2Pt', row.get('TwoPT', 0)))
    points += two_pt * two_pt_factor
    
    # 15. Extra Points (Kickers)
    xp_factor = settings.get('xpmade', 1.0)
    pat = row.get('PatMade', row.get('XP_Made', row.get('XPMade', 0)))
    points += pat * xp_factor
    
    # 16. Field Goals (Kickers)
    fg_0_19 = row.get('FGMade_0-19', row.get('FGMade_0_19', 0))
    points += fg_0_19 * settings.get('fgmade30_39', 3.0)
    
    fg_20_29 = row.get('FGMade_20-29', row.get('FGMade_20_29', 0))
    points += fg_20_29 * settings.get('fgmade30_39', 3.0)
    
    fg_30_39 = row.get('FGMade_30-39', row.get('FGMade_30_39', 0))
    points += fg_30_39 * settings.get('fgmade30_39', 3.0)
    
    fg_40_49 = row.get('FGMade_40-49', row.get('FGMade_40_49', 0))
    points += fg_40_49 * settings.get('fgmade40_49', 4.0)
    
    fg_50 = row.get('FGMade_50', row.get('FGMade_50+', 0))
    points += fg_50 * settings.get('fgmade50', 5.0)
    
    return points

# --- CORE DATA LOADING HELPER ---
@st.cache_data
def load_excel_sheets(raw_path, agg_path):
    """Loads sheet names from raw and aggregated Excel files."""
    raw_sheets = pd.ExcelFile(raw_path).sheet_names
    agg_sheets = pd.ExcelFile(agg_path).sheet_names
    return raw_sheets, agg_sheets

# --- ACTIVE GAMES PLAYED FILTER HELPER ---
def get_games_played_map(df_weekly):
    """Calculates games played by counting only weeks where the player recorded points or active stats."""
    if df_weekly.empty:
        return {}
    
    # Base filter: Non-zero fantasy points
    active_mask = (df_weekly['FantasyPoints'] != 0)
    
    # Footprint metrics (if they have non-zero carries, targets, yards, etc.)
    activity_cols = [
        'PassingATT', 'PassingAtt', 'PassingCMP', 'PassingCmp', 'PassingYDS', 'PassingYds', 'PassingTD', 'PassingInt',
        'RushingCarries', 'Carries', 'RushingYDS', 'RushingYds', 'RushingTD',
        'ReceivingRec', 'ReceivingRecs', 'ReceivingYDS', 'ReceivingYds', 'ReceivingTD', 'Targets', 'ReceivingTargets',
        'Fum', 'FumblesLost', 'Fumbles Lost', '2PT', '2Pt', 'TwoPT',
        'PatMade', 'XP_Made', 'XPMade', 'FGMade_0-19', 'FGMade_20-29', 'FGMade_30-39', 'FGMade_40-49', 'FGMade_50'
    ]
    for col in activity_cols:
        if col in df_weekly.columns:
            active_mask = active_mask | (df_weekly[col].fillna(0) != 0)
            
    df_active = df_weekly[active_mask]
    return df_active.groupby('PlayerName')['Week'].count().to_dict()

# --- DUPLICATE PLAYER MERGER (FOR MID-SEASON TRADES) ---
def merge_duplicate_players(df):
    """Consolidates players who changed NFL teams during the season."""
    if df.empty:
        return df
    
    group_cols = []
    if 'PlayerName' in df.columns:
        group_cols.append('PlayerName')
    if 'FantasyOwner' in df.columns:
        group_cols.append('FantasyOwner')
    if 'Pos' in df.columns:
        group_cols.append('Pos')
        
    if not group_cols:
        return df
        
    agg_dict = {}
    for col in df.columns:
        if col in group_cols:
            continue
        if col == 'Team':
            agg_dict[col] = lambda x: ', '.join(sorted(list(set(str(val) for val in x if pd.notna(val) and str(val).strip()))))
        elif pd.api.types.is_numeric_dtype(df[col]):
            agg_dict[col] = 'sum'
        else:
            agg_dict[col] = 'first'
            
    df_merged = df.groupby(group_cols, as_index=False).agg(agg_dict)
    return df_merged

# --- DICTIONARY FOR USER-FRIENDLY LABELS ---
FRIENDLY_STATS = {
    'FantasyPoints': 'Fantasy Points',
    'FantasyPointsPerGame': 'Points Per Game (FP/G)',
    'PassingYDS': 'Passing Yards',
    'PassingTD': 'Passing Touchdowns',
    'Passing TD': 'Passing Touchdowns',
    'PassingInt': 'Interceptions Thrown',
    'RushingYDS': 'Rushing Yards',
    'RushingTD': 'Rushing Touchdowns',
    'ReceivingRec': 'Receptions',
    'ReceivingYDS': 'Receiving Yards',
    'ReceivingTD': 'Receiving Touchdowns',
    'Fum': 'Fumbles Lost',
    '2PT': '2-Point Conversions',
    'PatMade': 'Extra Points Made',
    'EstimatedTouches': 'Est. Touches',
    'YardsPerCatch': 'Yards Per Catch',
    'TotalTDs': 'Total Touchdowns',
    'LongFGs': '40+ & 50+ FGs Made',
    'FGMade_40-49': '40-49 FG Made',
    'FGMade_50': '50+ FG Made'
}

# --- MASTER SLEEPER PLAYER DATABASE FOR TRANSLATION ---
@st.cache_data(ttl=86400)
def get_sleeper_player_db():
    """Fetches the master player database from Sleeper to map Sleeper IDs to metadata."""
    try:
        url = "https://api.sleeper.app/v1/players/nfl"
        resp = requests.get(url, timeout=15)
        if resp.status_code == 200:
            players_data = resp.json()
            id_to_meta = {}
            for p_id, p_info in players_data.items():
                full_name = p_info.get('full_name')
                if full_name:
                    id_to_meta[str(p_id)] = {
                        'name': full_name,
                        'position': p_info.get('position', 'UNK'),
                        'team': p_info.get('team', 'FA')
                    }
            return id_to_meta
    except:
        pass
    return {}

# --- SLEEPER API HELPER (NAME MAPPED FETCH) ---
@st.cache_data(ttl=600)
def get_sleeper_roster_map_direct(league_id, player_db):
    """Fetches rosters and maps Sleeper player IDs back to Clean Player Names."""
    if not league_id or not player_db:
        return {}
    try:
        # 1. Fetch Users
        users_url = f"https://api.sleeper.app/v1/league/{league_id}/users"
        users_resp = requests.get(users_url, timeout=5)
        if users_resp.status_code != 200:
            return {}
        users_data = users_resp.json()
        
        user_to_name = {}
        for u in users_data:
            metadata = u.get('metadata') or {}
            team_name = metadata.get('team_name')
            user_to_name[u['user_id']] = team_name if team_name else u['display_name']
            
        # 2. Fetch Rosters
        rosters_url = f"https://api.sleeper.app/v1/league/{league_id}/rosters"
        rosters_resp = requests.get(rosters_url, timeout=5)
        if rosters_resp.status_code != 200:
            return {}
        rosters_data = rosters_resp.json()
        
        name_to_owner = {}
        for r in rosters_data:
            owner_id = r.get('owner_id')
            owner_name = user_to_name.get(owner_id, "Unknown Owner")
            players_list = r.get('players', []) or []
            for p_id in players_list:
                player_info = player_db.get(str(p_id))
                if player_info:
                    player_name = player_info['name'] if isinstance(player_info, dict) else player_info
                    name_to_owner[player_name] = owner_name
                
        return name_to_owner
    except Exception as e:
        return {}

# --- INTELLIGENT ROSTER RETRIEVAL (MAPPING & TRAVERSAL) ---
@st.cache_data(ttl=600)
def fetch_roster_map_for_year(target_year, league_config, player_db):
    """Retrieves roster map using direct user config or falling back to backward-walking."""
    target_year_str = str(target_year)
    
    # Priority 1: Direct user-saved mapping for this year
    if target_year_str in league_config:
        return get_sleeper_roster_map_direct(league_config[target_year_str], player_db)
        
    # Priority 2: Walk backwards from the closest future mapped year
    mapped_years = [int(y) for y in league_config.keys()]
    future_years = [y for y in mapped_years if y > target_year]
    
    if future_years:
        closest_future_year = min(future_years)
        start_league_id = league_config[str(closest_future_year)]
        
        current_id = start_league_id
        for _ in range(6):
            try:
                league_url = f"https://api.sleeper.app/v1/league/{current_id}"
                resp = requests.get(league_url, timeout=5)
                if resp.status_code != 200:
                    break
                league_data = resp.json()
                league_season = league_data.get('season')
                
                # If we traversed back to the target year, fetch rosters
                if league_season == target_year_str:
                    return get_sleeper_roster_map_direct(current_id, player_db)
                    
                prev_id = league_data.get('previous_league_id')
                if not prev_id or prev_id == "0":
                    break
                current_id = prev_id
            except:
                break
                
    return {}

# --- DIRECT ROSTER RETRIEVAL FOR OFFSEASON (NO EXCEL DEPENDENCY) ---
@st.cache_data(ttl=600)
def fetch_sleeper_rosters_df(league_id, player_db):
    """Retrieves live rosters directly from Sleeper mapped with metadata."""
    if not league_id or not player_db:
        return pd.DataFrame()
    try:
        # 1. Fetch Users
        users_url = f"https://api.sleeper.app/v1/league/{league_id}/users"
        users_resp = requests.get(users_url, timeout=5)
        if users_resp.status_code != 200:
            return pd.DataFrame()
        users_data = users_resp.json()
        
        user_to_name = {}
        for u in users_data:
            metadata = u.get('metadata') or {}
            team_name = metadata.get('team_name')
            user_to_name[u['user_id']] = team_name if team_name else u['display_name']
            
        # 2. Fetch Rosters
        rosters_url = f"https://api.sleeper.app/v1/league/{league_id}/rosters"
        rosters_resp = requests.get(rosters_url, timeout=5)
        if rosters_resp.status_code != 200:
            return pd.DataFrame()
        rosters_data = rosters_resp.json()
        
        records = []
        for r in rosters_data:
            owner_id = r.get('owner_id')
            owner_name = user_to_name.get(owner_id, "Unknown Owner")
            players_list = r.get('players', []) or []
            for p_id in players_list:
                p_info = player_db.get(str(p_id))
                if p_info:
                    records.append({
                        'PlayerName': p_info.get('name', 'Unknown Player'),
                        'Pos': p_info.get('position', 'UNK'),
                        'Team': p_info.get('team', 'FA'),
                        'FantasyOwner': owner_name
                    })
        return pd.DataFrame(records)
    except:
        return pd.DataFrame()

# --- AUTOMATIC FILE RETRIEVAL HELPERS ---
def get_available_years(league_config=None):
    """Scans folder and merges unique years found in filenames and saved configurations."""
    files = os.listdir('.')
    years = set()
    for f in files:
        match = re.search(r'(?:raw_data_|aggregated_data_)(\d{4})', f)
        if match:
            years.add(int(match.group(1)))
    if league_config:
        for yr in league_config.keys():
            years.add(int(yr))
    return sorted(list(years), reverse=True)

def find_files_for_year(year):
    """Locates the exact raw and aggregated Excel files for a chosen year."""
    files = os.listdir('.')
    raw_file = None
    agg_file = None
    for f in files:
        if f.startswith(f'raw_data_{year}_') and f.endswith('.xlsx'):
            raw_file = f
        elif f.startswith(f'aggregated_data_{year}_') and f.endswith('.xlsx'):
            agg_file = f
    return raw_file, agg_file

# --- LEAGUE-WIDE ROSTER COMPILER ---
@st.cache_data
def compile_master_player_list(raw_path, agg_path, r_map):
    """Compiles all players from all 5 positions into a single unified directory."""
    positions = ['QB', 'RB', 'WR', 'TE', 'K']
    master_list = []
    
    for pos in positions:
        totals_key = f"{pos}_totals_raw"
        weekly_key = f"{pos}_weekly"
        try:
            df_pos = pd.read_excel(agg_path, sheet_name=totals_key)
            df_pos['Pos'] = pos
            df_pos['FantasyPoints'] = df_pos.apply(calculate_fantasy_points, axis=1)
            
            df_pos = assign_owner(df_pos, r_map)
            df_pos = merge_duplicate_players(df_pos)  # Merge duplicates first
            
            # Load corresponding weekly sheet to count played active games
            try:
                df_weekly = pd.read_excel(raw_path, sheet_name=weekly_key)
                df_weekly['FantasyPoints'] = df_weekly.apply(calculate_fantasy_points, axis=1)
                gp_map = get_games_played_map(df_weekly)
            except:
                gp_map = {}
                
            gp_col = next((c for c in ['GP', 'G', 'Games', 'GamesPlayed', 'Games Played'] if c in df_pos.columns), None)
            if gp_col:
                df_pos['GamesPlayed'] = df_pos[gp_col].astype(float)
            else:
                df_pos['GamesPlayed'] = df_pos['PlayerName'].map(gp_map).fillna(1).astype(float)
                
            df_pos['GamesPlayed'] = df_pos['GamesPlayed'].replace(0, 1)
            df_pos['FantasyPointsPerGame'] = df_pos['FantasyPoints'] / df_pos['GamesPlayed']
            
            # Filter out unowned players
            df_owned = df_pos[~df_pos['FantasyOwner'].isin(['Free Agent', 'Not Connected', 'Unknown (No PlayerName)', 'Unknown (No PlayerID)'])]
            
            cols_to_keep = ['PlayerName', 'Team', 'Pos', 'FantasyPoints', 'GamesPlayed', 'FantasyPointsPerGame', 'FantasyOwner']
            cols_present = [c for c in cols_to_keep if c in df_owned.columns]
            master_list.append(df_owned[cols_present])
        except:
            pass
            
    if not master_list:
        return pd.DataFrame()
    return pd.concat(master_list, ignore_index=True)

# --- COMPLETE NFL DIRECTORY COMPILER (FOR MARKET SHARE BREAKDOWNS) ---
@st.cache_data
def compile_all_nfl_players(raw_path, agg_path):
    """Compiles offensive skill positions for every player in the league, regardless of ownership."""
    positions = ['QB', 'RB', 'WR', 'TE']
    master_list = []
    for pos in positions:
        totals_key = f"{pos}_totals_raw"
        try:
            df_pos = pd.read_excel(agg_path, sheet_name=totals_key)
            df_pos['Pos'] = pos
            df_pos['FantasyPoints'] = df_pos.apply(calculate_fantasy_points, axis=1)
            df_pos = merge_duplicate_players(df_pos)
            
            cols_to_keep = ['PlayerName', 'Team', 'Pos', 'FantasyPoints']
            cols_present = [c for c in cols_to_keep if c in df_pos.columns]
            master_list.append(df_pos[cols_present])
        except:
            pass
    if not master_list:
        return pd.DataFrame()
    return pd.concat(master_list, ignore_index=True)


# --- SIDEBAR INTERFACE ---
st.sidebar.header("📁 Season Configuration")

# Load existing configurations database globally
league_config = load_league_config()

# Automatically detect available years from filenames and configuration file
available_years = get_available_years(league_config)
if not available_years:
    st.sidebar.error("No Excel files or saved Sleeper League mappings found. Use the settings below to save your league mapping.")
    st.stop()

# 1. Main dropdown
target_year = st.sidebar.selectbox("Select NFL Season Year", available_years)

# 2. Automatically locate both files matching selected year
selected_raw_file, selected_agg_file = find_files_for_year(target_year)

# Verify if we are mapping with Sleeper config or Excel files
mapped_for_target = str(target_year) in league_config
has_excel = bool(selected_raw_file and selected_agg_file)

if not has_excel and not mapped_for_target:
    st.sidebar.error(f"Missing data for {target_year}. Save a Sleeper League ID in the settings expander below to view offseason rosters.")
    st.stop()

# Set Offseason Mode and Historical Stats fallback logic
offseason_mode = not has_excel
using_historical_stats = False
stats_year = target_year

if offseason_mode and mapped_for_target:
    # Walk backward to find the closest year that has statistical Excel files
    for yr in sorted(available_years, reverse=True):
        if yr < target_year:
            raw_f, agg_f = find_files_for_year(yr)
            if raw_f and agg_f:
                selected_raw_file = raw_f
                selected_agg_file = agg_f
                stats_year = yr
                using_historical_stats = True
                offseason_mode = False  # Deactivate pure list mode to run statistical tabs
                break

# 3. SLEEPER CONFIGURATION MANAGER (CLEAN COLLAPSED EXPANDER)
st.sidebar.markdown("---")
with st.sidebar.expander("🔑 Sleeper League Manager Settings", expanded=False):
    if league_config:
        st.write("**Saved League Mappings:**")
        for yr, l_id in sorted(league_config.items(), reverse=True):
            st.caption(f"📅 **{yr}** : `{l_id}`")
    else:
        st.info("No saved league mappings. Add your first league ID below.")

    # Save mapping form
    with st.form("league_mapping_form", clear_on_submit=True):
        st.write("**Save/Update League ID**")
        map_year = st.number_input("Season Year", min_value=2015, max_value=2030, value=target_year, step=1)
        map_id = st.text_input("Sleeper League ID", placeholder="Enter League ID string")
        submit = st.form_submit_button("Save Season")
        
        if submit:
            if map_id.strip():
                league_config[str(map_year)] = map_id.strip()
                save_league_config(league_config)
                st.toast(f"Saved {map_year} League ID!", icon="✅")
                force_rerun()
            else:
                st.error("Please enter a valid League ID.")

# Reset configuration
if league_config:
    if st.sidebar.button("Clear All Saved IDs", use_container_width=True):
        save_league_config({})
        st.toast("Cleared configurations database.", icon="🧹")
        force_rerun()

# 4. Fetch Custom Sleeper scoring configurations and store globally prior to file loads
if league_config and str(target_year) in league_config:
    active_league_id = league_config[str(target_year)]
    with st.spinner("Syncing custom league scoring rules..."):
        custom_rules = get_league_scoring_settings(active_league_id)
        if custom_rules:
            SCORING_SETTINGS.clear()
            SCORING_SETTINGS.update(custom_rules)

# 5. Fetch Master Sleeper Player Database for Translation
with st.spinner("Loading Sleeper master player database (runs once)..."):
    player_db = get_sleeper_player_db()

# 6. Determine active mapping for selected dataset using translation
roster_map = {}
if player_db and league_config:
    with st.spinner(f"Loading {target_year} Season owner rosters..."):
        roster_map = fetch_roster_map_for_year(target_year, league_config, player_db)

if roster_map:
    if mapped_for_target:
        st.sidebar.success(f"Matched directly using saved {target_year} ID.")
    else:
        st.sidebar.success(f"Matched by auto-traversing back to {target_year}.")
else:
    if not offseason_mode:
        st.sidebar.warning(f"No owner mapping found for {target_year}. Expand settings above to add it.")

# Helper to assign owner based on Name matching (replaces conflicting ID matching)
def assign_owner(df, name_to_owner_map):
    if not name_to_owner_map:
        df['FantasyOwner'] = "Not Connected"
        return df
    
    def clean_name(n):
        if not isinstance(n, str):
            return ""
        n_clean = re.sub(r'\s+(Jr\.|Sr\.|III|II|IV|V)$', '', n, flags=re.IGNORECASE)
        return n_clean.strip().lower()
        
    cleaned_owner_map = {clean_name(k): v for k, v in name_to_owner_map.items() if k}
    
    if 'PlayerName' in df.columns:
        df['CleanName'] = df['PlayerName'].apply(clean_name)
        df['FantasyOwner'] = df['CleanName'].map(cleaned_owner_map).fillna("Free Agent")
        df.drop(columns=['CleanName'], inplace=True)
    else:
        df['FantasyOwner'] = "Unknown (No PlayerName)"
    return df


# --- MAIN APP LAYOUT ---
st.title("🏈 Fantasy NFL Interactive Dashboard")

if using_historical_stats:
    st.info(
        f"🏈 **Offseason Roster Analytics Mode ({target_year})**\n\n"
        f"Evaluating active **{target_year}** rosters and free agent pools using historical player statistics "
        f"from the **{stats_year}** season. This enables draft and trade evaluations using past metrics."
    )

# ================= OFFSEASON ROSTER-ONLY MODE (NO STATS AVAILABLE) =================
if offseason_mode:
    st.sidebar.info(f"🏈 Entering Offseason Roster Mode for {target_year}.")
    
    st.info(
        f"🏈 **Welcome to the {target_year} Offseason Roster Viewer!**\n\n"
        f"The {target_year} season hasn't started yet and no historical stats databases are available. "
        f"We've connected to Sleeper League ID `{league_config[str(target_year)]}` to fetch active offseason rosters."
    )
    
    df_live_rosters = fetch_sleeper_rosters_df(league_config[str(target_year)], player_db)
    
    if df_live_rosters.empty:
        st.warning("No roster mapping could be compiled from Sleeper. Please make sure players are currently rostered in this League ID.")
    else:
        tab_list, tab_compare = st.tabs(["📋 Roster Summary & Composition", "🔄 Offseason Upgrade Finder"])
        
        with tab_list:
            st.markdown("### 📋 Current League Rosters")
            
            # Calculate offseason summary metrics per team
            team_summary = df_live_rosters.groupby('FantasyOwner').agg(
                Total_Players=('PlayerName', 'count'),
                QBs=('Pos', lambda x: (x == 'QB').sum()),
                RBs=('Pos', lambda x: (x == 'RB').sum()),
                WRs=('Pos', lambda x: (x == 'WR').sum()),
                TEs=('Pos', lambda x: (x == 'TE').sum()),
                Ks=('Pos', lambda x: (x == 'K').sum())
            ).reset_index()
            
            col_summary, col_roster_select = st.columns([1.2, 1])
            
            with col_summary:
                st.markdown("**Team Size & Positional Count Summary**")
                st.dataframe(
                    team_summary.rename(columns={
                        'FantasyOwner': 'Fantasy Manager',
                        'Total_Players': 'Total Rostered',
                        'QBs': 'QB Count',
                        'RBs': 'RB Count',
                        'WRs': 'WR Count',
                        'TEs': 'TE Count',
                        'Ks': 'K Count'
                    }),
                    use_container_width=True,
                    hide_index=True
                )
                
            with col_roster_select:
                st.markdown("**🔍 Explore Individual Offseason Roster**")
                selected_manager = st.selectbox(
                    "Select Fantasy Manager to View Roster Details:",
                    options=sorted(df_live_rosters['FantasyOwner'].unique())
                )
                
                if selected_manager:
                    manager_roster = df_live_rosters[df_live_rosters['FantasyOwner'] == selected_manager].copy()
                    
                    pos_order = {'QB': 1, 'RB': 2, 'WR': 3, 'TE': 4, 'K': 5}
                    manager_roster['SortOrder'] = manager_roster['Pos'].map(pos_order).fillna(99)
                    manager_roster = manager_roster.sort_values(by='SortOrder')
                    
                    st.dataframe(
                        manager_roster[['PlayerName', 'Pos', 'Team']]
                        .rename(columns={
                            'PlayerName': 'Player Name',
                            'Pos': 'Position',
                            'Team': 'NFL Team'
                        }),
                        use_container_width=True,
                        hide_index=True
                    )
        
        with tab_compare:
            st.markdown("### 🔄 Offseason Upgrade Finder (Roster vs. Available Free Agents)")
            
            col_m, col_p = st.columns([1.2, 1])
            with col_m:
                selected_comp_manager = st.selectbox(
                    "Select Your Team to Compare:",
                    options=sorted(df_live_rosters['FantasyOwner'].unique()),
                    key="offseason_compare_manager"
                )
            with col_p:
                selected_comp_pos = st.selectbox(
                    "Select Position to Compare:",
                    options=['QB', 'RB', 'WR', 'TE', 'K'],
                    key="offseason_compare_pos"
                )
                
            if selected_comp_manager and selected_comp_pos:
                # Filter rostered players
                my_roster_pos = df_live_rosters[
                    (df_live_rosters['FantasyOwner'] == selected_comp_manager) & 
                    (df_live_rosters['Pos'] == selected_comp_pos)
                ]
                
                # Filter free agents from Sleeper database
                rostered_names = set(df_live_rosters['PlayerName'].unique())
                fa_pos_records = []
                for p_id, p_info in player_db.items():
                    if isinstance(p_info, dict):
                        name = p_info.get('name')
                        pos = p_info.get('position')
                        team = p_info.get('team')
                        if name and name not in rostered_names and pos == selected_comp_pos:
                            fa_pos_records.append({
                                'Player Name': name,
                                'NFL Team': team if team else 'FA'
                            })
                df_fa_pos = pd.DataFrame(fa_pos_records).sort_values(by='Player Name')
                
                col_my_roster, col_fa_pool = st.columns([1, 1])
                with col_my_roster:
                    st.markdown(f"**Your Rostered {selected_comp_pos}s**")
                    if my_roster_pos.empty:
                        st.info(f"No rostered {selected_comp_pos}s.")
                    else:
                        st.dataframe(
                            my_roster_pos[['PlayerName', 'Team']]
                            .rename(columns={'PlayerName': 'Player Name', 'Team': 'NFL Team'}),
                            use_index=False,
                            use_container_width=True
                        )
                with col_fa_pool:
                    st.markdown(f"**Available Free Agent {selected_comp_pos}s**")
                    if df_fa_pos.empty:
                        st.info(f"No available free agent {selected_comp_pos}s found.")
                    else:
                        st.dataframe(
                            df_fa_pos,
                            use_index=False,
                            use_container_width=True
                        )

# ================= STANDARD STATS MODE & HISTORICAL FALLBACK MODE =================
else:
    try:
        raw_sheet_names, agg_sheet_names = load_excel_sheets(selected_raw_file, selected_agg_file)
    except Exception as e:
        st.error(f"Error loading Excel spreadsheets: {e}")
        st.stop()

    selected_position = st.selectbox("Select Position to Analyze", ['QB', 'RB', 'WR', 'TE', 'K'])

    weekly_key = f"{selected_position}_weekly"
    totals_key = f"{selected_position}_totals_raw"
    averages_key = f"{selected_position}_weekly_averages"
    pivot_key = f"{selected_position}_weekly_points_pivot"

    try:
        df_raw_weekly = pd.read_excel(selected_raw_file, sheet_name=weekly_key)
        df_agg_totals = pd.read_excel(selected_agg_file, sheet_name=totals_key)
        df_agg_averages = pd.read_excel(selected_agg_file, sheet_name=averages_key)
        df_agg_pivot = pd.read_excel(selected_agg_file, sheet_name=pivot_key)
        
        # 1. Recalculate and normalize points to enforce standard database rules
        df_raw_weekly['FantasyPoints'] = df_raw_weekly.apply(calculate_fantasy_points, axis=1)
        df_agg_totals['FantasyPoints'] = df_agg_totals.apply(calculate_fantasy_points, axis=1)

        # 2. Map owners to the loaded datasets
        df_raw_weekly = assign_owner(df_raw_weekly, roster_map)
        df_agg_totals = assign_owner(df_agg_totals, roster_map)
        df_agg_averages = assign_owner(df_agg_averages, roster_map)
        df_agg_pivot = assign_owner(df_agg_pivot, roster_map)

        # 3. Consolidate traded players (e.g., Tank Bigsby) to prevent split records
        df_agg_totals = merge_duplicate_players(df_agg_totals)

        # 4. Now calculate active games played and FP/G dynamically on the merged dataset
        gp_col = next((c for c in ['GP', 'G', 'Games', 'GamesPlayed', 'Games Played'] if c in df_agg_totals.columns), None)
        if gp_col:
            df_agg_totals['GamesPlayed'] = df_agg_totals[gp_col].astype(float)
        else:
            gp_map = get_games_played_map(df_raw_weekly)
            df_agg_totals['GamesPlayed'] = df_agg_totals['PlayerName'].map(gp_map).fillna(1).astype(float)
            
        df_agg_totals['GamesPlayed'] = df_agg_totals['GamesPlayed'].replace(0, 1)
        df_agg_totals['FantasyPointsPerGame'] = df_agg_totals['FantasyPoints'] / df_agg_totals['GamesPlayed']

    except Exception as e:
        st.error(f"Error loading sheets for {selected_position}. Verify both Excel files contain data for this position.")
        st.stop()


    # Create Dashboard Tabs
    tab_leaderboard, tab_trends, tab_waivers, tab_league, tab_nfl_team = st.tabs([
        "🏆 Leaderboards & Consistency", 
        "📈 Player Trends & Head-to-Head", 
        "🕵️ Waiver Wire Explorer",
        "🛡️ Team Positional Breakdown",
        "📊 NFL Team Offense Share"
    ])

    # ================= TAB 1: LEADERBOARD & CONSISTENCY =================
    with tab_leaderboard:
        if using_historical_stats:
            st.caption(f"⚠️ Note: Rookies or players with no statistical records in {stats_year} will not appear on this leaderboard.")
            
        st.subheader(f"{selected_position} Season Leaders & Performance Metrics")
        
        # --- POSITION SPECIFIC DYNAMIC KPI METRICS ---
        st.markdown("### 🏆 Season Statistical Leaders")
        kpi_cols = st.columns(3)
        
        # KPI 1: Primary Yards Leader
        if selected_position == 'QB' and 'PassingYDS' in df_agg_totals.columns:
            leader_row = df_agg_totals.loc[df_agg_totals['PassingYDS'].idxmax()]
            kpi_cols[0].metric("Passing Yards Leader", f"{leader_row['PlayerName']}", f"{leader_row['PassingYDS']:.0f} Yds")
        elif selected_position == 'RB' and 'RushingYDS' in df_agg_totals.columns:
            leader_row = df_agg_totals.loc[df_agg_totals['RushingYDS'].idxmax()]
            kpi_cols[0].metric("Rushing Yards Leader", f"{leader_row['PlayerName']}", f"{leader_row['RushingYDS']:.0f} Yds")
        elif selected_position in ['WR', 'TE'] and 'ReceivingYDS' in df_agg_totals.columns:
            leader_row = df_agg_totals.loc[df_agg_totals['ReceivingYDS'].idxmax()]
            kpi_cols[0].metric("Receiving Yards Leader", f"{leader_row['PlayerName']}", f"{leader_row['ReceivingYDS']:.0f} Yds")
        elif selected_position == 'K' and 'PatMade' in df_agg_totals.columns:
            leader_row = df_agg_totals.loc[df_agg_totals['PatMade'].idxmax()]
            kpi_cols[0].metric("Extra Point Leader", f"{leader_row['PlayerName']}", f"{leader_row['PatMade']:.0f} XP")

        # KPI 2: TD Leader
        td_col = None
        label = ""
        if selected_position == 'QB' and 'PassingTD' in df_agg_totals.columns:
            td_col, label = 'PassingTD', "Passing TD Leader"
        elif selected_position == 'RB' and 'RushingTD' in df_agg_totals.columns:
            td_col, label = 'RushingTD', "Rushing TD Leader"
        elif selected_position in ['WR', 'TE'] and 'ReceivingTD' in df_agg_totals.columns:
            td_col, label = 'ReceivingTD', "Receiving TD Leader"
        elif selected_position == 'K':
            fg_cols = [col for col in df_agg_totals.columns if 'FG' in col]
            if fg_cols:
                td_col, label = fg_cols[0], "FG Leader"

        if td_col and td_col in df_agg_totals.columns:
            leader_row = df_agg_totals.loc[df_agg_totals[td_col].idxmax()]
            kpi_cols[1].metric(label, f"{leader_row['PlayerName']}", f"{leader_row[td_col]:.0f} Items")

        # KPI 3: Total Fantasy Points Leader (MVP)
        if 'FantasyPoints' in df_agg_totals.columns:
            leader_row = df_agg_totals.loc[df_agg_totals['FantasyPoints'].idxmax()]
            kpi_cols[2].metric("Position MVP", f"{leader_row['PlayerName']}", f"{leader_row['FantasyPoints']:.1f} Pts")
        
        st.markdown("---")
        
        col1, col2 = st.columns([1.1, 1.4])
        
        with col1:
            st.markdown("**Leaderboard Filters**")
            
            # INTERACTIVE METRIC SELECTOR
            available_cols = df_agg_totals.columns.tolist()
            stat_options = {k: v for k, v in FRIENDLY_STATS.items() if k in available_cols}
            
            selected_stat_key = st.selectbox(
                "Rank and Color Leaderboard By:",
                options=list(stat_options.keys()),
                format_func=lambda x: stat_options[x]
            )
            
            owner_filter = st.multiselect(
                "Filter by Fantasy Owner", 
                options=sorted(df_agg_totals['FantasyOwner'].unique()),
                default=sorted(df_agg_totals['FantasyOwner'].unique())
            )
            
            filtered_totals = df_agg_totals[df_agg_totals['FantasyOwner'].isin(owner_filter)]
            
            # Sort values based on selected stat descending
            filtered_totals = filtered_totals.sort_values(by=selected_stat_key, ascending=False)
            
            st.markdown(f"**Sorted Standings by {stat_options[selected_stat_key]}**")
            
            # Build flexible display columns including total FP and dynamic FP/G side-by-side
            display_columns = ['PlayerName', 'Team', selected_stat_key, 'FantasyOwner']
            if 'FantasyPoints' in filtered_totals.columns and selected_stat_key != 'FantasyPoints':
                display_columns.insert(2, 'FantasyPoints')
            if 'FantasyPointsPerGame' in filtered_totals.columns and selected_stat_key != 'FantasyPointsPerGame':
                display_columns.insert(3, 'FantasyPointsPerGame')
                
            tab1_format = {}
            for col in display_columns:
                friendly_col = FRIENDLY_STATS.get(col, col)
                if col in filtered_totals.columns and pd.api.types.is_numeric_dtype(filtered_totals[col]):
                    if col in ['FantasyPoints', 'FantasyPointsPerGame', 'FantasyPointsPerGame']:
                        tab1_format[friendly_col] = '{:.2f}'
                    else:
                        tab1_format[friendly_col] = '{:.0f}'

            filtered_totals_styled = (
                filtered_totals[display_columns]
                .rename(columns=FRIENDLY_STATS)
                .style.background_gradient(subset=[FRIENDLY_STATS[selected_stat_key]], cmap="Greens")
                .format(tab1_format)
            )
            st.dataframe(filtered_totals_styled, use_container_width=True)

        with col2:
            st.markdown("**Consistency vs. Performance Scatter Plot**")
            
            df_stats = df_raw_weekly.groupby(['PlayerName', 'Team', 'FantasyOwner'])['FantasyPoints'].agg(['mean', 'std']).reset_index()
            df_stats.rename(columns={'mean': 'Average Points', 'std': 'Consistency (Std Dev)'}, inplace=True)
            df_stats['Consistency (Std Dev)'] = df_stats['Consistency (Std Dev)'].fillna(0)
            
            df_stats_filtered = df_stats[df_stats['FantasyOwner'].isin(owner_filter)]
            
            highlight_player = st.selectbox(
                "🔍 Select a player to highlight on the plot below:", 
                options=["None"] + list(sorted(df_stats_filtered['PlayerName'].unique()))
            )
            
            if highlight_player != "None":
                df_stats_filtered['ColorGroup'] = df_stats_filtered['PlayerName'].apply(
                    lambda x: f"Highlighted ({highlight_player})" if x == highlight_player else "Others"
                )
                df_stats_filtered['SizeGroup'] = df_stats_filtered['PlayerName'].apply(
                    lambda x: 250 if x == highlight_player else 60
                )
                color_map = {f"Highlighted ({highlight_player})": "#FF5733", "Others": "#BDC3C7"}
            else:
                df_stats_filtered['ColorGroup'] = df_stats_filtered['FantasyOwner']
                df_stats_filtered['SizeGroup'] = 60
                color_map = None
            
            if not df_stats_filtered.empty:
                fig_scatter = px.scatter(
                    df_stats_filtered, 
                    x="Consistency (Std Dev)", 
                    y="Average Points", 
                    color="ColorGroup",
                    size="SizeGroup",
                    color_discrete_map=color_map,
                    hover_data=["PlayerName", "Team", "FantasyOwner"],
                    title="Who are your High-Floor vs. Volatile Assets?",
                    labels={"Consistency (Std Dev)": "Volatility (Standard Deviation)", "Average Points": "Average Weekly Points"}
                )
                fig_scatter.update_traces(marker=dict(sizemode='area', sizeref=1))
                fig_scatter.update_layout(legend_title="Category")
                st.plotly_chart(fig_scatter, use_container_width=True)
            else:
                st.info("No data available for the scatter plot under the current filters.")


    # ================= TAB 2: PLAYER TRENDS & HEAD-TO-HEAD =================
    with tab_trends:
        st.subheader("Player Comparison & Trajectory Analysis")
        
        player_options = sorted(df_raw_weekly['PlayerName'].unique())
        selected_players = st.multiselect(
            "Select Players to Compare", 
            options=player_options, 
            default=player_options[:2] if len(player_options) > 1 else player_options
        )
        
        if selected_players:
            # 1. Fantasy Points Weekly Trajectory
            fig_line = px.line(
                df_raw_weekly[df_raw_weekly['PlayerName'].isin(selected_players)], 
                x="Week", 
                y="FantasyPoints", 
                color="PlayerName",
                markers=True,
                hover_data=["Team", "FantasyOwner"],
                title="Weekly Points Trajectory Comparison",
                labels={"FantasyPoints": "Fantasy Points Scored", "Week": "Week of Season"}
            )
            fig_line.update_layout(xaxis=dict(tickmode='linear', tick0=1, dtick=1))
            st.plotly_chart(fig_line, use_container_width=True)
            
            # 2. CORE DETAILED STATS PROFILE COMPARISON (FACETED BAR CHART)
            st.markdown("---")
            st.subheader("📊 Core Statistical Profile Comparison (Season Totals)")
            
            if selected_position == 'QB':
                comparison_stats = ['PassingYDS', 'PassingTD', 'RushingYDS', 'RushingTD']
            elif selected_position == 'RB':
                comparison_stats = ['RushingYDS', 'RushingTD', 'ReceivingRec', 'ReceivingYDS']
            elif selected_position in ['WR', 'TE']:
                comparison_stats = ['ReceivingRec', 'ReceivingYDS', 'ReceivingTD']
            elif selected_position == 'K':
                comparison_stats = [col for col in df_agg_totals.columns if 'FG' in col or 'Pat' in col]
            else:
                comparison_stats = []
                
            comparison_stats = [s for s in comparison_stats if s in df_agg_totals.columns]
            
            if comparison_stats:
                df_compare_slice = df_agg_totals[df_agg_totals['PlayerName'].isin(selected_players)].copy()
                
                rename_map = {s: FRIENDLY_STATS.get(s, s) for s in comparison_stats}
                df_compare_slice = df_compare_slice.rename(columns=rename_map)
                friendly_stat_names = list(rename_map.values())
                
                df_melted = df_compare_slice.melt(
                    id_vars=['PlayerName'],
                    value_vars=friendly_stat_names,
                    var_name='NFL Statistic',
                    value_name='Value'
                )
                
                fig_bar = px.bar(
                    df_melted,
                    x='PlayerName',
                    y='Value',
                    color='PlayerName',
                    facet_col='NFL Statistic',
                    facet_col_spacing=0.06,
                    title="Head-to-Head Detailed Stat Breakdown",
                    labels={'Value': 'Cumulative Count / Yards', 'PlayerName': 'Player', 'NFL Statistic': 'Category'}
                )
                
                fig_bar.update_yaxes(matches=None, showticklabels=True)
                fig_bar.update_xaxes(tickangle=45)
                
                st.plotly_chart(fig_bar, use_container_width=True)
            else:
                st.info("Additional detailed stats are not available in this spreadsheet.")
        else:
            st.warning("Please select at least one player to view comparison profiles.")


    # ================= TAB 3: WAIVER WIRE EXPLORER =================
    with tab_waivers:
        st.subheader("Waiver Wire & Unowned Talent Finder")
        
        if not league_config:
            st.info("💡 Connect a Sleeper League ID in the sidebar to automatically sort players by who is currently owned versus free agents.")
            
        # Apply standard and scoping calculations directly on df_agg_totals so any slice inherits them
        cols = df_agg_totals.columns.tolist()
        
        # 1. Estimate PPR Metrics
        if 'ReceivingRec' in cols and 'ReceivingYDS' in cols:
            df_agg_totals['YardsPerCatch'] = df_agg_totals['ReceivingYDS'] / df_agg_totals['ReceivingRec'].replace(0, 1)
        
        # 2. Estimate Workhorse Touches
        carries_col = next((c for c in ['RushingCarries', 'TouchCarries', 'Carries'] if c in cols), None)
        if carries_col and 'ReceivingRec' in cols:
            df_agg_totals['EstimatedTouches'] = df_agg_totals[carries_col] + df_agg_totals['ReceivingRec']
            
        # 3. Sum elite field goals
        sniper_cols = [c for c in ['FGMade_40-49', 'FGMade_50'] if c in cols]
        if sniper_cols:
            df_agg_totals['LongFGs'] = df_agg_totals[sniper_cols].sum(axis=1)

        # Build clean Free Agent slice representing currently available unowned talent
        df_free_agents = df_agg_totals[df_agg_totals['FantasyOwner'] == "Free Agent"].copy()
        
        if df_free_agents.empty and league_config:
            st.success("There are no free agents available in this position, or all players are currently owned!")
        elif df_free_agents.empty:
            st.info("Please connect to a Sleeper league first to see which players on this list are unowned.")
        else:
            # --- WAIVER WIRE ADVANCED SCOUTING ENGINE ---
            st.markdown("### 🔍 Advanced Gem Hunter Configuration")
            
            # Define dynamic archetype list based on selected position
            archetypes = ["Total Fantasy Points (Standard)"]
            
            if selected_position == 'QB':
                if 'RushingYDS' in cols: 
                    archetypes.append("Konami Code QBs (Highest Rushing Yards)")
                if any(c in cols for c in ['PassingTD', 'Passing TD']): 
                    archetypes.append("TD Gunslingers (Highest Passing TDs)")
                    
            elif selected_position == 'RB':
                if 'ReceivingRec' in cols: 
                    archetypes.append("PPR Safety Valves (Highest Receptions)")
                if 'EstimatedTouches' in df_agg_totals.columns: 
                    archetypes.append("Workhorse Backs (Estimated Highest Touches)")
                elif 'RushingYDS' in cols:
                    archetypes.append("Ground Slashers (Highest Rushing Yards)")
                if 'RushingTD' in cols: 
                    archetypes.append("Goal-line Plungers (Highest Rushing TDs)")
                    
            elif selected_position in ['WR', 'TE']:
                if 'ReceivingRec' in cols: 
                    archetypes.append("PPR Target Monsters (Highest Receptions)")
                if 'YardsPerCatch' in df_agg_totals.columns: 
                    archetypes.append("Deep Threats (Highest Yards Per Catch)")
                if 'ReceivingTD' in cols: 
                    archetypes.append("Red-Zone Daggers (Highest Receiving TDs)")
                    
            elif selected_position == 'K':
                if 'LongFGs' in df_agg_totals.columns: 
                    archetypes.append("Long-Range Snipers (Elite 40+ & 50+ Yard FGs)")
                if 'PatMade' in cols: 
                    archetypes.append("Offense-Ride Kickers (Most Extra Points)")

            selected_archetype = st.selectbox("Select Waiver Gem Search Goal:", archetypes)
            
            # Define columns and sorting factors based on selection (always include total FP and FP/G side-by-side)
            sort_key = 'FantasyPoints'
            display_cols = ['PlayerName', 'Team', 'FantasyPoints', 'FantasyPointsPerGame']
            
            if selected_archetype == "Konami Code QBs (Highest Rushing Yards)":
                sort_key = 'RushingYDS'
                display_cols = ['PlayerName', 'Team', 'RushingYDS', 'FantasyPoints', 'FantasyPointsPerGame']
                
            elif selected_archetype in ["PPR Safety Valves (Highest Receptions)", "PPR Target Monsters (Highest Receptions)"]:
                sort_key = 'ReceivingRec'
                display_cols = ['PlayerName', 'Team', 'ReceivingRec', 'ReceivingYDS', 'FantasyPoints', 'FantasyPointsPerGame']
                
            elif selected_archetype == "Workhorse Backs (Estimated Highest Touches)":
                sort_key = 'EstimatedTouches'
                display_cols = ['PlayerName', 'Team', 'EstimatedTouches', 'RushingYDS', 'FantasyPoints', 'FantasyPointsPerGame']
                
            elif selected_archetype == "Ground Slashers (Highest Rushing Yards)":
                sort_key = 'RushingYDS'
                display_cols = ['PlayerName', 'Team', 'RushingYDS', 'FantasyPoints', 'FantasyPointsPerGame']
                
            elif selected_archetype == "Goal-line Plungers (Highest Rushing TDs)":
                sort_key = 'RushingTD'
                display_cols = ['PlayerName', 'Team', 'RushingTD', 'FantasyPoints', 'FantasyPointsPerGame']
                
            elif selected_archetype == "Deep Threats (Highest Yards Per Catch)":
                df_free_agents = df_free_agents[df_free_agents['ReceivingRec'] >= 3]
                sort_key = 'YardsPerCatch'
                display_cols = ['PlayerName', 'Team', 'YardsPerCatch', 'ReceivingRec', 'ReceivingYDS', 'FantasyPoints', 'FantasyPointsPerGame']
                
            elif selected_archetype == "Red-Zone Daggers (Highest Receiving TDs)":
                sort_key = 'ReceivingTD'
                display_cols = ['PlayerName', 'Team', 'ReceivingTD', 'FantasyPoints', 'FantasyPointsPerGame']
                
            elif selected_archetype == "Long-Range Snipers (Elite 40+ & 50+ Yard FGs)":
                sort_key = 'LongFGs'
                display_cols = ['PlayerName', 'Team', 'LongFGs', 'FGMade_40-49', 'FGMade_50', 'FantasyPoints', 'FantasyPointsPerGame']
                
            elif selected_archetype == "Offense-Ride Kickers (Most Extra Points)":
                sort_key = 'PatMade'
                display_cols = ['PlayerName', 'Team', 'PatMade', 'FantasyPoints', 'FantasyPointsPerGame']

            display_cols = [c for c in display_cols if c in df_free_agents.columns]
            
            # Sort and clean free agents
            df_fa_sorted = df_free_agents.sort_values(by=sort_key, ascending=False).copy()
            rename_dict = {c: FRIENDLY_STATS.get(c, c) for c in display_cols}
            
            # --- SIDE-BY-SIDE WAIVER LAYOUT ---
            col_table, col_plot = st.columns([1.1, 1.4])
            
            with col_table:
                st.markdown(f"**Gems Found for Strategy: `{selected_archetype}`**")
                
                format_dict = {}
                for col in display_cols:
                    friendly_col = rename_dict.get(col, col)
                    if pd.api.types.is_numeric_dtype(df_fa_sorted[col]):
                        if col in ['FantasyPoints', 'YardsPerCatch', 'FantasyPointsPerGame']:
                            format_dict[friendly_col] = "{:.2f}"
                        else:
                            format_dict[friendly_col] = "{:.0f}"

                st.dataframe(
                    df_fa_sorted[display_cols]
                    .rename(columns=rename_dict)
                    .style.background_gradient(subset=[rename_dict[sort_key]], cmap="Blues")
                    .format(format_dict),
                    use_container_width=True
                )
                
            with col_plot:
                st.markdown("**Free Agent Volatility vs. Performance**")
                
                # Filter the raw weekly records down to available Free Agents only
                df_fa_raw_weekly = df_raw_weekly[df_raw_weekly['FantasyOwner'] == "Free Agent"].copy()
                
                # Add calculated columns to raw weekly so they can be plotted
                raw_cols = df_fa_raw_weekly.columns.tolist()
                if 'ReceivingRec' in raw_cols and 'ReceivingYDS' in raw_cols:
                    df_fa_raw_weekly['YardsPerCatch'] = df_fa_raw_weekly['ReceivingYDS'] / df_fa_raw_weekly['ReceivingRec'].replace(0, 1)
                carries_col = next((c for c in ['RushingCarries', 'TouchCarries', 'Carries'] if c in raw_cols), None)
                if carries_col and 'ReceivingRec' in raw_cols:
                    df_fa_raw_weekly['EstimatedTouches'] = df_fa_raw_weekly[carries_col] + df_fa_raw_weekly['ReceivingRec']
                sniper_cols = [c for c in ['FGMade_40-49', 'FGMade_50'] if c in raw_cols]
                if sniper_cols:
                    df_fa_raw_weekly['LongFGs'] = df_fa_raw_weekly[sniper_cols].sum(axis=1)
                
                # Fallback safety check
                if sort_key not in df_fa_raw_weekly.columns:
                    sort_key = 'FantasyPoints'
                
                friendly_sort_name = FRIENDLY_STATS.get(sort_key, sort_key)
                
                if not df_fa_raw_weekly.empty:
                    df_fa_stats = df_fa_raw_weekly.groupby(['PlayerName', 'Team'])[sort_key].agg(['mean', 'std']).reset_index()
                    
                    x_axis_label = f"Volatility (Std Dev) of {friendly_sort_name}"
                    y_axis_label = f"Average Weekly {friendly_sort_name}"
                    
                    df_fa_stats.rename(columns={'mean': y_axis_label, 'std': x_axis_label}, inplace=True)
                    df_fa_stats[x_axis_label] = df_fa_stats[x_axis_label].fillna(0)
                    
                    highlight_fa = st.selectbox(
                        "🔍 Select an available Free Agent to locate on the plot below:",
                        options=["None"] + list(sorted(df_fa_stats['PlayerName'].unique())),
                        key="highlight_fa_selectbox" 
                    )
                    
                    if highlight_fa != "None":
                        df_fa_stats['ColorGroup'] = df_fa_stats['PlayerName'].apply(
                            lambda x: f"Highlighted ({highlight_fa})" if x == highlight_fa else "Available Free Agents"
                        )
                        df_fa_stats['SizeGroup'] = df_fa_stats['PlayerName'].apply(
                            lambda x: 250 if x == highlight_fa else 60
                        )
                        fa_color_map = {f"Highlighted ({highlight_fa})": "#FF5733", "Available Free Agents": "#3498DB"}
                    else:
                        df_fa_stats['ColorGroup'] = "Available Free Agents"
                        df_fa_stats['SizeGroup'] = 60
                        fa_color_map = {"Available Free Agents": "#3498DB"}
                        
                    fig_fa_scatter = px.scatter(
                        df_fa_stats,
                        x=x_axis_label,
                        y=y_axis_label,
                        color="ColorGroup",
                        size="SizeGroup",
                        color_discrete_map=fa_color_map,
                        hover_data=["PlayerName", "Team"],
                        title=f"Volatility & Ceiling Profile: {friendly_sort_name}",
                        labels={x_axis_label: x_axis_label, y_axis_label: y_axis_label}
                    )
                    fig_fa_scatter.update_traces(marker=dict(sizemode='area', sizeref=1))
                    fig_fa_scatter.update_layout(legend_title="Category")
                    st.plotly_chart(fig_fa_scatter, use_container_width=True)
                else:
                    st.info("Insufficient weekly data available to plot the Volatility map for free agents.")

            # --- ROSTER VS WAIVER COMPARISON SECTION ---
            st.markdown("---")
            st.markdown("### 🔄 Roster Upgrade Finder (Current Roster vs. Free Agents)")
            
            # Extract distinct fantasy owners excluding free agent codes
            active_owners = sorted([
                o for o in df_agg_totals['FantasyOwner'].unique() 
                if o not in ["Free Agent", "Not Connected", "Unknown (No PlayerName)", "Unknown (No PlayerID)"]
            ])
            
            if active_owners:
                col_sel_owner, _ = st.columns([1, 2.5])
                with col_sel_owner:
                    selected_compare_owner = st.selectbox(
                        "Select Your Team to Compare:",
                        options=active_owners,
                        key="compare_owner_select"
                    )
                
                if selected_compare_owner:
                    # Filter active players owned by selected manager
                    df_my_roster = df_agg_totals[df_agg_totals['FantasyOwner'] == selected_compare_owner].copy()
                    df_my_roster = df_my_roster.sort_values(by=sort_key, ascending=False)
                    
                    df_top_fa = df_fa_sorted.copy()
                    
                    col_my_team, col_fa_team = st.columns([1, 1])
                    
                    with col_my_team:
                        st.markdown(f"**Your Rostered {selected_position}s**")
                        if df_my_roster.empty:
                            st.info(f"You currently have no rostered {selected_position}s.")
                        else:
                            st.dataframe(
                                df_my_roster[display_cols]
                                .rename(columns=rename_dict)
                                .style.background_gradient(subset=[rename_dict[sort_key]], cmap="Greens")
                                .format(format_dict),
                                use_container_width=True
                            )
                            
                    with col_fa_team:
                        st.markdown(f"**Top Available Free Agent {selected_position}s**")
                        if df_top_fa.empty:
                            st.info(f"No free agent {selected_position}s available in the pool.")
                        else:
                            st.dataframe(
                                df_top_fa[display_cols].head(10)
                                .rename(columns=rename_dict)
                                .style.background_gradient(subset=[rename_dict[sort_key]], cmap="Blues")
                                .format(format_dict),
                                use_container_width=True
                            )

    # ================= TAB 4: LEAGUE TEAM POSITIONAL ANALYSIS =================
    with tab_league:
        st.subheader("🛡️ League Positional Strength Analysis")
        
        if not roster_map:
            st.info("💡 To view your league's positional breakdown, please connect your Sleeper League ID in the sidebar. This tab aggregates player data by their active managers.")
        else:
            # Compile master player list across all positions
            df_all_players_mapped = compile_master_player_list(selected_raw_file, selected_agg_file, roster_map)
            
            if df_all_players_mapped.empty:
                st.warning("Could not calculate team breakdown. Verify all positional sheets contain compiled stats.")
            else:
                # Compile standings metrics dynamically using unified players dataset
                grouped_stats = df_all_players_mapped.groupby(['FantasyOwner', 'Pos']).agg(
                    Total_Points=('FantasyPoints', 'sum'),
                    Total_Games=('GamesPlayed', 'sum')
                ).reset_index()
                
                grouped_stats['Total_Games'] = grouped_stats['Total_Games'].replace(0, 1)
                grouped_stats['PPG'] = grouped_stats['Total_Points'] / grouped_stats['Total_Games']
                
                # Pivot for cumulative points
                df_teams_raw = grouped_stats.pivot_table(
                    index='FantasyOwner',
                    columns='Pos',
                    values='Total_Points',
                    aggfunc='sum'
                ).fillna(0).reset_index()
                df_teams_raw.rename(columns={'FantasyOwner': 'Owner'}, inplace=True)
                
                # Pivot for points per game (FP/G)
                df_teams_ppg = grouped_stats.pivot_table(
                    index='FantasyOwner',
                    columns='Pos',
                    values='PPG',
                    aggfunc='sum'
                ).fillna(0).reset_index()
                df_teams_ppg.rename(columns={'FantasyOwner': 'Owner'}, inplace=True)
                
                available_positions = [pos for pos in ['QB', 'RB', 'WR', 'TE', 'K'] if pos in df_teams_raw.columns]
                
                # Standing display configuration selector
                st.markdown("### 📊 Standing Display Settings")
                view_mode = st.radio(
                    "Select Positional Metric to Display:",
                    options=["Cumulative Points", "Average Points Per Game (FP/G)"],
                    horizontal=True,
                    key="standings_view_mode_radio"
                )
                
                if view_mode == "Cumulative Points":
                    df_display = df_teams_raw.copy()
                    cmap_color = "Purples"
                else:
                    df_display = df_teams_ppg.copy()
                    cmap_color = "Blues"
                
                # Calculate the League Averages
                avg_row = {'Owner': 'League Average'}
                for pos in available_positions:
                    avg_row[pos] = df_display[pos].mean()
                    
                # Combine individual teams with the League Average Row
                df_teams_with_avg = pd.concat([df_display, pd.DataFrame([avg_row])], ignore_index=True)
                
                col_tbl, col_rad = st.columns([1, 1.4])
                
                with col_tbl:
                    st.markdown(f"**Positional Standings Grid ({view_mode})**")
                    
                    format_rules = {pos: '{:.2f}' for pos in available_positions}
                    df_teams_styled = (
                        df_teams_with_avg[['Owner'] + available_positions]
                        .style.background_gradient(subset=available_positions, cmap=cmap_color)
                        .format(format_rules)
                    )
                    st.dataframe(df_teams_styled, use_container_width=True)
                    
                with col_rad:
                    st.markdown("**Normalized Positional Strength Radar**")
                    
                    # Compute radial index scaling using cumulative points for scaling stability
                    df_radar_norm = df_teams_raw.copy()
                    avg_totals_map = {pos: df_teams_raw[pos].mean() for pos in available_positions}
                    
                    for pos in available_positions:
                        pos_avg = avg_totals_map[pos]
                        if pos_avg > 0:
                            df_radar_norm[pos] = (df_radar_norm[pos] / pos_avg) * 100
                        else:
                            df_radar_norm[pos] = 100.0
                    
                    avg_norm_row = {'Owner': 'League Average'}
                    for pos in available_positions:
                        avg_norm_row[pos] = 100.0
                    
                    df_radar_norm_with_avg = pd.concat([df_radar_norm, pd.DataFrame([avg_norm_row])], ignore_index=True)
                    
                    selected_teams = st.multiselect(
                        "Select Teams to Compare on Radar:",
                        options=list(df_radar_norm_with_avg['Owner'].unique()),
                        default=["League Average"] + ([df_teams_raw['Owner'].iloc[0]] if not df_teams_raw.empty else [])
                    )
                    
                    if selected_teams:
                        df_radar_selected = df_radar_norm_with_avg[df_radar_norm_with_avg['Owner'].isin(selected_teams)].copy()
                        df_radar_melted = df_radar_selected.melt(
                            id_vars=['Owner'],
                            value_vars=available_positions,
                            var_name='Position',
                            value_name='% of League Average'
                        )
                        
                        fig_radar = px.line_polar(
                            df_radar_melted,
                            r='% of League Average',
                            theta='Position',
                            color='Owner',
                            line_close=True,
                            title="Roster Volume comparison (100% = Exact Positional Average)"
                        )
                        fig_radar.update_traces(fill='toself', opacity=0.3)
                        fig_radar.update_traces(hovertemplate="%{theta}: %{r:.1f}%<extra></extra>")
                        st.plotly_chart(fig_radar, use_container_width=True)
                        st.caption("💡 **Interpretation:** 100% represents the exact League Average for that position. Vertices extending beyond 100% are above average, while vertices pulling inside 100% are below average.")
                    else:
                        st.warning("Please select at least one team to view on the spider comparison chart.")
                
                # --- TEAM ROSTER EXPLORER SECTION ---
                if not df_all_players_mapped.empty:
                    st.markdown("---")
                    
                    # Roster Count Breakdown Summary Section
                    st.markdown("### 📊 Active Roster Composition (Player Counts)")
                    counts_records = []
                    for owner in sorted(df_all_players_mapped['FantasyOwner'].unique()):
                        owner_df = df_all_players_mapped[df_all_players_mapped['FantasyOwner'] == owner]
                        counts_records.append({
                            'Fantasy Manager': owner,
                            'Total Rostered': len(owner_df),
                            'QB Count': (owner_df['Pos'] == 'QB').sum(),
                            'RB Count': (owner_df['Pos'] == 'RB').sum(),
                            'WR Count': (owner_df['Pos'] == 'WR').sum(),
                            'TE Count': (owner_df['Pos'] == 'TE').sum(),
                            'K Count': (owner_df['Pos'] == 'K').sum()
                        })
                    df_counts = pd.DataFrame(counts_records)
                    st.dataframe(df_counts, use_container_width=True, hide_index=True)
                    
                    st.markdown("---")
                    st.subheader("📋 Team Roster Explorer")
                    
                    selected_roster_owner = st.selectbox(
                        "Select Team to View Full Roster Details:",
                        options=sorted(df_all_players_mapped['FantasyOwner'].unique())
                    )
                    
                    if selected_roster_owner:
                        df_owner_roster = df_all_players_mapped[df_all_players_mapped['FantasyOwner'] == selected_roster_owner].copy()
                        df_owner_roster = df_owner_roster.sort_values(by='FantasyPoints', ascending=False)
                        
                        col_rost_tbl, col_rost_empty = st.columns([1.5, 1])
                        
                        with col_rost_tbl:
                            st.markdown(f"**Roster and Player Totals for: `{selected_roster_owner}`**")
                            st.dataframe(
                                df_owner_roster[['PlayerName', 'Pos', 'Team', 'FantasyPoints', 'FantasyPointsPerGame']]
                                .rename(columns={
                                    'PlayerName': 'Player', 
                                    'Pos': 'Position', 
                                    'Team': 'Team',
                                    'FantasyPoints': 'Total Points',
                                    'FantasyPointsPerGame': 'Points Per Game (FP/G)'
                                })
                                .style.background_gradient(subset=['Total Points'], cmap="Greens")
                                .format({'Total Points': '{:.2f}', 'Points Per Game (FP/G)': '{:.2f}'}),
                                use_container_width=True
                            )

    # ================= TAB 5: NFL TEAM OFFENSE SHARE =================
    with tab_nfl_team:
        st.subheader("📊 NFL Team Offensive Production & Market Share")
        
        df_all_nfl_players = compile_all_nfl_players(selected_raw_file, selected_agg_file)
        
        if df_all_nfl_players.empty:
            st.warning("Could not load NFL player database. Verify your aggregated spreadsheets are populated.")
        else:
            available_nfl_teams = sorted([t for t in df_all_nfl_players['Team'].unique() if isinstance(t, str) and len(t) <= 4])
            selected_nfl_team = st.selectbox("Select NFL Team to Analyze:", available_nfl_teams, index=0)
            
            df_team_offense = df_all_nfl_players[df_all_nfl_players['Team'] == selected_nfl_team].copy()
            df_team_offense = df_team_offense[df_team_offense['FantasyPoints'] > 0]
            
            if df_team_offense.empty:
                st.info(f"No positive offensive production data recorded for {selected_nfl_team} in this spreadsheet.")
            else:
                col_chart, col_stats = st.columns([1.4, 1.1])
                
                with col_chart:
                    st.markdown("**Offensive Hierarchy (Sunburst Chart)**")
                    fig_sunburst = px.sunburst(
                        df_team_offense,
                        path=['Pos', 'PlayerName'],
                        values='FantasyPoints',
                        title=f"{selected_nfl_team} Offensive Points Distribution",
                        color='Pos',
                        color_discrete_map={'QB': '#2C3E50', 'RB': '#2980B9', 'WR': '#27AE60', 'TE': '#E67E22'}
                    )
                    
                    # Visual enhancement: Adjusted height to 700px for a more readable breakdown
                    fig_sunburst.update_layout(
                        height=700,
                        margin=dict(t=50, l=10, r=10, b=10)
                    )
                    st.plotly_chart(fig_sunburst, use_container_width=True)
                    st.caption("💡 **How to read:** The inner circle splits points by position group. Clicking a position category zooms in to show a nested breakdown of individual players.")
                    
                with col_stats:
                    st.markdown("**Position Group Breakdown**")
                    
                    selected_group = st.selectbox(
                        "Filter Position Breakdown:",
                        options=[pos for pos in ['QB', 'RB', 'WR', 'TE'] if pos in df_team_offense['Pos'].unique()]
                    )
                    
                    df_group_offense = df_team_offense[df_team_offense['Pos'] == selected_group].copy()
                    
                    total_group_pts = df_group_offense['FantasyPoints'].sum()
                    if total_group_pts > 0:
                        df_group_offense['Share %'] = (df_group_offense['FantasyPoints'] / total_group_pts) * 100
                    else:
                        df_group_offense['Share %'] = 0.0
                        
                    df_group_offense = df_group_offense.sort_values(by='FantasyPoints', ascending=False)
                    
                    st.markdown(f"**{selected_nfl_team} {selected_group} Market Share Table**")
                    st.dataframe(
                        df_group_offense[['PlayerName', 'FantasyPoints', 'Share %']]
                        .rename(columns={'PlayerName': 'Player', 'FantasyPoints': 'Total Points'})
                        .style.background_gradient(subset=['Total Points'], cmap="Blues")
                        .format({'Total Points': '{:.2f}', 'Share %': '{:.1f}%'}),
                        use_container_width=True
                    )