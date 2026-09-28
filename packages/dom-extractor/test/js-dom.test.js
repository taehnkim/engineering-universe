import assert from "node:assert/strict";
import test from "node:test";
import { elementText, parsePage } from "../src/dom.js";

test("JS preparation retains article candidates after leading scripts", () => {
  const html = `<!DOCTYPE html><script>window.beforeHtml = true;</script>
    <html><body><h1>Real title</h1><article><p>Real article.</p></article></body></html>`;
  const page = parsePage(html);
  assert.equal(page.document.documentElement.localName, "html");
  assert.ok(page.candidates.some((candidate) => candidate.element.localName === "article"));
});

test("JS preparation skips commented-out html tags before the real root", () => {
  const html = `<!DOCTYPE html><!--[if IE 6]><html id="old"><![endif]-->
    <script>window.beforeHtml = true;</script>
    <html><body><h1>Real title</h1><article><p>Real article.</p></article></body></html>`;
  const page = parsePage(html);
  assert.equal(page.document.documentElement.localName, "html");
  assert.equal(page.document.documentElement.querySelectorAll("html").length, 0);
  assert.ok(page.candidates.some((candidate) => candidate.element.localName === "article"));
});

test("JS preparation ignores a notice before the page root", () => {
  const html = `<div class="old-browser-notice">Old browser warning</div>
    <!doctype html><html><body><h1>Real title</h1>
    <article><p>Real article.</p></article></body></html>`;
  const page = parsePage(html);
  assert.equal(page.document.documentElement.localName, "html");
  assert.ok(page.candidates.some((candidate) => candidate.element.localName === "article"));
  assert.ok(!elementText(page.document.documentElement).includes("Old browser warning"));
});

test("JS preparation removes image-linked home-brand text, not the article title", () => {
  const html = `<html><body><h1><a href="/">Engineering<img src="logo.png"></a></h1>
    <h1>Real title</h1><p>By Ada Lovelace</p><article><p>Real article.</p></article></body></html>`;
  const page = parsePage(html);
  const headings = page.candidates.filter((candidate) => candidate.element.localName === "h1");
  assert.deepEqual(headings.map((candidate) => elementText(candidate.element)), ["", "Real title"]);
});

test("JS preparation keeps a hero-overlay h1 but removes popup headings", () => {
  const html = `<html><body><main>
    <div class="image-overlay"><h1>Real article title</h1></div>
    <div class="popup-overlay"><h1>Subscribe now</h1></div>
    <div class="image-overlay" role="dialog"><h1>Sign in</h1></div>
    <div class="image-overlay" aria-hidden="true"><h1>Hidden title</h1></div>
    <article><p>Article body</p></article>
  </main></body></html>`;
  const page = parsePage(html);
  const headings = page.candidates.filter((candidate) => candidate.element.localName === "h1");
  assert.deepEqual(headings.map((candidate) => elementText(candidate.element)), ["Real article title"]);
});
