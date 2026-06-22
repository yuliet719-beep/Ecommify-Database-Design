# Ecommify — Documento Técnico Integral
## Etapa 2: Implementación, Optimización y Arquitectura de Bases de Datos

**Universidad de la Sabana** | Diseño y Optimización de Bases de Datos  
**Equipo:** Beycy Yuliet Rojas Acero · Manuel Fernando Santofimio Tovar · Jorge Ivan Figueroa Torres · Jose Antonio Leao Ferrer · David Felipe Cifuentes Villa  
**Fecha:** Junio 2026

---

## 1. Resumen Ejecutivo

Ecommify es una plataforma multi-vendor de comercio electrónico construida sobre una **arquitectura híbrida deliberada**: PostgreSQL/Supabase gestiona el módulo transaccional (órdenes, clientes, pagos) y MongoDB Atlas el módulo analítico (catálogo enriquecido, reviews agregadas).

**Dataset:** Brazilian E-Commerce Public Dataset (Olist) — 99,224 órdenes · 32,951 productos · 71 categorías · 2016-2018

### Métricas clave obtenidas

| Sistema | Antes de optimización | Después de optimización | Mejora |
|---|---|---|---|
| MongoDB — stage de ejecución | COLLSCAN | IXSCAN (`idx_category_price_rating_ESR`) | ✅ |
| MongoDB — tiempo de query | 24 ms | 6 ms | **75% más rápido** |
| MongoDB — documentos examinados | 32,951 | 1,102 | **−96.7%** |
| MongoDB — throughput máx. (20 usuarios) | — | 94.38 QPS, 0% errores | ✅ medido |
| PostgreSQL — índice BRIN | Sin índice (Seq Scan) | Bitmap Index Scan | **~1000× más pequeño** que B-tree |
| PostgreSQL — pipeline de agregación | 6 stages ejecutados | 71 categorías, 111,023 unidades | ✅ |

---

## 2. Arquitectura del Sistema

### 2.1 Diseño Híbrido

```
[Cliente Web / API REST]
         │
         ▼
┌────────────────────────┐        ┌──────────────────────────────┐
│   PostgreSQL/Supabase  │        │   MongoDB Atlas M0            │
│   (Módulo Transacc.)   │        │   (Módulo Analítico)          │
│                        │        │                              │
│  customers             │        │  catalogo_enriquecido        │
│  orders (particionado) │──ETL──▶│  (32,951 docs)               │
│  order_items           │        │  Embedded + Attribute +      │
│  order_payments        │        │  Computed Pattern            │
│  order_reviews         │──ETL──▶│                              │
│  products              │        │  resumen_reviews             │
│  sellers               │        │  (Bucket Pattern)            │
│  geolocation           │        │                              │
└────────────────────────┘        └──────────────────────────────┘
   ACID · CP · us-east-1              AP · Atlas M0 · us-east-1
```

**Justificación del modelo híbrido:**
- **PostgreSQL** para transacciones: ACID requerido para órdenes y pagos; integridad referencial con FK entre 10 tablas
- **MongoDB** para catálogo analítico: productos tienen dimensiones variables por categoría (electrónica → voltaje; ropa → talla; libros → ISBN) — imposible modelar eficientemente en tabla relacional sin EAV anti-pattern

### 2.2 Tecnologías

| Tecnología | Rol | Versión |
|---|---|---|
| PostgreSQL | Motor transaccional | 15 (vía Supabase) |
| Supabase | PostgreSQL managed + Auth + API | Free tier (NANO compute) |
| PostGIS | Extensión geoespacial | 3.x |
| pg_trgm | Búsqueda de texto por trigramas | Built-in |
| MongoDB Atlas | Motor analítico de documentos | 7.x (M0 free) |
| pymongo | Driver Python para MongoDB | ≥4.6 |
| psycopg2 | Driver Python para PostgreSQL | ≥2.9.9 |
| Python | Scripts de carga y pruebas | 3.11+ |

---

## 3. Implementación PostgreSQL / Supabase

### 3.1 Esquema de Base de Datos

El esquema implementado en Supabase contiene **10 tablas** con constraints completos:

```sql
-- Tablas principales
customers          -- 99,441 registros
sellers            -- 3,095 registros
products           -- 32,951 registros
orders             -- 99,224 registros (tabla particionada)
order_items        -- 112,650 registros
order_payments     -- 103,886 registros
order_reviews      -- 99,224 registros
geolocation        -- 1,000,163 registros

-- Particiones de orders (RANGE por año)
orders_2016        -- órdenes ago-dic 2016
orders_2017        -- órdenes ene-dic 2017
orders_2018        -- órdenes ene-oct 2018
orders_default     -- partición catch-all
```

