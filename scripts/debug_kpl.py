"""Debug: check kpl_concept_cons structure and concept matching."""
import pandas as pd
import re

kpl = pd.read_parquet(
    '/home/richard/data/market-data-platform/assets/tushare/a_share/'
    'kpl_concept_cons/a_share_all_kpl_concept_cons_latest/data/'
    'trade_date=20241014/part.parquet'
)
print(f"rows={len(kpl)} cols={list(kpl.columns)}")
names = kpl['con_name'].unique()
print(f"unique con_names={len(names)}")
for n in names[:15]:
    print(f"  [{n}]")

# Check dc_concept_cons
try:
    dcc = pd.read_parquet(
        '/home/richard/data/market-data-platform/assets/tushare/a_share/'
        'dc_concept_cons/a_share_all_dc_concept_cons_latest/data/'
        'trade_date=20241014/part.parquet'
    )
    print(f"\ndc_concept_cons rows={len(dcc)} cols={list(dcc.columns)}")
    if 'name' in dcc.columns:
        dn = dcc['name'].unique()
        print(f"unique names={len(dn)}")
        for n in dn[:15]:
            print(f"  [{n}]")
except Exception as e:
    print(f"dc_concept_cons not available: {e}")

# Test matching
hot = ['期货概念', '华为欧拉', '数字货币', '跨境支付(CIPS)']
print("\nMatching hot concepts in kpl:")
for c in hot:
    m = kpl[kpl['con_name'].str.contains(re.escape(c), case=False, na=False)]
    print(f"  [{c}] -> matches={len(m)}")
    if len(m) > 0:
        print(m[['ts_code','con_name']].head(3).to_string())
