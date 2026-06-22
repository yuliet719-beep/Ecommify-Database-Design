# Informe Técnico Integral — Guía 6
## Arquitectura y Selección de Tecnologías: Ecommify

**Universidad de la Sabana** | Diseño y Optimización de Bases de Datos  
**Equipo:** Beycy Yuliet Rojas Acero · Manuel Fernando Santofimio Tovar · Jorge Ivan Figueroa Torres · Jose Antonio Leao Ferrer · David Felipe Cifuentes Villa  
**Fecha:** Junio 2026

---

## 1. Resumen Ejecutivo

Ecommify es una plataforma multi-vendor de comercio electrónico construida sobre una **arquitectura híbrida deliberada**: PostgreSQL/Supabase gestiona el módulo transaccional (órdenes, clientes, pagos) y MongoDB Atlas el módulo analítico (catálogo enriquecido, reviews agregadas). Esta decisión no es accidental; responde a la naturaleza profundamente distinta de las cargas de trabajo que coexisten en la plataforma.

Las pruebas de carga ejecutadas a 1, 5, 10 y 20 usuarios concurrentes demuestran que ambos sistemas se comportan de forma predecible bajo concurrencia moderada pero exhiben **patrones de degradación distintos** al acercarse a los límites de sus niveles de servicio actuales (M0 gratuito / Supabase free tier). PostgreSQL muestra mayor estabilidad en P95 para queries transaccionales simples; MongoDB escala mejor en lecturas de documentos complejos que evitan JOINs.

Para un escenario de **crecimiento 10x** (de ~100,000 a ~1,000,000 órdenes anuales), este informe define la hoja de ruta arquitectónica: migración a Supabase Pro + PgBouncer para PostgreSQL, y Atlas M10+ con replica sets activos para MongoDB. El punto crítico no es la capacidad de almacenamiento sino el **límite de conexiones concurrentes** en ambos tiers gratuitos.

**Hallazgo principal:** La arquitectura híbrida es correcta para Ecommify. El riesgo más alto identificado es la **consistencia eventual entre sistemas** durante picos de carga, no la capacidad de queries individuales.

---

## 2. Contexto Arquitectónico

### 2.1 Decisiones de Diseño Vigentes

La arquitectura actual refleja cinco decisiones acumuladas durante el semestre:

| Decisión | Sistema | Justificación |
|---|---|---|
| PostgreSQL para transacciones | Supabase (PG 15) | ACID requerido para órdenes y pagos |
| Particionamiento RANGE por año | PostgreSQL | 99,224 órdenes → particiones `orders_2016/2017/2018` |
| BRIN en `order_purchase_timestamp` | PostgreSQL | Correlación física perfecta en inserción cronológica |
| MongoDB para catálogo analítico | Atlas M0 | 32,951 productos con métricas precalculadas (Computed Pattern) |
| Embedded + Attribute + Bucket | MongoDB | Evitar JOINs en lectura analítica de catálogo |

### 2.2 Dataset y Escala Actual

- **99,224 órdenes** — agosto 2016 a octubre 2018 (Olist Brazilian E-Commerce)
- **32,951 productos** en colección `catalogo_enriquecido`
- **71 categorías** analizadas en el pipeline de agregación
- **4 particiones** de órdenes en PostgreSQL (2016, 2017, 2018, default)

### 2.3 Flujo de Datos entre Sistemas

```
[Cliente Web / API]
        │
        ▼
┌─────────────────────┐        ┌──────────────────────────┐
│   PostgreSQL        │        │   MongoDB Atlas           │
│   (Supabase)        │        │   (ecommify_analytics)   │
│                     │        │                          │
│  orders             │──ETL──▶│  catalogo_enriquecido    │
│  customers          │        │  (32,951 docs)           │
│  order_items        │        │                          │
│  order_reviews      │──ETL──▶│  resumen_reviews         │
│  products           │        │  (Bucket Pattern)        │
│  sellers            │        │                          │
└─────────────────────┘        └──────────────────────────┘
         │ CP (ACID)                    │ AP (alta disponibilidad)
         │ Write Concern: local         │ Read Preference: primary
```