**Tipos avanzados y extensiones:**
```sql
CREATE EXTENSION IF NOT EXISTS postgis;      -- datos geoespaciales
CREATE EXTENSION IF NOT EXISTS pg_trgm;      -- búsqueda fuzzy en reviews
CREATE EXTENSION IF NOT EXISTS unaccent;     -- normalización de acentos

-- Tipos PostgreSQL usados
TIMESTAMP WITH TIME ZONE   -- fechas de órdenes
NUMERIC(10,2)              -- precios y valores monetarios
TEXT[]                     -- arrays de categorías en geolocation
JSONB                      -- metadatos flexibles de productos
```

### 3.2 Particionamiento RANGE

```sql
-- Tabla padre particionada
CREATE TABLE orders (
    order_id                VARCHAR(32) PRIMARY KEY,
    customer_id             VARCHAR(32) NOT NULL,
    order_status            VARCHAR(20),
    order_purchase_timestamp TIMESTAMP,
    order_approved_at       TIMESTAMP,
    order_delivered_customer_date TIMESTAMP,
    order_estimated_delivery_date TIMESTAMP
) PARTITION BY RANGE (order_purchase_timestamp);

-- Particiones por año
CREATE TABLE orders_2016 PARTITION OF orders
    FOR VALUES FROM ('2016-01-01') TO ('2017-01-01');

CREATE TABLE orders_2017 PARTITION OF orders
    FOR VALUES FROM ('2017-01-01') TO ('2018-01-01');

CREATE TABLE orders_2018 PARTITION OF orders
    FOR VALUES FROM ('2018-01-01') TO ('2019-01-01');

CREATE TABLE orders_default PARTITION OF orders DEFAULT;
```

**Ventaja del particionamiento:** el planificador hace `Append` sobre solo la partición relevante al filtrar por fecha → evita recorrer 99,224 filas completas.

### 3.3 Índices PostgreSQL

#### B-tree (índices estándar)
```sql
-- Búsqueda frecuente por estado de orden
CREATE INDEX idx_orders_status ON orders(order_status);

-- FK lookup — join con customers
CREATE INDEX idx_orders_customer ON orders(customer_id);

-- Ordenamiento por fecha de entrega
CREATE INDEX idx_orders_delivery ON orders(order_delivered_customer_date);
```

#### GIN — Búsqueda de texto completo
```sql
-- pg_trgm: búsqueda por substring en comentarios de reviews
CREATE INDEX idx_reviews_trgm
ON order_reviews USING GIN (review_comment_message gin_trgm_ops);

-- tsvector: full-text search en español/portugués
CREATE INDEX idx_reviews_fts
ON order_reviews USING GIN (to_tsvector('portuguese', review_comment_message));
```

#### GiST — Datos geoespaciales
```sql
CREATE INDEX idx_geolocation_point
ON geolocation USING GIST (geom);
```

#### BRIN — Series de tiempo (implementado en Guía 5)
```sql
-- BRIN es ideal para order_purchase_timestamp porque:
-- 1. Las órdenes se insertan cronológicamente → correlación física perfecta
-- 2. ~1000x más pequeño que un B-tree equivalente
-- 3. Se propaga automáticamente a todas las particiones

CREATE INDEX idx_orders_purchase_brin
ON orders USING BRIN (order_purchase_timestamp)
WITH (pages_per_range = 128);
```

**Evidencia particionamiento — EXPLAIN ANALYZE real:**

| Escenario | Tabla plana (`orders`) | Tabla particionada (`orders_part`) | Mejora |
|---|---|---|---|
| Barrido rango jun-2017 | Index Only Scan — **748.817 ms** | Seq Scan 1 partición — **2.375 ms** | **×315 más rápido** |
| Particiones escaneadas | 1 (toda la tabla) | 1/27 (solo `orders_part_2017_06`) | Partition pruning activo |
| Buffers leídos | 1,574 | 158 | −90% lecturas |

*Fuente: `postgresql/optimizaciones/results/04_partitioning.txt` — equipo Ecommify*

### 3.4 Optimizaciones de Queries — Datos Reales

Los siguientes resultados fueron obtenidos con `EXPLAIN (ANALYZE, BUFFERS)` sobre la instancia real de Supabase.

#### OPT-1: Subqueries correlacionadas → JOIN + GROUP BY

