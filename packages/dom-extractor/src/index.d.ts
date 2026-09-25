export type Field = "article" | "title" | "authors" | "date";
export type OutputFormat = "text" | "html";

/** The original flat response. Kept for existing extract(html) callers. */
export interface Selection {
  nodeId: number;
  html: string;
  text: string;
  /** Uncalibrated softmax share for the final node. */
  confidence: number;
}

export interface VersionedSelection {
  /** Original DOM node ID, presented as a one-based integer. */
  id: number | null;
  /** Extracted text. Present when the text format is requested. */
  value?: string;
  /** Original displayed date when a datetime attribute supplies an ISO value. */
  raw?: string;
  /** Selected-node HTML. Present only when the html format is requested. */
  html?: string;
  /** CSS path in the parsed source document; null for a missing author. */
  selector: string | null;
  /** Uncalibrated softmax share rounded to four decimal places. */
  confidence: number;
}

export interface VersionedAuthorSelection extends VersionedSelection {
  /** The selected author's byline as a string. A missing author is null. */
  value: string;
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

export interface VersionedExtractionResult {
  type: "article";
  schemaVersion: "1.0.0";
  modelVersion: string;
  sourceUrl: string | null;
  fields: Partial<{
    article: VersionedSelection | null;
    title: VersionedSelection | null;
    authors: VersionedAuthorSelection | null;
    date: VersionedSelection | null;
  }>;
  debug?: DebugInfo;
}

export interface ExtractionOptions {
  debug?: boolean;
  version?: "legacy" | "1.0.0";
  fields?: readonly Field[];
  formats?: readonly OutputFormat[];
  /** Explicit page URL. Overrides canonical and Open Graph URLs in the HTML. */
  sourceUrl?: string | null;
}

export const fields: readonly Field[];
export const schemaVersion: "1.0.0";

export function extract(
  html: string,
  options: ExtractionOptions & { version: "1.0.0" },
): Promise<VersionedExtractionResult>;
export function extract(
  html: string,
  options: ExtractionOptions & { formats: readonly OutputFormat[] },
): Promise<VersionedExtractionResult>;
export function extract(
  html: string,
  options: ExtractionOptions & { fields: readonly Field[] },
): Promise<VersionedExtractionResult>;
export function extract(
  html: string,
  options: ExtractionOptions & { sourceUrl: string | null },
): Promise<VersionedExtractionResult>;
export function extract(html: string, options: { debug: true; version?: "legacy" }): Promise<DebugExtractionResult>;
export function extract(html: string, options?: { debug?: false; version?: "legacy" }): Promise<ExtractionResult>;
export function extract(html: string, options: ExtractionOptions): Promise<ExtractionResult | DebugExtractionResult | VersionedExtractionResult>;

export function extractField(
  html: string, field: "authors", options: ExtractionOptions & { version: "1.0.0" },
): Promise<VersionedAuthorSelection | null>;
export function extractField(
  html: string, field: Field, options: ExtractionOptions & { version: "1.0.0" },
): Promise<VersionedSelection | null>;
export function extractField(html: string, field: Field, options?: ExtractionOptions): Promise<Selection | VersionedSelection | null>;

export function extractRelativePublicationDate(text?: string | null): string | null;
export function resolveRelativeDate(relativeDate: string, scrapedAt: string | Date): string;
