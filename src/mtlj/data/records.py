"""Canonical internal dataset record format.

Every dataset loader (one per data source) converts its source-specific
format into this one internal format. Mutation operators only ever read
this format; they never contain source-specific parsing logic.
"""

from pydantic import BaseModel, Field


class Passage(BaseModel):
    """A single supporting passage/context block for a record."""

    passage_id: str = Field(..., description="Unique id for this passage within its source.")
    text: str = Field(..., description="The raw passage text.")
    title: str | None = Field(None, description="Optional title/heading for the passage.")


class DatasetRecord(BaseModel):
    """The single internal format every loader must produce.

    One record = one (question, context, answer) unit fed into the
    mutation pipeline.
    """

    record_id: str = Field(..., description="Globally unique id, e.g. 'hotpotqa_000123'.")
    source_dataset: str = Field(..., description="Name of the originating dataset, e.g. 'fever'.")
    question: str = Field(..., description="The question or prompt associated with this record.")
    context: list[Passage] = Field(
        ...,
        min_length=1,
        description="One or more supporting passages. Always a list, even for one passage.",
    )
    answer: str = Field(..., description="The known-good/ground-truth answer text.")
    metadata: dict = Field(
        default_factory=dict,
        description="Optional free-form metadata from the source dataset.",
    )

    model_config = {"extra": "forbid"}
