-- ============================================================
-- ÍNDICE BRIN — Supabase Ecommify Project (Guía 5)
-- Tabla: orders | Columna: order_purchase_timestamp
-- ============================================================

-- ── PASO 1: EXPLAIN ANTES (ejecutar primero, tomar screenshot) ──
EXPLAIN (ANALYZE, BUFFERS)
SELECT order_id, order_status, order_purchase_timestamp
FROM orders
WHERE order_purchase_timestamp BETWEEN '2017-01-01' AND '2017-06-30';

-- ── PASO 2: Crear índice BRIN ────────────────────────────────
-- BRIN es ~1000x más pequeño que B-tree para series de tiempo.
-- Las órdenes se insertan cronológicamente → correlación física perfecta.
CREATE INDEX IF NOT EXISTS idx_orders_purchase_brin
ON orders
USING BRIN (order_purchase_timestamp)
WITH (pages_per_range = 128);

-- ── PASO 3: EXPLAIN DESPUÉS (tomar screenshot para comparar) ─
EXPLAIN (ANALYZE, BUFFERS)
SELECT order_id, order_status, order_purchase_timestamp
FROM orders
WHERE order_purchase_timestamp BETWEEN '2017-01-01' AND '2017-06-30';

-- ── PASO 4: Consulta más compleja para evidencia de mejora ───
EXPLAIN (ANALYZE, BUFFERS)
SELECT
    order_status,
    COUNT(*)                                                   AS total_pedidos,
    ROUND(AVG(EXTRACT(EPOCH FROM (
        order_delivered_customer_date - order_purchase_timestamp
    )) / 86400)::numeric, 2)                                   AS dias_entrega_prom
FROM orders
WHERE order_purchase_timestamp BETWEEN '2017-01-01' AND '2017-06-30'
  AND order_status = 'delivered'
GROUP BY order_status;

-- ── PASO 5: Comparar tamaños de índices ─────────────────────
SELECT
    indexname,
    pg_size_pretty(pg_relation_size(indexname::regclass)) AS tamanio,
    LEFT(indexdef, 90)                                     AS tipo
FROM pg_indexes
WHERE tablename = 'orders'
ORDER BY pg_relation_size(indexname::regclass) DESC;
