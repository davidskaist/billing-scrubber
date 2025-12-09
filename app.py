import streamlit as st
import pandas as pd
import io
import warnings

# Suppress warnings
warnings.filterwarnings("ignore")

# ==========================================
# CONFIGURATION & RULES
# ==========================================
MAX_SESSION_HOURS = 4        
MAX_SUPERVISION_HOURS = 2    
HIGH_DRIVE_TIME = 60         

CODE_DIRECT_CARE = 97153
CODE_SUPERVISION_1 = 97155
CODE_PARENT_TRAINING_ALL = [97156, 97157, 96167, 96168, 96170, 96171]

BASE_ADDON_PAIRS = {
    96158: 96159, 
    96164: 96165,  
    96167: 96168,  
    96170: 96171   
}
BASE_CODES = list(BASE_ADDON_PAIRS.keys())

SUPERVISION_PAIRS = {
    97155: 97153,
    96156: 96159 
}

CODES_CONFLICT_WITH_DIRECT = [96167, 96168] 
FORBIDDEN_LOCATIONS_CODES = [3, '03']
FORBIDDEN_LOCATIONS_TEXT = ['school']

# ==========================================
# LOGIC FUNCTION
# ==========================================
def scrub_data(df):
    # --- PRE-PROCESSING & CLEANING ---
    df.columns = df.columns.str.strip()
    
    # 1. CLEAN PROCEDURE CODES (The Fix!)
    # This forces text like "97153 Jersey" to become Numbers. 
    # If it can't turn it into a number, it deletes that row to prevent errors.
    df['ProcedureCode'] = pd.to_numeric(df['ProcedureCode'], errors='coerce')
    df = df.dropna(subset=['ProcedureCode'])

    # Handle Names
    if 'Client Name' not in df.columns:
        df['Client Name'] = df['ClientFirstName'].fillna('') + ' ' + df['ClientLastName'].fillna('')
    if 'Provider Name' not in df.columns:
        df['Provider Name'] = df['ProviderFirstName'].fillna('') + ' ' + df['ProviderLastName'].fillna('')

    # Handle Dates
    try:
        df['TimeWorkedFrom'] = pd.to_datetime(df['TimeWorkedFrom'])
        df['TimeWorkedTo'] = pd.to_datetime(df['TimeWorkedTo'])
        df['DateOfService'] = pd.to_datetime(df['DateOfService'])
    except Exception as e:
        return [f"Date Error: {e}"], df

    df['DateOnly'] = df['DateOfService'].dt.date
    df['is_supervised'] = False 
    
    issues = []

    # --- A. ROW CHECKS ---
    for index, row in df.iterrows():
        # 1. Max Session
        if row['TimeWorkedInHours'] > MAX_SESSION_HOURS:
            issues.append({'Client': row['Client Name'], 'Date': row['DateOnly'], 'Issue': 'Session > 4 Hours', 'Detail': f"{row['ProcedureCode']} lasted {row['TimeWorkedInHours']} hrs"})

        # 2. Supervision Max
        if row['ProcedureCode'] == CODE_SUPERVISION_1 and row['TimeWorkedInHours'] > MAX_SUPERVISION_HOURS:
             issues.append({'Client': row['Client Name'], 'Date': row['DateOnly'], 'Issue': 'Supervision > 2 Hours', 'Detail': f"{row['TimeWorkedInHours']} hrs"})

        # 3. Forbidden Locations
        loc_code = row.get('LocationCode', '')
        loc_desc = str(row.get('LocationDescription', '')).lower()
        if loc_code in FORBIDDEN_LOCATIONS_CODES or any(x in loc_desc for x in FORBIDDEN_LOCATIONS_TEXT):
             issues.append({'Client': row['Client Name'], 'Date': row['DateOnly'], 'Issue': 'Forbidden Location', 'Detail': f"{loc_code} {loc_desc}"})

        # 4. Travel Time
        if row['DriveTimeInMinutes'] > HIGH_DRIVE_TIME:
             issues.append({'Client': row['Client Name'], 'Date': row['DateOnly'], 'Issue': 'High Travel Time', 'Detail': f"{row['DriveTimeInMinutes']} mins"})

    # --- B. GROUP CHECKS ---
    for (client, date), group in df.groupby(['Client Name', 'DateOnly']):
        codes_today = group['ProcedureCode'].tolist()
        
        # 5. Conflict
        if CODE_DIRECT_CARE in codes_today and any(c in CODES_CONFLICT_WITH_DIRECT for c in codes_today):
            issues.append({'Client': client, 'Date': date, 'Issue': 'Direct Care & Family Conflict', 'Detail': 'Cannot bill 97153 + Family same day'})

        # 6. Duplicate Base
        for base_code in BASE_CODES:
            if codes_today.count(base_code) > 1:
                issues.append({'Client': client, 'Date': date, 'Issue': f'Duplicate Base {base_code}', 'Detail': 'Billed multiple times'})

        # 7. Add-on Logic & Timestamps
        for base_code, addon_code in BASE_ADDON_PAIRS.items():
            addon_rows = group[group['ProcedureCode'] == addon_code]
            base_rows = group[group['ProcedureCode'] == base_code]

            if not addon_rows.empty:
                if base_rows.empty:
                    issues.append({'Client': client, 'Date': date, 'Issue': f'Orphaned Add-on {addon_code}', 'Detail': 'No Base Code'})
                else:
                    if base_rows['TimeWorkedFrom'].min() > addon_rows['TimeWorkedFrom'].min():
                        issues.append({'Client': client, 'Date': date, 'Issue': 'Sequence Error', 'Detail': f'Base {base_code} started AFTER Add-on'})

    # --- C. OVERLAP CHECKS ---
    for sup_code, target_code in SUPERVISION_PAIRS.items():
        sup_rows = df[df['ProcedureCode'] == sup_code]
        target_rows = df[df['ProcedureCode'] == target_code]

        for i, sup in sup_rows.iterrows():
            overlap = target_rows[
                (target_rows['Client Name'] == sup['Client Name']) & 
                (target_rows['TimeWorkedFrom'] < sup['TimeWorkedTo']) & 
                (target_rows['TimeWorkedTo'] > sup['TimeWorkedFrom'])
            ]
            
            if overlap.empty:
                issues.append({'Client': sup['Client Name'], 'Date': sup['DateOnly'], 'Issue': f'No Overlap for {sup_code}', 'Detail': f'No concurrent {target_code}'})
            else:
                if sup_code == CODE_SUPERVISION_1:
                    df.loc[overlap.index, 'is_supervised'] = True

    # --- D. MONTHLY CHECKS ---
    for client, group in df.groupby('Client Name'):
        if not any(c in group['ProcedureCode'].tolist() for c in CODE_PARENT_TRAINING_ALL):
            issues.append({'Client': client, 'Date': 'Monthly', 'Issue': 'Missing Parent Training', 'Detail': 'None this month'})

        direct_providers = group[group['ProcedureCode'] == CODE_DIRECT_CARE]['Provider Name'].unique()
        for provider in direct_providers:
            p_sessions = group[(group['Provider Name'] == provider) & (group['ProcedureCode'] == CODE_DIRECT_CARE)]
            if not p_sessions['is_supervised'].any():
                issues.append({'Client': client, 'Date': 'Monthly', 'Issue': 'RBT Never Supervised', 'Detail': f'Provider {provider} had no overlap'})

    return issues