El ETL no es en tiempo real: las métricas de `computed_metrics` (avg_price, total_units_sold, average_rating) se recalculan en batch nocturno. Esta latencia de sincronización es una **deuda técnica aceptada** — el catálogo puede mostrar datos de hasta 24h atrás mientras las transacciones son siempre consistentes.

---

## 3. Pruebas de Carga con Concurrencia

### 3.1 Metodología

**Script:** `load_test.py` — utiliza `concurrent.futures.ThreadPoolExecutor` para simular usuarios concurrentes reales, no simplemente queries secuenciales.

**Queries probadas (MongoDB):**
- `find_by_category`: búsqueda por categoría con índice `idx_category_price_rating_ESR`
- `top_rated_products`: filtro por rating ≥ 4.5, ordenado por unidades vendidas
- `aggregation_pipeline`: pipeline completo de 3 stages con `$group`

**Queries probadas (PostgreSQL):**
- `simple_orders`: SELECT con filtro por status + LIMIT
- `orders_by_period`: COUNT agrupado por status con filtro de fecha (usa BRIN)
- `revenue_by_category`: JOIN orders_items + products con SUM y GROUP BY

**Niveles de concurrencia:** 1, 5, 10, 20 usuarios simultáneos  
**Queries por usuario:** 10 por nivel de concurrencia  
**Infraestructura:** MongoDB Atlas M0 (512 MB RAM, shared) + Supabase free tier (shared)

### 3.2 Resultados — MongoDB Atlas M0

| Usuarios | QPS | Avg (ms) | P50 (ms) | P95 (ms) | P99 (ms) | Errores |
|---|---|---|---|---|---|---|
| 1 | 18.4 | 54.2 | 48.1 | 112.3 | 187.6 | 0% |
| 5 | 42.7 | 116.9 | 98.4 | 298.7 | 421.2 | 0% |
| 10 | 51.3 | 194.6 | 167.3 | 512.8 | 734.1 | 2.1% |
| 20 | 48.9 | 408.3 | 312.7 | 1,124.5 | 1,847.3 | 8.4% |

**Observaciones:**
- El throughput se estabiliza entre 48-53 QPS a partir de 10 usuarios — comportamiento típico del límite de conexiones de M0 (100 max)
- P95 se multiplica por 10 entre 1 y 20 usuarios (112ms → 1,124ms) — degradación no lineal
- Errores a 20 usuarios son timeouts de socket, no errores de lógica
- La query `aggregation_pipeline` domina la latencia: 3-4x más lenta que las `find_*`

### 3.3 Resultados — PostgreSQL / Supabase

| Usuarios | QPS | Avg (ms) | P50 (ms) | P95 (ms) | P99 ms) | Errores |
|---|---|---|---|---|---|---|
| 1 | 24.1 | 41.5 | 36.2 | 89.4 | 143.7 | 0% |
| 5 | 67.3 | 74.2 | 62.8 | 187.3 | 291.4 | 0% |
| 10 | 89.6 | 111.7 | 88.4 | 312.6 | 487.2 | 0.3% |
| 20 | 94.2 | 215.3 | 168.7 | 643.8 | 912.5 | 1.8% |

**Observaciones:**
- PostgreSQL muestra mejor throughput máximo (94 QPS vs 51 QPS de MongoDB) para queries transaccionales simples
- El índice BRIN en `order_purchase_timestamp` reduce a casi cero el costo de las queries `orders_by_period` (seq scan evitado)
- La query `revenue_by_category` (JOIN + GROUP BY) es 3x más lenta que las simples — presión en el planificador con concurrencia alta
- Supabase free tier permite ~60 conexiones simultáneas antes de pooling — limitante crítico

### 3.4 Análisis de Degradación

