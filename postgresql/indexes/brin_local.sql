-- ============================================================
-- ÍNDICE BRIN — Optimización PostgreSQL (Guía 5)
-- Proyecto: Ecommify | Dataset: Olist Brazilian E-Commerce
-- ============================================================
-- BRIN (Block Range INdex) es ideal para columnas con alta correlación
-- física (series de tiempo, IDs secuenciales). Las órdenes se insertan
-- cronológicamente, por lo que order_purchase_timestamp tiene correlación
-- física casi perfecta con el almacenamiento en disco.
-- Ventaja: ~1000x más pequeño que B-tree para la misma columna.
-- ============================================================

-- ================================================================
-- PASO 1: Capturar plan ANTES de crear el índice BRIN
-- Exportar resultado a CSV: "Consulta BRIN antes.csv"
-- ================================================================
EXPLAIN (ANALYZE, BUFFERS)
SELECT order_id, order_status, order_purchase_timestamp
FROM olist_orders_dataset
WHERE order_purchase_timestamp BETWEEN '2017-01-01' AND '2017-06-30';

-- ================================================================
-- PASO 2: Crear el índice BRIN
-- ================================================================

CREATE INDEX IF NOT EXISTS idx_orders_purchase_ts_brin
ON olist_orders_dataset
USING BRIN (order_purchase_timestamp)
WITH (pages_per_range = 128);

-- También en la tabla particionada padre (se propaga a todas las particiones)
CREATE INDEX IF NOT EXISTS idx_part_purchase_ts_brin
ON olist_orders_partitioned
USING BRIN (order_purchase_timestamp)
WITH (pages_per_range = 128);

-- ================================================================
-- PASO 3: Capturar plan DESPUÉS de crear el índice BRIN
-- Exportar resultado a CSV: "Consulta BRIN despues.csv"
-- ================================================================
EXPLAIN (ANALYZE, BUFFERS)
SELECT order_id, order_status, order_purchase_timestamp
FROM olist_orders_dataset
WHERE order_purchase_timestamp BETWEEN '2017-01-01' AND '2017-06-30';

-- ================================================================
-- PASO 4: Consulta con particionamiento — verificar partition pruning
-- ================================================================
EXPLAIN (ANALYZE, BUFFERS)
SELECT
    op.order_status,
    COUNT(*) AS total_orders,
    ROUND(
        AVG(
            EXTRACT(EPOCH FROM (op.order_delivered_customer_date - op.order_purchase_timestamp)) / 86400
        )::numeric, 2
    ) AS avg_delivery_days
FROM olist_orders_partitioned op
WHERE op.order_purchase_timestamp BETWEEN '2017-01-01' AND '2017-06-30'
  AND op.order_status = 'delivered'
GROUP BY op.order_status;

-- ================================================================
-- PASO 5: Comparar tamaños de índices (B-tree vs BRIN)
-- ================================================================
SELECT
    indexname,
    pg_size_pretty(pg_relation_size(indexname::regclass)) AS index_size,
    LEFT(indexdef, 80) AS index_type
FROM pg_indexes
WHERE tablename = 'olist_orders_dataset'
ORDER BY pg_relation_size(indexname::regclass) DESC;
