export type Field =
  | "article"
  | "title"
  | "authors"
  | "date"
  | "summary"
  | "relative_date";

export interface Selection {
  nodeId: number;
  html: string;
  text: string;
}

export interface ExtractionOptions {
  /** When the HTML was fetched. Defaults to the current time. */
  scrapedAt?: string | Date;
}

export type ExtractionResult = Record<Field, Selection | null> & {
  scrapedAt: string;
  publishedAt: string | null;
  predictions: Record<Field, number | null>;
  diagnostics: {
    candidateCount: number;
    cleanupVersion: "chrome-v2";
    featureVersion: string;
    modelVersion: string;
    checkpointSha256: string;
    domBackend: "javascript";
  };
};

export const fields: readonly Field[];
export function extract(
  html: string,
  options?: ExtractionOptions,
): Promise<ExtractionResult>;
export function extractField(html: string, field: Field, options?: ExtractionOptions): Promise<Selection | null>;
export function extractRelativePublicationDate(text?: string | null): string | null;
export function resolveRelativeDate(
  relativeDate: string,
  scrapedAt: string | Date,
): string;
