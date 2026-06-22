"""
Pruebas de carga y escalabilidad — Guía 6 Ecommify
Simula usuarios concurrentes en PostgreSQL (Supabase) y MongoDB Atlas
Métricas: throughput, latencia promedio/P95/P99, tasa de error

Requisitos:
    pip install pymongo psycopg2-binary pandas python-dotenv
"""

import time
import statistics
import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

from pymongo import MongoClient
import psycopg2
import psycopg2.pool

# ================================================================
# CONFIGURACIÓN
# ================================================================
MONGODB_URI   = "mongodb+srv://yuliet719_db_user:9MBGZtXoskGBZ1RJ@ecommifycluster.dbpeigv.mongodb.net/?appName=EcommifyCluster"
SUPABASE_URL  = "postgresql://postgres:5pThc9CWW1rD0kID@db.aklzgzygjfxznpkkytae.supabase.co:5432/postgres"
OUTPUT_DIR    = r"C:\Users\Yuliet Rojas\OneDrive - Universidad de la Sabana\Diseño y Optimización BD\Unidad 5\evidencia_guia6"

os.makedirs(OUTPUT_DIR, exist_ok=True)

# Niveles de concurrencia a probar
CONCURRENCY_LEVELS = [1, 5, 10, 20]
QUERIES_PER_USER   = 10  # queries por usuario por prueba


# ================================================================
# QUERIES DE PRUEBA
# ================================================================

# PostgreSQL — queries representativas del módulo transaccional
PG_QUERIES = [
    ("simple_orders", """
        SELECT order_id, order_status, order_purchase_timestamp
        FROM orders
        WHERE order_status = 'delivered'
        LIMIT 100
    """),
    ("orders_by_period", """
        SELECT order_status, COUNT(*) as total
        FROM orders
        WHERE order_purchase_timestamp BETWEEN '2017-01-01' AND '2017-12-31'
        GROUP BY order_status
    """),
    ("revenue_by_category", """
        SELECT p.product_category_name,
               COUNT(*) as orders,
               ROUND(SUM(oi.price)::numeric, 2) as revenue
        FROM order_items oi
        JOIN products p ON oi.product_id = p.product_id
        GROUP BY p.product_category_name
        ORDER BY revenue DESC
        LIMIT 10
    """),
]

# MongoDB — queries representativas del módulo analítico
MONGO_QUERIES = [
    ("find_by_category", lambda col: list(
        col.find({"category_translations.en": "health_beauty"},
                 {"product_id": 1, "computed_metrics": 1}).limit(50)
    )),
    ("top_rated_products", lambda col: list(
        col.find({"computed_metrics.average_rating": {"$gte": 4.5}})
           .sort("computed_metrics.total_units_sold", -1)
           .limit(20)
    )),
    ("aggregation_pipeline", lambda col: list(
        col.aggregate([
            {"$match": {"computed_metrics.total_units_sold": {"$gt": 0}}},
            {"$group": {"_id": "$category_translations.en",
                        "total": {"$sum": "$computed_metrics.total_units_sold"},
                        "avg_rating": {"$avg": "$computed_metrics.average_rating"}}},
            {"$sort": {"total": -1}},
            {"$limit": 10}
        ])
    )),
]


# ================================================================
# FUNCIONES DE PRUEBA
# ================================================================

def run_pg_query(pool, query_name, query_sql):
    """Ejecuta una query PostgreSQL y retorna latencia en ms."""
    start = time.perf_counter()
    try:
        conn = pool.getconn()
        cur = conn.cursor()
        cur.execute(query_sql)
        cur.fetchall()
        cur.close()
        pool.putconn(conn)
        elapsed = (time.perf_counter() - start) * 1000
        return {"query": query_name, "latency_ms": round(elapsed, 2), "error": None}
    except Exception as e:
        elapsed = (time.perf_counter() - start) * 1000
        return {"query": query_name, "latency_ms": round(elapsed, 2), "error": str(e)}


def run_mongo_query(collection, query_name, query_fn):
    """Ejecuta una query MongoDB y retorna latencia en ms."""
    start = time.perf_counter()
    try:
        query_fn(collection)
        elapsed = (time.perf_counter() - start) * 1000
        return {"query": query_name, "latency_ms": round(elapsed, 2), "error": None}
    except Exception as e:
        elapsed = (time.perf_counter() - start) * 1000
        return {"query": query_name, "latency_ms": round(elapsed, 2), "error": str(e)}


