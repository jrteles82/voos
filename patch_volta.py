import re

with open('main.py', 'r', encoding='utf-8') as f:
    content = f.read()

old_group1 = """    idas = [r for r in parsed if str(r.get("origin", "")).upper() == "PVH" and str(r.get("destination", "")).upper() != "PVH"]
    voltas = [r for r in parsed if str(r.get("destination", "")).upper() == "PVH"]"""

new_group1 = """    # Group by direction intelligently based on the first origin if available
    primary_origin = parsed[0].get("origin", "").upper() if parsed else "PVH"
    idas = [r for r in parsed if str(r.get("origin", "")).upper() == primary_origin]
    voltas = [r for r in parsed if str(r.get("destination", "")).upper() == primary_origin]"""

content = content.replace(old_group1, new_group1)

old_format1 = """        *(_format_direction(idas_ok, idas_ok[0] if idas_ok else None, "IDAS (PVH -> destino):", "destination")),
        "",
        *(_format_direction(voltas_ok, voltas_ok[0] if voltas_ok else None, "VOLTAS (destino -> PVH):", "origin")),"""

new_format1 = """        *(_format_direction(idas_ok, idas_ok[0] if idas_ok else None, f"IDAS ({primary_origin} -> destino):", "destination")),
        "",
        *(_format_direction(voltas_ok, voltas_ok[0] if voltas_ok else None, f"VOLTAS (destino -> {primary_origin}):", "origin")),"""

content = content.replace(old_format1, new_format1)

old_group2 = """def _group_scan_rows_for_image(rows: list[dict]) -> list[tuple[str, list[dict]]]:
    idas = [
        r for r in rows
        if str(r.get("origin", "")).upper() == "PVH" and str(r.get("destination", "")).upper() != "PVH"
    ]
    voltas = [r for r in rows if str(r.get("destination", "")).upper() == "PVH"]"""

new_group2 = """def _group_scan_rows_for_image(rows: list[dict]) -> list[tuple[str, list[dict]]]:
    primary_origin = rows[0].get("origin", "").upper() if rows else "PVH"
    idas = [
        r for r in rows
        if str(r.get("origin", "")).upper() == primary_origin
    ]
    voltas = [r for r in rows if str(r.get("destination", "")).upper() == primary_origin]"""

content = content.replace(old_group2, new_group2)

old_format2 = """    if idas_ok:
        groups.append(("IDAS", idas_ok))
    if voltas_ok:
        groups.append(("VOLTAS PARA PVH", voltas_ok))"""

new_format2 = """    if idas_ok:
        groups.append(("IDAS", idas_ok))
    if voltas_ok:
        groups.append((f"VOLTAS PARA {primary_origin}", voltas_ok))"""

content = content.replace(old_format2, new_format2)

with open('main.py', 'w', encoding='utf-8') as f:
    f.write(content)
