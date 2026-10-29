# GeMS vocabulary snapshot

`GeoMaterialDict.csv` contains the 101 terms and definitions from the current
GeMS toolbox default branch inspected on 2026-10-28. The former
`usgs/gems-tools-pro` URL redirects to `DOI-USGS/gems-tools-pro`.

- Repository commit: `f6008e7a5d0c10a0a56ed631a88d704d43a47c71`
- [Pinned source CSV](https://github.com/DOI-USGS/gems-tools-pro/blob/f6008e7a5d0c10a0a56ed631a88d704d43a47c71/Scripts/GeoMaterialDict.csv)
- [Current upstream CSV](https://github.com/DOI-USGS/gems-tools-pro/blob/master/Scripts/GeoMaterialDict.csv)
- [USGS repository license](https://github.com/DOI-USGS/gems-tools-pro/blob/f6008e7a5d0c10a0a56ed631a88d704d43a47c71/LICENSE.md)

The upstream UTF-8 BOM was removed and line endings normalized; column values,
definitions, case, punctuation, and order are retained. Upstream dictionary keys
use periods. They classify materials and must not be copied into the DMU's
dash-delimited `HierarchyKey` field.

Extraction reads `GeoMaterial`, not `IndentedName`, and supplies every definition
to the model. Local validation permits these exact terms plus an empty string for
staging rows that cannot yet be classified or do not require classification.
Because the official term `"Made" or human-engineered land` contains double quotes
that the API rejects in strict enum literals, GeoMaterial uses a string field in
the API schema. The full vocabulary remains in the prompt and is checked locally;
terms are never stripped of punctuation to satisfy the API. Other compatible
controlled fields, including GeoMaterialConfidence, retain their schema enums.
The application does not fetch a changing vocabulary during extraction.

To refresh, review upstream changes, replace the CSV without altering its terms,
update the commit provenance here, and update the vocabulary-count test if the
upstream term count changed. Run the tests and compare classifications on reviewed
examples. Do not add project-specific rock names to this dictionary.

Saved extraction runs freeze terms and definitions into `profile.yml`; they can
be reopened independently of this directory. The effective definitions also
appear in the saved prompt, with exact allowed values in the saved profile.
