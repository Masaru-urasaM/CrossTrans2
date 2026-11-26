"""Shared attachment protocol for both desktop renderers."""
from src.core.source_marker import stated_clause


def build_attachment_prompt(target_lang, original, image_filenames, file_contents,
                            custom_prompt=None, source_language=None):
    file_list_text = ''
    if image_filenames:
        file_list_text += 'Images: ' + ', '.join(image_filenames) + '\n'
    if file_contents:
        file_list_text += 'Text files: ' + ', '.join(file_contents.keys()) + '\n'
    base_instruction = f'Translate to {target_lang}.'
    if custom_prompt:
        base_instruction += f' {custom_prompt}'
    prompt = f"""{base_instruction}

You have received the following files:
{file_list_text}
{f'Additional context from user: {original}' if original else ''}

CRITICAL INSTRUCTIONS:
1. For IMAGES: Perform OCR - extract ALL visible text EXACTLY as it appears.
   - Do NOT describe the image
   - Do NOT say "The image shows..." or "This appears to be..."
   - ONLY output the actual text you can read
   - Preserve the original formatting, line breaks, and structure

2. For TEXT FILES: Use the provided content directly.

3. Then translate all extracted text.

=== EXAMPLE (CORRECT) ===
If image shows a restaurant menu:
**[menu.jpg]:**
Today's Special
Grilled Salmon $18.99
Caesar Salad $12.50
---
NOT: "The image shows a restaurant menu with prices listed."

=== EXAMPLE (CORRECT) ===
If image shows a business document:
**[document.png]:**
Meeting Notes - January 15, 2026
Attendees: John, Mary, Bob
Action Items:
1. Review budget proposal
2. Schedule follow-up
---
NOT: "This is a business document containing meeting notes."

Return your response in this EXACT format:

===ORIGINAL===
**[filename1]:**
[For images: ALL text extracted via OCR, exactly as written]
[For text files: the original content]

**[filename2]:**
[extracted text]

===TRANSLATION===
**[filename1]:**
[translated text in {target_lang}]

**[filename2]:**
[translated text in {target_lang}]

IMPORTANT: Translate ALL text to {target_lang}. Process ALL files. Extract actual text from images (OCR), do not describe them."""
    if source_language:
        prompt += stated_clause(source_language).text
    return prompt


def split_attachment_answer(result):
    """Keep the established plain-answer fallback when the model omits markers."""
    if '===ORIGINAL===' in result and '===TRANSLATION===' in result:
        parts = result.split('===TRANSLATION===')
        return parts[0].replace('===ORIGINAL===', '').strip(), parts[1].strip()
    return '', result