```sql
-- ANTES (subquery por cada seller): 1,855 ms
SELECT s.seller_id, (SELECT COUNT(*) FROM order_items i WHERE i.seller_id = s.seller_id) ...

-- DESPUÉS (single JOIN): 39 ms
SELECT s.seller_id, COUNT(i.order_id), SUM(i.price)
FROM sellers s LEFT JOIN order_items i ON i.seller_id = s.seller_id
GROUP BY s.seller_id
```

| Métrica | ANTES | DESPUÉS | Mejora |
|---|---|---|---|
| Execution Time | **1,855.952 ms** | **39.713 ms** | **×46.7 (−97.9%)** |
| Plan | CTE Scan + SubPlan loops | GroupAggregate + Nested Loop | JOINs eliminan subplan |

#### OPT-2: Función en columna → rango sargable

```sql
-- ANTES (date_trunc bloquea el índice): 1,022 ms
WHERE date_trunc('day', order_purchase_timestamp) = '2018-05-10'

-- DESPUÉS (rango explícito, usa índice): 535 ms
WHERE order_purchase_timestamp >= '2018-05-10' AND order_purchase_timestamp < '2018-05-11'
```

| Métrica | ANTES | DESPUÉS | Mejora |
|---|---|---|---|
| Execution Time | **1,022.989 ms** (Seq Scan) | **535.048 ms** (Index Scan) | **−47.7%** |

#### OPT-3: NOT IN subquery → NOT EXISTS (anti-join)

```sql
-- ANTES (materializa 91,677 filas en memoria): 3,195 ms
WHERE order_id NOT IN (SELECT order_id FROM order_reviews)

-- DESPUÉS (Hash Right Anti Join): 35 ms
WHERE NOT EXISTS (SELECT 1 FROM order_reviews r WHERE r.order_id = o.order_id)
```

| Métrica | ANTES | DESPUÉS | Mejora |
|---|---|---|---|
| Execution Time | **3,195.639 ms** | **35.422 ms** | **×90.2 (−98.9%)** |
| Plan | Index Scan + SubPlan Materialize | Hash Right Anti Join | Elimina materialización |

#### OPT-4: Paginación con OFFSET → keyset pagination

```sql
-- ANTES (recorre 5,000 filas para saltar): 290 ms
SELECT * FROM products ORDER BY product_id LIMIT 24 OFFSET 5000

-- DESPUÉS (usa la clave del último registro): 0.085 ms
SELECT * FROM products WHERE product_id > 'a046d4d3...' ORDER BY product_id LIMIT 24
```

| Métrica | ANTES | DESPUÉS | Mejora |
|---|---|---|---|
| Execution Time | **290.068 ms** | **0.085 ms** | **×3,412 (−99.97%)** |
| Buffers leídos | 5,056 | 26 | −99.5% |

### 3.5 Evidencia de Índices B-tree — Datos Reales

#### IDX-1: Historial de cliente (`idx_customers_unique_id` + `idx_orders_customer`)

| Métrica | Sin índices | Con índices | Mejora |
|---|---|---|---|
| Execution Time | **882.947 ms** (Parallel Seq Scan) | **4.737 ms** (Index Scan) | **×186 (−99.5%)** |
| Workers usados | 2 (paralelo) | 0 (índice directo) | Sin overhead paralelo |
| Tamaño índice customer | — | 5,288 kB | — |
| Tamaño índice orders_fk | — | 8,672 kB | — |

#### IDX-2: Ítems recientes por vendedor (`idx_order_items_seller_ts`)

| Métrica | Sin índice | Con índice | Mejora |
|---|---|---|---|
| Execution Time | **1,206.577 ms** (Parallel Seq Scan) | **11.064 ms** (Index Scan) | **×109 (−99.1%)** |
| Plan | Gather Merge + Sort + Parallel Seq Scan | Nested Loop + Index Scan | Evita sort explícito |

*Fuente: `postgresql/optimizaciones/results/03_indexes.txt` — equipo Ecommify*

---

## 4. Implementación MongoDB Atlas

### 4.1 Modelado de Documentos

**Base de datos:** `ecommify_analytics`  
**Colecciones:** `catalogo_enriquecido` (32,951 docs) · `resumen_reviews` (Bucket Pattern)

#### Documento típico — catalogo_enriquecido
```json
{
  "_id": ObjectId("..."),
  "product_id": "abc123def456",
  
  "category_translations": {
    "pt": "cama_mesa_banho",
    "en": "bed_bath_table"
  },
  
  "photos_qty": 4,
  
  "specifications": [
    { "k": "weight_g",  "v": 2500 },
    { "k": "length_cm", "v": 45 },
    { "k": "height_cm", "v": 20 },
    { "k": "width_cm",  "v": 30 }
  ],
  
  "computed_metrics": {
    "total_units_sold": 47,
    "average_rating": 4.3,
    "avg_price": 89.90
  }
}
```

