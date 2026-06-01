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

# --- SCORING FUNCTION ---
def calculate_fantasy_points(row):
    points = 0.0
    row = row.fillna(0)
    points += row.get('PassingYDS', 0) / 25
    points += row.get('Passing TD', 0) * 4
    points += row.get('PassingInt', 0) * -2
    points += row.get('RushingYDS', 0) / 10
    points += row.get('RushingTD', 0) * 6
    points += row.get('ReceivingRec', 0) * 1
    points += row.get('ReceivingYDS', 0) / 10
    points += row.get('ReceivingTD', 0) * 6
    points += row.get('2PT', 0) * 2
    points += row.get('Fum', 0) * -2
    points += row.get('FGMade_0-19', 0) * 3
    points += row.get('FGMade_20-29', 0) * 3
    points += row.get('FGMade_30-39', 0) * 3
    points += row.get('FGMade_40-49', 0) * 4
    points += row.get('FGMade_50', 0) * 5
    points += row.get('PatMade', 0) * 1
    return points

# --- DICTIONARY FOR USER-FRIENDLY LABELS ---
FRIENDLY_STATS = {
    'FantasyPoints': 'Fantasy Points',
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
}

# --- MASTER SLEEPER PLAYER DATABASE FOR TRANSLATION ---
@st.cache_data(ttl=86400)
def get_sleeper_player_db():
    """Fetches the master player database from Sleeper to map Sleeper IDs to names."""
    try:
        url = "https://api.sleeper.app/v1/players/nfl"
        resp = requests.get(url, timeout=15)
        if resp.status_code == 200:
            players_data = resp.json()
            id_to_name = {}
            for p_id, p_info in players_data.items():
                full_name = p_info.get('full_name')
                if full_name:
                    id_to_name[str(p_id)] = full_name
            return id_to_name
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
                # Map the Sleeper Player ID -> Full Name
                player_name = player_db.get(str(p_id))
                if player_name:
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

# --- FILE DETECTION HELPERS ---
def find_excel_files():
    files = os.listdir('.')
    raw_files = [f for f in files if f.startswith('raw_data_') and f.endswith('.xlsx')]
    agg_files = [f for f in files if f.startswith('aggregated_data_') and f.endswith('.xlsx')]
    return sorted(raw_files), sorted(agg_files)


# --- SIDEBAR INTERFACE ---
st.sidebar.header("📁 Data Sources")
raw_files, agg_files = find_excel_files()

if not raw_files or not agg_files:
    st.warning("Please make sure your generated raw and aggregated Excel files are in the same folder as this script.")
    st.stop()

selected_raw_file = st.sidebar.selectbox("Select Raw Data File", raw_files)
selected_agg_file = st.sidebar.selectbox("Select Aggregated Summary File", agg_files)

# Extract target year from filename
year_match = re.search(r'raw_data_(\d{4})', selected_raw_file)
target_year = int(year_match.group(1)) if year_match else 2025

# --- SLEEPER CONFIGURATION MANAGER ---
st.sidebar.markdown("---")
st.sidebar.header("🔑 Sleeper League Manager")

# Load existing config database
league_config = load_league_config()

# Display active saved leagues
if league_config:
    st.sidebar.write("**Saved League Mappings:**")
    for yr, l_id in sorted(league_config.items(), reverse=True):
        st.sidebar.caption(f"📅 **{yr}** : `{l_id}`")
else:
    st.sidebar.info("No saved league mappings. Add your first league ID below.")

# Save mapping form
with st.sidebar.form("league_mapping_form", clear_on_submit=True):
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

# 1. Fetch Master Sleeper Player Database for Translation
with st.spinner("Loading Sleeper master player names database (runs once)..."):
    player_db = get_sleeper_player_db()

# 2. Determine active mapping for selected dataset using translation
roster_map = {}
mapped_for_target = str(target_year) in league_config

if player_db and league_config:
    with st.spinner(f"Loading {target_year} Season owner rosters..."):
        roster_map = fetch_roster_map_for_year(target_year, league_config, player_db)

if roster_map:
    if mapped_for_target:
        st.sidebar.success(f"Matched directly using saved {target_year} ID.")
    else:
        st.sidebar.success(f"Matched by auto-traversing back to {target_year}.")
else:
    st.sidebar.warning(f"No owner mapping found for {target_year}. Add the League ID in the form above to link your owners.")

# Helper to assign owner based on Name matching (replaces conflicting ID matching)
def assign_owner(df, name_to_owner_map):
    if not name_to_owner_map:
        df['FantasyOwner'] = "Not Connected"
        return df
    
    def clean_name(n):
        if not isinstance(n, str):
            return ""
        # Remove trailing suffixes to align naming systems (II, III, Jr, etc)
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


# --- CORE DATA LOADING ---
@st.cache_data
def load_excel_sheets(raw_path, agg_path):
    raw_sheets = pd.ExcelFile(raw_path).sheet_names
    agg_sheets = pd.ExcelFile(agg_path).sheet_names
    return raw_sheets, agg_sheets

