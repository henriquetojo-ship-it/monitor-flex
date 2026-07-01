import os, sys, urllib.request, json
from pathlib import Path
from dotenv import dotenv_values

MB_URL = "https://metabase.tools.mecanizou.com"

_ENV = dotenv_values(Path(__file__).resolve().parent.parent / ".env.local")
API_KEY = os.environ.get("METABASE_API_KEY") or _ENV.get("METABASE_API_KEY")
if not API_KEY:
    print("[ERRO] METABASE_API_KEY ausente. Defina em .env.local ou como variável de ambiente.", file=sys.stderr)
    sys.exit(1)

SQL_VALIDATE = """
WITH base_date AS (
    SELECT date_day
    FROM public_dimensions.dim_date
    WHERE date_day >= DATEADD(month, -1, CURRENT_DATE)
),

quote_metrics AS (
    SELECT
        fqri.quote_request_id,
        fqri.vehicle_manufacturer,
        fqri.vehicle_year,
        MIN(fqri.second_to_quote) / 60.0 AS tmo_minutes,
        COUNT(DISTINCT fqri.quote_request_item_id) AS total_pecas
    FROM public_facts.ft_quote_request_items fqri
        JOIN base_date bd ON fqri.quote_request_datetime::date = bd.date_day
    WHERE fqri.quote_type IN ('Normal Quote', 'Fast Quote - Normal')
        AND fqri.second_to_quote IS NOT NULL
        AND fqri.second_to_quote > 0
    GROUP BY fqri.quote_request_id, fqri.vehicle_manufacturer, fqri.vehicle_year
),

with_ranges AS (
    SELECT
        quote_request_id,
        COALESCE(vehicle_manufacturer, 'Sem montadora') AS montadora,
        CASE
            WHEN vehicle_year >= 2025 THEN '2025 a 2026'
            WHEN vehicle_year >= 2020 THEN '2020 a 2025'
            WHEN vehicle_year >= 2015 THEN '2015 a 2020'
            WHEN vehicle_year >= 2010 THEN '2010 a 2015'
            WHEN vehicle_year >= 2005 THEN '2005 a 2010'
            WHEN vehicle_year >= 2000 THEN '2000 a 2005'
            WHEN vehicle_year IS NOT NULL THEN 'Abaixo de 2000'
            ELSE 'Sem ano informado'
        END AS faixa_ano,
        CASE
            WHEN total_pecas <= 2  THEN 'De 1 a 2 pecas'
            WHEN total_pecas <= 5  THEN 'De 2 a 5 pecas'
            WHEN total_pecas <= 10 THEN 'De 5 a 10 pecas'
            ELSE 'Acima de 10 pecas'
        END AS faixa_pecas,
        tmo_minutes
    FROM quote_metrics
),

agg AS (
    SELECT
        montadora,
        faixa_ano,
        faixa_pecas,
        PERCENTILE_CONT(0.5)  WITHIN GROUP (ORDER BY tmo_minutes) AS mediana_tmo,
        PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY tmo_minutes) AS p75_tmo,
        COUNT(*) AS qtd_cotacoes
    FROM with_ranges
    GROUP BY montadora, faixa_ano, faixa_pecas
)

SELECT
    montadora,
    faixa_ano,
    faixa_pecas,
    ROUND(mediana_tmo::numeric, 1) AS mediana_tmo,
    ROUND(p75_tmo::numeric, 1)     AS p75_tmo,
    ROUND((p75_tmo - mediana_tmo)::numeric, 1) AS diferenca_min,
    ROUND((p75_tmo / NULLIF(mediana_tmo, 0))::numeric, 2) AS ratio_p75_mediana,
    qtd_cotacoes
FROM agg
ORDER BY montadora, faixa_ano, faixa_pecas
LIMIT 10
"""

def api(path, payload=None):
    url = f"{MB_URL}{path}"
    data = json.dumps(payload).encode() if payload else None
    method = "POST" if data else "GET"
    req = urllib.request.Request(url, data=data, method=method,
          headers={"x-api-key": API_KEY, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return json.loads(e.read())

# Step 1: validate SQL
print("=== VALIDATING SQL ===")
r = api("/api/dataset", {"database": 8, "type": "native", "native": {"query": SQL_VALIDATE}})
if r.get("error"):
    print("SQL ERROR:", r["error"])
    sys.exit(1)

cols = [c["name"] for c in r["data"]["cols"]]
rows = r["data"]["rows"]
print("OK — cols:", cols)
print(f"rows: {len(rows)}")
for row in rows[:5]:
    print(dict(zip(cols, row)))