#### Patrones de diseño implementados

| Patrón | Colección | Implementación | Beneficio |
|---|---|---|---|
| **Embedded** | catalogo_enriquecido | `category_translations` dentro del documento | Elimina JOIN para obtener traducción |
| **Attribute** | catalogo_enriquecido | `specifications` como array `[{k, v}]` | Permite indexar N dimensiones variables con un solo índice |
| **Computed** | catalogo_enriquecido | `computed_metrics` precalculadas al cargar | Evita recalcular avg_price y avg_rating en cada query |
| **Bucket** | resumen_reviews | Grupos de 5 reviews por documento | Reduce de ~99K documentos individuales a ~20K buckets |

#### Documento — resumen_reviews (Bucket Pattern)
```json
{
  "product_id": "abc123def456",
  "bucket_index": 0,
  "count": 5,
  "reviews": [
    {
      "review_score": 5,
      "review_comment_title": "Excelente",
      "review_comment_message": "Llegó en perfectas condiciones"
    }
  ]
}
```

### 4.2 JSON Schema Validation

```javascript
db.createCollection("catalogo_enriquecido", {
  validator: {
    $jsonSchema: {
      bsonType: "object",
      required: ["product_id", "computed_metrics"],
      properties: {
        product_id: { bsonType: "string" },
        computed_metrics: {
          bsonType: "object",
          required: ["total_units_sold", "average_rating", "avg_price"],
          properties: {
            total_units_sold: { bsonType: "int", minimum: 0 },
            average_rating:   { bsonType: "double", minimum: 0, maximum: 5 },
            avg_price:        { bsonType: "double", minimum: 0 }
          }
        }
      }
    }
  }
});
```

### 4.3 Índices MongoDB (Regla ESR)

La **Regla ESR** (Equality → Sort → Range) maximiza el uso del índice: los campos de igualdad van primero, luego los de ordenamiento, luego los de rango.

```javascript
// 1. COMPUESTO ESR — query principal de catálogo
db.catalogo_enriquecido.createIndex(
  { "category_translations.en": 1,
    "computed_metrics.avg_price": 1,
    "computed_metrics.average_rating": 1 },
  { name: "idx_category_price_rating_ESR" }
)

// 2. PARCIAL — solo productos con rating alto (filtra ~80% de docs)
db.catalogo_enriquecido.createIndex(
  { "computed_metrics.average_rating": -1 },
  { partialFilterExpression: { "computed_metrics.average_rating": { $gte: 4.0 } },
    name: "idx_high_rating_partial" }
)

// 3. TEXTO — full-text search en categorías
db.catalogo_enriquecido.createIndex(
  { "category_translations.pt": "text", "category_translations.en": "text" },
  { name: "idx_category_text", default_language: "portuguese" }
)

// 4. ATTRIBUTE PATTERN — buscar por dimensión específica
db.catalogo_enriquecido.createIndex(
  { "specifications.k": 1, "specifications.v": 1 },
  { name: "idx_specifications_kv_ESR" }
)

// 5. BUCKET ESR — acceso a reviews por producto
db.resumen_reviews.createIndex(
  { "product_id": 1, "bucket_index": 1, "count": 1 },
  { name: "idx_reviews_product_bucket_ESR" }
)

// 6. PARCIAL — solo reviews negativas (score ≤ 2)
db.resumen_reviews.createIndex(
  { "product_id": 1 },
  { partialFilterExpression: { "reviews.review_score": { $lte: 2 } },
    name: "idx_negative_reviews_partial" }
)
```

#### Evidencia .explain() — antes vs después

| Métrica | ANTES (sin índices) | DESPUÉS (idx_category_price_rating_ESR) |
|---|---|---|
| Stage | **COLLSCAN** | **IXSCAN** |
| `executionTimeMillis` | 24 ms | 6 ms |
| `totalDocsExamined` | 32,951 | 1,102 |
| `totalKeysExamined` | 0 | 1,102 |
| Mejora en tiempo | — | **75% más rápido** |
| Mejora en documentos | — | **−96.7%** |

Archivos de evidencia: `evidence/mongodb/explain_antes.json` · `evidence/mongodb/explain_despues.json`

### 4.4 Pipeline de Agregación (6 stages)

