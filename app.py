import streamlit as st
import pandas as pd
import pdfplumber
import io
import warnings
import re
from collections import Counter

# Suppress warnings
warnings.filterwarnings("ignore")

# ==========================================
# CONFIGURATION & RULES
# ==========================================
# --- Billing Rules ---
MAX_SESSION_HOURS = 4        
MAX_SUPERVISION_HOURS = 2    
HIGH_DRIVE_TIME = 60         

CODE_DIRECT_CARE = 97153
CODE_SUPERVISION_1 = 97155
CODE_PARENT_TRAINING_ALL = [97156, 97157, 96167, 96168, 96170, 96171]

BASE_ADDON_PAIRS = {96158: 96159, 96164: 96165, 96167: 96168, 96170: 96171}
BASE_CODES = list(BASE_ADDON_PAIRS.keys())
SUPERVISION_PAIRS = {97155: 97153, 96156: 96159}
CODES_CONFLICT_WITH_DIRECT = [96167, 96168] 
FORBIDDEN_LOCATIONS_CODES = [3, '03']
FORBIDDEN_LOCATIONS_TEXT = ['school']

# --- Note Scrubbing Rules ---
MIN_GOALS_PER_HOUR = 1

# ==========================================
# LOGIC: BILLING SCRUBBER
# ==========================================
def scrub_billing_data(df):
    # Pre-processing
    df.columns = df.columns.str.strip()
    # Clean Procedure Codes
    df['ProcedureCode'] = pd.to_numeric(df['ProcedureCode'], errors='coerce')
    df = df.dropna(subset=['ProcedureCode'])

    if 'Client Name' not in df.columns:
        df['Client Name'] = df['ClientFirstName'].fillna('') + ' ' + df['ClientLastName'].fillna('')
    if 'Provider Name' not in df.columns:
        df['Provider Name'] = df['ProviderFirstName'].fillna('') + ' ' + df['ProviderLastName'].fillna('')

    try:
        df['TimeWorkedFrom'] = pd.to_datetime(df['TimeWorkedFrom'])
        df['TimeWorkedTo'] = pd.to_datetime(df['TimeWorkedTo'])
        df['DateOfService'] = pd.to_datetime(df['DateOfService'])
    except Exception as e:
        return [f"Date Error: {e}"], df

    df['DateOnly'] = df['DateOfService'].dt.date
    df['is_supervised'] = False 
    
    issues = []

    # --- ROW CHECKS ---
    for index,
