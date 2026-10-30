"""The semi-automatic improvement loop of the data agent (decision record 009).

A failing run of an evaluation batch becomes a failure card; a proposal (from the ``improver`` profile or
written by hand) changes the knowledge base or drafts a new prompt version; deterministic guards check it;
a screen runs the affected cases on a candidate copy of the knowledge base, paired against the source
batch; the packet goes to the owner, who adopts or rejects. Nothing here writes to ``kb/`` or ``prompts/``
except ``decide`` on the owner's word.
"""
