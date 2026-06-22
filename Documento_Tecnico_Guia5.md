# Documento Técnico — Unidad 5
## Optimización de Rendimiento en MongoDB y PostgreSQL
### Proyecto Ecommify | Universidad de la Sabana

**Equipo:**
- Beycy Yuliet Rojas Acero
- Manuel Fernando Santofimio Tovar
- Jorge Ivan Figueroa Torres
- Jose Antonio Leao Ferrer
- David Felipe Cifuentes Villa

**Fecha:** Junio 2026  
**Repositorio:** https://github.com/yuliet719-beep/Ecommify-Database-Design

---

## a. Resumen Ejecutivo

El proyecto **Ecommify** implementa una arquitectura híbrida de base de datos para una plataforma multi-vendor de e-commerce, utilizando el dataset público Brazilian E-Commerce (Olist) con 99,224 órdenes y 32,951 productos.

La Unidad 5 aplica técnicas avanzadas de optimización sobre ambos sistemas:

**PostgreSQL / Supabase (módulo transaccional):**
- Índices especializados: B-tree, GIN, GiST y BRIN implementados y validados con EXPLAIN ANALYZE
- Particionamiento RANGE por año en tabla `orders` con propagación automática de índices
- 7 consultas complejas optimizadas con evidencia cuantitativa

**MongoDB Atlas (módulo analítico):**
- 6 índices optimizados (compuestos ESR, parciales, texto) sobre colecciones `catalogo_enriquecido` y `resumen_reviews`
- Pipeline de agregación de 6 stages con operadores `$match`, `$project`, `$group`, `$addFields`, `$sort` y `$facet`
- Reducción del **96.7%** en documentos examinados y **75%** en tiempo de ejecución tras indexación

**Resultados clave del pipeline:** 71 categorías analizadas, 111,023 unidades totales, precio promedio global $170.10, rating promedio 4.02.

---

## b. Implementación PostgreSQL

### b.1 Esquema en Supabase

El esquema transaccional de Ecommify está desplegado en Supabase (proyecto `aklzgzygjfxznpkkytae.supabase.co`) con las siguientes tablas principales:

| Tabla | Descripción | Filas aprox. |
|---|---|---|
| `orders` | Órdenes de compra (tabla particionada padre) | 99,224 |
| `customers` | Clientes únicos | 99,441 |
| `products` | Catálogo de productos | 32,951 |
| `order_items` | Líneas de detalle por orden | 112,650 |
| `order_payments` | Pagos registrados | 103,886 |
| `order_reviews` | Reseñas de clientes | 99,224 |
| `sellers` | Vendedores registrados | 3,095 |
| `geo_locations` | Coordenadas por código postal | 1,000,163 |

**Tipos de datos avanzados utilizados:**
- `JSONB`: campo `metadata` en `orders` para datos variables de configuración
- `USER-DEFINED` (tipo compuesto PostGIS): `shipping_address_snapshot` para coordenadas de entrega
- `TSTZRANGE`: rangos de tiempo para ventanas de análisis
- Arrays: usado en colecciones de etiquetas y atributos de productos

**Extensiones activas:**
- `PostGIS` — geolocalización y cálculos de distancia (fórmula Haversine en consultas complejas)
- `pg_trgm` — búsqueda por similitud de texto (índice GIN trigram en reseñas)
- `pgcrypto` — hashing de datos sensibles

### b.2 Estrategia de Indexación PostgreSQL

Se implementaron los 4 tipos de índices especializados requeridos:

#### Índices B-tree
Optimizan búsquedas de igualdad y rango sobre columnas de alta cardinalidad:

```sql
-- Compuesto: order_status + timestamp (consultas de análisis por período y estado)
CREATE INDEX idx_orders_status_purchase
ON orders USING btree (order_status, order_purchase_timestamp);

-- Cliente por ID (JOINs frecuentes con tabla customers)
CREATE INDEX idx_orders_customer
ON orders USING btree (customer_id);
```

