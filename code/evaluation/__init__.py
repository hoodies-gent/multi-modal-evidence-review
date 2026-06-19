"""Reusable evaluation framework for the claim-verification task.

Decoupled from any specific model/system: it consumes a predictions CSV and
(optionally) a ground-truth CSV, then validates schema conformance and scores
each output field by type. See code/docs/planning.md for the eval spec.
"""