```javascript
db.catalogo_enriquecido.aggregate([

  // Stage 1: $match — filtrado temprano, activa idx_category_price_rating_ESR
  { $match: {
      "computed_metrics.total_units_sold": { $gt: 0 },
      "category_translations.en": { $nin: [null, "", "nan"] }
  }},

  // Stage 2: $project — proyección temprana reduce payload antes del $group
  { $project: {
      _id: 0,
      product_id: 1,
      category_en:  "$category_translations.en",
      units_sold:   "$computed_metrics.total_units_sold",
      avg_rating:   "$computed_metrics.average_rating",
      avg_price:    "$computed_metrics.avg_price"
  }},

  // Stage 3: $group — agrupación por categoría
  { $group: {
      _id:                  "$category_en",
      total_products:       { $sum: 1 },
      total_units:          { $sum: "$units_sold" },
      avg_category_price:   { $avg: "$avg_price" },
      avg_category_rating:  { $avg: "$avg_rating" }
  }},

  // Stage 4: $addFields — score compuesto de rendimiento
  { $addFields: {
      performance_score: {
        $round: [
          { $add: [
              { $multiply: ["$avg_category_rating", 20] },
              { $divide:   ["$total_units", 10] }
          ]},
          2
        ]
      }
  }},

  // Stage 5: $sort
  { $sort: { performance_score: -1, total_units: -1 } },

  // Stage 6: $facet — análisis multidimensional en una sola pasada
  { $facet: {
      top_10_categorias: [
        { $limit: 10 },
        { $project: {
            _id: 0,
            categoria:   "$_id",
            productos:   "$total_products",
            unidades:    "$total_units",
            precio_prom: { $round: ["$avg_category_price", 2] },
            rating_prom: { $round: ["$avg_category_rating", 2] },
            score:       "$performance_score"
        }}
      ],
      estadisticas_globales: [
        { $group: {
            _id:               null,
            total_categorias:  { $sum: 1 },
            precio_global_avg: { $avg: "$avg_category_price" },
            rating_global_avg: { $avg: "$avg_category_rating" },
            total_unidades:    { $sum: "$total_units" }
        }},
        { $project: { _id: 0 }}
      ],
      categorias_bajo_rendimiento: [
        { $match: { performance_score: { $lt: 80 } } },
        { $limit: 5 },
        { $project: { _id: 0, categoria: "$_id",
                      score: "$performance_score",
                      rating_prom: { $round: ["$avg_category_rating", 2] } }}
      ]
  }}

], { allowDiskUse: true })
```

#### Resultados del pipeline

| Métrica | Resultado |
|---|---|
| Total categorías analizadas | 71 |
| Total unidades vendidas | 111,023 |
| Precio promedio global | $170.10 |
| Rating promedio global | 4.02 |
| **Top categoría (score)** | `bed_bath_table` — 1,188.30 |
| Tiempo de ejecución | ~120 ms |

Archivo de evidencia: `evidence/mongodb/pipeline_resultado.json`

### 4.5 Sharding — Diseño Teórico

*Atlas M0 no permite sharding real. Este diseño aplica para M10+.*

```javascript
// Colección: catalogo_enriquecido
// Shard key: categoría (alta cardinalidad) + product_id hashed (distribución uniforme)
sh.shardCollection("ecommify_analytics.catalogo_enriquecido", {
  "category_translations.en": 1,
  "product_id": "hashed"
})

// Colección: resumen_reviews
// Shard key: product_id hashed — acceso siempre por product_id
sh.shardCollection("ecommify_analytics.resumen_reviews", {
  "product_id": "hashed"
})
```

**Replica Set — configuración recomendada:**

| Parámetro | Valor | Justificación |
|---|---|---|
| Topología | 1 Primary + 2 Secondaries | Quorum para failover automático |
| Write Concern crítico | `{w: "majority", j: true}` | Escrituras de catálogo confirmadas en disco |
| Write Concern analítico | `{w: 1}` | Reviews — lag de replicación aceptable |
| Read Preference analítico | `secondaryPreferred` | Descarga queries de lectura del primary |
| Read Concern | `"majority"` para reportes · `"local"` para tiempo real | Balance consistencia/latencia |

---

## 5. Pruebas de Carga con Concurrencia

### 5.1 Metodología

**Script:** `load_test.py` — `concurrent.futures.ThreadPoolExecutor`  
**Queries MongoDB probadas:** find por categoría · top rated · aggregation pipeline  
**Queries PostgreSQL probadas:** órdenes por status · COUNT por período · revenue JOIN categoría  
**Niveles:** 1 · 5 · 10 · 20 usuarios concurrentes | 10 queries por usuario

### 5.2 Resultados MongoDB Atlas M0 — Datos Reales

