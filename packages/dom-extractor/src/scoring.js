import { elementText } from "./dom.js";
import { featurizePage } from "./features.js";
import { refineAuthor } from "./author-boundary.js";

function linear(input, weight, bias) {
  return weight.map((row, outputIndex) => {
    let value = bias[outputIndex];
    for (let index = 0; index < row.length; index += 1) value += row[index] * input[index];
    return value;
  });
}

const relu = (values) => values.map((value) => Math.max(0, value));

function averageEmbedding(tokenIds, embedding) {
  const total = Array(embedding[0].length).fill(0);
  let count = 0;
  for (const tokenId of tokenIds) {
    if (!tokenId) continue;
    count += 1;
    for (let column = 0; column < total.length; column += 1) {
      total[column] += embedding[tokenId][column];
    }
  }
  return total.map((value) => value / Math.max(1, count));
}

function scoreCandidate(features, index, weights) {
  const input = [
    ...weights.tagEmbedding[features.tagIds[index]],
    ...weights.tagEmbedding[features.parentTagIds[index]],
    ...weights.tagEmbedding[features.grandparentTagIds[index]],
    ...weights.tagEmbedding[features.previousTagIds[index]],
    ...weights.tagEmbedding[features.nextTagIds[index]],
    ...averageEmbedding(features.attributeTokenIds[index], weights.semanticEmbedding),
    ...averageEmbedding(features.textShapeTokenIds[index], weights.semanticEmbedding),
    ...features.numeric[index],
  ];
  const hidden0 = relu(linear(input, weights.linear0.weight, weights.linear0.bias));
  const hidden1 = relu(linear(hidden0, weights.linear1.weight, weights.linear1.bias));
  return linear(hidden1, weights.output.weight, weights.output.bias);
}

export function predict(page, model) {
  const features = featurizePage(page, model);
  const predictions = Object.fromEntries(model.fields.map((field) => [field, null]));
  if (page.candidates.length === 0) return { predictions, features, scores: [] };
  const scores = page.candidates.map((_, index) => scoreCandidate(features, index, model.weights));
  for (let fieldIndex = 0; fieldIndex < model.fields.length; fieldIndex += 1) {
    const field = model.fields[fieldIndex];
    let bestIndex = 0;
    for (let index = 1; index < scores.length; index += 1) {
      if (scores[index][fieldIndex] > scores[bestIndex][fieldIndex]) bestIndex = index;
    }
    predictions[field] = model.weights.missingScores[fieldIndex] > scores[bestIndex][fieldIndex]
      ? null : page.candidates[bestIndex].nodeId;
  }
  const titleId = predictions.title;
  const title = page.candidates.find((candidate) => candidate.nodeId === titleId)?.element;
  if (title && !elementText(title)) {
    const titleIndex = model.fields.indexOf("title");
    let best = -1;
    for (let index = 0; index < page.candidates.length; index += 1) {
      const element = page.candidates[index].element;
      if ((element.localName.toLowerCase() === "h1" ||
           (element.getAttribute("itemprop") ?? "").toLowerCase().includes("headline")) &&
          elementText(element) && (best < 0 || scores[index][titleIndex] > scores[best][titleIndex])) {
        best = index;
      }
    }
    if (best >= 0) predictions.title = page.candidates[best].nodeId;
  }
  const basePredictions = { ...predictions };
  if (predictions.authors !== null) {
    const authorIndex = model.fields.indexOf("authors");
    predictions.authors = refineAuthor(
      page, predictions.authors, scores.map((row) => row[authorIndex]), features.numeric,
      model.authorBoundary,
    );
  }
  return { predictions, basePredictions, features, scores };
}
