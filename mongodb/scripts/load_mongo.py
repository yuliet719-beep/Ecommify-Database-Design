"""
Carga de datos Olist en MongoDB Atlas — Guía 5 Ecommify
Colecciones: catalogo_enriquecido, resumen_reviews
Patrones: Attribute, Computed, Bucket

Requisitos:
    pip install pymongo pandas

Uso:
    1. Reemplazar MONGODB_URI con la cadena de conexión de Atlas
    2. Asegurarse de que los CSVs de Olist estén en la carpeta olist_data/
    3. Ejecutar: python load_mongo.py
"""

from pymongo import MongoClient
import pandas as pd
import math

# ================================================================
# CONFIGURACIÓN
# ================================================================
# IMPORTANTE: reemplaza "abc12" por el ID real de tu cluster en Atlas
# Lo encuentras en: Atlas → Connect → Drivers → copia la URI completa
MONGODB_URI = "mongodb+srv://yuliet719_db_user:9MBGZtXoskGBZ1RJ@ecommifycluster.dbpeigv.mongodb.net/?appName=EcommifyCluster"
DB_NAME = "ecommify_analytics"
DATA_PATH = r"C:\Users\Yuliet Rojas\OneDrive - Universidad de la Sabana\Archivos de David Felipe Cifuentes Villa - Avance U4 - FASE 1\olist_data" + "\\"


def safe_float(val, default=0.0):
    """Convierte a float manejando NaN."""
    try:
        result = float(val)
        return default if math.isnan(result) else round(result, 2)
    except (TypeError, ValueError):
        return default


def safe_int(val, default=0):
    """Convierte a int manejando NaN."""
    try:
        result = float(val)
        return default if math.isnan(result) else int(result)
    except (TypeError, ValueError):
        return default


def load_catalogo_enriquecido(db):
    """
    Colección principal de productos con patrones:
    - Embedded: category_translations dentro del documento
    - Attribute Pattern: specifications como array [{k, v}] para búsqueda uniforme
    - Computed Pattern: métricas precalculadas en computed_metrics
    """
    print("Cargando datasets...")
    products = pd.read_csv(DATA_PATH + "olist_products_dataset.csv")
    translations = pd.read_csv(DATA_PATH + "product_category_name_translation.csv")
    items = pd.read_csv(DATA_PATH + "olist_order_items_dataset.csv")
    reviews = pd.read_csv(DATA_PATH + "olist_order_reviews_dataset.csv")

    print(f"  Productos: {len(products)} | Items: {len(items)} | Reviews: {len(reviews)}")

    # Calcular métricas por producto (Computed Pattern)
    metrics = items.groupby("product_id").agg(
        total_units_sold=("order_id", "count"),
        avg_price=("price", "mean")
    ).reset_index()

    reviews_agg = reviews.groupby("order_id").agg(
        average_rating=("review_score", "mean")
    ).reset_index()
    items_reviews = items.merge(reviews_agg, on="order_id", how="left")
    rating_by_product = (
        items_reviews.groupby("product_id")["average_rating"]
        .mean()
        .reset_index()
    )

    # Combinar todo
    df = products.merge(translations, on="product_category_name", how="left")
    df = df.merge(metrics, on="product_id", how="left")
    df = df.merge(rating_by_product, on="product_id", how="left")

    docs = []
    for _, row in df.iterrows():
        doc = {
            "product_id": str(row["product_id"]),
            # Embedded: traducciones del nombre de categoría
            "category_translations": {
                "pt": str(row.get("product_category_name", "") or ""),
                "en": str(row.get("product_category_name_english", "") or "")
            },
            "photos_qty": safe_int(row.get("product_photos_qty")),
            # Attribute Pattern: dimensiones físicas como array k/v
            # Permite queries como: db.find({"specifications.k": "weight_g", "specifications.v": {$gt: 500}})
            "specifications": [
                {"k": "weight_g",  "v": safe_float(row.get("product_weight_g"))},
                {"k": "length_cm", "v": safe_float(row.get("product_length_cm"))},
                {"k": "height_cm", "v": safe_float(row.get("product_height_cm"))},
                {"k": "width_cm",  "v": safe_float(row.get("product_width_cm"))},
            ],
            # Computed Pattern: evita recalcular en cada query analítica
            "computed_metrics": {
                "total_units_sold": safe_int(row.get("total_units_sold")),
                "average_rating":   safe_float(row.get("average_rating")),
                "avg_price":        safe_float(row.get("avg_price")),
            }
        }
        docs.append(doc)

    print(f"  Insertando {len(docs)} documentos en catalogo_enriquecido...")
    db["catalogo_enriquecido"].drop()
    result = db["catalogo_enriquecido"].insert_many(docs)
    print(f"  Insertados: {len(result.inserted_ids)} documentos")
    return len(result.inserted_ids)


def load_resumen_reviews(db):
    """
    Colección de reviews con patrón Bucket:
    - Agrupa hasta 5 reviews por documento por producto
    - Evita documentos demasiado grandes (sin límite de array)
    - Facilita paginación y análisis por lote
    """
    items = pd.read_csv(DATA_PATH + "olist_order_items_dataset.csv")
    reviews = pd.read_csv(DATA_PATH + "olist_order_reviews_dataset.csv")

    reviews_sample = reviews.dropna(subset=["review_comment_message"]).copy()
    reviews_with_product = reviews_sample.merge(
        items[["order_id", "product_id"]].drop_duplicates("order_id"),
        on="order_id",
        how="inner"
    )

    buckets = []
    BUCKET_SIZE = 5  # máximo de reviews por documento (Bucket Pattern)

    for product_id, group in reviews_with_product.groupby("product_id"):
        reviews_list = group[[
            "review_score", "review_comment_title", "review_comment_message"
        ]].to_dict("records")

        # Limpiar NaN en títulos
        for r in reviews_list:
            r["review_comment_title"] = r.get("review_comment_title") or ""
            r["review_score"] = safe_int(r.get("review_score"))

        for i in range(0, len(reviews_list), BUCKET_SIZE):
            batch = reviews_list[i:i + BUCKET_SIZE]
            avg_score = sum(r["review_score"] for r in batch) / len(batch)
            buckets.append({
                "product_id":   str(product_id),
                "bucket_index": i // BUCKET_SIZE,
                "count":        len(batch),
                "avg_score":    round(avg_score, 2),
                "reviews":      batch
            })

    print(f"  Insertando {len(buckets)} buckets en resumen_reviews...")
    db["resumen_reviews"].drop()
    result = db["resumen_reviews"].insert_many(buckets)
    print(f"  Insertados: {len(result.inserted_ids)} buckets")
    return len(result.inserted_ids)


def main():
    print("Conectando a MongoDB Atlas...")
    client = MongoClient(MONGODB_URI)
    db = client[DB_NAME]

    # Verificar conexión
    client.admin.command("ping")
    print(f"Conexión exitosa a '{DB_NAME}'")

    n_productos = load_catalogo_enriquecido(db)
    n_buckets   = load_resumen_reviews(db)

    print("\n=== CARGA COMPLETADA ===")
    print(f"  catalogo_enriquecido: {n_productos} documentos")
    print(f"  resumen_reviews:      {n_buckets} buckets")
    print(f"\nVerificar en Atlas: db.catalogo_enriquecido.countDocuments()")

    client.close()


if __name__ == "__main__":
    main()
