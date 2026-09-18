"""Small neural DOM-node selector."""

from __future__ import annotations

import torch
from torch import nn


class DOMNodeSelector(nn.Module):
    """Score every candidate for four fields plus a learned missing option."""

    def __init__(
        self,
        tag_count: int,
        numeric_feature_count: int = 7,
        embedding_dim: int = 8,
        hidden_dim: int = 64,
        field_count: int = 4,
    ) -> None:
        super().__init__()
        self.tag_embedding = nn.Embedding(tag_count, embedding_dim, padding_idx=0)
        self.network = nn.Sequential(
            nn.Linear((embedding_dim * 2) + numeric_feature_count, hidden_dim),
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
        numeric: torch.Tensor,
        candidate_mask: torch.Tensor | None = None,
    ) -> torch.Tensor:
        features = torch.cat(
            (
                self.tag_embedding(tag_ids),
                self.tag_embedding(parent_tag_ids),
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
