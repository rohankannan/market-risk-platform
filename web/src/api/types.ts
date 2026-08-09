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
export type FactorsLatest = components["schemas"]["FactorsLatest"];
export type FactorTick = components["schemas"]["FactorTick"];
export type DeskDecomposition = components["schemas"]["DeskDecomposition"];
export type DeskBucket = components["schemas"]["DeskBucket"];
export type DeskExposure = components["schemas"]["DeskExposure"];
export type DeskPositions = components["schemas"]["DeskPositions"];
export type DeskPosition = components["schemas"]["DeskPosition"];
export type BacktestSummary = components["schemas"]["BacktestSummary"];
export type LRTest = components["schemas"]["LRTest"];
export type PlaSummary = components["schemas"]["PlaSummary"];
export type PlaPoint = components["schemas"]["PlaPoint"];
export type ScenarioCatalog = components["schemas"]["ScenarioCatalog"];
export type ScenarioSpec = components["schemas"]["ScenarioSpec"];
export type ScenarioShock = components["schemas"]["ScenarioShock"];
export type ScenarioResults = components["schemas"]["ScenarioResults"];
export type ScenarioResult = components["schemas"]["ScenarioResult"];
export type ModelDoc = components["schemas"]["ModelDoc"];
export type WhatIfResult = components["schemas"]["WhatIfResult"];
export type WhatIfDesk = components["schemas"]["WhatIfDesk"];
export type WhatIfShock = components["schemas"]["WhatIfShock"];
export type ScenarioShockVector = components["schemas"]["ScenarioShockVector"];
