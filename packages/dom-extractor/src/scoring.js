import { elementText } from "./dom.js";
import { featurizePage } from "./features.js";
import { refineAuthor } from "./author-boundary.js";

const compiledWeights = new WeakMap();

function packedLayer(layer) {
  return {
    weights: Float32Array.from(layer.weight.flat()),
    width: layer.weight[0].length,
    rows: layer.weight.length,
    bias: layer.bias,
  };
}

function packedWeights(weights) {
  let packed = compiledWeights.get(weights);
  if (!packed) {
    packed = {
      linear0: packedLayer(weights.linear0),
      linear1: packedLayer(weights.linear1),
      output: packedLayer(weights.output),
    };
    compiledWeights.set(weights, packed);
  }
  return packed;
}

function linear(input, layer, output, useRelu = false) {
  const { weights, width, rows, bias } = layer;
  for (let outputIndex = 0; outputIndex < rows; outputIndex += 1) {
    let value = bias[outputIndex];
    const start = outputIndex * width;
    for (let index = 0; index < width; index += 1) value += weights[start + index] * input[index];
    output[outputIndex] = useRelu ? Math.max(0, value) : value;
  }
}

function averageEmbeddingInto(tokenIds, embedding, output, offset) {
  const width = embedding[0].length;
  for (let column = 0; column < width; column += 1) output[offset + column] = 0;
  let count = 0;
  for (const tokenId of tokenIds) {
    if (!tokenId) continue;
    count += 1;
    for (let column = 0; column < width; column += 1) {
      output[offset + column] += embedding[tokenId][column];
    }
  }
  for (let column = 0; column < width; column += 1) {
    output[offset + column] /= Math.max(1, count);
  }
  return offset + width;
}

function scoreCandidate(features, index, weights, packed, scratch) {
  const { input, hidden0, hidden1, output } = scratch;
  let offset = 0;
  for (const tagId of [
    features.tagIds[index], features.parentTagIds[index], features.grandparentTagIds[index],
    features.previousTagIds[index], features.nextTagIds[index],
  ]) {
    input.set(weights.tagEmbedding[tagId], offset);
    offset += weights.tagEmbedding[tagId].length;
  }
  offset = averageEmbeddingInto(features.attributeTokenIds[index], weights.semanticEmbedding,
    input, offset);
  offset = averageEmbeddingInto(features.textShapeTokenIds[index], weights.semanticEmbedding,
    input, offset);
  input.set(features.numeric[index], offset);
  linear(input, packed.linear0, hidden0, true);
  linear(hidden0, packed.linear1, hidden1, true);
  linear(hidden1, packed.output, output);
  return Array.from(output);
}

export function predict(page, model) {
  const features = featurizePage(page, model);
  const predictions = Object.fromEntries(model.fields.map((field) => [field, null]));
  if (page.candidates.length === 0) return { predictions, features, scores: [] };
  const weights = model.weights;
  const packed = packedWeights(weights);
  const scratch = {
    input: new Float64Array(packed.linear0.width),
    hidden0: new Float64Array(packed.linear0.rows),
    hidden1: new Float64Array(packed.linear1.rows),
    output: new Float64Array(packed.output.rows),
  };
  const scores = page.candidates.map((_, index) => scoreCandidate(features, index, weights, packed, scratch));
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