# ==========================================
# USER INTERFACE (STREAMLIT)
# ==========================================
st.set_page_config(page_title="Billing Scrubber", page_icon="🧼")

st.title("🧼 Billing Compliance Scrubber")
st.markdown("Upload your **CSV** or **Excel** file to audit for CPT code errors, overlaps, and timestamps.")

uploaded_file = st.file_uploader("Upload Billing File", type=['csv', 'xlsx'])

if uploaded_file:
    st.divider()
    st.write("Running checks...")
    
    # Load file into DataFrame
    try:
        if uploaded_file.name.endswith('.csv'):
            df = pd.read_csv(uploaded_file)
        else:
            df = pd.read_excel(uploaded_file)
            
        # Run Logic
        issues = scrub_data(df)

        if not issues:
            st.success("✅ No Errors Found! This file is clean.")
        else:
            st.error(f"❌ Found {len(issues)} Issues")
            
            # Show Data on screen
            report_df = pd.DataFrame(issues)
            st.dataframe(report_df, use_container_width=True)

            # Create Download Button
            buffer = io.BytesIO()
            with pd.ExcelWriter(buffer, engine='xlsxwriter') as writer:
                report_df.to_excel(writer, index=False)
            
            st.download_button(
                label="📥 Download Report as Excel",
                data=buffer,
                file_name="Scrubbed_Report.xlsx",
                mime="application/vnd.ms-excel"
            )
            
    except Exception as e:
        st.error(f"An error occurred reading the file: {e}")
