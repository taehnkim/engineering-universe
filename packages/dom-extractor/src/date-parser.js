// Calendar-date parsing for selected publication nodes. Never infer a day from
// a month-only label, a reading time, or an ambiguous numeric date.
const MONTHS = new Map([
  "jan", "feb", "mar", "apr", "may", "jun",
  "jul", "aug", "sep", "oct", "nov", "dec",
].map((name, index) => [name, index + 1]));
const MONTH = "(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|" +
  "Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)";
const PATTERNS = [
  { kind: "iso", regex: /\b((?:19|20)\d{2})[-/.](\d{1,2})[-/.](\d{1,2})(?=$|[^\d])/gu },
  { kind: "month-first", regex: new RegExp(`\\b(${MONTH})\\.?\\s+(\\d{1,2})(?:st|nd|rd|th)?[,]?[\\s]+((?:19|20)\\d{2})\\b`, "giu") },
  { kind: "day-first", regex: new RegExp(`\\b(\\d{1,2})(?:st|nd|rd|th)?\\s+(${MONTH})\\.?[,]?[\\s]+((?:19|20)\\d{2})\\b`, "giu") },
  { kind: "numeric", regex: /\b(\d{1,2})\/(\d{1,2})\/((?:19|20)?\d{2})\b/gu },
];

function validDate(year, month, day) {
  if (year < 1900 || year > 2099 || month < 1 || month > 12 || day < 1 || day > 31) return null;
  const date = new Date(Date.UTC(year, month - 1, day));
  if (date.getUTCFullYear() !== year || date.getUTCMonth() !== month - 1 ||
      date.getUTCDate() !== day) return null;
  return date.toISOString().slice(0, 10);
}

export function publicationDates(value) {
  if (typeof value !== "string" || !value) return [];
  const dates = new Map();
  for (const { kind, regex } of PATTERNS) {
    for (const match of value.matchAll(regex)) {
      let year;
      let month;
      let day;
      if (kind === "iso") {
        [, year, month, day] = match;
      } else if (kind === "month-first") {
        month = MONTHS.get(match[1].slice(0, 3).toLowerCase());
        day = Number(match[2]);
        year = Number(match[3]);
      } else if (kind === "day-first") {
        day = Number(match[1]);
        month = MONTHS.get(match[2].slice(0, 3).toLowerCase());
        year = Number(match[3]);
      } else {
        const first = Number(match[1]);
        const second = Number(match[2]);
        if (first > 12 && second <= 12) [day, month] = [first, second];
        else if (second > 12 && first <= 12) [month, day] = [first, second];
        else if (/\ble\s*$/iu.test(value.slice(0, match.index)) &&
                 /^\s+par\b/iu.test(value.slice(match.index + match[0].length))) {
          // A French “le DD/MM/YYYY par …” byline supplies the missing locale.
          [day, month] = [first, second];
        } else continue; // 09/11/2026 has no locale-independent interpretation.
        year = Number(match[3]);
        if (match[3].length === 2) year += year < 50 ? 2000 : 1900;
      }
      const iso = validDate(Number(year), Number(month), Number(day));
      if (iso && !dates.has(iso)) dates.set(iso, { iso, raw: match[0] });
    }
  }
  return [...dates.values()];
}

export function parsePublicationDate(value) {
  const dates = publicationDates(value);
  return dates.length === 1 ? dates[0] : null;
}
