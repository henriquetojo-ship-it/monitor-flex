import os, sys, urllib.request, json, uuid
from pathlib import Path
from dotenv import dotenv_values

MB_URL = "https://metabase.tools.mecanizou.com"

_ENV = dotenv_values(Path(__file__).resolve().parent.parent / ".env.local")
API_KEY = os.environ.get("METABASE_API_KEY") or _ENV.get("METABASE_API_KEY")
if not API_KEY:
    print("[ERRO] METABASE_API_KEY ausente. Defina em .env.local ou como variável de ambiente.", file=sys.stderr)
    sys.exit(1)
DB_ID = 8
COLLECTION_ID = 1099  # cotacoes
DIM_DATE_FIELD_ID = 65662

def api(path, payload=None, method=None):
    url = f"{MB_URL}{path}"
    data = json.dumps(payload).encode() if payload is not None else None
    if method is None:
        method = "POST" if data else "GET"
    req = urllib.request.Request(url, data=data, method=method,
          headers={"x-api-key": API_KEY, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read()
        try:
            return json.loads(body)
        except Exception:
            return {"error": str(e), "body": body.decode()}

# ── base SQL CTEs (shared) ──────────────────────────────────────────────────

SQL_BASE_CTES = """WITH base_date AS (
    SELECT date_day
    FROM public_dimensions.dim_date
    WHERE {{base_date}}
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
        CASE
            WHEN COALESCE(vehicle_manufacturer, '') = '' THEN 'Sem montadora'
            ELSE vehicle_manufacturer
        END AS montadora,
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
            WHEN vehicle_year >= 2025 THEN 1
            WHEN vehicle_year >= 2020 THEN 2
            WHEN vehicle_year >= 2015 THEN 3
            WHEN vehicle_year >= 2010 THEN 4
            WHEN vehicle_year >= 2005 THEN 5
            WHEN vehicle_year >= 2000 THEN 6
            WHEN vehicle_year IS NOT NULL THEN 7
            ELSE 8
        END AS ordem_ano,
        CASE
            WHEN total_pecas <= 2  THEN 'De 1 a 2 peças'
            WHEN total_pecas <= 5  THEN 'De 2 a 5 peças'
            WHEN total_pecas <= 10 THEN 'De 5 a 10 peças'
            ELSE 'Acima de 10 peças'
        END AS faixa_pecas,
        CASE
            WHEN total_pecas <= 2  THEN 1
            WHEN total_pecas <= 5  THEN 2
            WHEN total_pecas <= 10 THEN 3
            ELSE 4
        END AS ordem_pecas,
        tmo_minutes
    FROM quote_metrics
),

agg AS (
    SELECT
        montadora,
        faixa_ano,
        ordem_ano,
        faixa_pecas,
        ordem_pecas,
        PERCENTILE_CONT(0.5)  WITHIN GROUP (ORDER BY tmo_minutes) AS mediana_tmo,
        PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY tmo_minutes) AS p75_tmo,
        COUNT(*) AS qtd_cotacoes
    FROM with_ranges
    GROUP BY montadora, faixa_ano, ordem_ano, faixa_pecas, ordem_pecas
)"""

SQL_Q1 = SQL_BASE_CTES + """

SELECT
    montadora,
    faixa_ano,
    faixa_pecas,
    ROUND(mediana_tmo::numeric, 1) AS mediana_tmo,
    qtd_cotacoes
FROM agg
ORDER BY montadora, ordem_ano, ordem_pecas"""

SQL_Q2 = SQL_BASE_CTES + """

SELECT
    montadora,
    faixa_ano,
    faixa_pecas,
    ROUND(p75_tmo::numeric, 1) AS p75_tmo,
    qtd_cotacoes
FROM agg
ORDER BY montadora, ordem_ano, ordem_pecas"""

SQL_Q3 = SQL_BASE_CTES + """

SELECT
    montadora,
    faixa_ano,
    faixa_pecas,
    ROUND(mediana_tmo::numeric, 1)                             AS mediana_tmo,
    ROUND(p75_tmo::numeric, 1)                                 AS p75_tmo,
    ROUND((p75_tmo - mediana_tmo)::numeric, 1)                 AS diferenca_min,
    ROUND((p75_tmo / NULLIF(mediana_tmo, 0))::numeric, 2)      AS ratio_p75_mediana,
    qtd_cotacoes
FROM agg
ORDER BY montadora, ordem_ano, ordem_pecas"""

# ── helpers ─────────────────────────────────────────────────────────────────

def make_template_tags(tag_id):
    return {
        "base_date": {
            "id": tag_id,
            "name": "base_date",
            "display-name": "Período",
            "type": "dimension",
            "dimension": ["field", DIM_DATE_FIELD_ID, None],
            "widget-type": "date/all-options",
            "default": "past3months",
            "required": False
        }
    }

def make_parameters(tag_id):
    return [
        {
            "id": tag_id,
            "slug": "base_date",
            "name": "Período",
            "type": "date/all-options",
            "target": ["dimension", ["template-tag", "base_date"]],
            "default": "past3months"
        }
    ]

def pivot_viz(value_col, value_type="type/Float"):
    return {
        "pivot_table.column_split": {
            "rows": [
                ["field", "montadora",   {"base-type": "type/Text"}],
                ["field", "faixa_pecas", {"base-type": "type/Text"}]
            ],
            "columns": [
                ["field", "faixa_ano",   {"base-type": "type/Text"}]
            ],
            "values": [
                ["field", value_col, {"base-type": value_type}]
            ]
        },
        "pivot_table.collapsed_rows": {"value": [], "rows": []},
        "column_settings": {}
    }

def table_viz_q3():
    return {
        "column_settings": {
            '["name","mediana_tmo"]':         {"column_title": "Mediana TMO (min)"},
            '["name","p75_tmo"]':             {"column_title": "P75 TMO (min)"},
            '["name","diferenca_min"]':       {"column_title": "Diferença P75−Mediana (min)"},
            '["name","ratio_p75_mediana"]':   {"column_title": "Razão P75÷Mediana"},
            '["name","qtd_cotacoes"]':        {"column_title": "Qtd Cotações"},
            '["name","montadora"]':           {"column_title": "Montadora"},
            '["name","faixa_ano"]':           {"column_title": "Faixa Ano"},
            '["name","faixa_pecas"]':         {"column_title": "Faixa Peças"}
        }
    }

def create_card(name, sql, display, viz_settings, tag_id):
    payload = {
        "name": name,
        "display": display,
        "collection_id": COLLECTION_ID,
        "database_id": DB_ID,
        "dataset_query": {
            "type": "native",
            "database": DB_ID,
            "native": {
                "query": sql,
                "template-tags": make_template_tags(tag_id)
            }
        },
        "parameters": make_parameters(tag_id),
        "visualization_settings": viz_settings
    }
    r = api("/api/card", payload)
    if r.get("id"):
        print(f"  ✓ Criado: {name} (ID {r['id']})")
        return r["id"]
    else:
        print(f"  ✗ Erro ao criar '{name}': {r.get('message') or r.get('error') or r}")
        return None

# ── validate then create ─────────────────────────────────────────────────────

print("=== Validando SQL (Q3 base — tudo incluído) ===")
r = api("/api/dataset", {
    "database": DB_ID,
    "type": "native",
    "native": {"query": SQL_Q3.replace("{{base_date}}", "date_day >= DATEADD(month, -1, CURRENT_DATE)")}
})
if r.get("error"):
    print("ERRO SQL:", r["error"])
    sys.exit(1)
cols = [c["name"] for c in r["data"]["cols"]]
print("Colunas:", cols)
print("Linhas:", len(r["data"]["rows"]))
for row in r["data"]["rows"][:3]:
    print(" ", dict(zip(cols, row)))

print()
print("=== Criando questions ===")

id_q1 = create_card(
    "[Cotações] TMO — Mediana por Montadora x Ano x Peças",
    SQL_Q1, "pivot",
    pivot_viz("mediana_tmo"),
    str(uuid.uuid4())
)

id_q2 = create_card(
    "[Cotações] TMO — P75 por Montadora x Ano x Peças",
    SQL_Q2, "pivot",
    pivot_viz("p75_tmo"),
    str(uuid.uuid4())
)

id_q3 = create_card(
    "[Cotações] TMO — Comparativo Mediana vs P75",
    SQL_Q3, "table",
    table_viz_q3(),
    str(uuid.uuid4())
)

print()
print("=== Validando questions criadas ===")
for qid, name in [(id_q1, "Q1"), (id_q2, "Q2"), (id_q3, "Q3")]:
    if qid is None:
        print(f"  {name}: pulado (não criado)")
        continue
    r = api(f"/api/card/{qid}/query", {"parameters": []}, method="POST")
    if r.get("error"):
        print(f"  {name} (ID {qid}) ERRO: {r['error']}")
    else:
        rows = len(r.get("data", {}).get("rows", []))
        print(f"  {name} (ID {qid}) OK — {rows} linhas")

print()
print("IDs:")
print(f"  Q1: {id_q1}")
print(f"  Q2: {id_q2}")
print(f"  Q3: {id_q3}")
