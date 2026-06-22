"""
Crea índices MongoDB y captura evidencia .explain() antes/después
Guía 5 Ecommify — ejecutar después de load_mongo.py
"""

from pymongo import MongoClient, ASCENDING, DESCENDING, TEXT
import json, os

MONGODB_URI = "mongodb+srv://yuliet719_db_user:9MBGZtXoskGBZ1RJ@ecommifycluster.dbpeigv.mongodb.net/?appName=EcommifyCluster"
DB_NAME     = "ecommify_analytics"
OUTPUT_DIR  = r"C:\Users\Yuliet Rojas\OneDrive - Universidad de la Sabana\Diseño y Optimización BD\Unidad 5\evidencia_mongodb"

os.makedirs(OUTPUT_DIR, exist_ok=True)

def guardar(nombre, data):
    path = os.path.join(OUTPUT_DIR, nombre)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, default=str)
    print(f"  Guardado: {path}")

def resumen_explain(plan, etiqueta):
    stats = plan.get("executionStats", {})
    print(f"\n  [{etiqueta}]")
    print(f"    Stage raíz   : {plan.get('queryPlanner',{}).get('winningPlan',{}).get('stage','?')}")
    print(f"    Tiempo (ms)  : {stats.get('executionTimeMillis','?')}")
    print(f"    Docs examinados: {stats.get('totalDocsExamined','?')}")
    print(f"    Docs devueltos : {stats.get('totalDocsReturned','?')}")

def main():
    print("Conectando a MongoDB Atlas...")
    client = MongoClient(MONGODB_URI)
    db = client[DB_NAME]
    client.admin.command("ping")
    print("Conexión exitosa\n")

    col = db["catalogo_enriquecido"]
    rev = db["resumen_reviews"]

    # ── VERIFICAR DATOS ──────────────────────────────────────────
    n_prod = col.count_documents({})
    n_rev  = rev.count_documents({})
    print(f"Documentos en catalogo_enriquecido : {n_prod}")
    print(f"Documentos en resumen_reviews      : {n_rev}")
    if n_prod == 0:
        print("ERROR: No hay documentos. Ejecuta load_mongo.py primero.")
        return

    # ── EXPLAIN ANTES ────────────────────────────────────────────
    print("\n=== EXPLAIN ANTES (sin índices) ===")
    query_test = {
        "category_translations.en": "computers_accessories",
        "computed_metrics.average_rating": {"$gte": 4.0}
    }
    plan_antes = col.find(query_test).sort("computed_metrics.avg_price", 1).explain()
    resumen_explain(plan_antes, "ANTES")
    guardar("explain_antes.json", plan_antes)

    # ── CREAR ÍNDICES ────────────────────────────────────────────
    print("\n=== CREANDO ÍNDICES ===")

    # 1. Compuesto ESR: Equality → Sort → Range
    col.create_index(
        [("category_translations.en", ASCENDING),
         ("computed_metrics.avg_price", ASCENDING),
         ("computed_metrics.average_rating", ASCENDING)],
        name="idx_category_price_rating_ESR"
    )
    print("  ✅ idx_category_price_rating_ESR")

    # 2. Parcial: solo productos con rating alto (evita indexar el 75% restante)
    col.create_index(
        [("computed_metrics.average_rating", DESCENDING)],
        partialFilterExpression={"computed_metrics.average_rating": {"$gte": 4.0}},
        name="idx_high_rating_partial"
    )
    print("  ✅ idx_high_rating_partial")

    # 3. Texto: búsqueda full-text en categorías
    col.create_index(
        [("category_translations.pt", TEXT),
         ("category_translations.en", TEXT)],
        name="idx_category_text",
        default_language="portuguese",
        weights={"category_translations.pt": 2, "category_translations.en": 1}
    )
    print("  ✅ idx_category_text")

    # 4. Compuesto en specifications (Attribute Pattern)
    col.create_index(
        [("specifications.k", ASCENDING),
         ("specifications.v", ASCENDING)],
        name="idx_specifications_kv_ESR"
    )
    print("  ✅ idx_specifications_kv_ESR")

    # 5. Compuesto ESR en resumen_reviews
    rev.create_index(
        [("product_id", ASCENDING),
         ("bucket_index", ASCENDING),
         ("count", ASCENDING)],
        name="idx_reviews_product_bucket_ESR"
    )
    print("  ✅ idx_reviews_product_bucket_ESR")

    # 6. Parcial: solo buckets con reviews negativas
    rev.create_index(
        [("product_id", ASCENDING),
         ("avg_score", ASCENDING)],
        partialFilterExpression={"avg_score": {"$lte": 2.5}},
        name="idx_negative_reviews_partial"
    )
    print("  ✅ idx_negative_reviews_partial")

    # ── EXPLAIN DESPUÉS ──────────────────────────────────────────
    print("\n=== EXPLAIN DESPUÉS (con índices) ===")
    plan_despues = col.find(query_test).sort("computed_metrics.avg_price", 1).explain()
    resumen_explain(plan_despues, "DESPUÉS")
    guardar("explain_despues.json", plan_despues)

    # ── TABLA COMPARATIVA ────────────────────────────────────────
    stats_a = plan_antes.get("executionStats", {})
    stats_d = plan_despues.get("executionStats", {})
    print("\n=== TABLA COMPARATIVA (para el documento técnico) ===")
    print(f"{'Métrica':<25} {'ANTES':>12} {'DESPUÉS':>12} {'Mejora':>10}")
    print("-" * 62)

    tiempo_a = stats_a.get("executionTimeMillis", 0) or 1
    tiempo_d = stats_d.get("executionTimeMillis", 0) or 1
    docs_a   = stats_a.get("totalDocsExamined", 0)
    docs_d   = stats_d.get("totalDocsExamined", 0)

    print(f"{'Tiempo (ms)':<25} {tiempo_a:>12} {tiempo_d:>12} {f'{round((1 - tiempo_d/tiempo_a)*100)}% menos':>10}")
    print(f"{'Docs examinados':<25} {docs_a:>12} {docs_d:>12} {f'{docs_a - docs_d} menos':>10}")

    # ── LISTAR TODOS LOS ÍNDICES ─────────────────────────────────
    print("\n=== ÍNDICES ACTIVOS EN catalogo_enriquecido ===")
    for idx in col.list_indexes():
        print(f"  {idx['name']}: {dict(idx['key'])}")

    print("\n=== ÍNDICES ACTIVOS EN resumen_reviews ===")
    for idx in rev.list_indexes():
        print(f"  {idx['name']}: {dict(idx['key'])}")

    print(f"\n✅ Evidencia guardada en: {OUTPUT_DIR}")
    client.close()

if __name__ == "__main__":
    main()
