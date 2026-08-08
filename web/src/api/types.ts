// Named aliases over the generated OpenAPI types - pages import these, never
// the raw paths object. Regenerate schema.d.ts with `npm run typegen`.
import type { components } from "./schema";

export type Meta = components["schemas"]["Meta"];
export type Desk = components["schemas"]["Desk"];
export type RiskSummary = components["schemas"]["RiskSummary"];
export type DeskRisk = components["schemas"]["DeskRisk"];
export type RiskHistory = components["schemas"]["RiskHistory"];
export type HistoryPoint = components["schemas"]["HistoryPoint"];
export type RiskMovers = components["schemas"]["RiskMovers"];
export type MoverRow = components["schemas"]["MoverRow"];
export type KeyRateExposures = components["schemas"]["KeyRateExposures"];
export type DeskDecomposition = components["schemas"]["DeskDecomposition"];
export type DeskBucket = components["schemas"]["DeskBucket"];
export type DeskExposure = components["schemas"]["DeskExposure"];
export type DeskPositions = components["schemas"]["DeskPositions"];
export type DeskPosition = components["schemas"]["DeskPosition"];
export type BacktestSummary = components["schemas"]["BacktestSummary"];
export type PlaSummary = components["schemas"]["PlaSummary"];
export type ScenarioCatalog = components["schemas"]["ScenarioCatalog"];
export type ScenarioResults = components["schemas"]["ScenarioResults"];
export type ModelDoc = components["schemas"]["ModelDoc"];