#### Índices GIN
Para búsqueda full-text y columnas JSONB:

```sql
-- Trigrams pg_trgm para búsqueda ILIKE en comentarios de reseñas
CREATE INDEX idx_reviews_message_trgm
ON order_reviews USING GIN (review_comment_message gin_trgm_ops);

-- Full-text search en português sobre comentarios
CREATE INDEX idx_reviews_fts
ON order_reviews USING GIN (to_tsvector('portuguese', review_comment_message));
```

#### Índices GiST
Para datos geoespaciales con PostGIS:

```sql
-- Alternativa GiST para full-text (menor memoria, mayor flexibilidad)
CREATE INDEX idx_reviews_fts_gist
ON order_reviews USING GiST (to_tsvector('portuguese', review_comment_message));
```

#### Índice BRIN (nuevo en Guía 5)
**Justificación técnica:** Las órdenes se insertan cronológicamente, lo que genera una correlación física casi perfecta entre `order_purchase_timestamp` y la posición física de las páginas en disco. El BRIN aprovecha esta correlación almacenando solo el rango mínimo/máximo por bloque de páginas, resultando en un índice ~1000x más pequeño que un B-tree equivalente.

```sql
CREATE INDEX idx_orders_purchase_brin
ON orders
USING BRIN (order_purchase_timestamp)
WITH (pages_per_range = 128);
```

**Resultado importante:** Al crearse sobre la tabla padre `orders`, el índice BRIN se propagó automáticamente a todas las particiones hijas (`orders_2016`, `orders_2017`, `orders_2018`, `orders_default`), lo que demuestra la sinergia entre particionamiento declarativo e indexación BRIN.

### b.3 Particionamiento RANGE

La tabla `orders` utiliza particionamiento declarativo por rango de tiempo sobre `order_purchase_timestamp`:

| Partición | Rango | Registros aprox. |
|---|---|---|
| `orders_2016` | 2016-01-01 → 2016-12-31 | ~329 |
| `orders_2017` | 2017-01-01 → 2017-12-31 | ~45,101 |
| `orders_2018` | 2018-01-01 → 2018-12-31 | ~54,011 |
| `orders_default` | Fuera de rango | ~0 |

**Beneficio medido:** El partition pruning es automático — una consulta que filtre por `BETWEEN '2017-01-01' AND '2017-06-30'` solo accede a `orders_2017`, ignorando el resto de particiones (verificado con `EXPLAIN ANALYZE`).

### b.4 Evidencia EXPLAIN ANALYZE

#### Consulta de referencia — ANTES de BRIN:
```
Execution Time: 0.058 ms
Stage: Index Scan using orders_2017_order_status_order_purchase_timestamp_idx
Buffers: shared hit=1
```

#### Después de crear BRIN:
El índice BRIN queda disponible para queries de rango puro (sin filtro `order_status`), siendo ~1000x más pequeño que el B-tree equivalente y ocupando mínima memoria en cache.

**Evidencia adicional disponible** (archivos CSV en `evidence/postgresql/`):
- 7 consultas complejas con comparativas antes/después de índices
- Reducción de tiempo en consulta Haversine (geolocalización): de >500ms a <50ms tras B-tree en `geolocation_zip_code_prefix`
- Mejora en búsqueda full-text: COLLSCAN → GIN index scan

---

## c. Implementación MongoDB

### c.1 Colecciones y Patrones de Diseño

#### Colección: `catalogo_enriquecido`
Almacena el catálogo de productos con información enriquecida para análisis. **32,951 documentos.**

**Patrones aplicados:**

**1. Embedded (datos de acceso conjunto):**
Las traducciones del nombre de categoría se almacenan directamente en el documento porque siempre se acceden junto con el producto:
```json
"category_translations": {
  "pt": "cama_mesa_banho",
  "en": "bed_bath_table"
}
```

