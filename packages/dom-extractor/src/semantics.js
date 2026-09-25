import { elementText } from "./dom.js";

export const DATE_LIKE_RE = /(?:\b(?:19|20)\d{2}[-/.](?:0?[1-9]|1[0-2])[-/.](?:0?[1-9]|[12]\d|3[01])\b)|(?:\b(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\.?\s+\d{1,2}(?:st|nd|rd|th)?,?\s+(?:19|20)\d{2}\b)/i;
export const RELATIVE_DATE_RE = /\b(?:an?|one|two|three|four|five|six|seven|eight|nine|ten|\d+)\s+(?:minute|hour|day|week|month|year)s?\s+ago\b/i;
export const READING_TIME_RE = /\b(?:an?|one|two|three|four|five|six|seven|eight|nine|ten|\d+)\s*(?:min(?:ute)?s?|hours?)\s+(?:read|reading)\b/i;
export const BY_PREFIX_RE = /^\s*(?:by\b|written\s+by\b|author\s*:?)/i;
export const BY_COLON_RE = /^\s*by\s*:/i;
export const WRITTEN_BY_RE = /^\s*written\s+by\b/i;
export const WRITTEN_BY_ANY_RE = /\bwritten\s+by\b/i;
export const ARTICLE_WRITTEN_BY_RE = /^\s*(?:this\s+)?article\s+(?:was\s+)?written\s+by\b/i;
export const AUTHOR_LABEL_RE = /^\s*authors?\s*:/i;
export const ACKNOWLEDGEMENTS_RE = /\backnowledg(?:e)?ments?\b/i;
export const CONTRIBUTOR_RE = /\bcontributors?|contributions?\b/i;
export const THANKS_TO_RE = /\b(?:special\s+)?thanks\s+to\b/i;
export const AND_LAST_NAME_RE = /(?:\band\b|&)\s+[\w.'’\-]+(?:\s+[\w.'’\-]+){0,4}[.!]?\s*$/i;
export const ORGANIZATION_AUTHOR_RE = /\b(?:engineering|research|developer|platform|security|infrastructure|applied\s+ai)\s+team\b|\bteam\b/i;
export const AUTHOR_ROLE_RE = /\b(?:engineer|researcher|scientist|member|staff|director|lead|manager|editor)\b/i;
export const PUBLISHED_MARKER_RE = /\b(?:publish(?:ed)?|posted)\b/i;
export const UPDATED_MARKER_RE = /\b(?:updated?|modified)\b/i;
export const MULTIPLE_NAME_RE = /(?:\s(?:and|und|et|y)\s|[,;&/·•])/i;
export const PROFILE_LINK_RE = /(?:\/|\b)(?:author|authors|profile|people|person|team|contributors?)(?:\/|\b)|\/@/i;
export const AUTHOR_ATTRIBUTE_RE = /\b(?:author|authors|contributor|contributors|profile|person|people)\b/i;
export const BYLINE_ATTRIBUTE_RE = /\b(?:byline|written[\s_-]*by)\b/i;

const ROLE_WORDS = new Map(Object.entries({
  author: "author", authors: "author", byline: "byline", contributor: "contributor",
  contributors: "contributor", profile: "profile", person: "person", people: "person",
  team: "team", date: "date", published: "published", publish: "published",
  updated: "updated", update: "updated", modified: "modified", headline: "headline",
  title: "title", summary: "summary", subtitle: "subtitle", subhead: "subtitle",
  standfirst: "summary", dek: "summary", excerpt: "summary", description: "summary",
  lead: "summary", acknowledgement: "acknowledgements", acknowledgements: "acknowledgements",
  acknowledgment: "acknowledgements", acknowledgments: "acknowledgements", content: "content",
}));

export function attributeValues(element) {
  const values = [];
  for (const name of ["class", "id", "itemprop", "rel", "aria-label"]) {
    const value = element.getAttribute(name);
    if (!value) continue;
    const parts = name === "class" || name === "rel" ? value.split(/\s+/) : [value];
    for (const part of parts) if (part) values.push([name, part]);
  }
  return values;
}

function normalizedWords(value) {
  return (value.replace(/([A-Z]+)([A-Z][a-z])/g, "$1 $2")
    .replace(/([a-z0-9])([A-Z])/g, "$1 $2")
    .match(/\w+/g) ?? [])
    .map((word) => word.toLowerCase())
    .filter((word) => word.replaceAll("_", "") && (word.length > 1 || word === "by"));
}

