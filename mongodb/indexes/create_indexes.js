// ============================================================
// Índices MongoDB — Guía 5 Ecommify
// Regla ESR: Equality → Sort → Range
// Ejecutar en MongoDB Shell (mongosh) o en Compass > Shell
// ============================================================

use("ecommify_analytics");

// ============================================================
// PASO 0: Capturar .explain() ANTES de crear índices
// ============================================================

// Query de referencia — guardar output como evidencia "ANTES"
print("=== EXPLAIN ANTES (COLLSCAN esperado) ===");
const explainAntes = db.catalogo_enriquecido.find({
    "category_translations.en": "computers_accessories",
    "computed_metrics.average_rating": { $gte: 4.0 }
}).sort({ "computed_metrics.avg_price": 1 }).explain("executionStats");

print("Stage:", explainAntes.queryPlanner.winningPlan.stage);
print("Docs examinados:", explainAntes.executionStats.totalDocsExamined);
print("Tiempo (ms):", explainAntes.executionStats.executionTimeMillis);

// ============================================================
// PASO 1: Crear índices en catalogo_enriquecido
// ============================================================

// 1. COMPUESTO ESR: Equality(category_en) → Sort(avg_price) → Range(average_rating)
// Justificación: la mayoría de queries filtra por categoría primero,
// luego ordena por precio, y opcionalmente filtra por rating mínimo.
db.catalogo_enriquecido.createIndex(
    {
        "category_translations.en": 1,
        "computed_metrics.avg_price": 1,
        "computed_metrics.average_rating": 1
    },
    { name: "idx_category_price_rating_ESR" }
);
print("✅ Creado: idx_category_price_rating_ESR");

// 2. PARCIAL: Solo productos con rating >= 4.0
// Justificación: ~75% de productos tienen rating < 4. El índice parcial
// cubre solo el subconjunto relevante, reduciendo tamaño y mejorando cache.
db.catalogo_enriquecido.createIndex(
    { "computed_metrics.average_rating": -1 },
    {
        partialFilterExpression: { "computed_metrics.average_rating": { $gte: 4.0 } },
        name: "idx_high_rating_partial"
    }
);
print("✅ Creado: idx_high_rating_partial");

// 3. TEXTO: Búsqueda full-text en categorías (portugués + inglés)
// Justificación: permite búsquedas como db.find({$text: {$search: "eletronicos"}})
// sin necesidad de regex (más eficiente que COLLSCAN con ILIKE).
db.catalogo_enriquecido.createIndex(
    {
        "category_translations.pt": "text",
        "category_translations.en": "text"
    },
    {
        name: "idx_category_text",
        default_language: "portuguese",
        weights: { "category_translations.pt": 2, "category_translations.en": 1 }
    }
);
print("✅ Creado: idx_category_text");

// 4. COMPUESTO en specifications (Attribute Pattern) — ESR
// Justificación: el Attribute Pattern almacena dimensiones como [{k, v}].
// Este índice permite queries eficientes como: find({"specifications.k":"weight_g","specifications.v":{$gt:500}})
db.catalogo_enriquecido.createIndex(
    { "specifications.k": 1, "specifications.v": 1 },
    { name: "idx_specifications_kv_ESR" }
);
print("✅ Creado: idx_specifications_kv_ESR");

// ============================================================
// PASO 2: Crear índices en resumen_reviews
// ============================================================

// 5. COMPUESTO ESR: Equality(product_id) → Sort(bucket_index) → Range(count)
// Justificación: todas las queries acceden por product_id, luego paginan
// por bucket_index, y a veces filtran buckets con count mínimo.
db.resumen_reviews.createIndex(
    { "product_id": 1, "bucket_index": 1, "count": 1 },
    { name: "idx_reviews_product_bucket_ESR" }
);
print("✅ Creado: idx_reviews_product_bucket_ESR");

// 6. PARCIAL: Solo buckets que contienen alguna review negativa (score <= 2)
// Justificación: análisis de calidad y alertas solo necesitan el subconjunto
// de reviews negativas — el índice parcial evita escanear reviews positivas.
db.resumen_reviews.createIndex(
    { "product_id": 1, "avg_score": 1 },
    {
        partialFilterExpression: { "avg_score": { $lte: 2.5 } },
        name: "idx_negative_reviews_partial"
    }
);
print("✅ Creado: idx_negative_reviews_partial");

// ============================================================
// PASO 3: Capturar .explain() DESPUÉS de crear índices
// ============================================================

print("\n=== EXPLAIN DESPUÉS (IXSCAN esperado) ===");
const explainDespues = db.catalogo_enriquecido.find({
    "category_translations.en": "computers_accessories",
    "computed_metrics.average_rating": { $gte: 4.0 }
}).sort({ "computed_metrics.avg_price": 1 }).explain("executionStats");

print("Stage:", explainDespues.queryPlanner.winningPlan.inputStage?.stage || explainDespues.queryPlanner.winningPlan.stage);
print("Docs examinados:", explainDespues.executionStats.totalDocsExamined);
print("Tiempo (ms):", explainDespues.executionStats.executionTimeMillis);

// ============================================================
// PASO 4: Verificar todos los índices creados
// ============================================================

print("\n=== ÍNDICES EN catalogo_enriquecido ===");
db.catalogo_enriquecido.getIndexes().forEach(idx => {
    print(`  ${idx.name}: ${JSON.stringify(idx.key)}`);
});

print("\n=== ÍNDICES EN resumen_reviews ===");
db.resumen_reviews.getIndexes().forEach(idx => {
    print(`  ${idx.name}: ${JSON.stringify(idx.key)}`);
});

// ============================================================
// PASO 5: Estadísticas de uso de índices (ejecutar DESPUÉS de correr queries)
// ============================================================

print("\n=== ESTADÍSTICAS DE USO (ejecutar después de varias queries) ===");
print("db.catalogo_enriquecido.aggregate([{ $indexStats: {} }])");