**2. Attribute Pattern (especificaciones variables):**
Las dimensiones físicas se modelan como array `[{k, v}]` para permitir queries uniformes sobre atributos heterogéneos. Sin este patrón, cada dimensión sería un campo separado y no se podría crear un índice único que las cubra todas:
```json
"specifications": [
  {"k": "weight_g",  "v": 225.0},
  {"k": "length_cm", "v": 16.0},
  {"k": "height_cm", "v": 10.0},
  {"k": "width_cm",  "v": 14.0}
]
```

**3. Computed Pattern (métricas precalculadas):**
Las métricas de rendimiento del producto se calculan en tiempo de carga y se almacenan ya procesadas, evitando costosos `$group` + `$lookup` en cada query analítica:
```json
"computed_metrics": {
  "total_units_sold": 145,
  "average_rating": 4.17,
  "avg_price": 146.78
}
```

#### Colección: `resumen_reviews`
Almacena reseñas agrupadas por producto. **~15,000+ buckets.**

**Patrón Bucket:**
Agrupa hasta 5 reseñas por documento, evitando el antipatrón de arrays ilimitados y facilitando la paginación:
```json
{
  "product_id": "abc123",
  "bucket_index": 0,
  "count": 5,
  "avg_score": 4.2,
  "reviews": [
    {"review_score": 5, "review_comment_message": "Ótimo produto!"},
    ...
  ]
}
```

### c.2 JSON Schema de Validación

```json
{
  "$jsonSchema": {
    "bsonType": "object",
    "required": ["product_id", "category_translations", "computed_metrics"],
    "properties": {
      "product_id":            {"bsonType": "string"},
      "category_translations": {"bsonType": "object"},
      "photos_qty":            {"bsonType": "int", "minimum": 0},
      "specifications": {
        "bsonType": "array",
        "items": {
          "bsonType": "object",
          "required": ["k", "v"],
          "properties": {
            "k": {"bsonType": "string"},
            "v": {"bsonType": ["double", "int", "null"]}
          }
        }
      },
      "computed_metrics": {
        "bsonType": "object",
        "properties": {
          "total_units_sold": {"bsonType": "int",    "minimum": 0},
          "average_rating":   {"bsonType": "double", "minimum": 0, "maximum": 5},
          "avg_price":        {"bsonType": "double", "minimum": 0}
        }
      }
    }
  }
}
```

### c.3 Índices Implementados

Se aplicó la regla **ESR (Equality → Sort → Range)** de MongoDB para todos los índices compuestos:

| # | Nombre | Tipo | Colección | Justificación |
|---|---|---|---|---|
| 1 | `idx_category_price_rating_ESR` | Compuesto ESR | catalogo_enriquecido | Equality: categoría · Sort: precio · Range: rating |
| 2 | `idx_high_rating_partial` | Parcial | catalogo_enriquecido | Solo rating ≥ 4.0 (~25% del total) — reduce tamaño del índice |
| 3 | `idx_category_text` | Texto | catalogo_enriquecido | Full-text search en pt/en con pesos diferenciados |
| 4 | `idx_specifications_kv_ESR` | Compuesto | catalogo_enriquecido | Soporte al Attribute Pattern — queries `{k: "weight_g", v: {$gt: 500}}` |
| 5 | `idx_reviews_product_bucket_ESR` | Compuesto ESR | resumen_reviews | Equality: product_id · Sort: bucket_index · Range: count |
| 6 | `idx_negative_reviews_partial` | Parcial | resumen_reviews | Solo avg_score ≤ 2.5 — subconjunto para alertas de calidad |

### c.4 Evidencia .explain() — Antes y Después

**Query de referencia:**
```javascript
db.catalogo_enriquecido.find({
  "category_translations.en": "computers_accessories",
  "computed_metrics.average_rating": { $gte: 4.0 }
}).sort({ "computed_metrics.avg_price": 1 })
```

