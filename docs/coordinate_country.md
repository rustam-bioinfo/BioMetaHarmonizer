# Coordinate-derived country lookup: packaging stage

The country lookup is not connected to post-processing yet. The existing
postprocess flag still performs coordinate parsing, health-state extraction,
and standards aliases, but not coordinate-derived country assignment.

## Compressed reference

The prepared binary resource belongs at this repository path:

```
src/biometaharmonizer/data/country_polygons.geojson.gz
```

It is 4,784,521 bytes. Upload the prepared gzip file without decompressing or
recompressing it. Its SHA-256 must be:

```
c9409375f93f613d3b616d3d3a5395225995884ed483d4ab6e4e7143edb6f69a
```

The GitHub connector exposes text-content writes, not a binary-file upload.
Therefore this binary is provided separately for a one-time upload to the
reviewer-revisions branch. The accompanying text commit does not add it.
Package-data configuration includes data/*.geojson.gz once the file is present.

## Preparation and audit

The manifest in schemas/country_reference_source.json records the pinned source,
compressed and uncompressed checksums, preparation software, and corrections.
The original uploaded Natural Earth file is not modified.

* The polygon layer declares OGC CRS84: WGS84 longitude/latitude.
* ISO_A2_EH is the base crosswalk; BMH_ISO_A2 contains the audited assignments.
* Nine US minor outlying island units use UM; Jan Mayen uses SJ.
* Egypt's invalid geometry was repaired using Shapely make_valid.
* All 298 polygons are retained: 280 mapped features and 18 unmapped features.
* The mapping represents 249 distinct ISO alpha-2 codes, not 249 sovereign states.
* The audit used system tzdata iso3166.tab. Runtime canonical names and accepted
  codes come from pycountry.
* Seventeen of eighteen illustrative spatial checks matched expected outcomes.
  The Longyearbyen example returned no polygon match. These are not accuracy
  estimates; coastlines and small features remain a limitation.

The loader verifies compressed size and SHA-256, bounded decompression,
uncompressed size and SHA-256, feature count, CRS metadata, and audit flags.
It then constructs the existing Shapely country lookup and caches it per process.
It does not download files or repair geometry during a user run.

## Python interface after uploading the resource

```python
from biometaharmonizer.country_reference import load_bundled_country_lookup

lookup = load_bundled_country_lookup()
result = lookup.lookup(48.8566, 2.3522)
```

Install geometry support in an editable checkout with:

```bash
python -m pip install -e ".[geo]"
```

Missing resource files raise a clear error. Boundaries, ambiguous overlaps,
unmapped units, and points outside all polygons remain unresolved. A failed
lookup does not establish that a point is offshore or in open ocean.

Next integration must retain native geography and classifier results, document
sample-datum assumptions, and record provenance for any filled standard fields.
