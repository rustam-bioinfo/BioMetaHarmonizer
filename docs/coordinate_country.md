# Coordinate-derived country lookup: foundation only

This component is not connected to the postprocess flag yet. Normal pipeline
behavior and existing extended outputs are unchanged by this preparatory commit.

## Implemented

* Offline point-in-polygon lookup with Shapely 2 and a reusable STRtree index.
* Canonical country names supplied through an explicit ISO-code/name dictionary.
* An explicit property-name mapping rather than guessed country-code attributes.
* Abstention for boundary points, conflicting overlaps, unmapped codes, and points
  outside all polygons. Unmapped polygons are retained, not silently discarded.
* Polygon validity, ring closure, geographic extent, and antimeridian checks.
* A file loader that requires an expected Git blob checksum and explicit EPSG:4326.
* Per-instance caching for repeated coordinate pairs.

Install the optional dependency in an editable checkout with:

```bash
python -m pip install -e ".[geo]"
```

A failed country lookup does not establish that a point is offshore or in open
ocean. The reference is a cartographic dataset, not a legal boundary authority.
The reference-layer CRS does not establish the datum of submitted sample points.
Any sample-datum assumption must be documented when this component is integrated.

## Pinned source and outstanding work

schemas/country_reference_source.json identifies the upstream map-unit GeoJSON
by immutable commit, Git blob SHA-1, and byte size. Its commit message is v5.1.0
packaging. No actual boundary dataset is bundled in this commit.

The upstream file is approximately 13.5 MB and could not be retrieved in full
through the available connector. Therefore its country-code property, full ISO
crosswalk, CRS metadata, real-world assignments, and geometry validity have not
been audited. The null country_code_field and false audit flags are intentional.

Before integration:

1. Obtain the exact file at the manifest source_url and verify its Git blob hash.
2. Inspect actual attributes and CRS; audit missing, special and conflicting codes.
3. Resolve any invalid or antimeridian-crossing geometries explicitly, recording
   the changes and the checksum of the resulting resource.
4. Validate real-world mainland, island, territory, border and offshore examples.
5. Connect only missing standard-oriented country fields to lookup results;
   retain native geography, historical annotations, and classifier predictions.

A Git blob SHA-1 hashes the bytes prefixed by "blob ", the decimal byte count,
and a NUL byte. It is not the same as the ordinary SHA-1 of the file.

Synthetic geometry tests exercise the engine but do not validate Natural Earth
coverage or the correctness of assignments on the evaluated BioSample datasets.