| Métrica | Sin índice | Con `idx_category_price_rating_ESR` |
|---|---|---|
| **Stage** | `COLLSCAN` | `IXSCAN` |
| **Tiempo de ejecución** | 24 ms | 6 ms |
| **Documentos examinados** | 32,951 | 1,102 |
| **Documentos devueltos** | ~100 | ~100 |
| **Mejora en tiempo** | — | **75% más rápido** |
| **Mejora en escaneo** | — | **96.7% menos documentos** |

**Interpretación:** El índice ESR permite a MongoDB usar directamente la entrada del índice para el campo de igualdad (`category_translations.en`), avanzar en orden de precio (`avg_price`) y aplicar el filtro de rango sobre rating sin escanear documentos innecesarios.

### c.5 Pipeline de Agregación Optimizado

**Nombre:** "Análisis de rendimiento por categoría de producto"  
**Colección:** `catalogo_enriquecido` | **Stages:** 6 | `allowDiskUse: true`

```
Stage 1: $match    → Filtra productos con ventas registradas
Stage 2: $project  → Proyección temprana (reduce payload ~60% antes del $group)
Stage 3: $group    → Agrupación por categoría con métricas agregadas
Stage 4: $addFields → Calcula performance_score compuesto
Stage 5: $sort     → Ordena por score descendente
Stage 6: $facet    → Análisis multidimensional en una sola pasada
```

**Optimizaciones aplicadas:**
1. **`$match` primero:** reduce el conjunto de documentos antes de cualquier transformación costosa; aprovecha `idx_category_price_rating_ESR`
2. **`$project` antes de `$group`:** elimina campos innecesarios (~60% menos datos en memoria)
3. **`$facet` al final:** permite obtener top-10, estadísticas globales y categorías bajo rendimiento en una sola pasada — sin `$facet` serían 3 queries separadas

**Resultados del pipeline:**

| Categoría | Unidades | Precio prom. | Rating prom. | Score |
|---|---|---|---|---|
| bed_bath_table | 11,115 | $107.46 | 3.84 | 1,188.30 |
| health_beauty | 9,670 | $146.78 | 4.17 | 1,050.32 |
| sports_leisure | 8,641 | $135.44 | 4.11 | 946.23 |
| furniture_decor | 8,334 | $103.23 | 3.88 | 910.91 |
| computers_accessories | 7,827 | $156.03 | 3.93 | 861.35 |

**Estadísticas globales:** 71 categorías · 111,023 unidades · precio prom. $170.10 · rating prom. 4.02

### c.6 Diseño Teórico de Sharding y Replica Sets

#### Shard Key — `catalogo_enriquecido`
```javascript
{ "category_translations.en": 1, "product_id": "hashed" }
```
**Justificación:** La combinación campo de rango + campo hashed garantiza distribución uniforme entre shards. `category_translations.en` permite routing directo para queries analíticas por categoría; `product_id` hashed evita hot spots al distribuir productos de una misma categoría entre shards.

#### Shard Key — `resumen_reviews`
```javascript
{ "product_id": "hashed" }
```
**Justificación:** Todas las queries acceden por `product_id`. El sharding hashed garantiza distribución uniforme sin hot spots, a diferencia del sharding por rango que concentraría productos populares.

#### Configuración del Replica Set
Topología: **1 Primary + 2 Secondaries** (región us-east-1)

| Operación | Write Concern | Read Preference | Justificación |
|---|---|---|---|
| Inserción de catálogo | `{w: "majority", j: true}` | — | Datos críticos — confirmar en ≥2 nodos |
| Inserción de reviews | `{w: 1}` | — | Tolerante a pérdida eventual |
| Queries analíticas | — | `secondaryPreferred` | Reduce carga del primary |
| Métricas en tiempo real | — | `local` | Menor latencia, consistencia eventual |

---

## d. Evidencia Cuantitativa de Mejoras

### d.1 PostgreSQL

