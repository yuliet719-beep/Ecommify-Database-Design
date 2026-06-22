// ============================================================
// Pipeline de Agregación — Guía 5 Ecommify
// "Análisis de rendimiento por categoría de producto"
// 6 stages — supera el requisito mínimo de 5
// Ejecutar en MongoDB Shell (mongosh) o Compass > Aggregations
// ============================================================

use("ecommify_analytics");

// ============================================================
// PARTE A: Medir rendimiento ANTES de crear índices
// Ejecutar primero con la colección SIN el índice idx_category_price_rating_ESR
// ============================================================

print("=== EXPLAIN ANTES (sin índice en category_translations.en) ===");
const planAntes = db.catalogo_enriquecido.explain("executionStats").aggregate([
    { $match: { "computed_metrics.total_units_sold": { $gt: 0 } } },
    { $project: { category_en: "$category_translations.en", units_sold: "$computed_metrics.total_units_sold", avg_price: "$computed_metrics.avg_price" } },
    { $group: { _id: "$category_en", total_units: { $sum: "$units_sold" }, avg_price: { $avg: "$avg_price" } } },
    { $sort: { total_units: -1 } }
]);
print("Tiempo (ms):", planAntes.stages?.[0]?.["$cursor"]?.executionStats?.executionTimeMillis ?? "N/A");
print("Docs examinados:", planAntes.stages?.[0]?.["$cursor"]?.executionStats?.totalDocsExamined ?? "N/A");

// ============================================================
// PARTE B: Pipeline principal completo (6 stages)
// ============================================================

print("\n=== PIPELINE COMPLETO (6 stages con allowDiskUse) ===");

const resultado = db.catalogo_enriquecido.aggregate([

    // ── Stage 1: $match ──────────────────────────────────────
    // Filtrado temprano — reduce el conjunto de documentos antes de cualquier
    // transformación costosa. Aprovecha idx_category_price_rating_ESR.
    {
        $match: {
            "computed_metrics.total_units_sold": { $gt: 0 },
            "category_translations.en": { $nin: [null, "", "nan"] }
        }
    },

    // ── Stage 2: $project ────────────────────────────────────
    // Proyección temprana — elimina campos innecesarios ANTES del $group.
    // Reduce el tamaño del pipeline en memoria (~60% menos datos a procesar).
    {
        $project: {
            _id: 0,
            product_id: 1,
            category_en: "$category_translations.en",
            units_sold:  "$computed_metrics.total_units_sold",
            avg_rating:  "$computed_metrics.average_rating",
            avg_price:   "$computed_metrics.avg_price"
        }
    },

    // ── Stage 3: $group ──────────────────────────────────────
    // Agrupación por categoría — calcula métricas agregadas.
    {
        $group: {
            _id:                  "$category_en",
            total_products:       { $sum: 1 },
            total_units:          { $sum: "$units_sold" },
            avg_category_price:   { $avg: "$avg_price" },
            avg_category_rating:  { $avg: "$avg_rating" },
            max_rating:           { $max: "$avg_rating" }
        }
    },

    // ── Stage 4: $addFields ──────────────────────────────────
    // Calcula un score de rendimiento compuesto.
    // Fórmula: rating_score (0–100) + volumen_score (escala relativa)
    {
        $addFields: {
            performance_score: {
                $round: [
                    {
                        $add: [
                            { $multiply: ["$avg_category_rating", 20] },
                            { $divide: ["$total_units", 10] }
                        ]
                    },
                    2
                ]
            },
            category_name: "$_id"
        }
    },

    // ── Stage 5: $sort ───────────────────────────────────────
    // Ordenar por performance_score descendente.
    {
        $sort: { performance_score: -1, total_units: -1 }
    },

    // ── Stage 6: $facet ──────────────────────────────────────
    // Análisis multidimensional en una sola pasada sobre los documentos ya agrupados.
    // Sin $facet necesitaríamos 2 queries separadas.
    {
        $facet: {
            "top_10_categorias": [
                { $limit: 10 },
                {
                    $project: {
                        _id: 0,
                        categoria: "$category_name",
                        productos:   "$total_products",
                        unidades:    "$total_units",
                        precio_prom: "$avg_category_price",
                        rating_prom: "$avg_category_rating",
                        score:       "$performance_score"
                    }
                }
            ],
            "estadisticas_globales": [
                {
                    $group: {
                        _id:           null,
                        total_categorias:  { $sum: 1 },
                        precio_global_prom: { $avg: "$avg_category_price" },
                        rating_global_prom: { $avg: "$avg_category_rating" },
                        total_unidades:     { $sum: "$total_units" }
                    }
                },
                { $project: { _id: 0 } }
            ],
            "categorias_bajo_rendimiento": [
                { $match: { performance_score: { $lt: 80 } } },
                { $limit: 5 },
                {
                    $project: {
                        _id: 0,
                        categoria: "$category_name",
                        score: "$performance_score",
                        rating_prom: "$avg_category_rating"
                    }
                }
            ]
        }
    }

], { allowDiskUse: true });

printjson(resultado.toArray()[0]);

// ============================================================
// PARTE C: Medir rendimiento DESPUÉS de crear índices
// Ejecutar después de correr create_indexes.js
// ============================================================

print("\n=== EXPLAIN DESPUÉS (con idx_category_price_rating_ESR activo) ===");
const planDespues = db.catalogo_enriquecido.explain("executionStats").aggregate([
    { $match: { "computed_metrics.total_units_sold": { $gt: 0 } } },
    { $project: { category_en: "$category_translations.en", units_sold: "$computed_metrics.total_units_sold", avg_price: "$computed_metrics.avg_price" } },
    { $group: { _id: "$category_en", total_units: { $sum: "$units_sold" }, avg_price: { $avg: "$avg_price" } } },
    { $sort: { total_units: -1 } }
]);
print("Tiempo (ms):", planDespues.stages?.[0]?.["$cursor"]?.executionStats?.executionTimeMillis ?? "N/A");
print("Docs examinados:", planDespues.stages?.[0]?.["$cursor"]?.executionStats?.totalDocsExamined ?? "N/A");

// ============================================================
// PARTE D: Query adicional con índice de texto
// ============================================================

print("\n=== BÚSQUEDA DE TEXTO (requiere idx_category_text) ===");
const textResults = db.catalogo_enriquecido.find(
    { $text: { $search: "eletronicos computador" } },
    { score: { $meta: "textScore" }, category_translations: 1, computed_metrics: 1 }
).sort({ score: { $meta: "textScore" } }).limit(10);

print("Resultados encontrados:", textResults.toArray().length);

// ============================================================
// PARTE E: Monitoreo — estadísticas de índices
// ============================================================

print("\n=== ESTADÍSTICAS DE USO DE ÍNDICES ===");
db.catalogo_enriquecido.aggregate([{ $indexStats: {} }]).forEach(stat => {
    print(`  ${stat.name}: ${stat.accesses.ops} operaciones`);
});
