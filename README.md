# Ecommify Database Design — Unidad 5

Proyecto académico: **Optimización de Rendimiento en MongoDB y PostgreSQL**  
Universidad de la Sabana | Asignatura: Optimización de rendimiento en MongoDB

**Equipo:** Beycy Yuliet Rojas Acero · Manuel Fernando Santofimio Tovar · Jorge Ivan Figueroa Torres · Jose Antonio Leao Ferrer · David Felipe Cifuentes Villa

---

## Arquitectura

Plataforma multi-vendor **Ecommify** con arquitectura híbrida:
- **PostgreSQL / Supabase** — módulo transaccional (órdenes, clientes, pagos)
- **MongoDB Atlas M0** — módulo analítico (catálogo enriquecido, reviews)
- **Dataset:** Brazilian E-Commerce Public Dataset (Olist) — 32,951 productos, 99,224 órdenes

---

## Estructura del Repositorio

```
├── postgresql/
│   └── indexes/
│       └── brin_index.sql          # Índice BRIN en Supabase (order_purchase_timestamp)
├── mongodb/
│   ├── indexes/
│   │   └── create_indexes.js       # 6 índices ESR, parciales y de texto
│   ├── pipelines/
│   │   └── analytics_pipeline.js  # Pipeline de agregación (6 stages)
│   └── scripts/
│       ├── load_mongo.py           # Carga datos Olist → MongoDB Atlas
│       ├── setup_indexes.py        # Crea índices + captura .explain()
│       └── run_pipeline.py         # Ejecuta pipeline + mide rendimiento
└── evidence/
    └── mongodb/
        ├── explain_antes.json      # .explain() antes de índices (COLLSCAN)
        ├── explain_despues.json    # .explain() después de índices (IXSCAN)
        └── pipeline_resultado.json # Resultado del pipeline de agregación
```

---

## Configuración

### Requisitos
```
Python 3.8+
pip install pymongo pandas
```

### PostgreSQL / Supabase
1. Conectar al proyecto Supabase: `aklzgzygjfxznpkkytae.supabase.co`
2. Abrir **SQL Editor** y ejecutar `postgresql/indexes/brin_index.sql`

### MongoDB Atlas
1. Reemplazar la URI en los scripts de `mongodb/scripts/`:
   ```python
   MONGODB_URI = "mongodb+srv://USER:PASSWORD@ecommifycluster.dbpeigv.mongodb.net/"
   ```
2. Ejecutar en orden:
   ```bash
   python mongodb/scripts/load_mongo.py      # 1. Cargar datos
   python mongodb/scripts/setup_indexes.py   # 2. Crear índices
   python mongodb/scripts/run_pipeline.py    # 3. Ejecutar pipeline
   ```

---

## Resultados de Optimización

### MongoDB — Índices

| Métrica | Sin índice | Con índice | Mejora |
|---|---|---|---|
| Stage | COLLSCAN | IXSCAN | ✅ Usa `idx_category_price_rating_ESR` |
| Tiempo de ejecución | 24 ms | 6 ms | **75% más rápido** |
| Documentos examinados | 32,951 | 1,102 | **−96.7%** |

### MongoDB — Índices implementados

| Nombre | Tipo | Colección | Justificación |
|---|---|---|---|
| `idx_category_price_rating_ESR` | Compuesto (ESR) | catalogo_enriquecido | Equality→Sort→Range |
| `idx_high_rating_partial` | Parcial | catalogo_enriquecido | Solo rating ≥ 4.0 |
| `idx_category_text` | Texto | catalogo_enriquecido | Full-text search |
| `idx_specifications_kv_ESR` | Compuesto | catalogo_enriquecido | Attribute Pattern |
| `idx_reviews_product_bucket_ESR` | Compuesto (ESR) | resumen_reviews | Bucket Pattern |
| `idx_negative_reviews_partial` | Parcial | resumen_reviews | Reviews negativas |

### MongoDB — Pipeline de Agregación (6 stages)

```
$match → $project → $group → $addFields → $sort → $facet
```

**Resultados:** 71 categorías analizadas | 111,023 unidades totales  
**Top categoría:** bed_bath_table (score: 1,188.30)  
**Rating global promedio:** 4.02

### PostgreSQL — Índice BRIN

Creado en `orders(order_purchase_timestamp)` — se propagó automáticamente a todas las particiones (`orders_2016`, `orders_2017`, `orders_2018`).

**Ventaja:** ~1000x más pequeño que B-tree para columnas de series de tiempo con alta correlación física.

---

## Patrones de Diseño MongoDB

| Patrón | Colección | Implementación |
|---|---|---|
| **Embedded** | catalogo_enriquecido | `category_translations` dentro del documento |
| **Attribute** | catalogo_enriquecido | `specifications` como array `[{k, v}]` |
| **Computed** | catalogo_enriquecido | `computed_metrics` precalculadas |
| **Bucket** | resumen_reviews | Grupos de 5 reviews por documento |
