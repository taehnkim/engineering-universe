export type Field =
  | "article"
  | "title"
  | "authors"
  | "date";

export interface Selection {
  nodeId: number;
  html: string;
  text: string;
  /** Uncalibrated softmax share for the final node. */
  confidence: number;
}

export interface ExtractionOptions {
  /** Include preprocessing and model metadata in the result. Defaults to false. */
  debug?: boolean;
}

export interface DebugInfo {
  candidateCount: number;
  cleanupVersion: string;
  featureVersion: string;
  modelVersion: string;
  checkpointSha256: string;
  domBackend: string;
}

export type ExtractionResult = Record<Field, Selection | null>;
export type DebugExtractionResult = ExtractionResult & { debug: DebugInfo };

export const fields: readonly Field[];
export function extract(
  html: string,
  options: ExtractionOptions & { debug: true },
): Promise<DebugExtractionResult>;
export function extract(
  html: string,
  options?: { debug?: false },
): Promise<ExtractionResult>;
export function extract(
  html: string,
  options: ExtractionOptions,
): Promise<ExtractionResult | DebugExtractionResult>;
export function extractField(html: string, field: Field, options?: ExtractionOptions): Promise<Selection | null>;
export function extractRelativePublicationDate(text?: string | null): string | null;
export function resolveRelativeDate(
  relativeDate: string,
  scrapedAt: string | Date,
): string;
