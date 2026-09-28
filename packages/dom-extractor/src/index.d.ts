export type Include = "html" | "source" | "debug";
export type ExtractErrorCode = "invalidInput" | "emptyInput" | "inputTooLarge" |
  "parseError" | "inferenceError" | "internalError";

export interface ExtractOptions {
  include?: readonly Include[];
}

export interface ExtractedField {
  /** Plain text from the selected node, or null below the internal threshold. */
  text: string | null;
  /** Uncalibrated candidate score, rounded to four decimal places. */
  confidence: number;
  /** Exact substring from input HTML. Present only with include: ["html"] and non-null text. */
  html?: string;
  /** Unique selector into the input document. Present only with include: ["source"]. */
  source?: { selector: string };
}

export interface ExtractedDateField extends ExtractedField {
  /** Calendar date in YYYY-MM-DD form when unambiguous; otherwise null. */
  iso: string | null;
}

export interface ExtractedFields {
  title: ExtractedField;
  body: ExtractedField;
  date: ExtractedDateField;
  byline: ExtractedField;
}

export interface ExtractionResult {
  modelVersion: string;
  fields: ExtractedFields;
  debug?: { rejected: Partial<Record<keyof ExtractedFields, { text: string }>> };
}

export interface BatchItemResult {
  status: "ok";
  result: Omit<ExtractionResult, "modelVersion">;
}

export interface BatchItemError {
  status: "error";
  error: { code: ExtractErrorCode; message: string };
}

export interface BatchResult {
  modelVersion: string;
  results: (BatchItemResult | BatchItemError)[];
}

export class ExtractError extends Error {
  readonly code: ExtractErrorCode;
  constructor(code: ExtractErrorCode, message: string);
}

export const modelVersion: string;
export function extract(html: string, options?: ExtractOptions): Promise<ExtractionResult>;
export function extractMany(htmls: string[], options?: ExtractOptions): Promise<BatchResult>;
