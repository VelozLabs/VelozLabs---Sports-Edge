import os
from pathlib import Path

import duckdb
from jinja2 import Environment, FileSystemLoader

from pipeline.config import DUCKDB_PATH, PROJECT_ROOT

# The folder where the static site will be generated
DOCS_DIR = PROJECT_ROOT / "docs"
TEMPLATES_DIR = PROJECT_ROOT / "dashboard" / "templates"

def generate_site():
    """Generates the flat HTML static site."""
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    
    # 1. Setup Jinja2 Environment
    env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)))
    index_template = env.get_template("index.html")
    
    # 2. Query DuckDB for the Leaderboard Data
    # We want the most recent games, focusing on betting stats: CSW, velo, whiffs, and AI projection
    with duckdb.connect(str(DUCKDB_PATH)) as con:
        # Get the most recent game date in the DB
        latest_date = con.execute("SELECT MAX(game_date) FROM pitcher_game_summary").fetchone()[0]
        
        if not latest_date:
            print("No data in Gold layer. Run the pipeline first.")
            return

        # Fetch leaderboard for the most recent day
        query = f"""
            SELECT 
                p.full_name AS Pitcher,
                pgs.total_pitches AS Pitches,
                ROUND(pgs.csw_rate * 100, 1) AS "CSW_Pct",
                ROUND(pgs.whiff_rate * 100, 1) AS "Whiff_Pct",
                ROUND(pgs.avg_velo, 1) AS "Avg_Velo_mph",
                ROUND(pgs.velo_vs_30d_avg, 1) AS "Velo_Delta",
                ROUND(pgs.avg_xwoba_allowed, 3) AS "xwOBA_Allowed",
                pgs.performance_tier AS Tier,
                LI.insight_json AS AI_Analysis
            FROM pitcher_game_summary pgs
            JOIN players p ON p.player_id = pgs.pitcher_id
            LEFT JOIN llm_insights LI 
                ON LI.pitcher_id = pgs.pitcher_id 
                AND LI.game_pk = pgs.game_pk
            WHERE pgs.game_date = '{latest_date}'
            ORDER BY pgs.csw_rate DESC
        """
        
        df = con.execute(query).df()
        
        # Parse the JSON insights so Jinja can render them natively
        import json
        
        def parse_json(val):
            if not val or pd.isna(val):
                return None
            try:
                return json.loads(val)
            except:
                return None
        
        import pandas as pd
        df['AI_Analysis'] = df['AI_Analysis'].apply(parse_json)
        
        # Convert DF to list of dicts for Jinja
        pitchers = df.to_dict(orient='records')
        
    # 3. Render HTML
    html_out = index_template.render(
        latest_date=latest_date,
        pitchers=pitchers
    )
    
    # 4. Write to docs/index.html
    out_file = DOCS_DIR / "index.html"
    with open(out_file, "w", encoding="utf-8") as f:
        f.write(html_out)
        
    print(f"✅ Generated static site at {out_file}")

if __name__ == "__main__":
    generate_site()