| Usuarios | QPS | Avg (ms) | P95 (ms) | P99 (ms) | Errores |
|---|---|---|---|---|---|
| 1 | 6.49 | 153.85 | 144.01 | 1,065.45 | 0% |
| 5 | 34.47 | 143.42 | 140.25 | 937.93 | 0% |
| 10 | 71.16 | 138.79 | 144.83 | 896.93 | 0% |
| 20 | 94.38 | 206.07 | 543.80 | 938.87 | 0% |

**Interpretación:**
- Throughput escala de 6.49 → 94.38 QPS (factor ×14.5 con 20× usuarios → eficiencia 72%)
- Avg latencia **mejora** de 1 a 10 usuarios (153 ms → 138 ms): efecto de connection pool warming
- P99 elevado en 1 usuario (1,065 ms): costo de TLS handshake en primera conexión en frío
- **0% de errores** en todos los niveles — M0 gestiona la cola sin rechazar conexiones

### 5.3 Resultados PostgreSQL — Datos Reales de EXPLAIN ANALYZE

*Fuente: `postgresql/optimizaciones/results/` — mediciones reales en Supabase del equipo Ecommify.*

| Optimización | Query | Sin optimización | Con optimización | Mejora |
|---|---|---|---|---|
| JOIN vs subqueries | Revenue por seller | **1,855.952 ms** | **39.713 ms** | **×46.7** |
| Rango sargable | Órdenes por día | **1,022.989 ms** | **535.048 ms** | **×1.9** |
| NOT EXISTS anti-join | Órdenes sin review | **3,195.639 ms** | **35.422 ms** | **×90.2** |
| Keyset pagination | Productos pág. 208 | **290.068 ms** | **0.085 ms** | **×3,412** |
| Índice B-tree compuesto | Historial de cliente | **882.947 ms** | **4.737 ms** | **×186** |
| Índice B-tree compuesto | Ítems por vendedor | **1,206.577 ms** | **11.064 ms** | **×109** |
| Particionamiento RANGE | Barrido mensual | **748.817 ms** | **2.375 ms** | **×315** |

### 5.4 Análisis de Degradación

```
Throughput MongoDB (QPS) vs Usuarios concurrentes:
  ●  1u →  6.49 QPS
  ● 5u  → 34.47 QPS  (+431%)
  ● 10u → 71.16 QPS  (+106%)
  ● 20u → 94.38 QPS  (+33%)   ← inicio de saturación de M0

Factor de degradación latencia Avg (20u vs 1u): ×1.34
  → Degradación baja — M0 absorbe la concurrencia en el rango 1-20 usuarios
  → A partir de 20u, P95 sube de 144 ms a 543 ms — señal de contención shared cluster
```

---

## 6. Análisis Comparativo PostgreSQL vs MongoDB

| Dimensión | PostgreSQL (Supabase) | MongoDB Atlas | Ganador en Ecommify |
|---|---|---|---|
| Modelo de datos | Tabular, esquema rígido | Documentos JSON flexibles | MongoDB para catálogo variable |
| Consistencia | ACID completo | ACID multi-doc desde 4.0, eventual por default | PostgreSQL para pagos |
| Escalabilidad horizontal | Limitada (Citus/particionamiento) | Sharding nativo | MongoDB para >1 TB |
| Queries analíticas | SQL maduro, window functions, CTEs | Pipeline de agregación | PostgreSQL para ad-hoc |
| Throughput (tier actual) | ~85-95 QPS estimado | **94.38 QPS medido** | Empate en rango probado |
| JOINs complejos | Nativo, optimizador maduro | `$lookup` — más lento | PostgreSQL |
| Búsqueda de texto | pg_trgm + tsvector | Text index + Atlas Search (Lucene) | MongoDB con Atlas Search |
| Monitoreo built-in | pg_stat_statements | Performance Advisor + Atlas Charts | MongoDB (mejor UX) |
| Backups automáticos | Supabase: daily en free tier | Atlas: no en M0 | PostgreSQL en producción |
| Schema evolution | ALTER TABLE (puede bloquear) | Sin schema obligatorio | MongoDB para cambios frecuentes |
| Índices especializados | B-tree, GIN, GiST, BRIN, Hash | Compuesto, texto, geo, hashed, wildcard | Empate (dominios distintos) |

---

## 7. Análisis CAP por Módulo

### 7.1 Teorema CAP aplicado

El sistema distribuido debe elegir entre Consistencia (C) y Disponibilidad (A) durante una partición de red (P es obligatorio).

### 7.2 Clasificación por módulo

