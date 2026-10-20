# Optional standards-oriented post-processing

The run command's existing postprocess flag enables this transformation. Without
that flag, native pipeline behavior and the 53-column schema remain unchanged.

Post-processing preserves all original columns, row order, and One Health results.
It appends the three coordinate/health fields and the seven aliases below, giving
63 columns for an input with the canonical 53-column schema. Aliases are naming
correspondences, not certification of MIxS or Darwin Core compliance.

| Native field | Additional output field | Rule |
| :--- | :--- | :--- |
| sequencing_method | seq_meth | Copy submitted sequencing technology or machine description. |
| assembly_method | assembly_software | Copy submitted assembly software description; do not infer absent versions or parameters. |
| host_disease | host_disease_stat | Copy submitted disease description, not host_health_state. No diagnosis or ontology validation is implied. |
| geo_country | country | Copy resolved country text without additional canonicalization. |
| geo_iso3166 | countryCode | Trim whitespace, uppercase, and export only codes in the installed pycountry ISO alpha-2 database. |
| geo_sea_ocean | waterBody | Copy the recognized water-body name. |
| geo_loc_name | locality | Copy retained BioSample location text directly, even if structured parsing failed. |

The original geo_region and geo_locality remain unchanged. locality is not
reconstructed from them. Neither stateProvince nor verbatimLocality is added.

HISTORICAL, XK, unknown codes, and non-string codes produce a missing countryCode;
the original geo_iso3166 value is preserved. NA remains a valid code for Namibia.
This filter does not verify that a sample actually originated in the named country.

Missing source columns produce empty alias columns. Existing target columns are
rejected rather than overwritten. Full controlled-vocabulary, structured-value,
and submission-checklist validation is outside this transformation.

The existing diagnostic report continues to describe coordinate parsing and
health-state issues. This change adds no geographic reconciliation, reverse
geocoding, administrative-region inference, or marine-jurisdiction assignment.

## Term references

* seq_meth: https://w3id.org/mixs/0000050
* assembly_software: https://w3id.org/mixs/0000058
* host_disease_stat: https://w3id.org/mixs/0000031
* Darwin Core locality: http://rs.tdwg.org/dwc/terms/locality
* Darwin Core country: http://rs.tdwg.org/dwc/terms/country
* Darwin Core countryCode: http://rs.tdwg.org/dwc/terms/countryCode
* Darwin Core waterBody: http://rs.tdwg.org/dwc/terms/waterBody

MIxS definitions: https://genomicsstandardsconsortium.github.io/mixs/term_list/
Darwin Core definitions: https://dwc.tdwg.org/terms/