raw_sheet_names, agg_sheet_names = load_excel_sheets(selected_raw_file, selected_agg_file)


# --- DASHBOARD PAGE LAYOUT ---
st.title("🏈 Fantasy NFL Interactive Dashboard")

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
    
    # Ensure standard scoring columns are calculated on raw weekly if not already calculated
    if 'FantasyPoints' not in df_raw_weekly.columns:
        df_raw_weekly['FantasyPoints'] = df_raw_weekly.apply(calculate_fantasy_points, axis=1)
        
    # Also ensure totals matches our scoring
    if 'FantasyPoints' not in df_agg_totals.columns:
        df_agg_totals['FantasyPoints'] = df_agg_totals.apply(calculate_fantasy_points, axis=1)

    # Map owners to the loaded datasets
    df_raw_weekly = assign_owner(df_raw_weekly, roster_map)
    df_agg_totals = assign_owner(df_agg_totals, roster_map)
    df_agg_averages = assign_owner(df_agg_averages, roster_map)
    df_agg_pivot = assign_owner(df_agg_pivot, roster_map)

except Exception as e:
    st.error(f"Error loading sheets for {selected_position}. Verify both Excel files contain data for this position.")
    st.stop()


# Create Dashboard Tabs
tab_leaderboard, tab_trends, tab_waivers = st.tabs([
    "🏆 Leaderboards & Consistency", 
    "📈 Player Trends & Head-to-Head", 
    "🕵️ Waiver Wire Explorer"
])

# ================= TAB 1: LEADERBOARD & CONSISTENCY =================
with tab_leaderboard:
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
        # Find highest FGMade column if available
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
        
        # Display table with dynamic coloring based on user's selected metric
        display_columns = ['PlayerName', 'Team', selected_stat_key, 'FantasyOwner']
        # If the metric selected wasn't FantasyPoints, let's include it anyway for context
        if selected_stat_key != 'FantasyPoints' and 'FantasyPoints' in filtered_totals.columns:
            display_columns.insert(3, 'FantasyPoints')
            
        filtered_totals_styled = (
            filtered_totals[display_columns]
            .rename(columns=FRIENDLY_STATS)
            .style.background_gradient(subset=[FRIENDLY_STATS[selected_stat_key]], cmap="Greens")
            .format({FRIENDLY_STATS[col]: '{:.2f}' if col == 'FantasyPoints' else '{:.0f}' 
                     for col in [selected_stat_key, 'FantasyPoints'] if col in FRIENDLY_STATS})
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
        
        # 2. CORE DETAILED STATS PROFILE COMPARISON (BAR CHART)
        st.markdown("---")
        st.subheader("📊 Core Statistical Profile Comparison (Season Totals)")
        
        # Select comparisons based on positions
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
            
        # Verify columns exist
        comparison_stats = [s for s in comparison_stats if s in df_agg_totals.columns]
        
        if comparison_stats:
            df_compare_slice = df_agg_totals[df_agg_totals['PlayerName'].isin(selected_players)].copy()
            
            # Rename column headers to friendly terms
            rename_map = {s: FRIENDLY_STATS.get(s, s) for s in comparison_stats}
            df_compare_slice = df_compare_slice.rename(columns=rename_map)
            friendly_stat_names = list(rename_map.values())
            
            # Melt structure for side-by-side Plotly bar chart
            df_melted = df_compare_slice.melt(
                id_vars=['PlayerName'],
                value_vars=friendly_stat_names,
                var_name='NFL Statistic',
                value_name='Value'
            )
            
            fig_bar = px.bar(
                df_melted,
                x='NFL Statistic',
                y='Value',
                color='PlayerName',
                barmode='group',
                title=f"Head-to-Head Detailed Stat Breakdown",
                labels={'Value': 'Cumulative Count / Yards', 'NFL Statistic': 'Category'}
            )
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
        
    df_free_agents = df_agg_totals[df_agg_totals['FantasyOwner'] == "Free Agent"]
    
    if df_free_agents.empty and league_config:
        st.success("There are no free agents available in this position, or all players are currently owned!")
    elif df_free_agents.empty:
        st.info("Please connect to a Sleeper league first to see which players on this list are unowned.")
    else:
        st.markdown(f"**Top Available Free Agents at {selected_position} (Sorted by Cumulative Season Points)**")
        
        df_fa_clean = df_free_agents[['PlayerName', 'Team', 'FantasyPoints']].copy()
        col_table, col_empty = st.columns([2, 1])
        
        with col_table:
            st.dataframe(
                df_fa_clean.rename(columns={'FantasyPoints': 'Total Fantasy Points'})
                .style.background_gradient(subset=['Total Fantasy Points'], cmap="Blues")
                .format({'Total Fantasy Points': '{:.2f}'}),
                use_container_width=True
            )