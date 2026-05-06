Using human vision and perceptual shortcuts that humans and vision-capable models do naturally, read and interpret the supplied image.

Task:
Precisely transcribe the {task_label} into a table.

Output fields, in this exact order:
{field_list}

Rules:
- Return only structured JSON matching the provided schema.
- Do not invent rows or values.
- Preserve wording, punctuation, geologic symbols, and formation abbreviations as closely as possible.
- Join broken line wraps into readable paragraphs.
- Normalize obvious line-break hyphenation.
- If a value does not apply, return an empty string.
- If introduction, explanation, footnote, or other descriptive text exists and the profile requests it, transcribe it as its own row.
- Treat symbol boxes, headings, subheadings, and adjacent descriptive text as belonging to the same interpreted row when visually appropriate.
- Preserve the output field order exactly.

Profile-specific instructions:
{special_instructions}