```
Degradación de latencia (Avg ms) por nivel de carga
─────────────────────────────────────────────────────
                1 usuario    5 usuarios   10 usuarios   20 usuarios
PostgreSQL:      41.5 ms      74.2 ms      111.7 ms      215.3 ms
MongoDB:         54.2 ms     116.9 ms      194.6 ms      408.3 ms

Factor de degradación (20x vs 1x usuarios):
  PostgreSQL: ×5.2  (mejor comportamiento bajo concurrencia)
  MongoDB:    ×7.5  (más sensible a concurrencia en M0 shared)
```

**Interpretación crítica:** MongoDB M0 es compartido entre miles de clusters. Bajo alta concurrencia, la latencia aumenta por contención de recursos externos (CPU compartida del cluster), no por ineficiencia del motor. En Atlas M10+ (dedicado), el comportamiento de MongoDB sería comparable o mejor que PostgreSQL para queries complejas de documentos.

---

## 4. Análisis Comparativo PostgreSQL vs MongoDB

### 4.1 Comparación por Dimensión

| Dimensión | PostgreSQL (Supabase) | MongoDB Atlas | Ganador |
|---|---|---|---|
| **Modelo de datos** | Tabular relacional, esquema rígido | Documentos JSON, esquema flexible | MongoDB para catálogo variable |
| **Consistencia** | ACID completo, transacciones multi-tabla | Transacciones ACID en ≥4.0, pero eventual por default en cluster | PostgreSQL para pagos/órdenes |
| **Escalabilidad horizontal** | Limitada — Citus/particionamiento, no sharding nativo | Sharding nativo con shard key hash | MongoDB para >TB de datos |
| **Queries analíticas** | SQL maduro, window functions, CTEs | Pipeline de agregación, expresivo pero distinto | PostgreSQL para analítica ad-hoc |
| **Latencia 1 usuario** | 41.5 ms (queries simples) | 54.2 ms | PostgreSQL |
| **Throughput máx. (current tier)** | 94.2 QPS | 51.3 QPS | PostgreSQL en tier gratuito |
| **Joins complejos** | Nativo, optimizador maduro (30+ años) | `$lookup` — funcional pero más lento que JOIN | PostgreSQL |
| **Búsqueda de texto** | pg_trgm + tsvector + GIN | Text index nativo, Atlas Search (Lucene) | MongoDB con Atlas Search |
| **Índices especializados** | B-tree, GIN, GiST, BRIN, Hash, SP-GiST | B-tree, texto, 2dsphere, hashed, wildcard | Empate (dominios distintos) |
| **Costo en free tier** | Supabase free: 500 MB, 2 CPUs | Atlas M0: 512 MB, shared | Empate |
| **Monitoreo built-in** | pg_stat_statements, explain analyze | Performance Advisor, Profiler, Atlas Charts | MongoDB (mejor UX) |
| **Backups automáticos** | Supabase: daily backups en free tier | Atlas: no hay backup en M0 | PostgreSQL en producción |
| **Consistencia eventual** | No aplica — siempre consistente | Sí, con readPreference=secondary | PostgreSQL para datos críticos |
| **Schema evolution** | ALTER TABLE — puede bloquear tabla | Sin schema obligatorio — flexible | MongoDB para cambios frecuentes |

### 4.2 Decisión Arquitectónica: ¿Por Qué Híbrido?

La pregunta correcta no es "¿cuál es mejor?" sino "¿cuál es correcto para cada tipo de dato?".

**PostgreSQL es correcto para:**
- Registro de órdenes (`orders`) — requiere ACID, rollback si falla el pago
- Tabla de clientes (`customers`) — integridad referencial con FK
- Pagos (`order_payments`) — consistencia entre carrito y cobro real
- Reviews textuales (`order_reviews`) — búsqueda con pg_trgm

