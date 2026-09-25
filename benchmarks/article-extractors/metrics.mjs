export const FIELDS = ["title", "body", "date", "byline"];

export function normalizedText(value) {
  return (value ?? "").normalize("NFKC").toLocaleLowerCase("en")
    .replace(/\s+/gu, " ").trim();
}

function comparableText(value, field) {
  const text = normalizedText(value);
  if (field !== "byline") return text;
  return text.replace(/^(?:(?:written|authored)\s+by|by|authors?)\s*[:\-]?\s*/u, "");
}

function tokens(value, field) {
  return comparableText(value, field).match(/[\p{L}\p{N}]+/gu) ?? [];
}

export function tokenF1(expected, actual, field) {
  if (!expected && !actual) return 1;
  if (!expected || !actual) return 0;
  const gold = tokens(expected, field);
  const predicted = tokens(actual, field);
  if (!gold.length && !predicted.length) return 1;
  if (!gold.length || !predicted.length) return 0;
  const counts = new Map();
  for (const token of gold) counts.set(token, (counts.get(token) ?? 0) + 1);
  let overlap = 0;
  for (const token of predicted) {
    const remaining = counts.get(token) ?? 0;
    if (remaining > 0) {
      overlap++;
      counts.set(token, remaining - 1);
    }
  }
  return 2 * overlap / (gold.length + predicted.length);
}

const MONTHS = new Map([
  "jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec",
].map((month, index) => [month, index + 1]));

export function canonicalDate(value, scrapedAt) {
  const raw = normalizedText(value);
  if (!raw) return null;
  const iso = /\b(\d{4})-(\d{1,2})-(\d{1,2})(?=$|[^\d])/u.exec(raw);
  if (iso) return validDate(+iso[1], +iso[2], +iso[3]);
  const names = "jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?";
  const monthFirst = new RegExp(`\\b(${names})\\.?\\s+(\\d{1,2})(?:st|nd|rd|th)?[,]?[\\s]+(\\d{4})\\b`, "u").exec(raw);
  if (monthFirst) return validDate(+monthFirst[3], MONTHS.get(monthFirst[1].slice(0, 3)), +monthFirst[2]);
  const dayFirst = new RegExp(`\\b(\\d{1,2})(?:st|nd|rd|th)?[\\s]+(${names})\\.?[,]?[\\s]+(\\d{4})\\b`, "u").exec(raw);
  if (dayFirst) return validDate(+dayFirst[3], MONTHS.get(dayFirst[2].slice(0, 3)), +dayFirst[1]);
  if (scrapedAt) {
    const relative = /\b(\d+)\s+(day|week)s?\s+ago\b/u.exec(raw);
    if (relative) {
      const date = new Date(scrapedAt);
      if (!Number.isNaN(date.getTime())) {
        date.setUTCDate(date.getUTCDate() - Number(relative[1]) * (relative[2] === "week" ? 7 : 1));
        return date.toISOString().slice(0, 10);
      }
    }
  }
  return null;
}

function validDate(year, month, day) {
  if (!year || !month || !day) return null;
  const date = new Date(Date.UTC(year, month - 1, day));
  return date.getUTCFullYear() === year && date.getUTCMonth() === month - 1 && date.getUTCDate() === day
    ? date.toISOString().slice(0, 10) : null;
}

export function fieldScore(expected, actual, field, scrapedAt) {
  if (!expected && !actual) return 1;
  if (!expected || !actual) return 0;
  if (field === "date") {
    const goldDate = canonicalDate(expected, scrapedAt);
    const predictedDate = canonicalDate(actual, scrapedAt);
    if (goldDate && predictedDate && goldDate === predictedDate) return 1;
  }
  return tokenF1(expected, actual, field);
}

export function exactMatch(expected, actual, field, scrapedAt) {
  if (!expected && !actual) return true;
  if (!expected || !actual) return false;
  if (field === "date") {
    const goldDate = canonicalDate(expected, scrapedAt);
    const predictedDate = canonicalDate(actual, scrapedAt);
    if (goldDate && predictedDate) return goldDate === predictedDate;
  }
  return comparableText(expected, field) === comparableText(actual, field);
}

export function percentile(values, fraction) {
  if (!values.length) return null;
  const sorted = [...values].sort((a, b) => a - b);
  return sorted[Math.max(0, Math.ceil(sorted.length * fraction) - 1)];
}