def calcular_metricas(resultados, nombre, concurrencia, duracion_total):
    """Calcula throughput, latencias y tasa de error."""
    latencias = [r["latency_ms"] for r in resultados if r["error"] is None]
    errores   = [r for r in resultados if r["error"] is not None]

    if not latencias:
        return {"sistema": nombre, "concurrencia": concurrencia, "error": "todas las queries fallaron"}

    latencias.sort()
    n = len(latencias)
    p95_idx = int(n * 0.95)
    p99_idx = int(n * 0.99)

    return {
        "sistema":          nombre,
        "concurrencia":     concurrencia,
        "total_queries":    len(resultados),
        "queries_ok":       len(latencias),
        "queries_error":    len(errores),
        "tasa_error_pct":   round(len(errores) / len(resultados) * 100, 2),
        "throughput_qps":   round(len(resultados) / duracion_total, 2),
        "latencia_avg_ms":  round(statistics.mean(latencias), 2),
        "latencia_min_ms":  round(min(latencias), 2),
        "latencia_max_ms":  round(max(latencias), 2),
        "latencia_p50_ms":  round(latencias[n // 2], 2),
        "latencia_p95_ms":  round(latencias[min(p95_idx, n-1)], 2),
        "latencia_p99_ms":  round(latencias[min(p99_idx, n-1)], 2),
    }


# ================================================================
# PRUEBAS DE CARGA
# ================================================================

def test_postgresql(concurrencia):
    """Prueba de carga PostgreSQL con N usuarios concurrentes."""
    print(f"\n  [PostgreSQL] Concurrencia: {concurrencia} usuarios...")
    try:
        pool = psycopg2.pool.ThreadedConnectionPool(1, concurrencia + 2, SUPABASE_URL)
    except Exception as e:
        print(f"    ❌ No se pudo conectar a Supabase: {e}")
        return None

    tareas = []
    for _ in range(concurrencia):
        for query_name, query_sql in PG_QUERIES:
            for _ in range(QUERIES_PER_USER):
                tareas.append((query_name, query_sql))

    resultados = []
    inicio = time.perf_counter()
    with ThreadPoolExecutor(max_workers=concurrencia) as executor:
        futures = [executor.submit(run_pg_query, pool, qn, qs) for qn, qs in tareas]
        for f in as_completed(futures):
            resultados.append(f.result())
    duracion = time.perf_counter() - inicio

    pool.closeall()
    metricas = calcular_metricas(resultados, "PostgreSQL", concurrencia, duracion)
    print(f"    throughput: {metricas.get('throughput_qps')} qps | "
          f"avg: {metricas.get('latencia_avg_ms')} ms | "
          f"p95: {metricas.get('latencia_p95_ms')} ms | "
          f"errores: {metricas.get('tasa_error_pct')}%")
    return metricas


def test_mongodb(concurrencia):
    """Prueba de carga MongoDB con N usuarios concurrentes."""
    print(f"\n  [MongoDB] Concurrencia: {concurrencia} usuarios...")
    try:
        client = MongoClient(MONGODB_URI, maxPoolSize=concurrencia + 5)
        col = client["ecommify_analytics"]["catalogo_enriquecido"]
    except Exception as e:
        print(f"    ❌ No se pudo conectar a MongoDB: {e}")
        return None

    tareas = []
    for _ in range(concurrencia):
        for query_name, query_fn in MONGO_QUERIES:
            for _ in range(QUERIES_PER_USER):
                tareas.append((query_name, query_fn))

    resultados = []
    inicio = time.perf_counter()
    with ThreadPoolExecutor(max_workers=concurrencia) as executor:
        futures = [executor.submit(run_mongo_query, col, qn, qf) for qn, qf in tareas]
        for f in as_completed(futures):
            resultados.append(f.result())
    duracion = time.perf_counter() - inicio

    client.close()
    metricas = calcular_metricas(resultados, "MongoDB", concurrencia, duracion)
    print(f"    throughput: {metricas.get('throughput_qps')} qps | "
          f"avg: {metricas.get('latencia_avg_ms')} ms | "
          f"p95: {metricas.get('latencia_p95_ms')} ms | "
          f"errores: {metricas.get('tasa_error_pct')}%")
    return metricas


# ================================================================
# MAIN
# ================================================================

def main():
    print("=" * 60)
    print("PRUEBAS DE CARGA — Ecommify Guía 6")
    print(f"Niveles de concurrencia: {CONCURRENCY_LEVELS}")
    print(f"Queries por usuario: {QUERIES_PER_USER}")
    print("=" * 60)

    todos_los_resultados = []

    for concurrencia in CONCURRENCY_LEVELS:
        print(f"\n{'='*40}")
        print(f"NIVEL DE CONCURRENCIA: {concurrencia} usuarios")
        print(f"{'='*40}")

        res_pg = test_postgresql(concurrencia)
        if res_pg:
            todos_los_resultados.append(res_pg)

        res_mongo = test_mongodb(concurrencia)
        if res_mongo:
            todos_los_resultados.append(res_mongo)

    # Guardar resultados
    output_path = os.path.join(OUTPUT_DIR, "load_test_results.json")
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(todos_los_resultados, f, indent=2)

    # Imprimir tabla resumen
    print("\n" + "=" * 80)
    print("TABLA RESUMEN — RESULTADOS DE CARGA")
    print("=" * 80)
    print(f"{'Sistema':<12} {'Usuarios':>8} {'QPS':>8} {'Avg ms':>8} {'P95 ms':>8} {'P99 ms':>8} {'Errores':>8}")
    print("-" * 80)
    for r in todos_los_resultados:
        if "error" not in r:
            print(f"{r['sistema']:<12} {r['concurrencia']:>8} "
                  f"{r['throughput_qps']:>8} {r['latencia_avg_ms']:>8} "
                  f"{r['latencia_p95_ms']:>8} {r['latencia_p99_ms']:>8} "
                  f"{r['tasa_error_pct']:>7}%")

    print(f"\n✅ Resultados guardados en: {output_path}")


if __name__ == "__main__":
    main()