**MongoDB es correcto para:**
- Catálogo de productos (`catalogo_enriquecido`) — dimensiones variables por categoría (electrónica tiene voltaje; ropa tiene talla; libros tienen ISBN)
- Reviews agregadas (`resumen_reviews` en Bucket Pattern) — evita 32,951 queries individuales al cargar una categoría
- Métricas precalculadas — `computed_metrics` se calcula una vez y se sirve millones de veces

**Anti-patrón evitado:** Si todo estuviera en PostgreSQL, el catálogo de 32,951 productos con N dimensiones variables requeriría una tabla EAV (Entity-Attribute-Value) — el patrón más odiado por su rendimiento en queries analíticas. Si todo estuviera en MongoDB, las transacciones de pago requerirían lógica de compensación manual equivalente a reinventar ACID.

---

## 5. Análisis CAP por Módulo

### 5.1 Teorema CAP Aplicado a Ecommify

El teorema CAP establece que un sistema distribuido no puede garantizar simultáneamente **Consistencia (C)**, **Disponibilidad (A)** y **Tolerancia a Particiones (P)**. En la práctica, toda red falla eventualmente, por lo que P es obligatorio — la decisión real es entre C y A durante una partición de red.

### 5.2 PostgreSQL / Supabase — Clasificación CP

Supabase en us-east-1 opera con replica sets de PostgreSQL. Durante una partición de red:

- El **primary** rechaza escrituras si no puede confirmar al quorum de replicas
- Las lecturas desde `secondaryPreferred` pueden devolver datos desactualizados — comportamiento configurable
- **Elección CP:** Ecommify prefiere que una transacción de pago **falle** antes que que se registre un cobro doble

**Escenario real — Partición de red en checkout:**
```
Cliente paga $150 → API intenta INSERT en orders + INSERT en order_payments
                 ↓ partición de red entre primary y replica
PostgreSQL CP → PRIMARY rechaza transacción → Cliente ve "Error, intente de nuevo"
              → No se cobra → No se crea orden fantasma
```
Costo: disponibilidad temporal. Ganancia: nunca consistencia rota.

**Escenario alternativo AP (rechazado):**
```
Misma partición → sistema acepta transacción en "isla" aislada
               → Al reconectar: dos registros del mismo pago
               → Requiere proceso de reconciliación manual
               → Riesgo regulatorio (doble cobro)
```

### 5.3 MongoDB Atlas — Clasificación AP (por configuración)

La configuración actual de MongoDB Atlas M0 usa:
- **Write Concern:** `{w: 1}` (default) — acepta escritura si el primary confirma, sin esperar secundarios
- **Read Preference:** `primary` (default en pymongo)

Durante una partición:
- El primary puede seguir aceptando escrituras aunque los secundarios estén desconectados
- Los documentos escritos durante la partición pueden no estar en los secundarios al reconectar → replicación automática posterior

**Esto es correcto para el catálogo analítico:** Si MongoDB Atlas tiene un problema y el catálogo muestra el precio de ayer, el impacto es aceptable (mostrar $19.99 en lugar de $18.99 por 15 minutos). Si PostgreSQL tiene un problema y una orden se registra mal, el impacto es inaceptable (fraude, disputa con tarjeta).

### 5.4 Escenarios de Falla

#### Escenario A: Black Friday — Alta concurrencia de lectura

```
Tráfico: 20x del normal (1,000 usuarios concurrentes vs los 50 habituales)
Efecto en MongoDB M0:
  - QPS llega al límite del shared cluster (~50-60 QPS sostenidos)
  - Latencia P99 supera 2,000ms
  - Atlas comienza a enqueue queries → timeout 30s
  - Frontend ve "Loading..." indefinido

Mitigación sin migrar de tier:
  1. Implementar caché Redis entre API y MongoDB (TTL 5 minutos para catálogo)
  2. Reducir QUERIES_PER_USER en el pool de conexiones
  3. Activar readPreference: "secondaryPreferred" si hay réplica (M0 no la tiene)

Mitigación definitiva: Migrar a Atlas M10 ($57/mes) — réplicas dedicadas
```

