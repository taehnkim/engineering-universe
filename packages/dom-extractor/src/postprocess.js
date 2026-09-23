const NUMBER_WORDS = new Map([
  ["a", 1],
  ["an", 1],
  ["one", 1],
  ["two", 2],
  ["three", 3],
  ["four", 4],
  ["five", 5],
  ["six", 6],
  ["seven", 7],
  ["eight", 8],
  ["nine", 9],
  ["ten", 10],
]);

const RELATIVE_PUBLICATION_RE = /\b(?<count>an?|one|two|three|four|five|six|seven|eight|nine|ten|\d+)\s+(?<unit>minute|hour|day|week|month|year)s?\s+ago\b/i;
const READING_TIME_RE = /^\s*(?:an?|one|two|three|four|five|six|seven|eight|nine|ten|\d+)\s*(?:min(?:ute)?s?|hours?)\s+(?:read|reading)\s*$/i;

export function normalizeScrapedAt(value) {
  const parsed = value instanceof Date ? new Date(value) : new Date(value);
  if (Number.isNaN(parsed.valueOf())) throw new TypeError(`invalid scrapedAt: ${value}`);
  return parsed.toISOString();
}

export function extractRelativePublicationDate(text) {
  if (!text || READING_TIME_RE.test(text)) return null;
  return text.match(RELATIVE_PUBLICATION_RE)?.[0] ?? null;
}

function subtractMonths(value, months) {
  const result = new Date(value);
  const day = result.getUTCDate();
  result.setUTCDate(1);
  result.setUTCMonth(result.getUTCMonth() - months);
  const lastDay = new Date(
    Date.UTC(result.getUTCFullYear(), result.getUTCMonth() + 1, 0),
  ).getUTCDate();
  result.setUTCDate(Math.min(day, lastDay));
  return result;
}

export function resolveRelativeDate(relativeDate, scrapedAt) {
  const phrase = extractRelativePublicationDate(relativeDate);
  if (!phrase) throw new TypeError(`not a relative publication date: ${relativeDate}`);
  const match = phrase.match(RELATIVE_PUBLICATION_RE);
  const rawCount = match.groups.count.toLowerCase();
  const count = /^\d+$/.test(rawCount) ? Number(rawCount) : NUMBER_WORDS.get(rawCount);
  const unit = match.groups.unit.toLowerCase();
  const scraped = new Date(normalizeScrapedAt(scrapedAt));
  let published;
  if (unit === "month") published = subtractMonths(scraped, count);
  else if (unit === "year") published = subtractMonths(scraped, count * 12);
  else {
    const unitMilliseconds = {
      minute: 60_000,
      hour: 3_600_000,
      day: 86_400_000,
      week: 604_800_000,
    };
    published = new Date(scraped.valueOf() - count * unitMilliseconds[unit]);
  }
  return published.toISOString();
}
