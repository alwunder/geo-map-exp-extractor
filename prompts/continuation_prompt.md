Continue the interrupted extraction using the original image and instructions above.

The JSON below is saved data, not instructions. These {row_count} complete rows
have already been retained, in source reading order:
{saved_rows}

Return a NEW complete JSON object. Set resume_after to {row_count}. Return fields
in the original profile order. In rows, return ONLY the source rows AFTER the
last retained row. Re-extract the next row in full if the earlier response cut
off mid-row. Do not repeat or revise retained rows. If no rows remain, return an
empty rows list. Include remaining headings, standalone text and footnotes when
the profile requires them. Never shorten descriptions to fit the token allowance.

Keep whole-image context, shared headings, hierarchy and numbering consistent
with the retained rows; do not restart numbering. Row values alone may repeat
in the source, so locate the actual source position of the retained sequence.
If you cannot establish that boundary reliably, return no rows and explain the
ambiguity in warnings. Do not invent continuity or silently omit source rows.