| Módulo | Sistema | Clasificación | Justificación |
|---|---|---|---|
| `orders` | PostgreSQL | **CP** | No se puede registrar doble cobro |
| `customers` | PostgreSQL | **CP** | Integridad referencial con FK |
| `order_payments` | PostgreSQL | **CP** | Cumplimiento regulatorio financiero |
| `catalogo_enriquecido` | MongoDB | **AP** | Lag de 24h en métricas analíticas aceptable |
| `resumen_reviews` | MongoDB | **AP** | Reviews son eventual — no críticas |

### 7.3 Escenarios de falla

**Black Friday — alta concurrencia (20× tráfico normal):**
```
MongoDB M0 satura a ~50-60 QPS sostenidos → P99 supera 2,000 ms
Mitigación sin migrar: caché Redis (TTL 5 min) entre API y MongoDB
Mitigación definitiva: migrar a Atlas M10 ($57/mes) — cluster dedicado
```

**Auditoría fiscal — exactitud de datos históricos:**
```
PostgreSQL CP → SELECT COUNT(*), SUM(price) FROM orders WHERE año = 2017
             → BRIN reduce costo → resultado exacto, garantizado por ACID
MongoDB AP   → computed_metrics puede tener lag de 24h del ETL batch
             → NO apto para reporte fiscal
```

**Falla del ETL de sincronización:**
```
Impacto en PostgreSQL: NINGUNO — datos transaccionales intactos
Impacto en MongoDB: métricas analíticas desactualizadas (lag 24-48h)
Detección: monitor en job ETL → alerta si no completa en 2h
Ventana de degradación: solo catálogo analítico, no transacciones
```

---

## 8. Sincronización PostgreSQL → MongoDB

### 8.1 Flujo ETL actual (batch nocturno)

```python
# Pseudocódigo del proceso de sincronización
def sync_postgres_to_mongo():
    # 1. Calcular métricas desde PostgreSQL
    query = """
        SELECT oi.product_id,
               COUNT(*) AS total_units_sold,
               AVG(oi.price) AS avg_price,
               AVG(r.review_score) AS average_rating
        FROM order_items oi
        LEFT JOIN order_reviews r ON oi.order_id = r.order_id
        GROUP BY oi.product_id
    """
    metrics = pg_conn.execute(query).fetchall()

    # 2. Actualizar computed_metrics en MongoDB
    for row in metrics:
        mongo_col.update_one(
            {"product_id": row["product_id"]},
            {"$set": {"computed_metrics": {
                "total_units_sold": row["total_units_sold"],
                "avg_price": row["avg_price"],
                "average_rating": row["average_rating"]
            }}},
            upsert=False
        )
```

**Limitación:** lag de hasta 24h entre transacción real y dato en catálogo.

### 8.2 Mejora propuesta — CDC (Change Data Capture)

Para escala 10×: reemplazar batch por captura en tiempo real del WAL de PostgreSQL:
```
PostgreSQL WAL → Debezium → Apache Kafka → Consumer Python → MongoDB updateOne
Latencia: 24h → < 5 minutos
```

---

## 9. Monitoreo

### 9.1 MongoDB Atlas — métricas clave

```javascript
// Estadísticas de uso de índices
db.catalogo_enriquecido.aggregate([{ $indexStats: {} }])

// Queries lentas (>100ms)
db.setProfilingLevel(1, { slowms: 100 })
db.system.profile.find().sort({ ts: -1 }).limit(10)
```

**Atlas Performance Advisor:** sugiere índices automáticamente basado en queries observadas. Disponible en Atlas → Performance Advisor → Index Suggestions.

### 9.2 PostgreSQL — métricas clave

```sql
-- Queries más lentas
SELECT query, mean_exec_time, calls
FROM pg_stat_statements
ORDER BY mean_exec_time DESC LIMIT 10;

-- Índices usados vs no usados
SELECT indexrelname, idx_scan, idx_tup_fetch
FROM pg_stat_user_indexes
WHERE idx_scan = 0;  -- índices que nunca se han usado

-- Tamaño de tablas e índices
SELECT relname, pg_size_pretty(pg_total_relation_size(oid))
FROM pg_class WHERE relkind = 'r' ORDER BY pg_total_relation_size(oid) DESC;
```

---

## 10. Recomendaciones para Escala 10×

### 10.1 Escenario actual vs 10×

| Métrica | Actual | 10× | Acción requerida |
|---|---|---|---|
| Órdenes anuales | ~50,000 | ~500,000 | Agregar particiones 2019-2024 |
| Productos en catálogo | 32,951 | ~330,000 | Migrar MongoDB a M10+ |
| Usuarios concurrentes pico | ~50 | ~500 | PgBouncer + Atlas M10 |
| Tamaño `catalogo_enriquecido` | ~45 MB | ~450 MB | Supera límite M0 (512 MB) |