| Optimización | Antes | Después | Mejora |
|---|---|---|---|
| Consulta CLV (7 JOINs) | ~850 ms | ~120 ms | **86% más rápido** |
| Búsqueda geolocalización (Haversine) | ~500 ms | ~48 ms | **90% más rápido** |
| Full-text search (tsvector) | SEQSCAN 45ms | GIN IXSCAN 3ms | **93% más rápido** |
| Partition pruning activo | Escanea 99,224 rows | Escanea 45,101 rows | **54% menos filas** |
| Índice BRIN creado | — | Propagado a 4 particiones | Tamaño mínimo |

### d.2 MongoDB

| Optimización | Antes | Después | Mejora |
|---|---|---|---|
| Query con filtro categoría + rating | 24 ms | 6 ms | **75% más rápido** |
| Documentos examinados | 32,951 | 1,102 | **96.7% menos** |
| Stage de ejecución | COLLSCAN | IXSCAN | Uso de índice |
| Pipeline 6 stages (71 categorías) | ~80 ms | ~25 ms | **~69% más rápido** |

---

## e. Sincronización entre Sistemas

La arquitectura híbrida define un flujo claro entre PostgreSQL y MongoDB:

```
PostgreSQL (transaccional)          MongoDB Atlas (analítico)
        │                                    │
   orders, items              →    catalogo_enriquecido
   order_reviews              →    resumen_reviews (buckets)
   products, sellers          →    computed_metrics
        │                                    │
   Datos operacionales            Métricas precalculadas
   (escritura frecuente)          (lectura optimizada)
```

**Estrategia de sincronización:** ETL batch nocturno — los scripts `load_mongo.py` recalculan `computed_metrics` (total_units_sold, average_rating, avg_price) agregando datos de `order_items` y `order_reviews` de PostgreSQL, y actualizan MongoDB Atlas. Esto implementa el **Computed Pattern**: las métricas costosas de calcular se precalculan en la carga, no en la consulta.

**Consistencia eventual:** El módulo analítico (MongoDB) puede estar hasta 24 horas desactualizado respecto al módulo transaccional (PostgreSQL). Esto es aceptable para análisis de tendencias y reportes gerenciales, pero no para datos operacionales en tiempo real.

---

## f. Lecciones Aprendidas

### Obstáculos y Soluciones

| Obstáculo | Solución Aplicada |
|---|---|
| `pymongo` moderno no acepta `explain=True` en `.aggregate()` | Usar `db.command("aggregate", ..., explain=True)` en su lugar |
| MongoDB Atlas M0 no permite sharding real | Diseño teórico documentado con justificación técnica equivalente |
| Índice BRIN no muestra mejora dramática si ya hay particionamiento | Usar BRIN como complemento al B-tree: menor tamaño, útil para queries de rango puro |
| `DATA_PATH` relativa falla al ejecutar desde otra carpeta | Siempre usar rutas absolutas con `r"..."` en Windows |
| EXPLAIN ANALYZE en Supabase restringido para algunas tablas del sistema | Consultar directamente `pg_indexes` en lugar de `pg_relation_size` |

### Limitaciones del Free Tier
- **MongoDB Atlas M0:** Sin sharding real, sin Performance Advisor completo, máximo 512 MB de almacenamiento — suficiente para el dataset Olist pero limitante en producción
- **Supabase Nano:** 60 conexiones máximas — puede generar errores con múltiples scripts corriendo simultáneamente
- **Google Colab:** Sesiones expiran — notebooks deben re-ejecutarse si la sesión cae

### Decisiones Técnicas Clave
1. **BRIN sobre B-tree para timestamp:** La correlación física de las órdenes (inserción cronológica) hace que BRIN sea óptimo — mucho más pequeño con efectividad similar para rangos de tiempo amplios
2. **`$project` antes de `$group` en pipeline:** Reduce el payload que procesa `$group` en ~60%, crítico cuando el dataset supera el límite de memoria de 100 MB
3. **Shard key hashed vs rango:** Para `product_id`, el sharding hashed evita el hot spot que generaría el rango en productos con IDs consecutivos populares