export function attributeSemanticTokens(element) {
  const tokens = [];
  for (const [name, value] of attributeValues(element)) {
    const words = normalizedWords(value);
    const set = new Set(words);
    if (set.has("written") && set.has("by")) tokens.push("role:byline");
    for (const word of words) if (ROLE_WORDS.has(word)) tokens.push(`role:${ROLE_WORDS.get(word)}`);
    if (name === "itemprop") {
      if (set.has("author")) tokens.push("schema:author");
      if (set.has("name")) tokens.push("schema:name");
      if (set.has("headline")) tokens.push("schema:headline");
      if (set.has("date") && set.has("published")) tokens.push("schema:date_published");
      if (set.has("date") && set.has("modified")) tokens.push("schema:date_modified");
    } else if (name === "rel" && set.has("author")) tokens.push("relation:author");
    else if (name === "aria-label") {
      for (const marker of ["author", "published", "updated"]) {
        if (set.has(marker)) tokens.push(`accessible:${marker}`);
      }
    }
  }
  return [...new Set(tokens)];
}

export function personNameShape(text, words, knownCapitalized) {
  const capitalized = knownCapitalized ?? words.filter((word) =>
    word && /\p{L}/u.test(word[0]) && word[0] === word[0].toUpperCase()).length;
  return /^@[\w.-]+$/u.test(text.trim()) ||
    (words.length >= 1 && words.length <= 8 && capitalized / words.length >= 0.5 && !DATE_LIKE_RE.test(text));
}

export function textShapeTokens(element, text, linkCount, knownWords, knownLength, knownCapitalized) {
  const candidateText = text ?? elementText(element);
  const words = knownWords ?? (candidateText ? candidateText.split(/\s+/) : []);
  const tokens = [];
  if (words.length > 0 && words.length <= 20) tokens.push("shape:short_text");
  else if (words.length <= 40) tokens.push("shape:medium_text");
  else if (words.length <= 100) tokens.push("shape:long_text");
  else tokens.push("shape:very_long_text");
  const links = linkCount ?? element.querySelectorAll("a").length;
  if (links === 1) tokens.push("shape:one_link");
  else if (links > 1) tokens.push("shape:multiple_links");
  if ((knownLength ?? [...candidateText].length) > 500 || words.length > 80) return [...new Set(tokens)];
  if (BY_PREFIX_RE.test(candidateText)) tokens.push("phrase:by_prefix");
  if (BY_COLON_RE.test(candidateText)) tokens.push("phrase:by_colon");
  if (WRITTEN_BY_ANY_RE.test(candidateText)) tokens.push("phrase:written_by");
  if (ARTICLE_WRITTEN_BY_RE.test(candidateText)) tokens.push("phrase:article_written_by");
  if (AUTHOR_LABEL_RE.test(candidateText)) tokens.push("phrase:author_label");
  if (ACKNOWLEDGEMENTS_RE.test(candidateText)) tokens.push("phrase:acknowledgements");
  if (CONTRIBUTOR_RE.test(candidateText)) tokens.push("phrase:contributors");
  if (THANKS_TO_RE.test(candidateText)) tokens.push("phrase:thanks_to");
  if (PUBLISHED_MARKER_RE.test(candidateText)) tokens.push("phrase:published");
  if (UPDATED_MARKER_RE.test(candidateText)) tokens.push("phrase:updated");
  if (!/^h[1-6]$|^title$/i.test(element.localName) &&
      personNameShape(candidateText, words, knownCapitalized)) {
    tokens.push("shape:single_name");
  }
  if (MULTIPLE_NAME_RE.test(candidateText)) tokens.push("shape:multiple_names");
  if (candidateText.includes(",")) tokens.push("shape:comma_list");
  if (AND_LAST_NAME_RE.test(candidateText)) tokens.push("shape:and_last_name");
  if (/^\s*@[\w.-]+\s*$/u.test(candidateText)) tokens.push("shape:handle");
  if (ORGANIZATION_AUTHOR_RE.test(candidateText)) tokens.push("shape:organization");
  if (AUTHOR_ROLE_RE.test(candidateText)) tokens.push("shape:name_and_role");
  const hasDate = DATE_LIKE_RE.test(candidateText);
  const reading = READING_TIME_RE.test(candidateText);
  if (hasDate) tokens.push("shape:absolute_date");
  if (RELATIVE_DATE_RE.test(candidateText)) tokens.push("shape:relative_date");
  if (reading) tokens.push("shape:reading_time");
  if (hasDate && reading) tokens.push("shape:date_plus_reading_time");
  return [...new Set(tokens)];
}
