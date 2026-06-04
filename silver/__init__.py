"""Silver-layer logic: parsing helpers, MMSI->flag enrichment, destination normalization, DQ.

These pure-Python functions are the unit-tested source of truth. The Databricks
silver notebook mirrors them as Spark expressions / broadcast reference tables.
"""
