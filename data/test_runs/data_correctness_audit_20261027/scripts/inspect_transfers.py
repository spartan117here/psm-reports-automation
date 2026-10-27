import json

transferred_codes = {'768', '859', '1183', '1189', '2267', '2294'}
for fpath, name in [
    ('data/raw/2026/10/25/innervex_subhiksham_memberlist_2026-10-25.json', 'SS'),
    ('data/raw/2026/10/25/innervex_viruksham_memberlist_2026-10-25.json', 'SV')
]:
    data = json.load(open(fpath, encoding='utf-8'))
    rows = data.get('data', data) if isinstance(data, dict) else data
    matches = [r for r in rows if str(r.get('EMPCODE', '')).strip() in transferred_codes]
    print(f"=== {name} Matches ({len(matches)}) ===")
    for m in matches:
        print(f"EMPCODE: {m.get('EMPCODE')}, COSTNAME: {m.get('COSTNAME')}, MSNO: {m.get('MSNO')}, AMOUNT: {m.get('RECAMOUNT')}, SCHDATE: {m.get('SCHDATE')}")
