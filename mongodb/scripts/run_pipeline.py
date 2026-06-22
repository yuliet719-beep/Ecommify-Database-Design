"""
Pipeline de Agregación MongoDB — Guía 5 Ecommify
6 stages: $match → $project → $group → $addFields → $sort → $facet
Captura executionTimeMillis antes y después para evidencia
"""

from pymongo import MongoClient
import json, os, time

MONGODB_URI = "mongodb+srv://yuliet719_db_user:9MBGZtXoskGBZ1RJ@ecommifycluster.dbpeigv.mongodb.net/?appName=EcommifyCluster"
DB_NAME     = "ecommify_analytics"
OUTPUT_DIR  = r"C:\Users\Yuliet Rojas\OneDrive - Universidad de la Sabana\Diseño y Optimización BD\Unidad 5\evidencia_mongodb"

os.makedirs(OUTPUT_DIR, exist_ok=True)

def guardar(nombre, data):
    path = os.path.join(OUTPUT_DIR, nombre)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, default=str)
    print(f"  Guardado: {path}")

# Pipeline de 6 stages para medir con explain
PIPELINE_EXPLAIN = [
    # Stage 1: $match — filtrado temprano, usa idx_category_price_rating_ESR
    {"$match": {
        "computed_metrics.total_units_sold": {"$gt": 0},
        "category_translations.en": {"$nin": [None, "", "nan"]}
    }},
    # Stage 2: $project — proyección temprana reduce payload antes del $group
    {"$project": {
        "_id": 0,
        "product_id": 1,
        "category_en": "$category_translations.en",
        "units_sold":  "$computed_metrics.total_units_sold",
        "avg_rating":  "$computed_metrics.average_rating",
        "avg_price":   "$computed_metrics.avg_price"
    }},
    # Stage 3: $group — agrupación por categoría
    {"$group": {
        "_id":                 "$category_en",
        "total_products":      {"$sum": 1},
        "total_units":         {"$sum": "$units_sold"},
        "avg_category_price":  {"$avg": "$avg_price"},
        "avg_category_rating": {"$avg": "$avg_rating"}
    }},
    # Stage 4: $addFields — score compuesto de rendimiento
    {"$addFields": {
        "performance_score": {
            "$round": [
                {"$add": [
                    {"$multiply": ["$avg_category_rating", 20]},
                    {"$divide": ["$total_units", 10]}
                ]},
                2
            ]
        }
    }},
    # Stage 5: $sort
    {"$sort": {"performance_score": -1, "total_units": -1}},
    # Stage 6: $facet — análisis multidimensional en una sola pasada
    {"$facet": {
        "top_10_categorias": [
            {"$limit": 10},
            {"$project": {
                "_id": 0,
                "categoria":   "$_id",
                "productos":   "$total_products",
                "unidades":    "$total_units",
                "precio_prom": {"$round": ["$avg_category_price", 2]},
                "rating_prom": {"$round": ["$avg_category_rating", 2]},
                "score":       "$performance_score"
            }}
        ],
        "estadisticas_globales": [
            {"$group": {
                "_id":               None,
                "total_categorias":  {"$sum": 1},
                "precio_global_avg": {"$avg": "$avg_category_price"},
                "rating_global_avg": {"$avg": "$avg_category_rating"},
                "total_unidades":    {"$sum": "$total_units"}
            }},
            {"$project": {"_id": 0}}
        ],
        "categorias_bajo_rendimiento": [
            {"$match": {"performance_score": {"$lt": 80}}},
            {"$limit": 5},
            {"$project": {"_id": 0, "categoria": "$_id", "score": "$performance_score", "rating_prom": {"$round": ["$avg_category_rating", 2]}}}
        ]
    }}
]

def main():
    print("Conectando a MongoDB Atlas...")
    client = MongoClient(MONGODB_URI)
    db = client[DB_NAME]
    client.admin.command("ping")
    print("Conexión exitosa\n")

    col = db["catalogo_enriquecido"]

    # ── EXPLAIN del pipeline (sin $facet para compatibilidad) ────
    pipeline_sin_facet = PIPELINE_EXPLAIN[:5]  # stages 1-5

    print("=== EXPLAIN DEL PIPELINE (stages 1-5) ===")
    plan_dict = db.command("aggregate", "catalogo_enriquecido",
                           pipeline=pipeline_sin_facet,
                           explain=True)
    guardar("explain_pipeline.json", plan_dict)

    stages_info = plan_dict.get("stages", [])
    print(f"  Stages en el plan: {len(stages_info)}")
    for i, s in enumerate(stages_info):
        nombre = list(s.keys())[0] if s else f"stage_{i}"
        print(f"    Stage {i+1}: {nombre}")

    # ── EJECUTAR PIPELINE COMPLETO ───────────────────────────────
    print("\n=== EJECUTANDO PIPELINE COMPLETO (6 stages) ===")
    inicio = time.time()
    resultado = list(col.aggregate(PIPELINE_EXPLAIN, allowDiskUse=True))
    tiempo_ms = round((time.time() - inicio) * 1000, 2)

    print(f"  Tiempo de ejecución: {tiempo_ms} ms")

    if resultado:
        data = resultado[0]
        guardar("pipeline_resultado.json", data)

        print("\n── TOP 10 CATEGORÍAS POR PERFORMANCE SCORE ──")
        print(f"{'#':<3} {'Categoría':<35} {'Unidades':>9} {'Precio':>8} {'Rating':>7} {'Score':>7}")
        print("-" * 72)
        for i, cat in enumerate(data.get("top_10_categorias", []), 1):
            print(f"{i:<3} {str(cat.get('categoria','')):<35} "
                  f"{cat.get('unidades',0):>9} "
                  f"{cat.get('precio_prom',0):>8.2f} "
                  f"{cat.get('rating_prom',0):>7.2f} "
                  f"{cat.get('score',0):>7.2f}")

        globales = data.get("estadisticas_globales", [{}])
        if globales:
            g = globales[0]
            print(f"\n── ESTADÍSTICAS GLOBALES ──")
            print(f"  Total categorías : {g.get('total_categorias', 0)}")
            print(f"  Total unidades   : {g.get('total_unidades', 0):,}")
            print(f"  Precio prom. global : ${g.get('precio_global_avg', 0):.2f}")
            print(f"  Rating prom. global : {g.get('rating_global_avg', 0):.2f}")

    print(f"\n✅ Resultados guardados en: {OUTPUT_DIR}")
    client.close()

if __name__ == "__main__":
    main()
