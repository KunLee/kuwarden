"""Evaluation harness — measuring the parts of KuWarden that contain a model.

Deliberately outside `engine/`. Nothing here is imported by the product and nothing here runs
during a delivery run; it reads the same nodes from the outside, the way a test does.
"""