### 10.2 Hoja de ruta

**PostgreSQL:**
1. **Inmediato:** Supabase Pro ($25/mes) — 8 GB storage, PgBouncer, 60 conexiones físicas → 10,000 lógicas
2. **3-12 meses:** Read replicas para separar queries de reporting de queries transaccionales
3. **Largo plazo:** CitusDB para sharding horizontal por `customer_id`

**MongoDB:**
1. **Inmediato:** Atlas M10 ($57/mes) — 2 GB RAM, replica set dedicado (3 nodos)
2. **3-12 meses:** Atlas Search (Lucene) para búsqueda semántica de catálogo
3. **Largo plazo:** Sharding con `{ "category_translations.en": 1, "product_id": "hashed" }`

**Total inversión mínima para 10×:** $82/mes (Supabase Pro + Atlas M10)

---

## 11. Lecciones Aprendidas

### Lo que funcionó

| Decisión | Resultado |
|---|---|
| Computed Pattern en MongoDB | Pipeline en 120 ms porque las métricas están precalculadas — sin JOINs en lectura |
| Regla ESR en índices | −96.7% documentos examinados, COLLSCAN → IXSCAN |
| Particionamiento RANGE antes de cargar datos | BRIN se propagó automáticamente a las 4 particiones |
| `$project` antes de `$group` en pipeline | Reduce el payload en memoria antes de la agregación costosa |

### Ajustes durante implementación

| Problema | Solución |
|---|---|
| `col.aggregate(..., explain=True)` falla en pymongo ≥4.0 | Usar `db.command("aggregate", ..., explain=True)` |
| `pg_relation_size()` restringida en Supabase free | Usar `pg_indexes` y `pg_stat_user_tables` |
| DATA_PATH relativo falla según directorio de ejecución | Usar siempre paths absolutos en scripts |
| Pooler Supabase `aws-0` da ENOTFOUND | Host correcto: `aws-1-us-east-1.pooler.supabase.com` |
| Credenciales hardcodeadas en scripts | Próxima iteración: migrar a `.env` con `python-dotenv` |

### Deuda técnica

| Deuda | Prioridad |
|---|---|
| ETL batch 24h → migrar a CDC (Debezium) | Alta |
| Sin backups en MongoDB Atlas M0 | Alta — migrar a M10 |
| Credentials en código fuente | Alta — usar `.env` |
| Sin pruebas de integración automatizadas | Media |
| Sin rate limiting en la API | Media |

---

## 12. Estructura del Repositorio

```
Ecommify-Database-Design/
├── postgresql/
│   └── indexes/
│       ├── brin_index.sql              # Índice BRIN en Supabase
│       └── brin_local.sql
├── mongodb/
│   ├── indexes/
│   │   └── create_indexes.js          # 6 índices ESR, parciales, texto
│   ├── pipelines/
│   │   └── analytics_pipeline.js      # Pipeline 6 stages
│   └── scripts/
│       ├── load_mongo.py              # Carga datos Olist → MongoDB Atlas
│       ├── setup_indexes.py           # Crea índices + captura .explain()
│       └── run_pipeline.py            # Ejecuta pipeline + mide rendimiento
├── evidence/
│   └── mongodb/
│       ├── explain_antes.json         # COLLSCAN: 24ms, 32,951 docs
│       ├── explain_despues.json       # IXSCAN: 6ms, 1,102 docs
│       └── pipeline_resultado.json    # Top 10 categorías, stats globales
├── evidencia_guia6/
│   └── load_test_results.json         # Resultados pruebas de carga
├── docs/
│   └── Etapa_2_Documento_Tecnico.md   # Este documento
├── guia6/
│   └── Documento_Tecnico_Integral_Guia6.md
├── load_test.py                       # Script pruebas de carga concurrentes
└── README.md
```

---

## Referencias

- MongoDB ESR Rule: https://www.mongodb.com/docs/manual/tutorial/equality-sort-range-rule/
- PostgreSQL BRIN: https://www.postgresql.org/docs/current/brin-intro.html
- CAP Theorem — Brewer (2000): https://dl.acm.org/doi/10.1145/343477.343502
- Supabase Pooler: https://supabase.com/docs/guides/database/connecting-to-postgres
- MongoDB Atlas Tiers: https://www.mongodb.com/docs/atlas/cluster-tier/
- Debezium CDC: https://debezium.io/documentation/reference/stable/
- Olist Dataset: https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce
