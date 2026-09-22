"""Small neural DOM-node selector."""

from __future__ import annotations

import torch
from torch import nn

from eng_universe.extraction.contract import FIELDS


class DOMNodeSelector(nn.Module):
    """Score every candidate for each field plus a learned missing option."""

    def __init__(
        self,
        tag_count: int,
        numeric_feature_count: int = 40,
        semantic_token_count: int = 2,
        embedding_dim: int = 6,
        semantic_embedding_dim: int = 3,
        hidden_dim: int = 36,
        field_count: int = len(FIELDS),
    ) -> None:
        super().__init__()
        self.tag_embedding = nn.Embedding(tag_count, embedding_dim, padding_idx=0)
        self.semantic_embedding = nn.Embedding(
            semantic_token_count, semantic_embedding_dim, padding_idx=0
        )
        self.network = nn.Sequential(
            nn.Linear(
                (embedding_dim * 5) + semantic_embedding_dim + numeric_feature_count,
                hidden_dim,
            ),
            nn.ReLU(),
            nn.Linear(hidden_dim, hidden_dim),
            nn.ReLU(),
            nn.Linear(hidden_dim, field_count),
        )
        self.missing_scores = nn.Parameter(torch.zeros(field_count))

    def forward(
        self,
        tag_ids: torch.Tensor,
        parent_tag_ids: torch.Tensor,
        grandparent_tag_ids: torch.Tensor,
        previous_tag_ids: torch.Tensor,
        next_tag_ids: torch.Tensor,
        semantic_token_ids: torch.Tensor,
        numeric: torch.Tensor,
        candidate_mask: torch.Tensor | None = None,
    ) -> torch.Tensor:
        semantic_mask = semantic_token_ids.ne(0).unsqueeze(-1)
        semantic_sum = (
            self.semantic_embedding(semantic_token_ids) * semantic_mask
        ).sum(dim=-2)
        semantic_average = semantic_sum / semantic_mask.sum(dim=-2).clamp_min(1)
        features = torch.cat(
            (
                self.tag_embedding(tag_ids),
                self.tag_embedding(parent_tag_ids),
                self.tag_embedding(grandparent_tag_ids),
                self.tag_embedding(previous_tag_ids),
                self.tag_embedding(next_tag_ids),
                semantic_average,
                numeric,
            ),
            dim=-1,
        )
        candidate_scores = self.network(features)
        if candidate_mask is not None:
            candidate_scores = candidate_scores.masked_fill(
                ~candidate_mask.unsqueeze(-1), torch.finfo(candidate_scores.dtype).min
            )
        missing = self.missing_scores.view(1, 1, -1).expand(
            candidate_scores.shape[0], -1, -1
        )
        return torch.cat((candidate_scores, missing), dim=1)