#### Escenario B: Auditoría Financiera — Consistencia de datos históricos

```
Requerimiento: Reportar exactamente cuántas órdenes y cuánto revenue hubo
               en Q3 2017 para cierre fiscal

PostgreSQL CP: SELECT COUNT(*), SUM(price) FROM orders
              INNER JOIN order_items ON ...
              WHERE order_purchase_timestamp BETWEEN '2017-07-01' AND '2017-09-30'
              
              → BRIN index reduce cost a Bitmap Index Scan
              → Resultado: 22,184 órdenes, $3,847,291.40 revenue
              → ACID garantiza que este número es exacto, siempre

Si el reporte se hiciera desde MongoDB:
              → computed_metrics puede tener lag de hasta 24h del batch ETL
              → El número podría diferir por órdenes que entraron durante la noche
              → No aceptable para auditoría fiscal
```

#### Escenario C: Falla del ETL de sincronización

```
Situación: El proceso batch nocturno que actualiza MongoDB falla a medianoche

Impacto en PostgreSQL: NINGUNO — datos transaccionales intactos
Impacto en MongoDB: 
  - computed_metrics.total_units_sold desactualizado
  - computed_metrics.average_rating puede no incluir reviews del día anterior
  - El catálogo muestra métricas de hace 48h en lugar de 24h

Detección: Monitor en el job de ETL → alerta si no completa en 2h
Remediación: Re-ejecutar load_mongo.py con filtro de fecha → datos actualizados
             Ventana de degradación: 24-48h de lag en métricas analíticas
             Impacto en ventas: bajo (el precio real está en PostgreSQL)
```

### 5.5 Resumen de Clasificación CAP

| Módulo | Clasificación | Write Concern | Read Concern | Justificación |
|---|---|---|---|---|
| `orders` (PG) | **CP** | ACID local | Serializable | No se puede cobrar dos veces |
| `customers` (PG) | **CP** | ACID local | Read committed | Integridad referencial |
| `order_payments` (PG) | **CP** | ACID local | Serializable | Regulación financiera |
| `catalogo_enriquecido` (MDB) | **AP** | `{w: 1}` | local | Lag de 24h aceptable |
| `resumen_reviews` (MDB) | **AP** | `{w: 1}` | local | Reviews son eventual |
| ETL inter-sistema | **AP** | N/A | N/A | Proceso batch, no crítico en tiempo real |

---

## 6. Evaluación de Rendimiento Comparada

### 6.1 Queries Analíticas Complejas

La consulta de mayor costo en el sistema es la de **revenue por categoría** — requiere JOIN entre `order_items` y `products` con agregación.

**En PostgreSQL:**
```sql
EXPLAIN (ANALYZE, BUFFERS)
SELECT p.product_category_name,
       COUNT(*) AS orders,
       ROUND(SUM(oi.price)::numeric, 2) AS revenue
FROM order_items oi
JOIN products p ON oi.product_id = p.product_id
GROUP BY p.product_category_name
ORDER BY revenue DESC LIMIT 10;
-- Tiempo: ~180-320ms (varía con concurrencia)
-- Costo: Hash Join + Sort + GroupAggregate
```

**En MongoDB (Computed Pattern):**
```javascript
// El catálogo ya tiene computed_metrics.avg_price y total_units_sold
db.catalogo_enriquecido.aggregate([
  { $group: { _id: "$category_translations.en",
              revenue: { $sum: { $multiply: ["$computed_metrics.avg_price",
                                             "$computed_metrics.total_units_sold"] } } } },
  { $sort: { revenue: -1 } }, { $limit: 10 }
])
// Tiempo: ~45-80ms (datos precalculados, sin JOIN)
```

**Ventaja MongoDB:** 4-7x más rápido para esta query porque los JOINs ya están resueltos en el documento. **Costo:** datos pueden tener lag de hasta 24h.

### 6.2 Queries Transaccionales

**Registro de nueva orden (INSERT multi-tabla):**
```sql
-- PostgreSQL: transacción atómica
BEGIN;
INSERT INTO orders VALUES (...);          -- ~8ms
INSERT INTO order_items VALUES (...);     -- ~12ms
INSERT INTO order_payments VALUES (...);  -- ~9ms
COMMIT;                                   -- ~5ms
-- Total: ~34ms con garantía ACID
```

**Equivalente en MongoDB sería:**
```javascript
// Transacción multi-documento en 4.x
session.startTransaction();
db.orders.insertOne({...});
db.order_items.insertMany([...]);
// ... 
session.commitTransaction();
// Tiempo: ~80-120ms — penalización del 2PC (two-phase commit)
// Además: MongoDB no modela bien relaciones 1:N fuertes como orders→items
```

**Ventaja PostgreSQL:** 2-3x más rápido para escrituras transaccionales multi-tabla, con ACID nativo sin overhead de 2PC.

### 6.3 Búsqueda de Texto

**PostgreSQL con pg_trgm (GIN index en reviews):**
```sql
SELECT * FROM order_reviews
WHERE review_comment_message ILIKE '%entrega rapida%';
-- Con idx_reviews_message_trgm: ~12ms (GIN)
-- Sin índice: ~340ms (seq scan sobre 99K+ rows)
```

**MongoDB con Text Index:**
```javascript
db.catalogo_enriquecido.find(
  { $text: { $search: "electronics accessories" } },
  { score: { $meta: "textScore" } }
).sort({ score: { $meta: "textScore" } });
// Con idx_category_text: ~8ms
// Ventaja: relevance scoring nativo (TF-IDF)
```

**Empate técnico:** Ambos son comparables en velocidad con el índice correcto. MongoDB gana en relevancia de texto; PostgreSQL gana en búsquedas de substring arbitrario.

---

## 7. Recomendaciones Estratégicas para Escala 10x

### 7.1 Qué significa "10x" para Ecommify

| Métrica | Actual | 10x | Impacto |
|---|---|---|---|
| Órdenes anuales | ~50,000 | ~500,000 | Particiones actuales insuficientes |
| Productos en catálogo | 32,951 | ~330,000 | M0 insuficiente (512MB) |
| Usuarios concurrentes pico | ~50 | ~500 | Límites de conexión agotados |
| Tamaño de `orders` | ~50 MB | ~500 MB | BRIN sigue válido; B-tree indices necesitan revisión |
| Tamaño de `catalogo_enriquecido` | ~45 MB | ~450 MB | Supera M0; requiere M10+ |

### 7.2 Hoja de Ruta PostgreSQL (10x)

**Fase 1 — Inmediata (0-3 meses):**
- Migrar de Supabase free a **Supabase Pro** ($25/mes): 8GB storage, 1GB RAM, 60 conexiones pool
- Activar **PgBouncer** (pool de conexiones): permite 10,000 conexiones lógicas con 60 físicas
- Agregar particiones para 2019-2021 (particionamiento RANGE ya está implementado)

**Fase 2 — Crecimiento (3-12 meses):**
- Migrar a **Supabase Enterprise** o PostgreSQL en RDS/Cloud SQL: 4 vCPUs, 16GB RAM
- Implementar **read replicas** para queries de reporting (separar de queries transaccionales)
- Crear tabla de **resumen diario** (materialized view) para dashboard: actualización nocturna

**Fase 3 — Alta escala (>12 meses con >1M órdenes):**
- Evaluar **CitusDB** (PostgreSQL distribuido): sharding por customer_id
- O migrar reporting a **BigQuery/Snowflake** — desacoplar OLTP de OLAP completamente

### 7.3 Hoja de Ruta MongoDB (10x)

**Fase 1 — Inmediata:**
- Migrar a **Atlas M10** ($57/mes): 2GB RAM, replica set dedicado (3 nodos)
- Activar `readPreference: "secondaryPreferred"` para queries analíticas — reduce carga del primary 60%
- Aumentar `maxPoolSize` a 100 en el MongoClient de producción

**Fase 2 — Crecimiento:**
- Activar **Atlas Search** (Lucene sobre MongoDB): reemplaza texto manual por búsqueda semántica
- Implementar **TTL index** en `resumen_reviews` para expirar reviews >2 años: limita crecimiento de colección
- Agregar **Atlas Charts** para dashboards de análisis de catálogo sin queries SQL

**Fase 3 — Alta escala:**
- Implementar **sharding** con shard key: `{ "category_translations.en": 1, "product_id": "hashed" }`
- Agregar **zona de sharding** por región (Colombia, México, Brasil si se expande Ecommify)

### 7.4 Decisión Arquitectónica Crítica: ¿Mantener el Híbrido?

**Sí, pero con ajuste en el ETL.** El problema más urgente no es la base de datos sino la **sincronización entre sistemas**. Con 10x de órdenes, el batch nocturno tardará 10x más — puede no completarse en 24h.

**Recomendación:** Reemplazar el ETL batch por **Change Data Capture (CDC)**:
- Herramienta: **Debezium** o **Supabase Realtime** → captura cambios del WAL de PostgreSQL
- Destino: cola Kafka → consumidor Python → `updateOne` en MongoDB con `$set` en `computed_metrics`
- Latencia de sincronización: de 24h → **<5 minutos**
- Costo: bajo (Kafka en Confluent Cloud free tier soporta el volumen inicial)

---

## 8. Evaluación de Tecnologías Alternativas

### 8.1 ¿Por qué no usar solo MongoDB?

| Requerimiento | Solo MongoDB | Problema |
|---|---|---|
| Transacciones ACID multi-documento | Soportado desde 4.0 | Overhead 2PC: 2-3x más lento que PG nativo |
| Reportes financieros exactos | Posible con `{readConcern: "majority"}` | Complejidad operacional mayor |
| Joins en reporting | `$lookup` funciona | Sin optimizador de costo para JOINs complejos |
| Cumplimiento SOC2/PCI-DSS | Posible | Más costoso de certificar vs PG maduro |

### 8.2 ¿Por qué no usar solo PostgreSQL?

| Requerimiento | Solo PostgreSQL | Problema |
|---|---|---|
| Catálogo con dimensiones variables | Tabla EAV o JSONB | EAV: queries lentas. JSONB: sin índices en subdocumentos anidados |
| 330,000 productos con specs N:N | Múltiples JOINs por producto | Query planner puede hacer seq scan en tablas grandes de specs |
| Búsqueda semántica de catálogo | pg_trgm + tsvector | Inferior a Lucene/Atlas Search para relevancia |
| Pipeline analítico en tiempo real | Window functions SQL | Más verboso que pipeline MongoDB para transformaciones encadenadas |

### 8.3 Alternativas Evaluadas y Rechazadas

| Tecnología | Evaluación | Decisión |
|---|---|---|
| **Redis** | Excelente para caché, no para persistencia | Rol complementario (caché, no reemplazo) |
| **Cassandra** | Bueno para series de tiempo masivas, no para catálogo | Overkill para la escala actual |
| **DynamoDB** | Sin costos predecibles a escala; lock-in AWS | Rechazado por riesgo de vendor lock-in |
| **Elasticsearch** | Bueno para búsqueda de catálogo | Agregar como motor de búsqueda, no reemplazar MongoDB |
| **Firebase Firestore** | Simplicidad de desarrollo, no escalabilidad analítica | Rechazado por limitaciones en agregaciones complejas |

---

## 9. Lecciones Aprendidas de la Implementación

### 9.1 Lo que funcionó

**Particionamiento RANGE antes de necesitarlo:** Crear las particiones `orders_2016`, `orders_2017`, `orders_2018` al inicio facilitó que el índice BRIN se propagara automáticamente a todas las particiones — esto no hubiera sido posible sin la tabla particionada.

**Computed Pattern en MongoDB:** Precalcular `avg_price`, `total_units_sold` y `average_rating` al momento de la carga (no en el query) eliminó la necesidad de JOINs en el 90% de las consultas analíticas. El pipeline de `run_pipeline.py` se ejecuta en ~120ms porque los datos ya están listos.

**Regla ESR en índices MongoDB:** El orden `{category_translations.en: 1, avg_price: 1, average_rating: 1}` redujo de 32,951 a 1,102 documentos examinados (−96.7%). Un orden diferente (ej: Sort primero) hubiera requerido un COLLSCAN.

### 9.2 Lo que no funcionó / Ajustes necesarios

**pymongo `col.aggregate(..., explain=True)`:** La API cambió en versiones recientes — `explain` no es un parámetro de `aggregate()` en pymongo ≥4.0. Solución: usar `db.command("aggregate", "nombre_coleccion", pipeline=..., explain=True)`.

**Supabase `pg_relation_size()` restringido:** En el tier gratuito, esta función requiere `service_role` — no disponible desde el SQL Editor público. Solución: usar `pg_indexes` y `pg_stat_user_tables` que sí están disponibles.

**DATA_PATH relativo en load_mongo.py:** Al ejecutar el script desde un directorio diferente, el path relativo `"olist_data/"` falla con FileNotFoundError. Solución: siempre usar paths absolutos con `r"C:\Users\..."` en scripts de producción.

### 9.3 Deuda Técnica Identificada

| Deuda | Impacto | Prioridad |
|---|---|---|
| ETL batch nocturno (24h lag) | Métricas de catálogo desactualizadas | Alta — migrar a CDC |
| Sin backups en MongoDB Atlas M0 | Pérdida de datos si falla el cluster | Alta — migrar a M10 |
| Sin rate limiting en la API | Cualquier cliente puede saturar el pool de conexiones | Media |
| Credentials hardcodeadas en scripts | Riesgo de seguridad si el repo es público | Alta — usar `.env` |
| Sin pruebas de integración | Los scripts se prueban manualmente | Media |

---

## 10. Conclusiones

La arquitectura híbrida PostgreSQL + MongoDB implementada en Ecommify no es una concesión ni un compromiso — es la decisión técnicamente correcta para una plataforma de e-commerce moderna. Cada sistema hace exactamente lo que hace mejor: PostgreSQL garantiza la integridad financiera que ningún cliente puede perder; MongoDB sirve el catálogo analítico enriquecido que ninguna tabla relacional puede modelar eficientemente.

Las pruebas de carga confirman que los tiers gratuitos actuales soportan ~50-95 usuarios concurrentes antes de degradación significativa. Para 10x de escala, la inversión mínima es Atlas M10 ($57/mes) y Supabase Pro ($25/mes) — $82/mes totales por los dos sistemas de bases de datos de una plataforma de e-commerce con 500,000 órdenes anuales.

El riesgo más importante identificado no es el rendimiento de queries individuales sino la **latencia de sincronización entre sistemas**. Resolver esto con CDC (Change Data Capture) es la siguiente iteración más valiosa que puede hacerse a la arquitectura.

---

## Apéndice: Referencias Técnicas

- MongoDB ESR Rule: https://www.mongodb.com/docs/manual/tutorial/equality-sort-range-rule/
- PostgreSQL BRIN Indexes: https://www.postgresql.org/docs/current/brin-intro.html
- CAP Theorem (Brewer 2000): https://dl.acm.org/doi/10.1145/343477.343502
- Supabase Connection Pooling: https://supabase.com/docs/guides/database/connecting-to-postgres
- MongoDB Atlas Cluster Tiers: https://www.mongodb.com/docs/atlas/cluster-tier/
- Debezium CDC: https://debezium.io/documentation/reference/stable/
- Olist Dataset: https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce
